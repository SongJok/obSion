"""Fail-closed backup/restore drill evidence for the Alpha.1 candidate.

The operator runbook documents PostgreSQL backup and restore as prose.  This
module turns one real drill into durable, redacted, checksummed evidence: two
throwaway pinned PostgreSQL containers are created, the source is migrated with
Alembic and seeded through the real control-plane REST API, a custom-format
``pg_dump`` is restored into the fresh target, and parity invariants are
verified.  The ledger under ``docs/release/evidence/alpha1/`` is validated
offline by the release-candidate gate.  A skipped or unreachable check is a
failure, never a pass, and drill credentials never leave process memory.

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
import subprocess
import tempfile
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import yaml

DRILL_OPT_IN_ENV = "OBSION_DR_DRILL"
LEDGER_KIND = "DrillEvidenceLedger"
LADDER_KIND = "DrillEvidenceLadder"
_RELEASE_LINE = "alpha.1"
_CHECK_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_TABLE_PATTERN = re.compile(r"^[a-z_][a-z0-9_]*$")
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_ALEMBIC_PATTERN = re.compile(r"^[0-9a-f]{8,32}$")
_SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_CREDENTIAL_URI_PATTERN = re.compile(r"://[^/\s:]+:[^@\s]+@")
_OUTCOME_CLASSIFICATIONS = frozenset({"passed"})
_LEDGER_CLASSIFICATIONS = frozenset({"passed", "failed"})
_TIMING_KEYS = (
    "migrateSeconds",
    "seedSeconds",
    "dumpSeconds",
    "restoreSeconds",
    "verifySeconds",
    "totalSeconds",
)
_MAX_DETAIL_CHARS = 240
_COMMAND_TIMEOUT_SECONDS = 600
_READY_ATTEMPTS = 60
_FORBIDDEN_KEYS = frozenset(
    {"password", "secret", "token", "dsn", "database_url", "databaseurl", "authorization"}
)


class DrillError(ValueError):
    """Raised when drill recording or validation cannot proceed safely."""


@dataclass(frozen=True, slots=True)
class CheckSpec:
    check_id: str
    surface: str
    allowed: frozenset[str]


@dataclass(frozen=True, slots=True)
class DrillContract:
    name: str
    global_opt_in: str
    postgres_image: str
    database: str
    minimum_rows: dict[str, int]
    checks: tuple[CheckSpec, ...]


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: str


CommandRunner = Callable[[tuple[str, ...], Mapping[str, str]], CommandResult]
Seeder = Callable[[str], None]


class _PostgresContainerSpec(Protocol):
    """Structural surface shared by every drill contract that starts PostgreSQL."""

    @property
    def postgres_image(self) -> str: ...

    @property
    def database(self) -> str: ...


class _CheckLadder(Protocol):
    """Structural surface shared by every drill contract that records checks."""

    @property
    def checks(self) -> tuple[CheckSpec, ...]: ...


def load_drill_contract(path: Path, repository_root: Path) -> DrillContract:
    document = _load_mapping(path, "drill evidence ladder contract")
    if document.get("apiVersion") != "obsion.ai/v1":
        raise DrillError("drill evidence ladder apiVersion must be obsion.ai/v1")
    if document.get("kind") != LADDER_KIND:
        raise DrillError(f"drill evidence ladder kind must be {LADDER_KIND}")
    metadata = _mapping(document, "metadata", "drill evidence ladder")
    name = _string(metadata, "name", "drill evidence ladder")
    if not _CHECK_ID_PATTERN.fullmatch(name):
        raise DrillError("drill evidence ladder metadata.name must be a lowercase slug")
    if _string(metadata, "releaseLine", "drill evidence ladder") != _RELEASE_LINE:
        raise DrillError(f"drill evidence ladder releaseLine must be {_RELEASE_LINE}")
    spec = _mapping(document, "spec", "drill evidence ladder")
    global_opt_in = _string(spec, "globalOptIn", "drill evidence ladder")
    if not global_opt_in.startswith("OBSION_"):
        raise DrillError("drill evidence ladder globalOptIn must be an OBSION_ variable")
    postgres_image = _string(spec, "postgresImage", "drill evidence ladder")
    if "/" not in postgres_image or ":" not in postgres_image:
        raise DrillError("drill evidence ladder postgresImage must be a pinned repository:tag")
    database = _string(spec, "database", "drill evidence ladder")
    if not _TABLE_PATTERN.fullmatch(database):
        raise DrillError("drill evidence ladder database must be a lowercase identifier")
    minimum_rows = _minimum_rows(spec)
    raw_checks = spec.get("checks")
    if not isinstance(raw_checks, list) or not raw_checks:
        raise DrillError("drill evidence ladder checks must be a non-empty list")
    checks: list[CheckSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_checks):
        if not isinstance(item, dict):
            raise DrillError(f"drill evidence check at index {index} must be an object")
        check_id = _string(item, "id", "drill evidence check")
        if not _CHECK_ID_PATTERN.fullmatch(check_id):
            raise DrillError(f"drill evidence check id is invalid: {check_id}")
        if check_id in seen:
            raise DrillError("drill evidence check ids must be unique")
        seen.add(check_id)
        surface = _string(item, "surface", "drill evidence check")
        allowed = frozenset(_unique_strings(item, "allowed", "drill evidence check"))
        if not allowed or not allowed <= _OUTCOME_CLASSIFICATIONS:
            raise DrillError(
                f"drill evidence check {check_id} allowed classifications must be passed"
            )
        checks.append(CheckSpec(check_id=check_id, surface=surface, allowed=allowed))
    return DrillContract(
        name=name,
        global_opt_in=global_opt_in,
        postgres_image=postgres_image,
        database=database,
        minimum_rows=minimum_rows,
        checks=tuple(checks),
    )


def record_drill_evidence(
    contract_path: Path,
    output_path: Path,
    repository_root: Path,
    *,
    env: Mapping[str, str] | None = None,
    runner: CommandRunner | None = None,
    seeder: Seeder | None = None,
    revision: str | None = None,
) -> dict[str, Any]:
    """Run the backup/restore drill and write a redacted, checksummed ledger."""

    root = repository_root.resolve()
    environment = dict(os.environ if env is None else env)
    contract = load_drill_contract(contract_path, root)
    if environment.get(contract.global_opt_in, "").strip() != "1":
        raise DrillError(f"{contract.global_opt_in}=1 is required")
    resolved_revision = revision if revision is not None else _current_revision(root)
    if not _REVISION_PATTERN.fullmatch(resolved_revision):
        raise DrillError("drill evidence revision must be a full git SHA")
    execute = runner if runner is not None else _command_runner
    seed = seeder if seeder is not None else _seed_drill_dataset

    recorded: dict[str, dict[str, Any]] = {}
    password = secrets.token_urlsafe(24)
    suffix = secrets.token_hex(4)
    source = f"obsion-drill-src-{suffix}"
    target = f"obsion-drill-tgt-{suffix}"
    timings: dict[str, float] = {}
    started = time.monotonic()
    drill_state: dict[str, Any] = {
        "alembic_head": "",
        "dump_sha256": "",
        "dump_size": 0,
        "row_counts": {},
    }
    try:
        _stage_migrate(contract, execute, seed, source, password, recorded, timings, drill_state)
        _stage_dump(contract, execute, source, recorded, timings, drill_state)
        _stage_restore_and_verify(
            contract, execute, source, target, password, recorded, timings, drill_state
        )
    finally:
        for container in (source, target):
            execute(("docker", "rm", "-f", container), {})
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
        _reject_credential_material(entry["detail"], password)
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


def validate_drill_evidence(
    contract_path: Path,
    ledger_paths: list[Path],
    repository_root: Path,
) -> dict[str, Any]:
    """Validate recorded drill ledgers against the ladder contract offline."""

    root = repository_root.resolve()
    contract = load_drill_contract(contract_path, root)
    if not ledger_paths:
        raise DrillError("drill evidence validation requires at least one ledger")
    seen_paths: set[Path] = set()
    for ledger_path in ledger_paths:
        resolved = ledger_path.resolve()
        if resolved in seen_paths:
            raise DrillError("drill evidence ledgers must be unique files")
        seen_paths.add(resolved)
        document = _load_mapping(resolved, "drill evidence ledger")
        _validate_ledger(document, contract, root)
    return {
        "ledgers": len(ledger_paths),
        "checks": len(contract.checks),
    }


def _stage_migrate(
    contract: DrillContract,
    execute: CommandRunner,
    seed: Seeder,
    source: str,
    password: str,
    recorded: dict[str, dict[str, Any]],
    timings: dict[str, float],
    drill_state: dict[str, Any],
) -> None:
    check = _check(contract, "source-migrated")
    docker = execute(("docker", "version", "--format", "{{.Server.Version}}"), {})
    if docker.returncode != 0:
        _fail_remaining(contract, recorded, "docker is required for the drill")
        return
    port = _start_postgres(contract, execute, source, password)
    if port is None:
        _fail_remaining(contract, recorded, "source container did not start")
        return
    database_url = f"postgresql+asyncpg://obsion:{password}@127.0.0.1:{port}/{contract.database}"
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
    head = _scalar(execute, source, contract.database, "SELECT version_num FROM alembic_version")
    if head is None or not _ALEMBIC_PATTERN.fullmatch(head):
        _fail_remaining(contract, recorded, "alembic head is unreadable after upgrade")
        return
    drill_state["alembic_head"] = head
    _record(recorded, check, "passed", f"alembic head {head}")

    seed_started = time.monotonic()
    try:
        seed(database_url)
    except Exception as exc:  # noqa: BLE001 - any seeder failure fails the drill closed
        timings["seedSeconds"] = round(time.monotonic() - seed_started, 3)
        _fail_remaining(contract, recorded, f"scenario failed: {type(exc).__name__}")
        return
    timings["seedSeconds"] = round(time.monotonic() - seed_started, 3)
    shortfalls: list[str] = []
    for table, minimum in sorted(contract.minimum_rows.items()):
        observed = _scalar(
            execute,
            source,
            contract.database,
            f'SELECT count(*) FROM "{table}"',  # noqa: S608 - contract-validated identifier
        )
        if observed is None or not observed.isdigit() or int(observed) < minimum:
            shortfalls.append(table)
    if shortfalls:
        _fail_remaining(
            contract,
            recorded,
            "minimum rows not met: " + ", ".join(shortfalls),
        )
        return
    _record(
        recorded,
        _check(contract, "dataset-seeded"),
        "passed",
        f"seeded {len(contract.minimum_rows)} threshold tables",
    )


def _stage_dump(
    contract: DrillContract,
    execute: CommandRunner,
    source: str,
    recorded: dict[str, dict[str, Any]],
    timings: dict[str, float],
    drill_state: dict[str, Any],
) -> None:
    if recorded.get("dump-created") is not None:
        return
    check = _check(contract, "dump-created")
    dump_started = time.monotonic()
    dump = execute(
        ("docker", "exec", source, "pg_dump", "-U", "obsion", "-Fc", "-d", contract.database),
        {},
    )
    timings["dumpSeconds"] = round(time.monotonic() - dump_started, 3)
    if dump.returncode != 0 or not dump.stdout:
        _fail_remaining(contract, recorded, "pg_dump produced no backup")
        return
    digest = hashlib.sha256(dump.stdout).hexdigest()
    drill_state["dump_sha256"] = digest
    drill_state["dump_size"] = len(dump.stdout)
    drill_state["dump_bytes"] = dump.stdout
    _record(recorded, check, "passed", f"size={len(dump.stdout)} sha256={digest[:12]}")


def _stage_restore_and_verify(
    contract: DrillContract,
    execute: CommandRunner,
    source: str,
    target: str,
    password: str,
    recorded: dict[str, dict[str, Any]],
    timings: dict[str, float],
    drill_state: dict[str, Any],
) -> None:
    if recorded.get("restore-completed") is not None:
        return
    check = _check(contract, "restore-completed")
    port = _start_postgres(contract, execute, target, password)
    if port is None:
        _fail_remaining(contract, recorded, "target container did not start")
        return
    restore_started = time.monotonic()
    copied = CommandResult(returncode=1, stdout=b"", stderr="not run")
    restored = CommandResult(returncode=1, stdout=b"", stderr="not run")
    with tempfile.NamedTemporaryFile(prefix="obsion-drill-", suffix=".dump") as handle:
        handle.write(drill_state["dump_bytes"])
        handle.flush()
        copied = execute(
            ("docker", "cp", handle.name, f"{target}:/tmp/obsion-drill.dump"),  # noqa: S108 - fixed in-container path
            {},
        )
        if copied.returncode == 0:
            restored = execute(
                (
                    "docker",
                    "exec",
                    target,
                    "pg_restore",
                    "-U",
                    "obsion",
                    "-d",
                    contract.database,
                    "--exit-on-error",
                    "/tmp/obsion-drill.dump",  # noqa: S108 - fixed in-container path
                ),
                {},
            )
    timings["restoreSeconds"] = round(time.monotonic() - restore_started, 3)
    if copied.returncode != 0 or restored.returncode != 0:
        _fail_remaining(contract, recorded, "pg_restore failed")
        return
    _record(recorded, check, "passed", "restore exited cleanly")

    verify_started = time.monotonic()
    try:
        _verify_schema_parity(contract, execute, source, target, recorded, drill_state)
        _verify_row_counts(contract, execute, source, target, recorded, drill_state)
        _verify_referential_integrity(contract, execute, target, recorded)
        _verify_audit_preserved(contract, execute, source, target, recorded)
    finally:
        timings["verifySeconds"] = round(time.monotonic() - verify_started, 3)


def _verify_schema_parity(
    contract: DrillContract,
    execute: CommandRunner,
    source: str,
    target: str,
    recorded: dict[str, dict[str, Any]],
    drill_state: dict[str, Any],
) -> None:
    if recorded.get("schema-version-parity") is not None:
        return
    check = _check(contract, "schema-version-parity")
    restored = _scalar(
        execute, target, contract.database, "SELECT version_num FROM alembic_version"
    )
    if restored is None or restored != drill_state["alembic_head"]:
        _fail_remaining(contract, recorded, "restored alembic version diverged")
        return
    _record(recorded, check, "passed", f"alembic head {restored}")


def _verify_row_counts(
    contract: DrillContract,
    execute: CommandRunner,
    source: str,
    target: str,
    recorded: dict[str, dict[str, Any]],
    drill_state: dict[str, Any],
) -> None:
    if recorded.get("row-count-parity") is not None:
        return
    check = _check(contract, "row-count-parity")
    tables = _rows(
        execute,
        source,
        contract.database,
        "SELECT c.relname FROM pg_class AS c "
        "JOIN pg_namespace AS n ON n.oid = c.relnamespace "
        "WHERE n.nspname = 'public' AND c.relkind = 'r' ORDER BY c.relname",
    )
    if not tables:
        _fail_remaining(contract, recorded, "source tables are unreadable")
        return
    counts: dict[str, int] = {}
    diverged: list[str] = []
    for table in tables:
        if not _TABLE_PATTERN.fullmatch(table):
            _fail_remaining(contract, recorded, f"unexpected table name {table}")
            return
        source_count = _scalar(
            execute,
            source,
            contract.database,
            f'SELECT count(*) FROM "{table}"',  # noqa: S608 - catalog-sourced, pattern-validated
        )
        target_count = _scalar(
            execute,
            target,
            contract.database,
            f'SELECT count(*) FROM "{table}"',  # noqa: S608 - catalog-sourced, pattern-validated
        )
        if (
            source_count is None
            or target_count is None
            or not source_count.isdigit()
            or not target_count.isdigit()
            or source_count != target_count
        ):
            diverged.append(table)
            continue
        counts[table] = int(target_count)
    if diverged:
        _fail_remaining(contract, recorded, "count mismatch: " + ", ".join(diverged))
        return
    drill_state["row_counts"] = counts
    _record(recorded, check, "passed", f"{len(counts)} tables in parity")


def _verify_referential_integrity(
    contract: DrillContract,
    execute: CommandRunner,
    target: str,
    recorded: dict[str, dict[str, Any]],
) -> None:
    if recorded.get("referential-integrity") is not None:
        return
    check = _check(contract, "referential-integrity")
    orphan_queries = {
        "run_steps": "SELECT count(*) FROM run_steps s LEFT JOIN runs r ON r.id = s.run_id "
        "WHERE r.id IS NULL",
        "events": "SELECT count(*) FROM events e LEFT JOIN runs r ON r.id = e.run_id "
        "WHERE e.run_id IS NOT NULL AND r.id IS NULL",
        "turns": "SELECT count(*) FROM turns t LEFT JOIN threads th ON th.id = t.thread_id "
        "WHERE th.id IS NULL",
        "threads": "SELECT count(*) FROM threads th LEFT JOIN workspaces w "
        "ON w.id = th.workspace_id WHERE w.id IS NULL",
    }
    orphaned: list[str] = []
    for label, query in sorted(orphan_queries.items()):
        value = _scalar(execute, target, contract.database, query)
        if value != "0":
            orphaned.append(label)
    if orphaned:
        _fail_remaining(
            contract,
            recorded,
            "orphaned rows: " + ", ".join(orphaned),
        )
        return
    _record(recorded, check, "passed", f"{len(orphan_queries)} orphan probes clean")


def _verify_audit_preserved(
    contract: DrillContract,
    execute: CommandRunner,
    source: str,
    target: str,
    recorded: dict[str, dict[str, Any]],
) -> None:
    if recorded.get("audit-preserved") is not None:
        return
    check = _check(contract, "audit-preserved")
    query = "SELECT id FROM audit_logs ORDER BY id"
    source_ids = _rows(execute, source, contract.database, query)
    target_ids = _rows(execute, target, contract.database, query)
    if not target_ids or source_ids != target_ids:
        _fail_remaining(contract, recorded, "audit log identities diverged after restore")
        return
    _record(recorded, check, "passed", f"audit identities identical ({len(target_ids)})")


def _start_postgres(
    contract: _PostgresContainerSpec,
    execute: CommandRunner,
    container: str,
    password: str,
) -> str | None:
    started = execute(
        (
            "docker",
            "run",
            "-d",
            "--name",
            container,
            "-e",
            "POSTGRES_USER=obsion",
            "-e",
            f"POSTGRES_PASSWORD={password}",
            "-e",
            f"POSTGRES_DB={contract.database}",
            "-p",
            "127.0.0.1::5432",
            contract.postgres_image,
        ),
        {},
    )
    if started.returncode != 0:
        return None
    for _ in range(_READY_ATTEMPTS):
        ready = execute(
            ("docker", "exec", container, "pg_isready", "-U", "obsion", "-d", contract.database),
            {},
        )
        if ready.returncode == 0:
            break
        time.sleep(1)
    else:
        return None
    port = execute(("docker", "port", container, "5432"), {})
    if port.returncode != 0:
        return None
    match = re.search(r":(\d+)", port.stdout.decode("utf-8", errors="replace"))
    return match.group(1) if match else None


def _scalar(execute: CommandRunner, container: str, database: str, query: str) -> str | None:
    result = execute(
        ("docker", "exec", container, "psql", "-U", "obsion", "-d", database, "-tAc", query),
        {},
    )
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace").strip()


def _rows(execute: CommandRunner, container: str, database: str, query: str) -> list[str]:
    result = execute(
        ("docker", "exec", container, "psql", "-U", "obsion", "-d", database, "-tAc", query),
        {},
    )
    if result.returncode != 0:
        return []
    output = result.stdout.decode("utf-8", errors="replace").strip()
    return [line for line in output.splitlines() if line] if output else []


def _seed_drill_dataset(database_url: str) -> None:
    """Drive a real governed Harness scenario through the control-plane REST API."""

    from fastapi.testclient import TestClient

    from obsion.config import Environment, Settings
    from obsion.main import create_app

    settings = Settings(
        environment=Environment.TEST,
        database_url=database_url,
        allowed_origins=["http://testserver"],
        dev_bearer_token=secrets.token_urlsafe(24),
        run_worker_concurrency=2,
        event_stream_heartbeat_seconds=5,
    )
    with TestClient(
        create_app(settings),
        headers={"Authorization": f"Bearer {settings.dev_bearer_token.get_secret_value()}"},
    ) as client:
        workspace = client.post(
            "/api/v1/workspaces",
            json={"name": "Drill workspace", "description": "Backup/restore drill tenant"},
        )
        if workspace.status_code != 201:
            raise DrillError(f"drill workspace creation failed: {workspace.status_code}")
        document = client.post(
            "/api/v1/knowledge/documents",
            files={
                "file": (
                    "drill-policy.md",
                    b"# Drill policy\nEvery restore must preserve audit history and evidence.",
                    "text/markdown",
                )
            },
            data={
                "source": "dr-drill",
                "external_id": "drill-policy-v1",
                "title": "Drill policy",
                "classification": "INTERNAL",
                "acl": '{"organization": true}',
            },
        )
        if document.status_code != 201:
            raise DrillError(f"drill knowledge ingest failed: {document.status_code}")
        thread = client.post(
            "/api/v1/threads",
            json={"workspace_id": workspace.json()["id"], "title": "Drill thread"},
        )
        if thread.status_code != 201:
            raise DrillError(f"drill thread creation failed: {thread.status_code}")
        created = client.post(
            f"/api/v1/threads/{thread.json()['id']}/turns",
            json={"input": "What must every restore preserve?"},
        )
        if created.status_code != 202:
            raise DrillError(f"drill turn creation failed: {created.status_code}")
        run_id = created.json()["run"]["id"]
        status = ""
        for _ in range(240):
            run = client.get(f"/api/v1/runs/{run_id}")
            if run.status_code != 200:
                raise DrillError(f"drill run polling failed: {run.status_code}")
            status = run.json()["status"]
            if status in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
            time.sleep(0.5)
        if status != "COMPLETED":
            raise DrillError(f"drill run did not complete: {status or 'timeout'}")
        evidence = client.get(f"/api/v1/runs/{run_id}/evidence")
        if evidence.status_code != 200 or not evidence.json():
            raise DrillError("drill run produced no evidence")


def _command_runner(argv: tuple[str, ...], extra_env: Mapping[str, str]) -> CommandResult:
    executable = shutil.which(argv[0])
    if executable is None:
        return CommandResult(returncode=127, stdout=b"", stderr=f"{argv[0]} not found")
    try:
        result = subprocess.run(  # noqa: S603 - fixed drill commands, no user input
            [executable, *argv[1:]],
            capture_output=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
            check=False,
            env={**os.environ, **extra_env},
        )
    except (OSError, subprocess.TimeoutExpired):
        return CommandResult(returncode=124, stdout=b"", stderr="command failed or timed out")
    return CommandResult(
        returncode=result.returncode,
        stdout=result.stdout,
        stderr=result.stderr.decode("utf-8", errors="replace"),
    )


def _check(contract: _CheckLadder, check_id: str) -> CheckSpec:
    for check in contract.checks:
        if check.check_id == check_id:
            return check
    raise DrillError(f"drill ladder is missing check {check_id}")


def _record(
    recorded: dict[str, dict[str, Any]],
    check: CheckSpec,
    classification: str,
    detail: str,
) -> None:
    recorded[check.check_id] = {
        "id": check.check_id,
        "surface": check.surface,
        "classification": classification,
        "detail": detail[:_MAX_DETAIL_CHARS],
        "recordedAt": _utc_now(),
    }


def _fail_remaining(
    contract: _CheckLadder,
    recorded: dict[str, dict[str, Any]],
    detail: str,
) -> None:
    # A skip is never a pass: once a stage fails, every downstream check is a
    # failure so the ledger can never masquerade as partial evidence.
    pending = [check for check in contract.checks if recorded.get(check.check_id) is None]
    for index, check in enumerate(pending):
        _record(
            recorded,
            check,
            "failed",
            detail if index == 0 else "unreachable after upstream failure",
        )


def _build_ledger(
    contract: DrillContract,
    contract_path: Path,
    root: Path,
    *,
    revision: str,
    results: list[dict[str, Any]],
    timings: dict[str, float],
    drill_state: dict[str, Any],
) -> dict[str, Any]:
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
            "alembicHead": drill_state["alembic_head"],
            "checks": results,
            "timings": {key: round(float(timings.get(key, 0.0)), 3) for key in _TIMING_KEYS},
            "dump": {
                "format": "custom",
                "sizeBytes": int(drill_state["dump_size"]),
                "sha256": drill_state["dump_sha256"],
            },
            "rowCounts": dict(sorted(drill_state["row_counts"].items())),
        },
    }
    ledger["spec"]["checksum"] = _canonical_digest(ledger)
    return ledger


def _validate_ledger(
    document: dict[str, Any],
    contract: DrillContract,
    root: Path,
) -> None:
    if document.get("apiVersion") != "obsion.ai/v1":
        raise DrillError("drill evidence ledger apiVersion must be obsion.ai/v1")
    if document.get("kind") != LEDGER_KIND:
        raise DrillError(f"drill evidence ledger kind must be {LEDGER_KIND}")
    metadata = _mapping(document, "metadata", "drill evidence ledger")
    if _string(metadata, "name", "drill evidence ledger") != contract.name:
        raise DrillError("drill evidence ledger name must match the ladder contract")
    if _string(metadata, "releaseLine", "drill evidence ledger") != _RELEASE_LINE:
        raise DrillError(f"drill evidence ledger releaseLine must be {_RELEASE_LINE}")
    revision = _string(metadata, "revision", "drill evidence ledger")
    if not _REVISION_PATTERN.fullmatch(revision):
        raise DrillError("drill evidence ledger revision must be a full git SHA")
    _timestamp(metadata, "recordedAt", "drill evidence ledger")

    spec = _mapping(document, "spec", "drill evidence ledger")
    contract_ref = _string(spec, "contract", "drill evidence ledger")
    if Path(contract_ref).is_absolute() or ".." in Path(contract_ref).parts:
        raise DrillError("drill evidence ledger contract must stay inside the repository")
    if _string(spec, "postgresImage", "drill evidence ledger") != contract.postgres_image:
        raise DrillError("drill evidence ledger postgresImage must match the ladder contract")
    _validate_checks(spec, contract)
    head = _string(spec, "alembicHead", "drill evidence ledger")
    if not _ALEMBIC_PATTERN.fullmatch(head):
        raise DrillError("drill evidence ledger alembicHead is invalid")

    _validate_timings(spec)
    _validate_dump(spec)
    _validate_row_counts(spec, contract)

    checksum = _string(spec, "checksum", "drill evidence ledger")
    payload = {**document, "spec": {key: value for key, value in spec.items() if key != "checksum"}}
    if checksum != _canonical_digest(payload):
        raise DrillError("drill evidence ledger checksum mismatch")


def _validate_checks(spec: dict[str, Any], contract: DrillContract) -> None:
    raw_checks = spec.get("checks")
    if not isinstance(raw_checks, list) or not raw_checks:
        raise DrillError("drill evidence ledger checks must be a non-empty list")
    contract_checks = {check.check_id: check for check in contract.checks}
    seen: set[str] = set()
    for index, item in enumerate(raw_checks):
        if not isinstance(item, dict):
            raise DrillError(f"drill evidence ledger check at index {index} must be an object")
        _reject_forbidden_keys(item)
        check_id = _string(item, "id", "drill evidence ledger check")
        if check_id in seen:
            raise DrillError("drill evidence ledger check ids must be unique")
        seen.add(check_id)
        check = contract_checks.get(check_id)
        if check is None:
            raise DrillError(f"drill evidence ledger references unknown check {check_id}")
        if _string(item, "surface", "drill evidence ledger check") != check.surface:
            raise DrillError(f"drill evidence ledger check {check_id} surface mismatch")
        classification = _string(item, "classification", "drill evidence ledger check")
        if classification not in _LEDGER_CLASSIFICATIONS:
            raise DrillError(f"drill evidence ledger check {check_id} classification is invalid")
        if classification == "failed":
            raise DrillError(
                f"drill evidence ledger check {check_id} failed and cannot be evidence"
            )
        detail = item.get("detail", "")
        if not isinstance(detail, str) or len(detail) > _MAX_DETAIL_CHARS:
            raise DrillError(f"drill evidence ledger check {check_id} detail is invalid")
        _reject_forbidden_values(detail, check_id)
        _timestamp(item, "recordedAt", "drill evidence ledger check")
    if seen != set(contract_checks):
        raise DrillError("drill evidence ledger must record every ladder check")


def _validate_timings(spec: dict[str, Any]) -> None:
    timings = _mapping(spec, "timings", "drill evidence ledger")
    if set(timings) != set(_TIMING_KEYS):
        raise DrillError("drill evidence ledger timings must record every stage")
    for key, value in timings.items():
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
            raise DrillError(f"drill evidence ledger timing {key} must be a non-negative number")


def _validate_dump(spec: dict[str, Any]) -> None:
    dump = _mapping(spec, "dump", "drill evidence ledger")
    if dump.get("format") != "custom":
        raise DrillError("drill evidence ledger dump format must be custom")
    size = dump.get("sizeBytes")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        raise DrillError("drill evidence ledger dump sizeBytes must be a positive integer")
    digest = _string(dump, "sha256", "drill evidence ledger")
    if not _SHA256_PATTERN.fullmatch(digest):
        raise DrillError("drill evidence ledger dump sha256 must be a lowercase hex digest")


def _validate_row_counts(spec: dict[str, Any], contract: DrillContract) -> None:
    counts = _mapping(spec, "rowCounts", "drill evidence ledger")
    if not counts:
        raise DrillError("drill evidence ledger rowCounts must not be empty")
    for table, value in counts.items():
        if not _TABLE_PATTERN.fullmatch(str(table)):
            raise DrillError(f"drill evidence ledger rowCounts table is invalid: {table}")
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise DrillError(f"drill evidence ledger row count for {table} must be >= 0")
    shortfalls = [
        table for table, minimum in contract.minimum_rows.items() if counts.get(table, 0) < minimum
    ]
    if shortfalls:
        raise DrillError(
            "drill evidence ledger rowCounts miss contract minimums: " + ", ".join(shortfalls)
        )


def _reject_forbidden_keys(item: Mapping[str, Any]) -> None:
    for key in item:
        if str(key).lower() in _FORBIDDEN_KEYS:
            raise DrillError(f"drill evidence ledger contains forbidden key {key}")


def _reject_forbidden_values(value: str, check_id: str) -> None:
    if _CREDENTIAL_URI_PATTERN.search(value):
        raise DrillError(
            f"drill evidence check {check_id} detail contains credential-shaped material"
        )


def _reject_credential_material(detail: str, password: str) -> None:
    if password and password in detail:
        raise DrillError("drill evidence detail contains credential material")
    _reject_forbidden_values(detail, "ledger")


def _minimum_rows(spec: dict[str, Any]) -> dict[str, int]:
    raw = spec.get("minimumRows")
    if not isinstance(raw, dict) or not raw:
        raise DrillError("drill evidence ladder minimumRows must be a non-empty object")
    minimums: dict[str, int] = {}
    for table, value in raw.items():
        if not _TABLE_PATTERN.fullmatch(str(table)):
            raise DrillError(f"drill evidence ladder minimumRows table is invalid: {table}")
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise DrillError(f"drill evidence ladder minimumRows {table} must be >= 1")
        minimums[str(table)] = value
    return minimums


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _current_revision(root: Path) -> str:
    git = shutil.which("git")
    if git is None:
        raise DrillError("unable to resolve the checked-out git revision")
    try:
        result = subprocess.run(  # noqa: S603 - fixed git invocation
            [git, "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise DrillError("unable to resolve the checked-out git revision") from exc
    revision = result.stdout.strip()
    if result.returncode != 0 or not _REVISION_PATTERN.fullmatch(revision):
        raise DrillError("unable to resolve the checked-out git revision")
    return revision


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _relative(path: Path, root: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return resolved.as_posix()


def _load_mapping(path: Path, label: str) -> dict[str, Any]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DrillError(f"unable to load {label}: {path}") from exc
    if not isinstance(document, dict):
        raise DrillError(f"{label} must be an object")
    return document


def _mapping(parent: dict[str, Any], key: str, label: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise DrillError(f"{label} {key} must be an object")
    return value


def _string(parent: dict[str, Any], key: str, label: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DrillError(f"{label} {key} must be a non-empty string")
    return value.strip()


def _unique_strings(parent: dict[str, Any], key: str, label: str) -> list[str]:
    value = parent.get(key)
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise DrillError(f"{label} {key} must be a non-empty string list")
    normalized = [item.strip() for item in value]
    if len(set(normalized)) != len(normalized):
        raise DrillError(f"{label} {key} must not contain duplicates")
    return normalized


def _timestamp(parent: dict[str, Any], key: str, label: str) -> str:
    value = _string(parent, key, label)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DrillError(f"{label} {key} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise DrillError(f"{label} {key} must include a timezone")
    return value
