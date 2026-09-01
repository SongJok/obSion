"""Phase 84: live-tenant evidence ledger recording and candidate-gate binding."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from obsion.release.candidate import ReleaseCandidateError, validate_release_candidate
from obsion.release.live_evidence import (
    LiveEvidenceError,
    ProbeRun,
    _canonical_digest,
    load_ladder_contract,
    record_live_evidence,
    validate_live_evidence,
)
from obsion.release.notes import validate_release_notes

ROOT = Path(__file__).resolve().parents[3]
CONTRACT = ROOT / "docs" / "release" / "alpha1-live-evidence-contract.yaml"
GATES = ROOT / "docs" / "release" / "alpha1-candidate-gates.yaml"
LEDGERS = [
    ROOT / "docs" / "release" / "evidence" / "alpha1" / "feishu-readonly-live.yaml",
    ROOT / "docs" / "release" / "evidence" / "alpha1" / "feishu-agent-live.yaml",
]
REVISION = "a" * 40
ENV = {
    "OBSION_FEISHU_LIVE": "1",
    "OBSION_FEISHU_APP_ID": "cli-phase84-unit",
    "OBSION_FEISHU_APP_SECRET": "phase84-unit-secret",
}
EXPECTED_PROBES = {
    "feishu-tenant-token",
    "feishu-chat-listing",
    "feishu-docs-missing-denial",
    "feishu-wiki-space-list",
    "feishu-browse-gateway",
    "feishu-send-probe",
}


def _junit(status: str) -> str:
    if status == "passed":
        case = '<testcase classname="probe" name="test_probe"/>'
    elif status == "skipped":
        case = (
            '<testcase classname="probe" name="test_probe"><skipped message="opt-in"/></testcase>'
        )
    else:
        case = '<testcase classname="probe" name="test_probe"><failure message="boom"/></testcase>'
    return f'<?xml version="1.0"?><testsuite tests="1">{case}</testsuite>'


def _runner(outcomes: dict[str, tuple[str, str | None, str | None]]):
    def run(probe, probe_dir: Path, env) -> ProbeRun:
        status, classification, detail = outcomes[probe.probe_id]
        junit_path = probe_dir / "junit.xml"
        junit_path.write_text(_junit(status), encoding="utf-8")
        if classification is not None:
            payload = {
                "probe": probe.probe_id,
                "classification": classification,
                "detail": detail or "",
            }
            (probe_dir / f"{probe.probe_id}.json").write_text(json.dumps(payload), encoding="utf-8")
        return ProbeRun(returncode=0 if status != "failed" else 1, junit_path=junit_path)

    return run


def _record(tmp_path: Path, outcomes, *, include_optional: bool = False, env=None):
    return record_live_evidence(
        CONTRACT,
        tmp_path / "ledger.yaml",
        ROOT,
        profile_label="unit",
        include_optional=include_optional,
        env=ENV if env is None else env,
        runner=_runner(outcomes),
        revision=REVISION,
    )


def _all_outcomes(classification: str = "passed"):
    outcomes = {
        "feishu-tenant-token": ("passed", "passed", "tenant token authenticated"),
        "feishu-chat-listing": ("passed", classification, "FeishuDeniedError"),
        "feishu-docs-missing-denial": ("passed", "denied", "FeishuDocsDeniedError"),
        "feishu-wiki-space-list": ("passed", "denied", "FeishuDocsDeniedError"),
        "feishu-browse-gateway": ("passed", "denied", "status=403 feishu_docs_upstream_denied"),
        "feishu-send-probe": ("passed", "passed", "message_id=om_unit"),
    }
    return outcomes


def test_ladder_contract_binds_real_probe_tests() -> None:
    contract = load_ladder_contract(CONTRACT, ROOT)
    assert {probe.probe_id for probe in contract.probes} == EXPECTED_PROBES
    optional = [probe for probe in contract.probes if probe.optional]
    assert [probe.probe_id for probe in optional] == ["feishu-send-probe"]
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for marker in ("live", "feishu_browse_live", "feishu_send_live"):
        assert f'"{marker}:' in pyproject
    send_probe = next(probe for probe in contract.probes if probe.optional)
    assert send_probe.required_env == ("OBSION_FEISHU_SEND_LIVE", "OBSION_FEISHU_LIVE_CHAT_ID")


def test_live_probes_emit_structured_records() -> None:
    im_tests = (ROOT / "apps" / "im-adapter" / "tests" / "test_feishu.py").read_text(
        encoding="utf-8"
    )
    for probe_id in ("feishu-tenant-token", "feishu-chat-listing", "feishu-send-probe"):
        assert f'"{probe_id}"' in im_tests
    for test_file, probe_id in (
        ("test_phase64_feishu_knowledge.py", "feishu-docs-missing-denial"),
        ("test_phase65_feishu_wiki_spaces.py", "feishu-wiki-space-list"),
        ("test_phase78_vendor_knowledge_read_gateway.py", "feishu-browse-gateway"),
    ):
        source = (ROOT / "services" / "control-plane" / "tests" / test_file).read_text(
            encoding="utf-8"
        )
        assert "write_probe_record" in source
        assert f'"{probe_id}"' in source


def test_recorder_requires_global_opt_in_and_credentials(tmp_path: Path) -> None:
    with pytest.raises(LiveEvidenceError, match="OBSION_FEISHU_LIVE=1 is required"):
        _record(tmp_path, _all_outcomes(), env={})
    with pytest.raises(LiveEvidenceError, match="OBSION_FEISHU_APP_ID is required"):
        _record(tmp_path, _all_outcomes(), env={"OBSION_FEISHU_LIVE": "1"})


def test_recorder_classifies_and_checksums_ledger(tmp_path: Path) -> None:
    env = {**ENV, "OBSION_FEISHU_SEND_LIVE": "1", "OBSION_FEISHU_LIVE_CHAT_ID": "oc_unit"}
    summary = _record(tmp_path, _all_outcomes("denied"), include_optional=True, env=env)
    assert summary["failed"] == []
    assert summary["passed"] == 2
    assert summary["denied"] == 4
    ledger = yaml.safe_load((tmp_path / "ledger.yaml").read_text(encoding="utf-8"))
    assert ledger["kind"] == "LiveEvidenceLedger"
    assert ledger["metadata"]["profile"] == "unit"
    assert ledger["metadata"]["revision"] == REVISION
    assert ledger["metadata"]["appFingerprint"].startswith("sha256:")
    assert "cli-phase84-unit" not in json.dumps(ledger)
    assert "phase84-unit-secret" not in json.dumps(ledger)
    entries = {entry["id"]: entry for entry in ledger["spec"]["probes"]}
    assert entries["feishu-chat-listing"]["classification"] == "denied"
    assert entries["feishu-send-probe"]["classification"] == "passed"
    result = validate_live_evidence(CONTRACT, [tmp_path / "ledger.yaml"], ROOT)
    assert result["covered"] == 6


def test_recorder_marks_optional_probe_skipped_when_not_requested(tmp_path: Path) -> None:
    summary = _record(tmp_path, _all_outcomes())
    assert summary["skipped"] == 1
    ledger = yaml.safe_load((tmp_path / "ledger.yaml").read_text(encoding="utf-8"))
    entries = {entry["id"]: entry for entry in ledger["spec"]["probes"]}
    assert entries["feishu-send-probe"]["classification"] == "skipped"
    with pytest.raises(LiveEvidenceError, match="do not cover ladder probes"):
        validate_live_evidence(CONTRACT, [tmp_path / "ledger.yaml"], ROOT)


def test_recorder_optional_probe_requires_send_opt_in(tmp_path: Path) -> None:
    with pytest.raises(LiveEvidenceError, match="OBSION_FEISHU_SEND_LIVE"):
        _record(tmp_path, _all_outcomes(), include_optional=True)


def test_recorder_fails_closed_without_probe_record(tmp_path: Path) -> None:
    outcomes = _all_outcomes()
    outcomes["feishu-tenant-token"] = ("passed", None, None)
    summary = _record(tmp_path, outcomes)
    assert summary["failed"] == ["feishu-tenant-token"]


def test_recorder_treats_skip_as_failure(tmp_path: Path) -> None:
    outcomes = _all_outcomes()
    outcomes["feishu-chat-listing"] = ("skipped", None, None)
    summary = _record(tmp_path, outcomes)
    assert summary["failed"] == ["feishu-chat-listing"]


def test_recorder_rejects_outcomes_outside_contract(tmp_path: Path) -> None:
    outcomes = _all_outcomes()
    outcomes["feishu-tenant-token"] = ("passed", "denied", "FeishuDeniedError")
    summary = _record(tmp_path, outcomes)
    assert summary["failed"] == ["feishu-tenant-token"]


def test_recorder_rejects_credential_material_in_detail(tmp_path: Path) -> None:
    outcomes = _all_outcomes()
    outcomes["feishu-tenant-token"] = (
        "passed",
        "passed",
        f"token {ENV['OBSION_FEISHU_APP_SECRET']}",
    )
    with pytest.raises(LiveEvidenceError, match="credential material"):
        _record(tmp_path, outcomes)
    outcomes = _all_outcomes()
    outcomes["feishu-tenant-token"] = ("passed", "passed", "app cli_aa19d30c2c789bcf replied")
    with pytest.raises(LiveEvidenceError, match="credential-shaped material"):
        _record(tmp_path, outcomes)


def test_ledger_validation_detects_tampering(tmp_path: Path) -> None:
    env = {**ENV, "OBSION_FEISHU_SEND_LIVE": "1", "OBSION_FEISHU_LIVE_CHAT_ID": "oc_unit"}
    _record(tmp_path, _all_outcomes("denied"), include_optional=True, env=env)
    ledger = yaml.safe_load((tmp_path / "ledger.yaml").read_text(encoding="utf-8"))
    ledger["spec"]["probes"][0]["classification"] = "denied"
    (tmp_path / "tampered.yaml").write_text(yaml.safe_dump(ledger), encoding="utf-8")
    with pytest.raises(LiveEvidenceError, match="checksum mismatch"):
        validate_live_evidence(CONTRACT, [tmp_path / "tampered.yaml"], ROOT)


def test_ledger_validation_rejects_failed_or_forbidden_entries(tmp_path: Path) -> None:
    env = {**ENV, "OBSION_FEISHU_SEND_LIVE": "1", "OBSION_FEISHU_LIVE_CHAT_ID": "oc_unit"}
    _record(tmp_path, _all_outcomes("denied"), include_optional=True, env=env)
    ledger = yaml.safe_load((tmp_path / "ledger.yaml").read_text(encoding="utf-8"))

    failed = yaml.safe_load(yaml.safe_dump(ledger))
    failed["spec"]["probes"][0]["classification"] = "failed"
    failed["spec"]["checksum"] = _canonical_digest(
        {**failed, "spec": {k: v for k, v in failed["spec"].items() if k != "checksum"}}
    )
    (tmp_path / "failed.yaml").write_text(yaml.safe_dump(failed), encoding="utf-8")
    with pytest.raises(LiveEvidenceError, match="cannot be evidence"):
        validate_live_evidence(CONTRACT, [tmp_path / "failed.yaml"], ROOT)

    forbidden = yaml.safe_load(yaml.safe_dump(ledger))
    forbidden["spec"]["probes"][0]["token"] = "t-hidden"
    forbidden["spec"]["checksum"] = _canonical_digest(
        {**forbidden, "spec": {k: v for k, v in forbidden["spec"].items() if k != "checksum"}}
    )
    (tmp_path / "forbidden.yaml").write_text(yaml.safe_dump(forbidden), encoding="utf-8")
    with pytest.raises(LiveEvidenceError, match="forbidden key"):
        validate_live_evidence(CONTRACT, [tmp_path / "forbidden.yaml"], ROOT)


def test_recorded_live_ledgers_validate_against_ladder_contract() -> None:
    for ledger in LEDGERS:
        assert ledger.is_file(), f"missing recorded live evidence: {ledger}"
    result = validate_live_evidence(CONTRACT, LEDGERS, ROOT)
    assert result == {"ledgers": 2, "probes": 6, "covered": 6}


def test_candidate_gate_binds_live_evidence_without_promotion() -> None:
    summary = validate_release_candidate(GATES, None, ROOT, contract_only=True)
    assert summary["live_evidence_ledgers"] == 2
    assert summary["live_evidence_probes"] == 6
    assert summary["promotion_eligible"] is False
    assert len(summary["pending_operator_gates"]) == 6


def test_candidate_gate_rejects_live_evidence_outside_evidence_dir(tmp_path: Path) -> None:
    document = yaml.safe_load(GATES.read_text(encoding="utf-8"))
    document["spec"]["liveEvidence"]["ledgers"] = ["docs/release/0.83.0-dev.yaml"]
    (tmp_path / "gates.yaml").write_text(yaml.safe_dump(document), encoding="utf-8")
    with pytest.raises(ReleaseCandidateError, match="docs/release/evidence/alpha1/"):
        validate_release_candidate(tmp_path / "gates.yaml", None, ROOT, contract_only=True)


def _gated_environment() -> dict[str, str]:
    environment = dict(os.environ)
    for name in (
        "OBSION_FEISHU_LIVE",
        "OBSION_FEISHU_BROWSE_LIVE",
        "OBSION_FEISHU_SEND_LIVE",
        "OBSION_FEISHU_APP_ID",
        "OBSION_FEISHU_APP_SECRET",
        "OBSION_FEISHU_LIVE_CHAT_ID",
        "OBSION_LIVE_PROFILE",
    ):
        environment.pop(name, None)
    return environment


def test_make_target_is_fail_closed() -> None:
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    target = makefile.split("record-feishu-live-evidence:", 1)[1].split("\n\n", 1)[0]
    assert "OBSION_FEISHU_LIVE=1 is required" in target
    assert "OBSION_LIVE_PROFILE is required" in target
    assert "--include-send-probe" in target
    make = shutil.which("make")
    assert make is not None
    result = subprocess.run(  # noqa: S603 - fixed local Make target, no user input
        [make, "record-feishu-live-evidence"],
        cwd=ROOT,
        env=_gated_environment(),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 2
    assert "OBSION_FEISHU_LIVE=1 is required" in result.stdout


def test_cli_record_live_evidence_is_registered_and_gated() -> None:
    source = (ROOT / "services" / "control-plane" / "src" / "obsion" / "cli.py").read_text(
        encoding="utf-8"
    )
    assert '"record-live-evidence"' in source
    assert "--profile-label" in source
    assert "--include-send-probe" in source
    uv = shutil.which("uv")
    assert uv is not None
    result = subprocess.run(  # noqa: S603 - fixed CLI invocation, no user input
        [uv, "run", "obsion", "record-live-evidence", "--profile-label", "unit"],
        cwd=ROOT,
        env=_gated_environment(),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode != 0
    assert "OBSION_FEISHU_LIVE=1 is required" in result.stderr


def test_release_notes_and_project_status_track_phase84() -> None:
    result = validate_release_notes(ROOT / "docs" / "release" / "0.84.0-dev.yaml", ROOT)
    assert result["version"] == "0.84.0-dev"
    status = yaml.safe_load((ROOT / "docs" / "project-status.yaml").read_text(encoding="utf-8"))
    assert status["version"] == "0.93.0-dev"
    assert status["current_phase"] == "phase-93"
    assert "phase-84" in status["completed_phases"]


def test_env_example_documents_live_profile() -> None:
    example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "OBSION_LIVE_PROFILE=" in example
