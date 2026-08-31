"""Fail-closed artifact-store backup/restore drill evidence for Alpha.1.

The operator runbook treats PostgreSQL as the transactional source of truth and
the artifact bucket as the byte store that must survive alongside it.  Phase 85
recorded the PostgreSQL drill; this module records the object-storage half of
the same recovery story: a throwaway pinned PostgreSQL container is migrated
and seeded through the real control-plane REST API against a throwaway pinned
MinIO container, the source bucket is snapshotted into a canonical per-object
manifest with SHA-256 checksums, the snapshot is restored into a fresh bucket
on a second throwaway MinIO container, and parity plus database-reference
consistency are verified.  The redacted, checksummed ledger under
``docs/release/evidence/alpha1/`` is validated offline by the
release-candidate gate.  A skipped or unreachable check is a failure, never a
pass, and drill credentials never leave process memory.

The drill is repository-local readiness input only: it never satisfies the
operator-owned staging backup-restore gate and never feeds promotion
eligibility.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shutil
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any, Protocol

import yaml

from obsion.release.drill import (
    _ALEMBIC_PATTERN,
    _CHECK_ID_PATTERN,
    _REVISION_PATTERN,
    _SHA256_PATTERN,
    _TABLE_PATTERN,
    CheckSpec,
    CommandRunner,
    DrillError,
    _canonical_digest,
    _check,
    _command_runner,
    _current_revision,
    _fail_remaining,
    _load_mapping,
    _mapping,
    _record,
    _reject_credential_material,
    _reject_forbidden_keys,
    _reject_forbidden_values,
    _relative,
    _rows,
    _scalar,
    _start_postgres,
    _string,
    _timestamp,
    _unique_strings,
    _utc_now,
)

LEDGER_KIND = "ArtifactDrillEvidenceLedger"
LADDER_KIND = "ArtifactDrillEvidenceLadder"
_RELEASE_LINE = "alpha.1"
_OUTCOME_CLASSIFICATIONS = frozenset({"passed"})
_LEDGER_CLASSIFICATIONS = frozenset({"passed", "failed"})
_BUCKET_PATTERN = re.compile(r"^[a-z0-9][a-z0-9.-]*[a-z0-9]$")
_TIMING_KEYS = (
    "migrateSeconds",
    "seedSeconds",
    "snapshotSeconds",
    "restoreSeconds",
    "verifySeconds",
    "totalSeconds",
)
_MAX_DETAIL_CHARS = 240
_READY_ATTEMPTS = 60


@dataclass(frozen=True, slots=True)
class ArtifactDrillContract:
    name: str
    global_opt_in: str
    postgres_image: str
    minio_image: str
    database: str
    bucket: str
    minimum_objects: int
    checks: tuple[CheckSpec, ...]


@dataclass(frozen=True, slots=True)
class ObjectStat:
    size: int
    content_type: str
    metadata: dict[str, str]


class BucketClient(Protocol):
    """Minimal bucket surface the drill needs; fakes are injected in tests."""

    def ensure_bucket(self) -> None: ...

    def list_keys(self) -> list[str]: ...

    def stat(self, key: str) -> ObjectStat: ...

    def get(self, key: str) -> bytes: ...

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> None: ...


BucketClientFactory = Callable[[str, str, str, str], BucketClient]


@dataclass(frozen=True, slots=True)
class SeedTarget:
    database_url: str
    object_endpoint: str
    access_key: str
    secret_key: str
    bucket: str


ArtifactSeeder = Callable[[SeedTarget], None]


def load_artifact_drill_contract(path: Path, repository_root: Path) -> ArtifactDrillContract:
    document = _load_mapping(path, "artifact drill evidence ladder contract")
    if document.get("apiVersion") != "obsion.ai/v1":
        raise DrillError("artifact drill ladder apiVersion must be obsion.ai/v1")
    if document.get("kind") != LADDER_KIND:
        raise DrillError(f"artifact drill ladder kind must be {LADDER_KIND}")
    metadata = _mapping(document, "metadata", "artifact drill ladder")
    name = _string(metadata, "name", "artifact drill ladder")
    if not _CHECK_ID_PATTERN.fullmatch(name):
        raise DrillError("artifact drill ladder metadata.name must be a lowercase slug")
    if _string(metadata, "releaseLine", "artifact drill ladder") != _RELEASE_LINE:
        raise DrillError(f"artifact drill ladder releaseLine must be {_RELEASE_LINE}")
    spec = _mapping(document, "spec", "artifact drill ladder")
    global_opt_in = _string(spec, "globalOptIn", "artifact drill ladder")
    if not global_opt_in.startswith("OBSION_"):
        raise DrillError("artifact drill ladder globalOptIn must be an OBSION_ variable")
    postgres_image = _string(spec, "postgresImage", "artifact drill ladder")
    if "/" not in postgres_image or ":" not in postgres_image:
        raise DrillError("artifact drill ladder postgresImage must be a pinned repository:tag")
    minio_image = _string(spec, "minioImage", "artifact drill ladder")
    if "/" not in minio_image or ":" not in minio_image:
        raise DrillError("artifact drill ladder minioImage must be a pinned repository:tag")
    database = _string(spec, "database", "artifact drill ladder")
    if not _TABLE_PATTERN.fullmatch(database):
        raise DrillError("artifact drill ladder database must be a lowercase identifier")
    bucket = _string(spec, "bucket", "artifact drill ladder")
    if not _BUCKET_PATTERN.fullmatch(bucket):
        raise DrillError("artifact drill ladder bucket must be a valid bucket name")
    minimum_objects = spec.get("minimumObjects")
    if not isinstance(minimum_objects, int) or isinstance(minimum_objects, bool):
        raise DrillError("artifact drill ladder minimumObjects must be an integer")
    if minimum_objects < 1:
        raise DrillError("artifact drill ladder minimumObjects must be >= 1")
    raw_checks = spec.get("checks")
    if not isinstance(raw_checks, list) or not raw_checks:
        raise DrillError("artifact drill ladder checks must be a non-empty list")
    checks: list[CheckSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_checks):
        if not isinstance(item, dict):
            raise DrillError(f"artifact drill check at index {index} must be an object")
        check_id = _string(item, "id", "artifact drill check")
        if not _CHECK_ID_PATTERN.fullmatch(check_id):
            raise DrillError(f"artifact drill check id is invalid: {check_id}")
        if check_id in seen:
            raise DrillError("artifact drill check ids must be unique")
        seen.add(check_id)
        surface = _string(item, "surface", "artifact drill check")
        allowed = frozenset(_unique_strings(item, "allowed", "artifact drill check"))
        if not allowed or not allowed <= _OUTCOME_CLASSIFICATIONS:
            raise DrillError(
                f"artifact drill check {check_id} allowed classifications must be passed"
            )
        checks.append(CheckSpec(check_id=check_id, surface=surface, allowed=allowed))
    return ArtifactDrillContract(
        name=name,
        global_opt_in=global_opt_in,
        postgres_image=postgres_image,
        minio_image=minio_image,
        database=database,
        bucket=bucket,
        minimum_objects=minimum_objects,
        checks=tuple(checks),
    )


def record_artifact_drill_evidence(
    contract_path: Path,
    output_path: Path,
    repository_root: Path,
    *,
    env: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
    seeder: ArtifactSeeder | None = None,
    client_factory: BucketClientFactory | None = None,
    revision: str | None = None,
) -> dict[str, Any]:
    """Run the artifact-store drill and write a redacted, checksummed ledger."""

    root = repository_root.resolve()
    environment = dict(os.environ if env is None else env)
    contract = load_artifact_drill_contract(contract_path, root)
    if environment.get(contract.global_opt_in, "").strip() != "1":
        raise DrillError(f"{contract.global_opt_in}=1 is required")
    resolved_revision = revision if revision is not None else _current_revision(root)
    if not _REVISION_PATTERN.fullmatch(resolved_revision):
        raise DrillError("artifact drill evidence revision must be a full git SHA")
    execute = runner if runner is not None else _command_runner
    seed = seeder if seeder is not None else _seed_artifact_scenario
    factory = client_factory if client_factory is not None else _minio_client_factory

    recorded: dict[str, dict[str, Any]] = {}
    pg_password = secrets.token_urlsafe(24)
    root_user = f"obsion-{secrets.token_hex(6)}"
    root_password = secrets.token_urlsafe(24)
    suffix = secrets.token_hex(4)
    postgres = f"obsion-artdrill-pg-{suffix}"
    source = f"obsion-artdrill-src-{suffix}"
    target = f"obsion-artdrill-tgt-{suffix}"
    timings: dict[str, float] = {}
    started = time.monotonic()
    drill_state: dict[str, Any] = {
        "alembic_head": "",
        "manifest": [],
        "snapshot_dir": None,
        "source_host_port": "",
        "database_references": {"artifacts": 0, "documentVersions": 0},
    }
    try:
        _stage_provision(
            contract,
            execute,
            postgres,
            source,
            pg_password,
            root_user,
            root_password,
            recorded,
            timings,
            drill_state,
        )
        source_client = _stage_seed(
            contract,
            execute,
            factory,
            seed,
            postgres,
            root_user,
            root_password,
            recorded,
            timings,
            drill_state,
        )
        if source_client is not None:
            _stage_snapshot(contract, source_client, recorded, timings, drill_state)
            _stage_restore_and_verify(
                contract,
                execute,
                factory,
                postgres,
                target,
                root_user,
                root_password,
                recorded,
                timings,
                drill_state,
            )
    finally:
        for container in (postgres, source, target):
            execute(("docker", "rm", "-f", container), {})
        snapshot_dir = drill_state.get("snapshot_dir")
        if snapshot_dir is not None:
            shutil.rmtree(snapshot_dir, ignore_errors=True)
        timings["totalSeconds"] = round(time.monotonic() - started, 3)

    results = [recorded[check.check_id] for check in contract.checks]
    ledger = _build_ledger(
        contract,
        contract_path,
        root,
        revision=resolved_revision,
        results=results,
        timings=timings,
        drill_state=drill_state,
    )
    for entry in results:
        _reject_credential_material(entry["detail"], pg_password)
        _reject_credential_material(entry["detail"], root_password)
        if root_user in entry["detail"]:
            raise DrillError("artifact drill detail contains credential material")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        yaml.safe_dump(ledger, sort_keys=True, allow_unicode=True), encoding="utf-8"
    )
    failed = [entry["id"] for entry in results if entry["classification"] == "failed"]
    return {
        "ledger": _relative(output_path, root),
        "revision": resolved_revision,
        "checks": len(results),
        "passed": sum(1 for entry in results if entry["classification"] == "passed"),
        "failed": failed,
        "timings": {key: round(float(timings.get(key, 0.0)), 3) for key in _TIMING_KEYS},
    }


def validate_artifact_drill_evidence(
    contract_path: Path,
    ledger_paths: list[Path],
    repository_root: Path,
) -> dict[str, Any]:
    """Validate recorded artifact drill ledgers against the contract offline."""

    root = repository_root.resolve()
    contract = load_artifact_drill_contract(contract_path, root)
    if not ledger_paths:
        raise DrillError("artifact drill evidence validation requires at least one ledger")
    seen_paths: set[Path] = set()
    for ledger_path in ledger_paths:
        resolved = ledger_path.resolve()
        if resolved in seen_paths:
            raise DrillError("artifact drill evidence ledgers must be unique files")
        seen_paths.add(resolved)
        document = _load_mapping(resolved, "artifact drill evidence ledger")
        _validate_ledger(document, contract, root)
    return {
        "ledgers": len(ledger_paths),
        "checks": len(contract.checks),
    }


def _stage_provision(
    contract: ArtifactDrillContract,
    execute: CommandRunner,
    postgres: str,
    source: str,
    pg_password: str,
    root_user: str,
    root_password: str,
    recorded: dict[str, dict[str, Any]],
    timings: dict[str, float],
    drill_state: dict[str, Any],
) -> None:
    check = _check(contract, "source-migrated")
    docker = execute(("docker", "version", "--format", "{{.Server.Version}}"), {})
    if docker.returncode != 0:
        _fail_remaining(contract, recorded, "docker is required for the drill")
        return
    pg_port = _start_postgres(contract, execute, postgres, pg_password)
    if pg_port is None:
        _fail_remaining(contract, recorded, "source database container did not start")
        return
    source_host_port = _start_minio(contract, execute, source, root_user, root_password)
    if source_host_port is None:
        _fail_remaining(contract, recorded, "source bucket container did not start")
        return
    drill_state["source_host_port"] = source_host_port
    database_url = (
        f"postgresql+asyncpg://obsion:{pg_password}@127.0.0.1:{pg_port}/{contract.database}"
    )
    migrate_started = time.monotonic()
    migration = execute(
        (
            "uv",
            "run",
            "--package",
            "obsion-control-plane",
            "alembic",
            "-c",
            "services/control-plane/alembic.ini",
            "upgrade",
            "head",
        ),
        {"OBSION_DATABASE_URL": database_url},
    )
    timings["migrateSeconds"] = round(time.monotonic() - migrate_started, 3)
    if migration.returncode != 0:
        _fail_remaining(contract, recorded, "alembic upgrade head failed")
        return
    head = _scalar(execute, postgres, contract.database, "SELECT version_num FROM alembic_version")
    if head is None or not _ALEMBIC_PATTERN.fullmatch(head):
        _fail_remaining(contract, recorded, "alembic head is unreadable after upgrade")
        return
    drill_state["alembic_head"] = head
    drill_state["database_url"] = database_url
    _record(recorded, check, "passed", f"alembic head {head}")


def _stage_seed(
    contract: ArtifactDrillContract,
    execute: CommandRunner,
    factory: BucketClientFactory,
    seed: ArtifactSeeder,
    postgres: str,
    root_user: str,
    root_password: str,
    recorded: dict[str, dict[str, Any]],
    timings: dict[str, float],
    drill_state: dict[str, Any],
) -> BucketClient | None:
    if recorded.get("objects-seeded") is not None:
        return None
    check = _check(contract, "objects-seeded")
    host_port = str(drill_state["source_host_port"])
    client = factory(host_port, root_user, root_password, contract.bucket)
    try:
        client.ensure_bucket()
    except Exception:  # noqa: BLE001 - any bucket failure fails the drill closed
        _fail_remaining(contract, recorded, "source bucket cannot be provisioned")
        return None
    seed_target = SeedTarget(
        database_url=str(drill_state["database_url"]),
        object_endpoint=f"http://{host_port}",
        access_key=root_user,
        secret_key=root_password,
        bucket=contract.bucket,
    )
    seed_started = time.monotonic()
    try:
        seed(seed_target)
    except Exception as exc:  # noqa: BLE001 - any seeder failure fails the drill closed
        timings["seedSeconds"] = round(time.monotonic() - seed_started, 3)
        _fail_remaining(contract, recorded, f"scenario failed: {type(exc).__name__}")
        return None
    timings["seedSeconds"] = round(time.monotonic() - seed_started, 3)
    try:
        keys = client.list_keys()
    except Exception:  # noqa: BLE001 - any bucket failure fails the drill closed
        _fail_remaining(contract, recorded, "source bucket listing failed after seeding")
        return None
    if len(keys) < contract.minimum_objects:
        _fail_remaining(
            contract,
            recorded,
            f"bucket holds {len(keys)} objects below minimum {contract.minimum_objects}",
        )
        return None
    artifact_refs = _scalar(
        execute,
        postgres,
        contract.database,
        "SELECT count(*) FROM artifacts WHERE storage_key IS NOT NULL",
    )
    version_refs = _scalar(
        execute,
        postgres,
        contract.database,
        "SELECT count(*) FROM document_versions WHERE content_ref IS NOT NULL",
    )
    if (
        artifact_refs is None
        or version_refs is None
        or not artifact_refs.isdigit()
        or not version_refs.isdigit()
        or int(artifact_refs) < 1
        or int(version_refs) < 1
    ):
        _fail_remaining(contract, recorded, "database storage references were not persisted")
        return None
    drill_state["database_references"] = {
        "artifacts": int(artifact_refs),
        "documentVersions": int(version_refs),
    }
    _record(
        recorded,
        check,
        "passed",
        f"{len(keys)} objects with {int(artifact_refs) + int(version_refs)} database references",
    )
    return client


def _stage_snapshot(
    contract: ArtifactDrillContract,
    source_client: BucketClient,
    recorded: dict[str, dict[str, Any]],
    timings: dict[str, float],
    drill_state: dict[str, Any],
) -> None:
    if recorded.get("snapshot-created") is not None:
        return
    check = _check(contract, "snapshot-created")
    snapshot_started = time.monotonic()
    snapshot_dir = Path(tempfile.mkdtemp(prefix="obsion-artdrill-snapshot-"))
    drill_state["snapshot_dir"] = snapshot_dir
    payload_dir = snapshot_dir / "payloads"
    payload_dir.mkdir()
    manifest: list[dict[str, Any]] = []
    try:
        keys = source_client.list_keys()
        for ordinal, key in enumerate(sorted(keys)):
            stat = source_client.stat(key)
            data = source_client.get(key)
            if len(data) != stat.size:
                raise DrillError("an object changed size during the snapshot")
            digest = hashlib.sha256(data).hexdigest()
            (payload_dir / str(ordinal)).write_bytes(data)
            manifest.append(
                {
                    "key": key,
                    "payload": str(ordinal),
                    "sizeBytes": len(data),
                    "sha256": digest,
                    "contentType": stat.content_type,
                    "metadata": dict(sorted(stat.metadata.items())),
                }
            )
    except Exception as exc:  # noqa: BLE001 - any snapshot failure fails the drill closed
        timings["snapshotSeconds"] = round(time.monotonic() - snapshot_started, 3)
        _fail_remaining(contract, recorded, f"snapshot failed: {type(exc).__name__}")
        return
    timings["snapshotSeconds"] = round(time.monotonic() - snapshot_started, 3)
    if not manifest:
        _fail_remaining(contract, recorded, "snapshot captured no objects")
        return
    (snapshot_dir / "manifest.json").write_text(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    drill_state["manifest"] = manifest
    total = sum(entry["sizeBytes"] for entry in manifest)
    _record(
        recorded,
        check,
        "passed",
        f"{len(manifest)} objects, {total} bytes",
    )


def _stage_restore_and_verify(
    contract: ArtifactDrillContract,
    execute: CommandRunner,
    factory: BucketClientFactory,
    postgres: str,
    target: str,
    root_user: str,
    root_password: str,
    recorded: dict[str, dict[str, Any]],
    timings: dict[str, float],
    drill_state: dict[str, Any],
) -> None:
    if recorded.get("restore-completed") is not None:
        return
    check = _check(contract, "restore-completed")
    target_port = _start_minio(contract, execute, target, root_user, root_password)
    if target_port is None:
        _fail_remaining(contract, recorded, "target bucket container did not start")
        return
    target_client = factory(target_port, root_user, root_password, contract.bucket)
    snapshot_dir = Path(str(drill_state["snapshot_dir"]))
    manifest: list[dict[str, Any]] = drill_state["manifest"]
    restore_started = time.monotonic()
    try:
        target_client.ensure_bucket()
        for entry in manifest:
            payload = Path(snapshot_dir) / "payloads" / entry["payload"]
            target_client.put(
                entry["key"],
                payload.read_bytes(),
                content_type=entry["contentType"],
                metadata=entry["metadata"],
            )
    except Exception:  # noqa: BLE001 - any restore failure fails the drill closed
        timings["restoreSeconds"] = round(time.monotonic() - restore_started, 3)
        _fail_remaining(contract, recorded, "restore into the fresh bucket failed")
        return
    timings["restoreSeconds"] = round(time.monotonic() - restore_started, 3)
    _record(recorded, check, "passed", f"{len(manifest)} objects restored")

    verify_started = time.monotonic()
    try:
        _verify_object_count(contract, target_client, manifest, recorded)
        _verify_content_checksums(contract, target_client, manifest, recorded)
        _verify_metadata(contract, target_client, manifest, recorded)
        _verify_database_consistency(contract, execute, postgres, manifest, recorded)
    finally:
        timings["verifySeconds"] = round(time.monotonic() - verify_started, 3)


def _verify_object_count(
    contract: ArtifactDrillContract,
    target_client: BucketClient,
    manifest: list[dict[str, Any]],
    recorded: dict[str, dict[str, Any]],
) -> None:
    if recorded.get("object-count-parity") is not None:
        return
    check = _check(contract, "object-count-parity")
    try:
        keys = sorted(target_client.list_keys())
    except Exception:  # noqa: BLE001 - any listing failure fails the drill closed
        _fail_remaining(contract, recorded, "restored bucket listing failed")
        return
    expected = sorted(entry["key"] for entry in manifest)
    if keys != expected:
        _fail_remaining(contract, recorded, "restored key set diverged from the snapshot")
        return
    _record(recorded, check, "passed", f"{len(keys)} keys in parity")


def _verify_content_checksums(
    contract: ArtifactDrillContract,
    target_client: BucketClient,
    manifest: list[dict[str, Any]],
    recorded: dict[str, dict[str, Any]],
) -> None:
    if recorded.get("content-checksum-parity") is not None:
        return
    check = _check(contract, "content-checksum-parity")
    diverged = 0
    try:
        for entry in manifest:
            data = target_client.get(entry["key"])
            if hashlib.sha256(data).hexdigest() != entry["sha256"]:
                diverged += 1
    except Exception:  # noqa: BLE001 - any read failure fails the drill closed
        _fail_remaining(contract, recorded, "restored object reads failed")
        return
    if diverged:
        _fail_remaining(contract, recorded, f"{diverged} objects failed checksum parity")
        return
    _record(recorded, check, "passed", f"{len(manifest)} checksums identical")


def _verify_metadata(
    contract: ArtifactDrillContract,
    target_client: BucketClient,
    manifest: list[dict[str, Any]],
    recorded: dict[str, dict[str, Any]],
) -> None:
    if recorded.get("metadata-parity") is not None:
        return
    check = _check(contract, "metadata-parity")
    diverged = 0
    try:
        for entry in manifest:
            stat = target_client.stat(entry["key"])
            if (
                stat.content_type != entry["contentType"]
                or dict(sorted(stat.metadata.items())) != entry["metadata"]
            ):
                diverged += 1
    except Exception:  # noqa: BLE001 - any stat failure fails the drill closed
        _fail_remaining(contract, recorded, "restored object stat failed")
        return
    if diverged:
        _fail_remaining(contract, recorded, f"{diverged} objects failed metadata parity")
        return
    _record(recorded, check, "passed", f"{len(manifest)} metadata sets identical")


def _verify_database_consistency(
    contract: ArtifactDrillContract,
    execute: CommandRunner,
    postgres: str,
    manifest: list[dict[str, Any]],
    recorded: dict[str, dict[str, Any]],
) -> None:
    if recorded.get("database-consistency") is not None:
        return
    check = _check(contract, "database-consistency")
    by_key = {entry["key"]: entry["sha256"] for entry in manifest}
    queries = (
        "SELECT storage_key || '|' || checksum_sha256 FROM artifacts "
        "WHERE storage_key IS NOT NULL ORDER BY storage_key",
        "SELECT content_ref || '|' || checksum_sha256 FROM document_versions "
        "WHERE content_ref IS NOT NULL ORDER BY content_ref",
    )
    references = 0
    for query in queries:
        for line in _rows(execute, postgres, contract.database, query):
            key, separator, checksum = line.partition("|")
            if not separator:
                _fail_remaining(contract, recorded, "database reference rows are unreadable")
                return
            references += 1
            restored = by_key.get(key)
            if restored is None:
                _fail_remaining(
                    contract, recorded, "restored bucket misses a database-referenced object"
                )
                return
            if restored != checksum:
                _fail_remaining(
                    contract, recorded, "restored object checksum diverged from the database"
                )
                return
    if references < 1:
        _fail_remaining(contract, recorded, "no database storage references to verify")
        return
    _record(recorded, check, "passed", f"{references} database references consistent")


def _start_minio(
    contract: ArtifactDrillContract,
    execute: CommandRunner,
    container: str,
    root_user: str,
    root_password: str,
) -> str | None:
    started = execute(
        (
            "docker",
            "run",
            "-d",
            "--name",
            container,
            "-e",
            f"MINIO_ROOT_USER={root_user}",
            "-e",
            f"MINIO_ROOT_PASSWORD={root_password}",
            "-p",
            "127.0.0.1::9000",
            contract.minio_image,
            "server",
            "/data",
        ),
        {},
    )
    if started.returncode != 0:
        return None
    for _ in range(_READY_ATTEMPTS):
        ready = execute(
            (
                "docker",
                "exec",
                container,
                "curl",
                "-f",
                "-s",
                "http://localhost:9000/minio/health/live",
            ),
            {},
        )
        if ready.returncode == 0:
            break
        time.sleep(1)
    else:
        return None
    port = execute(("docker", "port", container, "9000"), {})
    if port.returncode != 0:
        return None
    match = re.search(r":(\d+)", port.stdout.decode("utf-8", errors="replace"))
    return f"127.0.0.1:{match.group(1)}" if match else None


def _seed_artifact_scenario(target: SeedTarget) -> None:
    """Write knowledge and file artifacts through the real REST API into MinIO."""

    from fastapi.testclient import TestClient

    from obsion.config import Environment, Settings
    from obsion.main import create_app

    settings = Settings(
        environment=Environment.TEST,
        database_url=target.database_url,
        allowed_origins=["http://testserver"],
        dev_bearer_token=secrets.token_urlsafe(24),
        run_worker_concurrency=2,
        event_stream_heartbeat_seconds=5,
        object_store_backend="minio",
        object_store_endpoint=target.object_endpoint,
        object_store_access_key=target.access_key,
        object_store_secret_key=target.secret_key,
        object_store_bucket=target.bucket,
    )
    with TestClient(
        create_app(settings),
        headers={"Authorization": f"Bearer {settings.dev_bearer_token.get_secret_value()}"},
    ) as client:
        workspace = client.post(
            "/api/v1/workspaces",
            json={"name": "Artifact drill workspace", "description": "Object-store drill"},
        )
        if workspace.status_code != 201:
            raise DrillError(f"drill workspace creation failed: {workspace.status_code}")
        document = client.post(
            "/api/v1/knowledge/documents",
            files={
                "file": (
                    "drill-policy.md",
                    b"# Drill policy\nArtifact bytes must survive every restore.",
                    "text/markdown",
                )
            },
            data={
                "source": "artifact-drill",
                "external_id": "artifact-drill-policy-v1",
                "title": "Artifact drill policy",
                "classification": "INTERNAL",
                "acl": '{"organization": true}',
            },
        )
        if document.status_code != 201:
            raise DrillError(f"drill knowledge ingest failed: {document.status_code}")
        workspace_id = workspace.json()["id"]
        payload = b"# Runbook\nSnapshot the bucket, restore it, verify every checksum.\n"
        artifact = client.post(
            f"/api/v1/workspaces/{workspace_id}/artifacts",
            files={"file": ("drill-runbook.md", payload, "text/markdown")},
            data={
                "title": "Drill runbook",
                "path": "/drill/runbook.md",
                "lineage": "{}",
            },
        )
        if artifact.status_code != 201:
            raise DrillError(f"drill artifact upload failed: {artifact.status_code}")
        artifact_id = artifact.json()["id"]
        content = client.get(f"/api/v1/artifacts/{artifact_id}/content")
        if content.status_code != 200 or content.content != payload:
            raise DrillError("drill artifact content roundtrip failed")


class _MinioBucketClient:
    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def ensure_bucket(self) -> None:
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)

    def list_keys(self) -> list[str]:
        return sorted(
            item.object_name for item in self._client.list_objects(self._bucket, recursive=True)
        )

    def stat(self, key: str) -> ObjectStat:
        result = self._client.stat_object(self._bucket, key)
        headers = dict(result.metadata or {})
        content_type = str(headers.pop("Content-Type", "application/octet-stream"))
        return ObjectStat(
            size=int(result.size),
            content_type=content_type,
            metadata=_user_metadata(headers),
        )

    def get(self, key: str) -> bytes:
        response = self._client.get_object(self._bucket, key)
        try:
            return bytes(response.read())
        finally:
            response.close()
            response.release_conn()

    def put(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> None:
        self._client.put_object(
            self._bucket,
            key,
            BytesIO(data),
            len(data),
            content_type=content_type,
            metadata=dict(metadata),
        )


def _user_metadata(headers: Mapping[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in headers.items():
        lowered = str(key).lower()
        if lowered.startswith("x-amz-meta-"):
            normalized[lowered.removeprefix("x-amz-meta-")] = str(value)
    return dict(sorted(normalized.items()))


def _minio_client_factory(
    endpoint: str,
    access_key: str,
    secret_key: str,
    bucket: str,
) -> BucketClient:
    from minio import Minio

    return _MinioBucketClient(
        Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=False),
        bucket,
    )


def _build_ledger(
    contract: ArtifactDrillContract,
    contract_path: Path,
    root: Path,
    *,
    revision: str,
    results: list[dict[str, Any]],
    timings: dict[str, float],
    drill_state: dict[str, Any],
) -> dict[str, Any]:
    manifest: list[dict[str, Any]] = drill_state["manifest"]
    manifest_digest = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    key_digest = hashlib.sha256(
        json.dumps(sorted(entry["key"] for entry in manifest), separators=(",", ":")).encode(
            "utf-8"
        )
    ).hexdigest()
    ledger: dict[str, Any] = {
        "apiVersion": "obsion.ai/v1",
        "kind": LEDGER_KIND,
        "metadata": {
            "name": contract.name,
            "releaseLine": _RELEASE_LINE,
            "revision": revision,
            "recordedAt": _utc_now(),
        },
        "spec": {
            "contract": _relative(contract_path, root),
            "postgresImage": contract.postgres_image,
            "minioImage": contract.minio_image,
            "database": contract.database,
            "bucket": contract.bucket,
            "alembicHead": drill_state["alembic_head"],
            "checks": results,
            "timings": {key: round(float(timings.get(key, 0.0)), 3) for key in _TIMING_KEYS},
            "snapshot": {
                "objectCount": len(manifest),
                "totalBytes": sum(entry["sizeBytes"] for entry in manifest),
                "manifestSha256": manifest_digest,
                "objectKeysSha256": key_digest,
            },
            "databaseReferences": dict(drill_state["database_references"]),
        },
    }
    ledger["spec"]["checksum"] = _canonical_digest(ledger)
    return ledger


def _validate_ledger(
    document: dict[str, Any],
    contract: ArtifactDrillContract,
    root: Path,
) -> None:
    if document.get("apiVersion") != "obsion.ai/v1":
        raise DrillError("artifact drill ledger apiVersion must be obsion.ai/v1")
    if document.get("kind") != LEDGER_KIND:
        raise DrillError(f"artifact drill ledger kind must be {LEDGER_KIND}")
    metadata = _mapping(document, "metadata", "artifact drill ledger")
    if _string(metadata, "name", "artifact drill ledger") != contract.name:
        raise DrillError("artifact drill ledger name must match the ladder contract")
    if _string(metadata, "releaseLine", "artifact drill ledger") != _RELEASE_LINE:
        raise DrillError(f"artifact drill ledger releaseLine must be {_RELEASE_LINE}")
    revision = _string(metadata, "revision", "artifact drill ledger")
    if not _REVISION_PATTERN.fullmatch(revision):
        raise DrillError("artifact drill ledger revision must be a full git SHA")
    _timestamp(metadata, "recordedAt", "artifact drill ledger")

    spec = _mapping(document, "spec", "artifact drill ledger")
    contract_ref = _string(spec, "contract", "artifact drill ledger")
    if Path(contract_ref).is_absolute() or ".." in Path(contract_ref).parts:
        raise DrillError("artifact drill ledger contract must stay inside the repository")
    if _string(spec, "postgresImage", "artifact drill ledger") != contract.postgres_image:
        raise DrillError("artifact drill ledger postgresImage must match the ladder contract")
    if _string(spec, "minioImage", "artifact drill ledger") != contract.minio_image:
        raise DrillError("artifact drill ledger minioImage must match the ladder contract")
    if _string(spec, "database", "artifact drill ledger") != contract.database:
        raise DrillError("artifact drill ledger database must match the ladder contract")
    if _string(spec, "bucket", "artifact drill ledger") != contract.bucket:
        raise DrillError("artifact drill ledger bucket must match the ladder contract")
    _validate_checks(spec, contract)
    head = _string(spec, "alembicHead", "artifact drill ledger")
    if not _ALEMBIC_PATTERN.fullmatch(head):
        raise DrillError("artifact drill ledger alembicHead is invalid")

    _validate_timings(spec)
    _validate_snapshot(spec, contract)
    references = _mapping(spec, "databaseReferences", "artifact drill ledger")
    for label in ("artifacts", "documentVersions"):
        value = references.get(label)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise DrillError(f"artifact drill ledger databaseReferences {label} must be >= 1")

    checksum = _string(spec, "checksum", "artifact drill ledger")
    payload = {**document, "spec": {key: value for key, value in spec.items() if key != "checksum"}}
    if checksum != _canonical_digest(payload):
        raise DrillError("artifact drill ledger checksum mismatch")


def _validate_checks(spec: dict[str, Any], contract: ArtifactDrillContract) -> None:
    raw_checks = spec.get("checks")
    if not isinstance(raw_checks, list) or not raw_checks:
        raise DrillError("artifact drill ledger checks must be a non-empty list")
    contract_checks = {check.check_id: check for check in contract.checks}
    seen: set[str] = set()
    for index, item in enumerate(raw_checks):
        if not isinstance(item, dict):
            raise DrillError(f"artifact drill ledger check at index {index} must be an object")
        _reject_forbidden_keys(item)
        check_id = _string(item, "id", "artifact drill ledger check")
        if check_id in seen:
            raise DrillError("artifact drill ledger check ids must be unique")
        seen.add(check_id)
        check = contract_checks.get(check_id)
        if check is None:
            raise DrillError(f"artifact drill ledger references unknown check {check_id}")
        if _string(item, "surface", "artifact drill ledger check") != check.surface:
            raise DrillError(f"artifact drill ledger check {check_id} surface mismatch")
        classification = _string(item, "classification", "artifact drill ledger check")
        if classification not in _LEDGER_CLASSIFICATIONS:
            raise DrillError(f"artifact drill ledger check {check_id} classification is invalid")
        if classification == "failed":
            raise DrillError(
                f"artifact drill ledger check {check_id} failed and cannot be evidence"
            )
        detail = item.get("detail", "")
        if not isinstance(detail, str) or len(detail) > _MAX_DETAIL_CHARS:
            raise DrillError(f"artifact drill ledger check {check_id} detail is invalid")
        _reject_forbidden_values(detail, check_id)
        _timestamp(item, "recordedAt", "artifact drill ledger check")
    if seen != set(contract_checks):
        raise DrillError("artifact drill ledger must record every ladder check")


def _validate_timings(spec: dict[str, Any]) -> None:
    timings = _mapping(spec, "timings", "artifact drill ledger")
    if set(timings) != set(_TIMING_KEYS):
        raise DrillError("artifact drill ledger timings must record every stage")
    for key, value in timings.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise DrillError(f"artifact drill ledger timing {key} must be a non-negative number")


def _validate_snapshot(spec: dict[str, Any], contract: ArtifactDrillContract) -> None:
    snapshot = _mapping(spec, "snapshot", "artifact drill ledger")
    count = snapshot.get("objectCount")
    if not isinstance(count, int) or isinstance(count, bool) or count < contract.minimum_objects:
        raise DrillError("artifact drill ledger snapshot misses the contract minimum objects")
    total = snapshot.get("totalBytes")
    if not isinstance(total, int) or isinstance(total, bool) or total <= 0:
        raise DrillError("artifact drill ledger snapshot totalBytes must be a positive integer")
    manifest_digest = _string(snapshot, "manifestSha256", "artifact drill ledger")
    if not _SHA256_PATTERN.fullmatch(manifest_digest):
        raise DrillError("artifact drill ledger manifestSha256 must be a lowercase hex digest")
    key_digest = _string(snapshot, "objectKeysSha256", "artifact drill ledger")
    if not _SHA256_PATTERN.fullmatch(key_digest):
        raise DrillError("artifact drill ledger objectKeysSha256 must be a lowercase hex digest")
