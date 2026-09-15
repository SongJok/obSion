import importlib.util
from pathlib import Path

import yaml


def test_ci_failure_annotations_preserve_failure_and_hide_assertion_bodies(tmp_path):
    root = Path(__file__).resolve().parents[3]
    path = root / "scripts/report_pytest_failures.py"
    spec = importlib.util.spec_from_file_location("ci_failure_annotations", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = tmp_path / "test-results.xml"
    report.write_text(
        '<testsuites><testsuite><testcase classname="test_review" name="bad%&#10;::warning::">'
        '<failure type="AssertionError">PRIVATE_ASSERTION_BODY</failure></testcase>'
        '<testcase name="pass"/><testcase name="skip"><skipped/></testcase>'
        "</testsuite></testsuites>"
    )
    result = module.annotations(report)
    assert len(result) == 1 and "test_review::bad%25%0A::warning::" in result[0]
    assert "AssertionError" in result[0] and "PRIVATE_ASSERTION_BODY" not in result[0]
    assert "\n" not in result[0]
    assert not module.annotations(tmp_path / "missing.xml")[0].startswith("::error")
    report.write_text("<testsuite><testcase name='pass'/></testsuite>")
    assert module.annotations(report)[0].startswith("::notice::JUnit has no failing testcases")
    report.write_text(
        "<testsuite>"
        + "<testcase name='bad'><error>PRIVATE_ERROR_BODY</error></testcase>" * 25
        + "</testsuite>"
    )
    assert len(module.annotations(report)) == 20
    assert "PRIVATE_ERROR_BODY" not in "".join(module.annotations(report))
    report.write_text(
        "<testsuite><testcase name='pool'><failure>PRIVATE_ERROR_BODY\n"
        "RuntimeError: Queue is bound to a different event loop\n"
        "database is locked\nassert 500 == 202</failure></testcase></testsuite>"
    )
    diagnostic = module.annotations(report)[0]
    assert "diagnostics=event_loop_mismatch,sqlite_busy,http_500_expected_202" in diagnostic
    assert "PRIVATE_ERROR_BODY" not in diagnostic and "RuntimeError: Queue" not in diagnostic
    steps = yaml.safe_load((root / ".github/workflows/ci.yml").read_text())["jobs"]["quality"][
        "steps"
    ]
    index = next(i for i, step in enumerate(steps) if path.name in step.get("run", ""))
    assert steps[index]["if"] == "failure()"
    assert "--junitxml=test-results.xml" in steps[index - 1]["run"]
    assert not any(step.get("continue-on-error") for step in steps)
