"""Expose bounded test identities to check annotations, never assertion bodies."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path


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
        message = f"{identity} ({kind}); full details: coverage artifact/test-results.xml"
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
