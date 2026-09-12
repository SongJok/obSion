"""首版发布门禁不能漏掉 HIGH 漏洞或静默跳过撤销迁移。"""

from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[3]


def test_ci_blocks_all_high_and_critical_findings() -> None:
    workflow = yaml.safe_load((_ROOT / ".github/workflows/ci.yml").read_text())
    for job in ("quality", "containers"):
        scans = [
            step["with"]
            for step in workflow["jobs"][job]["steps"]
            if step.get("uses", "").startswith("aquasecurity/trivy-action@")
        ]
        assert scans, f"{job} 缺少漏洞扫描门禁"
        for scan in scans:
            assert {part.strip() for part in scan["severity"].split(",")} >= {"HIGH", "CRITICAL"}
            assert scan["ignore-unfixed"] is False
            assert str(scan["exit-code"]) == "1"


def test_ci_scans_python_workspace_sbom_despite_uv_lock_parser_limit() -> None:
    workflow = yaml.safe_load((_ROOT / ".github/workflows/ci.yml").read_text())
    steps = workflow["jobs"]["quality"]["steps"]
    generation = next(
        index for index, step in enumerate(steps) if "obsion sbom" in step.get("run", "")
    )
    scan = next(
        index
        for index, step in enumerate(steps)
        if step.get("uses", "").startswith("aquasecurity/trivy-action@")
        and step.get("with", {}).get("scan-type") == "sbom"
    )
    assert generation < scan
    assert steps[scan]["with"]["scan-ref"] == "${{ runner.temp }}/obsion.cdx.json"
    assert "$RUNNER_TEMP/obsion.cdx.json" in steps[generation]["run"]


def test_memory_revoke_migration_has_an_isolated_opt_in_ci_database() -> None:
    workflow = yaml.safe_load((_ROOT / ".github/workflows/ci.yml").read_text())
    entries = workflow["jobs"]["migration-round-trips"]["strategy"]["matrix"]["include"]
    entry = next(item for item in entries if item["name"] == "m4-memory-revoke")
    assert entry["opt_in"] == "OBSION_RUN_MEMORY_REVOKE_MIGRATION_TEST"
    assert entry["test"] == (
        "services/control-plane/tests/integration/test_postgres_memory_revoke_migration.py"
    )
    assert (root_test := _ROOT / entry["test"]).is_file()
    assert entry["opt_in"] in root_test.read_text()
    assert len({item["database"] for item in entries}) == len(entries)


def test_source_ledger_migration_has_an_isolated_opt_in_ci_database() -> None:
    workflow = yaml.safe_load((_ROOT / ".github/workflows/ci.yml").read_text())
    entries = workflow["jobs"]["migration-round-trips"]["strategy"]["matrix"]["include"]
    entry = next(item for item in entries if item["name"] == "m2-project-source")
    assert entry["opt_in"] == "OBSION_RUN_PROJECT_SOURCE_MIGRATION_TEST"
    assert entry["test"] == (
        "services/control-plane/tests/integration/test_postgres_project_source_migration.py"
    )
    assert (root_test := _ROOT / entry["test"]).is_file()
    assert entry["opt_in"] in root_test.read_text()
    assert len({item["database"] for item in entries}) == len(entries)
