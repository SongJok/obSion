"""Fail-closed live-tenant evidence ledger for the Alpha.1 candidate.

Operator-run live probes already exist as opt-in pytest markers.  This module
turns one recorded ladder run into a durable, redacted, checksummed ledger under
``docs/release/evidence/alpha1/`` that the release-candidate gate can validate
without ever re-running vendor traffic.  A skip is never a pass, a probe that
does not emit a result record fails closed, and credential material can never
enter a ledger.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

LIVE_PROBE_DIR_ENV = "OBSION_LIVE_PROBE_DIR"
LEDGER_KIND = "LiveEvidenceLedger"
LADDER_KIND = "LiveEvidenceLadder"
_RELEASE_LINE = "alpha.1"
_PROBE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_PROFILE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_FINGERPRINT_PATTERN = re.compile(r"^sha256:[0-9a-f]{12}$")
_APP_ID_PATTERN = re.compile(r"\bcli_[0-9a-z]{10,}\b")
_TENANT_TOKEN_PATTERN = re.compile(r"\bt-[A-Za-z0-9_-]{20,}\b")
_OUTCOME_CLASSIFICATIONS = frozenset({"passed", "denied"})
_LEDGER_CLASSIFICATIONS = frozenset({"passed", "denied", "failed", "skipped"})
_PROBE_TIMEOUT_SECONDS = 300
_MAX_DETAIL_CHARS = 240


class LiveEvidenceError(ValueError):
    """Raised when live-evidence recording or validation cannot proceed safely."""


@dataclass(frozen=True, slots=True)
class ProbeSpec:
    probe_id: str
    surface: str
    node_id: str
    allowed: frozenset[str]
    optional: bool
    required_env: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class LadderContract:
    name: str
    global_opt_in: str
    credential_env: tuple[str, ...]
    probes: tuple[ProbeSpec, ...]


@dataclass(frozen=True, slots=True)
class ProbeRun:
    returncode: int
    junit_path: Path | None


ProbeRunner = Callable[[ProbeSpec, Path, Mapping[str, str]], ProbeRun]


def write_probe_record(probe: str, classification: str, detail: str) -> None:
    """Emit a structured live-probe outcome when the recorder requested one.

    Tests stay human-runnable: without ``OBSION_LIVE_PROBE_DIR`` this is a no-op.
    """

    directory = os.environ.get(LIVE_PROBE_DIR_ENV, "").strip()
    if not directory:
        return
    if classification not in _OUTCOME_CLASSIFICATIONS:
        raise LiveEvidenceError(f"live probe classification is invalid: {classification}")
    payload = {
        "probe": probe,
        "classification": classification,
        "detail": detail[:_MAX_DETAIL_CHARS],
    }
    Path(directory, f"{probe}.json").write_text(
        json.dumps(payload, sort_keys=True), encoding="utf-8"
    )


def load_ladder_contract(path: Path, repository_root: Path) -> LadderContract:
    document = _load_mapping(path, "live evidence ladder contract")
    if document.get("apiVersion") != "obsion.ai/v1":
        raise LiveEvidenceError("live evidence ladder apiVersion must be obsion.ai/v1")
    if document.get("kind") != LADDER_KIND:
        raise LiveEvidenceError(f"live evidence ladder kind must be {LADDER_KIND}")
    metadata = _mapping(document, "metadata", "live evidence ladder")
    name = _string(metadata, "name", "live evidence ladder")
    if not _PROBE_ID_PATTERN.fullmatch(name):
        raise LiveEvidenceError("live evidence ladder metadata.name must be a lowercase slug")
    if _string(metadata, "releaseLine", "live evidence ladder") != _RELEASE_LINE:
        raise LiveEvidenceError(f"live evidence ladder releaseLine must be {_RELEASE_LINE}")
    spec = _mapping(document, "spec", "live evidence ladder")
    global_opt_in = _string(spec, "globalOptIn", "live evidence ladder")
    if not global_opt_in.startswith("OBSION_"):
        raise LiveEvidenceError("live evidence ladder globalOptIn must be an OBSION_ variable")
    credential_env = _unique_strings(spec, "credentialEnv", "live evidence ladder")
    for variable in credential_env:
        if not variable.startswith("OBSION_"):
            raise LiveEvidenceError("live evidence ladder credentialEnv must use OBSION_ variables")
    raw_probes = spec.get("probes")
    if not isinstance(raw_probes, list) or not raw_probes:
        raise LiveEvidenceError("live evidence ladder probes must be a non-empty list")
    probes: list[ProbeSpec] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_probes):
        if not isinstance(item, dict):
            raise LiveEvidenceError(f"live evidence probe at index {index} must be an object")
        probe_id = _string(item, "id", "live evidence probe")
        if not _PROBE_ID_PATTERN.fullmatch(probe_id):
            raise LiveEvidenceError(f"live evidence probe id is invalid: {probe_id}")
        if probe_id in seen:
            raise LiveEvidenceError("live evidence probe ids must be unique")
        seen.add(probe_id)
        surface = _string(item, "surface", "live evidence probe")
        node_id = _string(item, "nodeId", "live evidence probe")
        _validate_node_id(node_id, repository_root)
        allowed = frozenset(_unique_strings(item, "allowed", "live evidence probe"))
        if not allowed or not allowed <= _OUTCOME_CLASSIFICATIONS:
            raise LiveEvidenceError(
                f"live evidence probe {probe_id} allowed classifications must be passed/denied"
            )
        optional = item.get("optional", False)
        if not isinstance(optional, bool):
            raise LiveEvidenceError(f"live evidence probe {probe_id} optional must be a boolean")
        required_env: tuple[str, ...] = ()
        raw_required = item.get("requiredEnv")
        if raw_required is not None:
            required_env = tuple(_unique_strings(item, "requiredEnv", "live evidence probe"))
            for variable in required_env:
                if not variable.startswith("OBSION_"):
                    raise LiveEvidenceError(
                        f"live evidence probe {probe_id} requiredEnv must use OBSION_ variables"
                    )
        if optional and not required_env:
            raise LiveEvidenceError(
                f"optional live evidence probe {probe_id} must declare requiredEnv"
            )
        if not optional and required_env:
            raise LiveEvidenceError(
                f"required live evidence probe {probe_id} cannot declare extra requiredEnv"
            )
        probes.append(
            ProbeSpec(
                probe_id=probe_id,
                surface=surface,
                node_id=node_id,
                allowed=allowed,
                optional=optional,
                required_env=required_env,
            )
        )
    return LadderContract(
        name=name,
        global_opt_in=global_opt_in,
        credential_env=tuple(credential_env),
        probes=tuple(probes),
    )


def record_live_evidence(
    contract_path: Path,
    output_path: Path,
    repository_root: Path,
    *,
    profile_label: str,
    include_optional: bool = False,
    env: Mapping[str, str] | None = None,
    runner: ProbeRunner | None = None,
    revision: str | None = None,
) -> dict[str, Any]:
    """Run the declared live ladder and write a redacted, checksummed ledger."""

    root = repository_root.resolve()
    environment = dict(os.environ if env is None else env)
    contract = load_ladder_contract(contract_path, root)
    if not _PROFILE_PATTERN.fullmatch(profile_label):
        raise LiveEvidenceError("live evidence profile label must be a lowercase slug")
    if environment.get(contract.global_opt_in, "").strip() != "1":
        raise LiveEvidenceError(f"{contract.global_opt_in}=1 is required")
    for variable in contract.credential_env:
        if not environment.get(variable, "").strip():
            raise LiveEvidenceError(f"{variable} is required")
    secrets = tuple(
        environment[variable].strip()
        for variable in contract.credential_env
        if environment.get(variable, "").strip()
    )
    fingerprint = "sha256:" + hashlib.sha256(secrets[0].encode("utf-8")).hexdigest()[:12]
    resolved_revision = revision if revision is not None else _current_revision(root)
    if not _REVISION_PATTERN.fullmatch(resolved_revision):
        raise LiveEvidenceError("live evidence revision must be a full git SHA")
    execute = runner if runner is not None else _pytest_runner

    results: list[dict[str, Any]] = []
    for probe in contract.probes:
        recorded_at = _utc_now()
        if probe.optional and not include_optional:
            results.append(
                _result_entry(
                    probe,
                    "skipped",
                    "optional probe not requested",
                    recorded_at,
                )
            )
            continue
        missing = [
            variable for variable in probe.required_env if not environment.get(variable, "").strip()
        ]
        if missing:
            raise LiveEvidenceError(
                f"live evidence probe {probe.probe_id} requires {', '.join(missing)}"
            )
        with tempfile.TemporaryDirectory(prefix="obsion-live-probe-") as probe_dir:
            run = execute(probe, Path(probe_dir), environment)
            results.append(_classify_probe(probe, run, Path(probe_dir), recorded_at, secrets))

    ledger = _build_ledger(
        contract,
        contract_path,
        root,
        profile_label=profile_label,
        fingerprint=fingerprint,
        revision=resolved_revision,
        results=results,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        yaml.safe_dump(ledger, sort_keys=True, allow_unicode=True), encoding="utf-8"
    )
    failed = [entry["id"] for entry in results if entry["classification"] == "failed"]
    return {
        "profile": profile_label,
        "ledger": _relative(output_path, root),
        "revision": resolved_revision,
        "probes": len(results),
        "passed": sum(1 for entry in results if entry["classification"] == "passed"),
        "denied": sum(1 for entry in results if entry["classification"] == "denied"),
        "skipped": sum(1 for entry in results if entry["classification"] == "skipped"),
        "failed": failed,
    }


def validate_live_evidence(
    contract_path: Path,
    ledger_paths: list[Path],
    repository_root: Path,
) -> dict[str, Any]:
    """Validate recorded ledgers against the ladder contract without vendor traffic."""

    root = repository_root.resolve()
    contract = load_ladder_contract(contract_path, root)
    if not ledger_paths:
        raise LiveEvidenceError("live evidence validation requires at least one ledger")
    seen_paths: set[Path] = set()
    coverage: dict[str, str] = {}
    for ledger_path in ledger_paths:
        resolved = ledger_path.resolve()
        if resolved in seen_paths:
            raise LiveEvidenceError("live evidence ledgers must be unique files")
        seen_paths.add(resolved)
        document = _load_mapping(resolved, "live evidence ledger")
        _validate_ledger(document, contract, root)
        for entry in document["spec"]["probes"]:
            classification = entry["classification"]
            if classification in _OUTCOME_CLASSIFICATIONS:
                coverage[entry["id"]] = classification
    missing = sorted(probe.probe_id for probe in contract.probes if probe.probe_id not in coverage)
    if missing:
        raise LiveEvidenceError(
            "live evidence ledgers do not cover ladder probes: " + ", ".join(missing)
        )
    for probe in contract.probes:
        if coverage[probe.probe_id] not in probe.allowed:
            raise LiveEvidenceError(
                f"live evidence probe {probe.probe_id} outcome {coverage[probe.probe_id]} "
                "is outside the contract-allowed classifications"
            )
    return {
        "ledgers": len(ledger_paths),
        "probes": len(contract.probes),
        "covered": len(coverage),
    }


def _validate_ledger(
    document: dict[str, Any],
    contract: LadderContract,
    root: Path,
) -> None:
    if document.get("apiVersion") != "obsion.ai/v1":
        raise LiveEvidenceError("live evidence ledger apiVersion must be obsion.ai/v1")
    if document.get("kind") != LEDGER_KIND:
        raise LiveEvidenceError(f"live evidence ledger kind must be {LEDGER_KIND}")
    metadata = _mapping(document, "metadata", "live evidence ledger")
    name = _string(metadata, "name", "live evidence ledger")
    if name != contract.name:
        raise LiveEvidenceError("live evidence ledger name must match the ladder contract")
    if _string(metadata, "releaseLine", "live evidence ledger") != _RELEASE_LINE:
        raise LiveEvidenceError(f"live evidence ledger releaseLine must be {_RELEASE_LINE}")
    profile = _string(metadata, "profile", "live evidence ledger")
    if not _PROFILE_PATTERN.fullmatch(profile):
        raise LiveEvidenceError("live evidence ledger profile must be a lowercase slug")
    fingerprint = _string(metadata, "appFingerprint", "live evidence ledger")
    if not _FINGERPRINT_PATTERN.fullmatch(fingerprint):
        raise LiveEvidenceError("live evidence ledger appFingerprint must be a truncated sha256")
    revision = _string(metadata, "revision", "live evidence ledger")
    if not _REVISION_PATTERN.fullmatch(revision):
        raise LiveEvidenceError("live evidence ledger revision must be a full git SHA")
    _timestamp(metadata, "recordedAt", "live evidence ledger")

    spec = _mapping(document, "spec", "live evidence ledger")
    contract_ref = _string(spec, "contract", "live evidence ledger")
    if Path(contract_ref).is_absolute() or ".." in Path(contract_ref).parts:
        raise LiveEvidenceError("live evidence ledger contract must stay inside the repository")
    raw_probes = spec.get("probes")
    if not isinstance(raw_probes, list) or not raw_probes:
        raise LiveEvidenceError("live evidence ledger probes must be a non-empty list")
    contract_probes = {probe.probe_id: probe for probe in contract.probes}
    seen: set[str] = set()
    for index, item in enumerate(raw_probes):
        if not isinstance(item, dict):
            raise LiveEvidenceError(
                f"live evidence ledger probe at index {index} must be an object"
            )
        _reject_forbidden_keys(item)
        probe_id = _string(item, "id", "live evidence ledger probe")
        if probe_id in seen:
            raise LiveEvidenceError("live evidence ledger probe ids must be unique")
        seen.add(probe_id)
        probe = contract_probes.get(probe_id)
        if probe is None:
            raise LiveEvidenceError(f"live evidence ledger references unknown probe {probe_id}")
        if _string(item, "surface", "live evidence ledger probe") != probe.surface:
            raise LiveEvidenceError(f"live evidence ledger probe {probe_id} surface mismatch")
        classification = _string(item, "classification", "live evidence ledger probe")
        if classification not in _LEDGER_CLASSIFICATIONS:
            raise LiveEvidenceError(
                f"live evidence ledger probe {probe_id} classification is invalid"
            )
        if classification == "failed":
            raise LiveEvidenceError(
                f"live evidence ledger probe {probe_id} failed and cannot be evidence"
            )
        if classification == "skipped" and not probe.optional:
            raise LiveEvidenceError(f"required live evidence probe {probe_id} cannot be skipped")
        detail = item.get("detail", "")
        if not isinstance(detail, str) or len(detail) > _MAX_DETAIL_CHARS:
            raise LiveEvidenceError(f"live evidence ledger probe {probe_id} detail is invalid")
        _reject_forbidden_values(detail, probe_id)
        _timestamp(item, "recordedAt", "live evidence ledger probe")
    if seen != set(contract_probes):
        raise LiveEvidenceError("live evidence ledger must record every ladder probe")

    checksum = _string(spec, "checksum", "live evidence ledger")
    payload = {**document, "spec": {key: value for key, value in spec.items() if key != "checksum"}}
    if checksum != _canonical_digest(payload):
        raise LiveEvidenceError("live evidence ledger checksum mismatch")


def _build_ledger(
    contract: LadderContract,
    contract_path: Path,
    root: Path,
    *,
    profile_label: str,
    fingerprint: str,
    revision: str,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    ledger: dict[str, Any] = {
        "apiVersion": "obsion.ai/v1",
        "kind": LEDGER_KIND,
        "metadata": {
            "name": contract.name,
            "releaseLine": _RELEASE_LINE,
            "profile": profile_label,
            "appFingerprint": fingerprint,
            "revision": revision,
            "recordedAt": _utc_now(),
        },
        "spec": {
            "contract": _relative(contract_path, root),
            "probes": results,
        },
    }
    ledger["spec"]["checksum"] = _canonical_digest(ledger)
    return ledger


def _classify_probe(
    probe: ProbeSpec,
    run: ProbeRun,
    probe_dir: Path,
    recorded_at: str,
    secrets: tuple[str, ...],
) -> dict[str, Any]:
    status, junit_detail = _junit_status(run.junit_path)
    if run.returncode not in {0, 1}:
        return _result_entry(probe, "failed", "probe runner failed", recorded_at)
    if status == "failed":
        return _result_entry(probe, "failed", junit_detail, recorded_at)
    if status == "skipped":
        # A skip is never a pass: the only legitimate skipped entry is an
        # optional probe the operator did not request, recorded upstream.
        return _result_entry(
            probe, "failed", f"probe skipped after opt-in: {junit_detail}", recorded_at
        )
    record_path = probe_dir / f"{probe.probe_id}.json"
    if not record_path.is_file():
        return _result_entry(probe, "failed", "probe did not emit a result record", recorded_at)
    record = _load_probe_record(record_path, probe)
    classification = record["classification"]
    detail = record["detail"]
    for secret in secrets:
        if secret and secret in detail:
            raise LiveEvidenceError(
                f"live evidence probe {probe.probe_id} detail contains credential material"
            )
    _reject_forbidden_values(detail, probe.probe_id)
    if classification not in probe.allowed:
        return _result_entry(
            probe,
            "failed",
            f"outcome {classification} is outside contract-allowed classifications",
            recorded_at,
        )
    return _result_entry(probe, classification, detail, recorded_at)


def _load_probe_record(path: Path, probe: ProbeSpec) -> dict[str, str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LiveEvidenceError(
            f"live evidence probe {probe.probe_id} emitted an invalid result record"
        ) from exc
    if not isinstance(payload, dict) or set(payload) != {"probe", "classification", "detail"}:
        raise LiveEvidenceError(
            f"live evidence probe {probe.probe_id} emitted an invalid result record"
        )
    if payload["probe"] != probe.probe_id:
        raise LiveEvidenceError(
            f"live evidence probe {probe.probe_id} emitted a record for another probe"
        )
    classification = payload["classification"]
    detail = payload["detail"]
    if classification not in _OUTCOME_CLASSIFICATIONS or not isinstance(detail, str):
        raise LiveEvidenceError(
            f"live evidence probe {probe.probe_id} emitted an invalid result record"
        )
    return {"classification": classification, "detail": detail}


def _junit_status(junit_path: Path | None) -> tuple[str, str]:
    if junit_path is None or not junit_path.is_file():
        return "failed", "probe did not produce a junit report"
    try:
        tree = ET.parse(junit_path)  # noqa: S314 - junit files are recorder-produced
    except ET.ParseError:
        return "failed", "probe junit report is invalid"
    root = tree.getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    cases = [case for suite in suites for case in suite.iter("testcase")]
    if len(cases) != 1:
        return "failed", "probe junit report must contain exactly one testcase"
    case = cases[0]
    if case.find("failure") is not None or case.find("error") is not None:
        return "failed", "probe test failed"
    skipped = case.find("skipped")
    if skipped is not None:
        return "skipped", (skipped.get("message") or "probe skipped")[:_MAX_DETAIL_CHARS]
    return "passed", ""


def _result_entry(
    probe: ProbeSpec,
    classification: str,
    detail: str,
    recorded_at: str,
) -> dict[str, Any]:
    return {
        "id": probe.probe_id,
        "surface": probe.surface,
        "classification": classification,
        "detail": detail[:_MAX_DETAIL_CHARS],
        "recordedAt": recorded_at,
    }


def _pytest_runner(probe: ProbeSpec, probe_dir: Path, env: Mapping[str, str]) -> ProbeRun:
    uv = shutil.which("uv")
    if uv is None:
        raise LiveEvidenceError("uv is required to run live evidence probes")
    junit_path = probe_dir / "junit.xml"
    subprocess_env = dict(env)
    subprocess_env[LIVE_PROBE_DIR_ENV] = str(probe_dir)
    # The global read ladder opt-in authorizes the read-only browse probe; the
    # write probe keeps its own pre-existing OBSION_FEISHU_SEND_LIVE opt-in.
    subprocess_env.setdefault("OBSION_FEISHU_BROWSE_LIVE", "1")
    try:
        result = subprocess.run(  # noqa: S603 - fixed pytest invocation
            [
                uv,
                "run",
                "pytest",
                "--no-cov",
                "-p",
                "no:cacheprovider",
                probe.node_id,
                "--junitxml",
                str(junit_path),
            ],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT_SECONDS,
            check=False,
            env=subprocess_env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise LiveEvidenceError(f"live evidence probe {probe.probe_id} runner failed") from exc
    return ProbeRun(returncode=result.returncode, junit_path=junit_path)


def _validate_node_id(node_id: str, root: Path) -> None:
    file_part, separator, test_name = node_id.partition("::")
    if not separator or not test_name:
        raise LiveEvidenceError(f"live evidence nodeId is invalid: {node_id}")
    relative = Path(file_part)
    if relative.is_absolute() or ".." in relative.parts:
        raise LiveEvidenceError("live evidence nodeId must stay inside the repository")
    resolved = (root / relative).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise LiveEvidenceError("live evidence nodeId must stay inside the repository") from exc
    if not resolved.is_file():
        raise LiveEvidenceError(f"live evidence probe test file does not exist: {file_part}")
    if f"def {test_name}(" not in resolved.read_text(encoding="utf-8"):
        raise LiveEvidenceError(f"live evidence probe test does not exist: {node_id}")


def _reject_forbidden_keys(item: Mapping[str, Any]) -> None:
    forbidden = {"app_id", "app_secret", "appSecret", "token", "secret", "authorization"}
    for key in item:
        if str(key).lower() in forbidden:
            raise LiveEvidenceError(f"live evidence ledger contains forbidden key {key}")


def _reject_forbidden_values(value: str, probe_id: str) -> None:
    if _APP_ID_PATTERN.search(value) or _TENANT_TOKEN_PATTERN.search(value):
        raise LiveEvidenceError(
            f"live evidence probe {probe_id} detail contains credential-shaped material"
        )
    if "bearer" in value.lower():
        raise LiveEvidenceError(
            f"live evidence probe {probe_id} detail contains authorization material"
        )


def _canonical_digest(payload: Mapping[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _current_revision(root: Path) -> str:
    git = shutil.which("git")
    if git is None:
        raise LiveEvidenceError("unable to resolve the checked-out git revision")
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
        raise LiveEvidenceError("unable to resolve the checked-out git revision") from exc
    revision = result.stdout.strip()
    if result.returncode != 0 or not _REVISION_PATTERN.fullmatch(revision):
        raise LiveEvidenceError("unable to resolve the checked-out git revision")
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
        raise LiveEvidenceError(f"unable to load {label}: {path}") from exc
    if not isinstance(document, dict):
        raise LiveEvidenceError(f"{label} must be an object")
    return document


def _mapping(parent: dict[str, Any], key: str, label: str) -> dict[str, Any]:
    value = parent.get(key)
    if not isinstance(value, dict):
        raise LiveEvidenceError(f"{label} {key} must be an object")
    return value


def _string(parent: dict[str, Any], key: str, label: str) -> str:
    value = parent.get(key)
    if not isinstance(value, str) or not value.strip():
        raise LiveEvidenceError(f"{label} {key} must be a non-empty string")
    return value.strip()


def _unique_strings(parent: dict[str, Any], key: str, label: str) -> list[str]:
    value = parent.get(key)
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not item.strip() for item in value)
    ):
        raise LiveEvidenceError(f"{label} {key} must be a non-empty string list")
    normalized = [item.strip() for item in value]
    if len(set(normalized)) != len(normalized):
        raise LiveEvidenceError(f"{label} {key} must not contain duplicates")
    return normalized


def _timestamp(parent: dict[str, Any], key: str, label: str) -> str:
    value = _string(parent, key, label)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise LiveEvidenceError(f"{label} {key} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise LiveEvidenceError(f"{label} {key} must include a timezone")
    return value
