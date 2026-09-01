"""Phase 86: artifact-store drill evidence recording and candidate-gate binding."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import pytest
import yaml

from obsion.config import Environment, Settings
from obsion.main import _uses_memory_object_store
from obsion.release.artifact_drill import (
    ObjectStat,
    _canonical_digest,
    load_artifact_drill_contract,
    record_artifact_drill_evidence,
    validate_artifact_drill_evidence,
)
from obsion.release.candidate import ReleaseCandidateError, validate_release_candidate
from obsion.release.drill import CommandResult, DrillError
from obsion.release.notes import validate_release_notes

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "docs" / "release" / "alpha1-artifact-drill-evidence-contract.yaml"
GATES = ROOT / "docs" / "release" / "alpha1-candidate-gates.yaml"
LEDGER = ROOT / "docs" / "release" / "evidence" / "alpha1" / "artifact-store-drill.yaml"
REVISION = "c" * 40
ENV = {"OBSION_DR_DRILL": "1"}
HEAD = "abc123def456"
EXPECTED_CHECKS = (
    "source-migrated",
    "objects-seeded",
    "snapshot-created",
    "restore-completed",
    "object-count-parity",
    "content-checksum-parity",
    "metadata-parity",
    "database-consistency",
)


@dataclass(slots=True)
class _StoredObject:
    data: bytes
    content_type: str
    metadata: dict[str, str]


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


_KNOWLEDGE_KEY = "00000000-0000-7000-8000-000000000001/knowledge/document/version"
_KNOWLEDGE_DATA = b"# Drill policy\nArtifact bytes must survive every restore.\n"
_ARTIFACT_KEY = "00000000-0000-7000-8000-000000000001/artifacts/workspace/file"
_ARTIFACT_DATA = b"# Runbook\nSnapshot the bucket, restore it, verify every checksum.\n"


def _seeded_objects() -> dict[str, _StoredObject]:
    return {
        _KNOWLEDGE_KEY: _StoredObject(
            _KNOWLEDGE_DATA,
            "text/markdown",
            {"sha256": _sha(_KNOWLEDGE_DATA), "classification": "INTERNAL"},
        ),
        _ARTIFACT_KEY: _StoredObject(
            _ARTIFACT_DATA,
            "text/markdown",
            {"sha256": _sha(_ARTIFACT_DATA), "classification": "INTERNAL"},
        ),
    }


class _FakeBucketClient:
    def __init__(
        self,
        store: dict[str, _StoredObject],
        *,
        fail_put: bool = False,
        raise_on_get: bool = False,
        drop_key: str | None = None,
        corrupt_get: bool = False,
        corrupt_stat: bool = False,
    ) -> None:
        self._store = store
        self._fail_put = fail_put
        self._raise_on_get = raise_on_get
        self._drop_key = drop_key
        self._corrupt_get = corrupt_get
        self._corrupt_stat = corrupt_stat

    def ensure_bucket(self) -> None:
        return None

    def list_keys(self) -> list[str]:
        return sorted(key for key in self._store if key != self._drop_key)

    def stat(self, key: str) -> ObjectStat:
        item = self._store[key]
        metadata = dict(item.metadata)
        if self._corrupt_stat:
            metadata["classification"] = "PUBLIC"
        return ObjectStat(len(item.data), item.content_type, metadata)

    def get(self, key: str) -> bytes:
        if self._raise_on_get:
            raise RuntimeError("read failed")
        data = self._store[key].data
        return data + b"tampered" if self._corrupt_get else data

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> None:
        if self._fail_put:
            raise RuntimeError("write failed")
        self._store[key] = _StoredObject(data, content_type, dict(metadata))


def _factory(
    stores: dict[str, dict[str, _StoredObject]],
    *,
    source_raise_on_get: bool = False,
    target_fail_put: bool = False,
    target_drop_key: str | None = None,
    target_corrupt_get: bool = False,
    target_corrupt_stat: bool = False,
):
    def create(endpoint: str, access_key: str, secret_key: str, bucket: str) -> _FakeBucketClient:
        store = stores.setdefault(endpoint, {})
        if endpoint.endswith("55901"):
            return _FakeBucketClient(
                store,
                fail_put=target_fail_put,
                drop_key=target_drop_key,
                corrupt_get=target_corrupt_get,
                corrupt_stat=target_corrupt_stat,
            )
        return _FakeBucketClient(store, raise_on_get=source_raise_on_get)

    return create


def _seeded_stores() -> dict[str, dict[str, _StoredObject]]:
    return {"127.0.0.1:55900": _seeded_objects()}


def _runner(
    *,
    docker_rc: int = 0,
    pg_run_rc: int = 0,
    src_run_rc: int = 0,
    tgt_run_rc: int = 0,
    migrate_rc: int = 0,
    artifact_ref_count: str = "1",
    version_ref_count: str = "1",
    artifact_ref_lines: list[str] | None = None,
    version_ref_lines: list[str] | None = None,
):
    artifact_lines = (
        artifact_ref_lines
        if artifact_ref_lines is not None
        else [f"{_ARTIFACT_KEY}|{_sha(_ARTIFACT_DATA)}"]
    )
    version_lines = (
        version_ref_lines
        if version_ref_lines is not None
        else [f"{_KNOWLEDGE_KEY}|{_sha(_KNOWLEDGE_DATA)}"]
    )

    def run(argv: tuple[str, ...], env: Mapping[str, str]) -> CommandResult:
        a = list(argv)
        if a[:2] == ["docker", "version"]:
            return CommandResult(docker_rc, b"27.0.0\n" if docker_rc == 0 else b"", "")
        if a[:2] == ["docker", "run"]:
            name = a[4]
            rc = pg_run_rc if "-pg-" in name else (src_run_rc if "-src-" in name else tgt_run_rc)
            return CommandResult(rc, b"container\n" if rc == 0 else b"", "")
        if a[:2] == ["docker", "port"]:
            name = a[2]
            port = "55432" if "-pg-" in name else ("55900" if "-src-" in name else "55901")
            return CommandResult(0, f"127.0.0.1:{port}\n".encode(), "")
        if a[:3] == ["docker", "rm", "-f"]:
            return CommandResult(0, b"", "")
        if a[0] == "uv":
            return CommandResult(migrate_rc, b"migrated\n" if migrate_rc == 0 else b"", "")
        if a[:2] == ["docker", "exec"]:
            if "pg_isready" in a or "curl" in a:
                return CommandResult(0, b"ok\n", "")
            if "psql" in a:
                query = a[-1]
                if "version_num" in query:
                    return CommandResult(0, f"{HEAD}\n".encode(), "")
                if "|| '|' ||" in query and "FROM artifacts" in query:
                    return CommandResult(0, ("\n".join(artifact_lines) + "\n").encode(), "")
                if "|| '|' ||" in query:
                    return CommandResult(0, ("\n".join(version_lines) + "\n").encode(), "")
                if "count(*)" in query and "FROM artifacts" in query:
                    return CommandResult(0, f"{artifact_ref_count}\n".encode(), "")
                if "count(*)" in query:
                    return CommandResult(0, f"{version_ref_count}\n".encode(), "")
        raise AssertionError(f"unexpected command: {a}")

    return run


def _record(tmp_path: Path, runner=None, seeder=None, factory=None, env=None):
    return record_artifact_drill_evidence(
        CONTRACT,
        tmp_path / "ledger.yaml",
        ROOT,
        env=ENV if env is None else env,
        runner=_runner() if runner is None else runner,
        seeder=(lambda _target: None) if seeder is None else seeder,
        client_factory=_factory(_seeded_stores()) if factory is None else factory,
        revision=REVISION,
    )


def _load_ledger(tmp_path: Path) -> dict:
    return yaml.safe_load((tmp_path / "ledger.yaml").read_text(encoding="utf-8"))


def _recompute_checksum(document: dict) -> None:
    spec = document["spec"]
    document["spec"]["checksum"] = _canonical_digest(
        {**document, "spec": {key: value for key, value in spec.items() if key != "checksum"}}
    )


def test_artifact_contract_binds_pinned_images_and_ordered_checks() -> None:
    contract = load_artifact_drill_contract(CONTRACT, ROOT)
    assert contract.name == "alpha1-artifact-store"
    assert contract.global_opt_in == "OBSION_DR_DRILL"
    assert contract.minimum_objects >= 2
    assert [check.check_id for check in contract.checks] == list(EXPECTED_CHECKS)
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert f"image: {contract.minio_image}" in compose
    assert f"image: {contract.postgres_image}" in compose
    assert contract.minio_image == "quay.io/minio/minio:RELEASE.2025-04-22T22-12-26Z"
    assert contract.postgres_image == "pgvector/pgvector:0.8.6-pg17-bookworm"


def test_recorder_requires_global_opt_in(tmp_path: Path) -> None:
    with pytest.raises(DrillError, match="OBSION_DR_DRILL=1 is required"):
        _record(tmp_path, env={})


def test_recorder_classifies_and_checksums_ledger(tmp_path: Path) -> None:
    summary = _record(tmp_path)
    assert summary["failed"] == []
    assert summary["passed"] == 8
    assert summary["checks"] == 8
    assert set(summary["timings"]) == {
        "migrateSeconds",
        "seedSeconds",
        "snapshotSeconds",
        "restoreSeconds",
        "verifySeconds",
        "totalSeconds",
    }
    ledger = _load_ledger(tmp_path)
    assert ledger["kind"] == "ArtifactDrillEvidenceLedger"
    assert ledger["metadata"]["name"] == "alpha1-artifact-store"
    assert ledger["metadata"]["revision"] == REVISION
    assert ledger["spec"]["alembicHead"] == HEAD
    assert ledger["spec"]["bucket"] == "obsion-artifacts-drill"
    snapshot = ledger["spec"]["snapshot"]
    assert snapshot["objectCount"] == 2
    assert snapshot["totalBytes"] == len(_KNOWLEDGE_DATA) + len(_ARTIFACT_DATA)
    assert re.fullmatch(r"[0-9a-f]{64}", snapshot["manifestSha256"])
    assert re.fullmatch(r"[0-9a-f]{64}", snapshot["objectKeysSha256"])
    assert ledger["spec"]["databaseReferences"] == {"artifacts": 1, "documentVersions": 1}
    entries = {entry["id"]: entry for entry in ledger["spec"]["checks"]}
    assert set(entries) == set(EXPECTED_CHECKS)
    assert all(entry["classification"] == "passed" for entry in entries.values())
    json.dumps(ledger, sort_keys=True)
    result = validate_artifact_drill_evidence(CONTRACT, [tmp_path / "ledger.yaml"], ROOT)
    assert result == {"ledgers": 1, "checks": 8}


def test_recorder_never_records_credential_material(tmp_path: Path) -> None:
    _record(tmp_path)
    raw = (tmp_path / "ledger.yaml").read_text(encoding="utf-8")
    assert "MINIO_ROOT" not in raw
    assert "POSTGRES_PASSWORD" not in raw
    assert "asyncpg" not in raw
    assert not re.search(r"://[^/\s:]+:[^@\s]+@", raw)
    for forbidden in ("password", "secret", "token", "dsn"):
        for entry in _load_ledger(tmp_path)["spec"]["checks"]:
            assert forbidden not in {str(key).lower() for key in entry}


def test_recorder_fails_closed_without_docker(tmp_path: Path) -> None:
    summary = _record(tmp_path, runner=_runner(docker_rc=127))
    assert summary["passed"] == 0
    assert summary["failed"] == list(EXPECTED_CHECKS)
    entries = {entry["id"]: entry for entry in _load_ledger(tmp_path)["spec"]["checks"]}
    assert entries["source-migrated"]["detail"] == "docker is required for the drill"
    assert entries["database-consistency"]["detail"] == "unreachable after upstream failure"
    with pytest.raises(DrillError, match="cannot be evidence"):
        validate_artifact_drill_evidence(CONTRACT, [tmp_path / "ledger.yaml"], ROOT)


def test_recorder_fails_closed_when_postgres_container_fails(tmp_path: Path) -> None:
    summary = _record(tmp_path, runner=_runner(pg_run_rc=1))
    assert summary["failed"] == list(EXPECTED_CHECKS)
    entries = {entry["id"]: entry for entry in _load_ledger(tmp_path)["spec"]["checks"]}
    assert entries["source-migrated"]["detail"] == "source database container did not start"


def test_recorder_fails_closed_when_minio_source_fails(tmp_path: Path) -> None:
    summary = _record(tmp_path, runner=_runner(src_run_rc=1))
    assert summary["failed"] == list(EXPECTED_CHECKS)
    entries = {entry["id"]: entry for entry in _load_ledger(tmp_path)["spec"]["checks"]}
    assert entries["source-migrated"]["detail"] == "source bucket container did not start"


def test_recorder_fails_closed_when_migration_fails(tmp_path: Path) -> None:
    summary = _record(tmp_path, runner=_runner(migrate_rc=1))
    assert summary["failed"] == list(EXPECTED_CHECKS)
    entries = {entry["id"]: entry for entry in _load_ledger(tmp_path)["spec"]["checks"]}
    assert entries["source-migrated"]["detail"] == "alembic upgrade head failed"


def test_recorder_fails_closed_when_seeder_raises(tmp_path: Path) -> None:
    def seeder(_target) -> None:
        raise RuntimeError("boom")

    summary = _record(tmp_path, seeder=seeder)
    assert summary["passed"] == 1
    entries = {entry["id"]: entry for entry in _load_ledger(tmp_path)["spec"]["checks"]}
    assert entries["source-migrated"]["classification"] == "passed"
    assert entries["objects-seeded"]["detail"] == "scenario failed: RuntimeError"
    assert entries["snapshot-created"]["detail"] == "unreachable after upstream failure"


def test_recorder_fails_closed_on_bucket_shortfall(tmp_path: Path) -> None:
    objects = _seeded_objects()
    objects.pop(_ARTIFACT_KEY)
    factory = _factory({"127.0.0.1:55900": objects})
    summary = _record(tmp_path, factory=factory)
    assert summary["passed"] == 1
    entries = {entry["id"]: entry for entry in _load_ledger(tmp_path)["spec"]["checks"]}
    assert "below minimum" in entries["objects-seeded"]["detail"]


def test_recorder_fails_closed_on_missing_database_references(tmp_path: Path) -> None:
    summary = _record(tmp_path, runner=_runner(artifact_ref_count="0"))
    assert summary["passed"] == 1
    entries = {entry["id"]: entry for entry in _load_ledger(tmp_path)["spec"]["checks"]}
    assert entries["objects-seeded"]["detail"] == "database storage references were not persisted"


def test_recorder_fails_closed_on_snapshot_error(tmp_path: Path) -> None:
    factory = _factory(_seeded_stores(), source_raise_on_get=True)
    summary = _record(tmp_path, factory=factory)
    assert summary["passed"] == 2
    entries = {entry["id"]: entry for entry in _load_ledger(tmp_path)["spec"]["checks"]}
    assert entries["snapshot-created"]["detail"] == "snapshot failed: RuntimeError"
    assert entries["restore-completed"]["detail"] == "unreachable after upstream failure"


def test_recorder_fails_closed_on_restore_error(tmp_path: Path) -> None:
    factory = _factory(_seeded_stores(), target_fail_put=True)
    summary = _record(tmp_path, factory=factory)
    assert summary["passed"] == 3
    entries = {entry["id"]: entry for entry in _load_ledger(tmp_path)["spec"]["checks"]}
    assert entries["restore-completed"]["detail"] == "restore into the fresh bucket failed"


def test_recorder_fails_closed_on_count_divergence(tmp_path: Path) -> None:
    factory = _factory(_seeded_stores(), target_drop_key=_ARTIFACT_KEY)
    summary = _record(tmp_path, factory=factory)
    assert summary["passed"] == 4
    entries = {entry["id"]: entry for entry in _load_ledger(tmp_path)["spec"]["checks"]}
    assert entries["object-count-parity"]["detail"] == "restored key set diverged from the snapshot"


def test_recorder_fails_closed_on_checksum_divergence(tmp_path: Path) -> None:
    factory = _factory(_seeded_stores(), target_corrupt_get=True)
    summary = _record(tmp_path, factory=factory)
    assert summary["passed"] == 5
    entries = {entry["id"]: entry for entry in _load_ledger(tmp_path)["spec"]["checks"]}
    assert "failed checksum parity" in entries["content-checksum-parity"]["detail"]


def test_recorder_fails_closed_on_metadata_divergence(tmp_path: Path) -> None:
    factory = _factory(_seeded_stores(), target_corrupt_stat=True)
    summary = _record(tmp_path, factory=factory)
    assert summary["passed"] == 6
    entries = {entry["id"]: entry for entry in _load_ledger(tmp_path)["spec"]["checks"]}
    assert "failed metadata parity" in entries["metadata-parity"]["detail"]


def test_recorder_fails_closed_on_database_checksum_divergence(tmp_path: Path) -> None:
    runner = _runner(artifact_ref_lines=[f"{_ARTIFACT_KEY}|{'0' * 64}"])
    summary = _record(tmp_path, runner=runner)
    assert summary["passed"] == 7
    entries = {entry["id"]: entry for entry in _load_ledger(tmp_path)["spec"]["checks"]}
    assert entries["database-consistency"]["detail"] == (
        "restored object checksum diverged from the database"
    )


def test_recorder_fails_closed_on_missing_database_object(tmp_path: Path) -> None:
    runner = _runner(version_ref_lines=[f"missing/key|{'1' * 64}"])
    summary = _record(tmp_path, runner=runner)
    assert summary["passed"] == 7
    entries = {entry["id"]: entry for entry in _load_ledger(tmp_path)["spec"]["checks"]}
    assert entries["database-consistency"]["detail"] == (
        "restored bucket misses a database-referenced object"
    )


def test_ledger_validation_detects_tampering(tmp_path: Path) -> None:
    _record(tmp_path)
    ledger = _load_ledger(tmp_path)
    ledger["spec"]["snapshot"]["objectCount"] = 99
    (tmp_path / "ledger.yaml").write_text(yaml.safe_dump(ledger), encoding="utf-8")
    with pytest.raises(DrillError, match="checksum mismatch"):
        validate_artifact_drill_evidence(CONTRACT, [tmp_path / "ledger.yaml"], ROOT)


def test_ledger_validation_rejects_failed_forbidden_and_shortfall(tmp_path: Path) -> None:
    _record(tmp_path)
    ledger = _load_ledger(tmp_path)
    failed = dict(ledger)
    failed["spec"] = dict(ledger["spec"])
    failed["spec"]["checks"] = [dict(entry) for entry in ledger["spec"]["checks"]]
    failed["spec"]["checks"][0]["classification"] = "failed"
    _recompute_checksum(failed)
    (tmp_path / "failed.yaml").write_text(yaml.safe_dump(failed), encoding="utf-8")
    with pytest.raises(DrillError, match="cannot be evidence"):
        validate_artifact_drill_evidence(CONTRACT, [tmp_path / "failed.yaml"], ROOT)

    forbidden = _load_ledger(tmp_path)
    forbidden["spec"]["checks"] = [dict(entry) for entry in forbidden["spec"]["checks"]]
    forbidden["spec"]["checks"][0]["password"] = "hunter2"
    _recompute_checksum(forbidden)
    (tmp_path / "forbidden.yaml").write_text(yaml.safe_dump(forbidden), encoding="utf-8")
    with pytest.raises(DrillError, match="forbidden key"):
        validate_artifact_drill_evidence(CONTRACT, [tmp_path / "forbidden.yaml"], ROOT)

    shortfall = _load_ledger(tmp_path)
    shortfall["spec"] = dict(shortfall["spec"])
    shortfall["spec"]["snapshot"] = dict(shortfall["spec"]["snapshot"], objectCount=1)
    _recompute_checksum(shortfall)
    (tmp_path / "shortfall.yaml").write_text(yaml.safe_dump(shortfall), encoding="utf-8")
    with pytest.raises(DrillError, match="minimum objects"):
        validate_artifact_drill_evidence(CONTRACT, [tmp_path / "shortfall.yaml"], ROOT)


def test_recorded_ledger_validates_against_contract() -> None:
    assert LEDGER.is_file(), "missing recorded artifact drill evidence ledger"
    result = validate_artifact_drill_evidence(CONTRACT, [LEDGER], ROOT)
    assert result == {"ledgers": 1, "checks": 8}
    ledger = yaml.safe_load(LEDGER.read_text(encoding="utf-8"))
    assert ledger["spec"]["snapshot"]["objectCount"] >= 2
    assert ledger["spec"]["timings"]["totalSeconds"] > 0


def test_candidate_gate_binds_both_ladders_without_promotion() -> None:
    summary = validate_release_candidate(GATES, None, ROOT, contract_only=True)
    assert summary["drill_evidence_ledgers"] == 2
    assert summary["drill_evidence_checks"] == 16
    assert summary["live_evidence_ledgers"] == 2
    assert summary["promotion_eligible"] is False
    assert len(summary["pending_operator_gates"]) == 6
    assert "backup-restore-drill" in summary["pending_operator_gates"]


def test_candidate_gate_rejects_duplicate_ladder_contracts(tmp_path: Path) -> None:
    document = yaml.safe_load(GATES.read_text(encoding="utf-8"))
    ladders = document["spec"]["drillEvidence"]["ladders"]
    document["spec"]["drillEvidence"]["ladders"] = [ladders[0], ladders[0]]
    (tmp_path / "gates.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ReleaseCandidateError, match="unique contracts"):
        validate_release_candidate(tmp_path / "gates.yaml", None, ROOT, contract_only=True)


def _gated_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("OBSION_DR_DRILL", None)
    return environment


def test_make_target_is_fail_closed() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("record-artifact-drill-evidence:", 1)[1].split("\n\n", 1)[0]
    assert "OBSION_DR_DRILL=1 is required" in target
    assert "docker is required" in target
    make = shutil.which("make")
    assert make is not None
    result = subprocess.run(  # noqa: S603 - fixed local Make target, no user input
        [make, "record-artifact-drill-evidence"],
        cwd=ROOT,
        env=_gated_environment(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 2
    assert "OBSION_DR_DRILL=1 is required" in result.stdout


def test_cli_record_artifact_drill_evidence_is_registered_and_gated() -> None:
    source = (ROOT / "services" / "control-plane" / "src" / "obsion" / "cli.py").read_text(
        encoding="utf-8"
    )
    assert '"record-artifact-drill-evidence"' in source
    uv = shutil.which("uv")
    assert uv is not None
    result = subprocess.run(  # noqa: S603 - fixed CLI invocation, no user input
        [uv, "run", "obsion", "record-artifact-drill-evidence"],
        cwd=ROOT,
        env=_gated_environment(),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode != 0
    assert "OBSION_DR_DRILL=1 is required" in result.stderr


def test_object_store_backend_selection_is_explicit() -> None:
    assert _uses_memory_object_store(Settings(environment=Environment.TEST)) is True
    assert (
        _uses_memory_object_store(
            Settings(environment=Environment.TEST, object_store_backend="minio")
        )
        is False
    )
    assert _uses_memory_object_store(Settings(environment=Environment.DEVELOPMENT)) is False
    assert (
        _uses_memory_object_store(
            Settings(environment=Environment.DEVELOPMENT, object_store_backend="memory")
        )
        is True
    )


def test_release_notes_and_project_status_track_phase86() -> None:
    result = validate_release_notes(ROOT / "docs" / "release" / "0.87.0-dev.yaml", ROOT)
    assert result["version"] == "0.87.0-dev"
    status = yaml.safe_load((ROOT / "docs" / "project-status.yaml").read_text(encoding="utf-8"))
    assert status["version"] == "0.97.0-dev"
    assert status["current_phase"] == "phase-97"
    assert "phase-87" in status["completed_phases"]


def test_env_example_documents_backend_and_drill_opt_in() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "OBSION_DR_DRILL=" in example
    assert "OBSION_OBJECT_STORE_BACKEND=auto" in example
