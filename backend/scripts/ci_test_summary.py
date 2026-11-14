"""
Turn pytest's JUnit report into a short Markdown table for the GitHub Actions
run page: passed, failed, skipped and the time taken, plus the name and first
line of every failure.

Usage (CI): pytest --junitxml=pytest-report.xml; python -m scripts.ci_test_summary pytest-report.xml
Writes to $GITHUB_STEP_SUMMARY when set, otherwise prints.
"""
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def summarize(xml_path: Path) -> str:
    root = ET.parse(xml_path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    total = sum(int(s.get("tests", 0)) for s in suites)
    failed = sum(int(s.get("failures", 0)) + int(s.get("errors", 0)) for s in suites)
    skipped = sum(int(s.get("skipped", 0)) for s in suites)
    seconds = sum(float(s.get("time", 0)) for s in suites)
    lines = ["### Backend tests", "",
             "| Passed | Failed | Skipped | Total | Time |", "| --- | --- | --- | --- | --- |",
             f"| {total - failed - skipped} | {failed} | {skipped} | {total} | {seconds:.1f} s |", ""]
    failures = [(case, problem) for case in root.iter("testcase")
                for problem in list(case.findall("failure")) + list(case.findall("error"))]
    if failures:
        lines += ["**Failed tests**", ""]
        for case, problem in failures:
            first = (problem.get("message") or problem.text or "").strip().splitlines()[:1]
            lines.append(f"- `{case.get('classname')}::{case.get('name')}`: {first[0][:200] if first else ''}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    table = summarize(Path(sys.argv[1] if len(sys.argv) > 1 else "pytest-report.xml"))
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as f:
            f.write(table + "\n")
    print(table)


if __name__ == "__main__":
    main()
