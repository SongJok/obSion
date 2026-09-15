"""Expose bounded test identities to check annotations, never assertion bodies."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

_DIAGNOSTICS = (
    ("event_loop_mismatch", "is bound to a different event loop"),
    ("sqlite_busy", "database is locked"),
    ("pool_timeout", "QueuePool limit of size"),
    ("http_500_expected_202", "assert 500 == 202"),
    ("http_500_expected_200", "assert 500 == 200"),
)


def annotations(path: Path) -> list[str]:
    if not path.is_file():
        return ["::notice::Python test report was not produced; inspect the failed step."]
    root = ET.parse(path).getroot()  # noqa: S314 -- runner-generated JUnit, no external input
    records: list[str] = []
    for case in root.iter("testcase"):
        problem = next((item for item in case if item.tag in {"failure", "error"}), None)
        if problem is None:
            continue
        identity = f"{case.get('classname', '')}::{case.get('name', '')}"[:600]
        kind = problem.get("type", problem.tag)[:100]
        # Emit only known labels. Tracebacks, URLs, values and assertion bodies
        # remain in the access-controlled artifact, not public check annotations.
        body = f"{problem.get('message', '')}\n{problem.text or ''}"
        hints = [label for label, marker in _DIAGNOSTICS if marker in body]
        diagnostic = f"; diagnostics={','.join(hints)}" if hints else ""
        message = (
            f"{identity} ({kind}){diagnostic}; full details: coverage artifact/test-results.xml"
        )
        message = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        records.append(f"::error title=Python test failure::{message}")
        if len(records) == 20:
            break
    return records or [
        "::notice::JUnit has no failing testcases; inspect coverage or test runner failure."
    ]


if __name__ == "__main__":
    for annotation in annotations(Path(sys.argv[1])):
        print(annotation)
