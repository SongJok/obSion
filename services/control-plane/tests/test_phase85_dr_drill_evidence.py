"""Phase 85: backup/restore drill evidence recording and candidate-gate binding."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest
import yaml

import obsion.db.models  # noqa: F401 - register tables on Base.metadata
from obsion.config import Environment, Settings
from obsion.db.base import Base
from obsion.db.session import Database
from obsion.release.candidate import ReleaseCandidateError, validate_release_candidate
from obsion.release.drill import (
    CommandResult,
    DrillError,
    _canonical_digest,
    _seed_drill_dataset,
    load_drill_contract,
    record_drill_evidence,
    validate_drill_evidence,
)
from obsion.release.notes import validate_release_notes

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "docs" / "release" / "alpha1-drill-evidence-contract.yaml"
GATES = ROOT / "docs" / "release" / "alpha1-candidate-gates.yaml"
LEDGER = ROOT / "docs" / "release" / "evidence" / "alpha1" / "backup-restore-drill.yaml"
REVISION = "b" * 40
ENV = {"OBSION_DR_DRILL": "1"}
HEAD = "abc123def456"
TABLES = (
    "alembic_version",
    "audit_logs",
    "claims",
    "events",
    "evidence",
    "run_steps",
    "runs",
    "threads",
    "turns",
    "users",
    "workspaces",
)
EXPECTED_CHECKS = (
    "source-migrated",
    "dataset-seeded",
    "dump-created",
    "restore-completed",
    "schema-version-parity",
    "row-count-parity",
    "referential-integrity",
    "audit-preserved",
)


def _runner(
    *,
    docker_rc: int = 0,
    migrate_rc: int = 0,
    dump: bytes = b"PGDMP-phase85-unit",
    restore_rc: int = 0,
    source_head: str = HEAD,
    target_head: str | None = None,
    source_count: int = 3,
    target_count: int | None = None,
    orphans: int = 0,
    source_audit: tuple[str, ...] = ("audit-1", "audit-2"),
    target_audit: tuple[str, ...] | None = None,
):
    def run(argv: tuple[str, ...], env: Mapping[str, str]) -> CommandResult:
        a = list(argv)
        if a[:2] == ["docker", "version"]:
            return CommandResult(docker_rc, b"27.0.0\n" if docker_rc == 0 else b"", "")
        if a[:2] == ["docker", "run"]:
            return CommandResult(0, b"container\n", "")
        if a[:2] == ["docker", "cp"]:
            return CommandResult(0, b"", "")
        if a[:2] == ["docker", "port"]:
            return CommandResult(0, b"127.0.0.1:55432\n", "")
        if a[:3] == ["docker", "rm", "-f"]:
            return CommandResult(0, b"", "")
        if a[0] == "uv":
            return CommandResult(migrate_rc, b"migrated\n" if migrate_rc == 0 else b"", "")
        if a[:2] == ["docker", "exec"]:
            container = a[2]
            is_source = "-src-" in container
            if "pg_isready" in a:
                return CommandResult(0, b"ready\n", "")
            if "pg_dump" in a:
                return CommandResult(0, dump, "")
            if "pg_restore" in a:
                return CommandResult(restore_rc, b"", "")
            if "psql" in a:
                query = a[-1]
                if "version_num" in query:
                    head = source_head if is_source else (target_head or source_head)
                    return CommandResult(0, f"{head}\n".encode(), "")
                if "relname" in query:
                    return CommandResult(0, ("\n".join(TABLES) + "\n").encode(), "")
                if "LEFT JOIN" in query:
                    return CommandResult(0, f"{orphans}\n".encode(), "")
                if "count(*)" in query:
                    count = source_count if is_source else (target_count or source_count)
                    return CommandResult(0, f"{count}\n".encode(), "")
                if "ORDER BY id" in query:
                    ids = source_audit if is_source else (target_audit or source_audit)
                    return CommandResult(0, ("\n".join(ids) + "\n").encode(), "")
        raise AssertionError(f"unexpected command: {a}")

    return run


def _record(tmp_path: Path, runner=None, seeder=None, env=None):
    return record_drill_evidence(
        CONTRACT,
        tmp_path / "ledger.yaml",
        ROOT,
        env=ENV if env is None else env,
        runner=_runner() if runner is None else runner,
        seeder=(lambda _url: None) if seeder is None else seeder,
        revision=REVISION,
    )


def _load_ledger(tmp_path: Path) -> dict:
    return yaml.safe_load((tmp_path / "ledger.yaml").read_text(encoding="utf-8"))


def _recompute_checksum(document: dict) -> None:
    spec = document["spec"]
    document["spec"]["checksum"] = _canonical_digest(
        {**document, "spec": {key: value for key, value in spec.items() if key != "checksum"}}
    )


def test_drill_contract_binds_pinned_postgres_and_ordered_checks() -> None:
    contract = load_drill_contract(CONTRACT, ROOT)
    assert contract.name == "alpha1-backup-restore"
    assert contract.global_opt_in == "OBSION_DR_DRILL"
    assert [check.check_id for check in contract.checks] == list(EXPECTED_CHECKS)
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert f"image: {contract.postgres_image}" in compose
    assert contract.postgres_image == "pgvector/pgvector:0.8.6-pg17-bookworm"
    for table in ("workspaces", "runs", "events", "evidence", "audit_logs"):
        assert contract.minimum_rows[table] >= 1


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
        "dumpSeconds",
        "restoreSeconds",
        "verifySeconds",
        "totalSeconds",
    }
    ledger = _load_ledger(tmp_path)
    assert ledger["kind"] == "DrillEvidenceLedger"
    assert ledger["metadata"]["name"] == "alpha1-backup-restore"
    assert ledger["metadata"]["revision"] == REVISION
    assert ledger["spec"]["alembicHead"] == HEAD
    assert ledger["spec"]["dump"]["format"] == "custom"
    assert re.fullmatch(r"[0-9a-f]{64}", ledger["spec"]["dump"]["sha256"])
    assert ledger["spec"]["rowCounts"]["workspaces"] == 3
    entries = {entry["id"]: entry for entry in ledger["spec"]["checks"]}
    assert set(entries) == set(EXPECTED_CHECKS)
    assert all(entry["classification"] == "passed" for entry in entries.values())
    json.dumps(ledger, sort_keys=True)
    result = validate_drill_evidence(CONTRACT, [tmp_path / "ledger.yaml"], ROOT)
    assert result == {"ledgers": 1, "checks": 8}


def test_recorder_never_records_credential_material(tmp_path: Path) -> None:
    _record(tmp_path)
    raw = (tmp_path / "ledger.yaml").read_text(encoding="utf-8")
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
    assert entries["audit-preserved"]["detail"] == "unreachable after upstream failure"
    with pytest.raises(DrillError, match="cannot be evidence"):
        validate_drill_evidence(CONTRACT, [tmp_path / "ledger.yaml"], ROOT)


def test_recorder_fails_closed_when_migration_fails(tmp_path: Path) -> None:
    summary = _record(tmp_path, runner=_runner(migrate_rc=1))
    assert summary["failed"] == list(EXPECTED_CHECKS)
    entries = {entry["id"]: entry for entry in _load_ledger(tmp_path)["spec"]["checks"]}
    assert entries["source-migrated"]["detail"] == "alembic upgrade head failed"


def test_recorder_fails_closed_when_seeder_raises(tmp_path: Path) -> None:
    def seeder(_url: str) -> None:
        raise RuntimeError("boom")

    summary = _record(tmp_path, seeder=seeder)
    assert summary["passed"] == 1
    entries = {entry["id"]: entry for entry in _load_ledger(tmp_path)["spec"]["checks"]}
    assert entries["source-migrated"]["classification"] == "passed"
    assert entries["dataset-seeded"]["detail"] == "scenario failed: RuntimeError"
    assert entries["dump-created"]["detail"] == "unreachable after upstream failure"


def test_recorder_fails_closed_when_minimum_rows_missing(tmp_path: Path) -> None:
    summary = _record(tmp_path, runner=_runner(source_count=0, target_count=3))
    assert summary["passed"] == 1
    entries = {entry["id"]: entry for entry in _load_ledger(tmp_path)["spec"]["checks"]}
    assert entries["dataset-seeded"]["classification"] == "failed"
    assert "minimum rows not met" in entries["dataset-seeded"]["detail"]


def test_recorder_fails_closed_on_empty_dump(tmp_path: Path) -> None:
    summary = _record(tmp_path, runner=_runner(dump=b""))
    assert summary["passed"] == 2
    entries = {entry["id"]: entry for entry in _load_ledger(tmp_path)["spec"]["checks"]}
    assert entries["dump-created"]["detail"] == "pg_dump produced no backup"


def test_recorder_fails_closed_on_restore_error(tmp_path: Path) -> None:
    summary = _record(tmp_path, runner=_runner(restore_rc=1))
    assert summary["passed"] == 3
    entries = {entry["id"]: entry for entry in _load_ledger(tmp_path)["spec"]["checks"]}
    assert entries["restore-completed"]["detail"] == "pg_restore failed"


def test_recorder_fails_closed_on_schema_divergence(tmp_path: Path) -> None:
    summary = _record(tmp_path, runner=_runner(target_head="000000000000"))
    assert summary["passed"] == 4
    entries = {entry["id"]: entry for entry in _load_ledger(tmp_path)["spec"]["checks"]}
    assert entries["schema-version-parity"]["classification"] == "failed"


def test_recorder_fails_closed_on_count_mismatch(tmp_path: Path) -> None:
    summary = _record(tmp_path, runner=_runner(target_count=2))
    assert summary["passed"] == 5
    entries = {entry["id"]: entry for entry in _load_ledger(tmp_path)["spec"]["checks"]}
    assert "count mismatch" in entries["row-count-parity"]["detail"]


def test_recorder_fails_closed_on_orphans(tmp_path: Path) -> None:
    summary = _record(tmp_path, runner=_runner(orphans=1))
    assert summary["passed"] == 6
    entries = {entry["id"]: entry for entry in _load_ledger(tmp_path)["spec"]["checks"]}
    assert "orphaned rows" in entries["referential-integrity"]["detail"]


def test_recorder_fails_closed_on_audit_divergence(tmp_path: Path) -> None:
    summary = _record(tmp_path, runner=_runner(target_audit=("audit-1", "audit-9")))
    assert summary["passed"] == 7
    entries = {entry["id"]: entry for entry in _load_ledger(tmp_path)["spec"]["checks"]}
    assert entries["audit-preserved"]["classification"] == "failed"


def test_ledger_validation_detects_tampering(tmp_path: Path) -> None:
    _record(tmp_path)
    ledger = _load_ledger(tmp_path)
    ledger["spec"]["rowCounts"]["workspaces"] = 99
    (tmp_path / "tampered.yaml").write_text(yaml.safe_dump(ledger), encoding="utf-8")
    with pytest.raises(DrillError, match="checksum mismatch"):
        validate_drill_evidence(CONTRACT, [tmp_path / "tampered.yaml"], ROOT)


def test_ledger_validation_rejects_failed_forbidden_and_shortfall(tmp_path: Path) -> None:
    _record(tmp_path)
    ledger = _load_ledger(tmp_path)

    failed = yaml.safe_load(yaml.safe_dump(ledger))
    failed["spec"]["checks"][0]["classification"] = "failed"
    _recompute_checksum(failed)
    (tmp_path / "failed.yaml").write_text(yaml.safe_dump(failed), encoding="utf-8")
    with pytest.raises(DrillError, match="cannot be evidence"):
        validate_drill_evidence(CONTRACT, [tmp_path / "failed.yaml"], ROOT)

    forbidden = yaml.safe_load(yaml.safe_dump(ledger))
    forbidden["spec"]["checks"][0]["password"] = "hidden"
    _recompute_checksum(forbidden)
    (tmp_path / "forbidden.yaml").write_text(yaml.safe_dump(forbidden), encoding="utf-8")
    with pytest.raises(DrillError, match="forbidden key"):
        validate_drill_evidence(CONTRACT, [tmp_path / "forbidden.yaml"], ROOT)

    credentialed = yaml.safe_load(yaml.safe_dump(ledger))
    credentialed["spec"]["checks"][0]["detail"] = "restored postgres://user:pass@host/db"
    _recompute_checksum(credentialed)
    (tmp_path / "credentialed.yaml").write_text(yaml.safe_dump(credentialed), encoding="utf-8")
    with pytest.raises(DrillError, match="credential-shaped material"):
        validate_drill_evidence(CONTRACT, [tmp_path / "credentialed.yaml"], ROOT)

    shortfall = yaml.safe_load(yaml.safe_dump(ledger))
    shortfall["spec"]["rowCounts"]["workspaces"] = 0
    _recompute_checksum(shortfall)
    (tmp_path / "shortfall.yaml").write_text(yaml.safe_dump(shortfall), encoding="utf-8")
    with pytest.raises(DrillError, match="miss contract minimums"):
        validate_drill_evidence(CONTRACT, [tmp_path / "shortfall.yaml"], ROOT)


def test_seeder_creates_threshold_rows(tmp_path: Path) -> None:
    url = f"sqlite+aiosqlite:///{tmp_path}/drill.db"

    async def create_schema() -> None:
        settings = Settings(
            environment=Environment.TEST,
            database_url=url,
            allowed_origins=["http://testserver"],
            dev_bearer_token="phase85-schema-token",
        )
        database = Database(settings)
        async with database.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        await database.dispose()

    asyncio.run(create_schema())
    _seed_drill_dataset(url)

    async def counts() -> dict[str, int]:
        from sqlalchemy import text

        settings = Settings(
            environment=Environment.TEST,
            database_url=url,
            allowed_origins=["http://testserver"],
            dev_bearer_token="phase85-schema-token",
        )
        database = Database(settings)
        observed: dict[str, int] = {}
        async with database.engine.connect() as connection:
            for table in load_drill_contract(CONTRACT, ROOT).minimum_rows:
                result = await connection.execute(
                    text(f'SELECT count(*) FROM "{table}"')  # noqa: S608 - contract tables
                )
                observed[table] = int(result.scalar_one())
        await database.dispose()
        return observed

    observed = asyncio.run(counts())
    contract = load_drill_contract(CONTRACT, ROOT)
    for table, minimum in contract.minimum_rows.items():
        assert observed[table] >= minimum, f"{table}: {observed[table]} < {minimum}"


def test_recorded_drill_ledger_validates_against_contract() -> None:
    assert LEDGER.is_file(), "missing recorded drill evidence ledger"
    result = validate_drill_evidence(CONTRACT, [LEDGER], ROOT)
    assert result == {"ledgers": 1, "checks": 8}
    ledger = yaml.safe_load(LEDGER.read_text(encoding="utf-8"))
    assert ledger["spec"]["dump"]["sizeBytes"] > 0
    assert ledger["spec"]["timings"]["totalSeconds"] > 0


def test_candidate_gate_binds_drill_evidence_without_promotion() -> None:
    summary = validate_release_candidate(GATES, None, ROOT, contract_only=True)
    assert summary["drill_evidence_ledgers"] == 2
    assert summary["drill_evidence_checks"] == 16
    assert summary["live_evidence_ledgers"] == 2
    assert summary["promotion_eligible"] is False
    assert len(summary["pending_operator_gates"]) == 6
    assert "backup-restore-drill" in summary["pending_operator_gates"]


def test_candidate_gate_rejects_drill_evidence_outside_evidence_dir(tmp_path: Path) -> None:
    document = yaml.safe_load(GATES.read_text(encoding="utf-8"))
    document["spec"]["drillEvidence"]["ladders"][0]["ledgers"] = ["docs/release/0.84.0-dev.yaml"]
    (tmp_path / "gates.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ReleaseCandidateError, match="docs/release/evidence/alpha1/"):
        validate_release_candidate(tmp_path / "gates.yaml", None, ROOT, contract_only=True)


def _gated_environment() -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("OBSION_DR_DRILL", None)
    return environment


def test_make_target_is_fail_closed() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("record-drill-evidence:", 1)[1].split("\n\n", 1)[0]
    assert "OBSION_DR_DRILL=1 is required" in target
    assert "docker is required" in target
    make = shutil.which("make")
    assert make is not None
    result = subprocess.run(  # noqa: S603 - fixed local Make target, no user input
        [make, "record-drill-evidence"],
        cwd=ROOT,
        env=_gated_environment(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 2
    assert "OBSION_DR_DRILL=1 is required" in result.stdout


def test_cli_record_drill_evidence_is_registered_and_gated() -> None:
    source = (ROOT / "services" / "control-plane" / "src" / "obsion" / "cli.py").read_text(
        encoding="utf-8"
    )
    assert '"record-drill-evidence"' in source
    uv = shutil.which("uv")
    assert uv is not None
    result = subprocess.run(  # noqa: S603 - fixed CLI invocation, no user input
        [uv, "run", "obsion", "record-drill-evidence"],
        cwd=ROOT,
        env=_gated_environment(),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode != 0
    assert "OBSION_DR_DRILL=1 is required" in result.stderr


def test_release_notes_and_project_status_track_phase85() -> None:
    result = validate_release_notes(ROOT / "docs" / "release" / "0.85.0-dev.yaml", ROOT)
    assert result["version"] == "0.85.0-dev"
    status = yaml.safe_load((ROOT / "docs" / "project-status.yaml").read_text(encoding="utf-8"))
    assert status["version"] == "0.89.0-dev"
    assert status["current_phase"] == "phase-89"
    assert "phase-85" in status["completed_phases"]


def test_env_example_documents_drill_opt_in() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "OBSION_DR_DRILL=" in example
