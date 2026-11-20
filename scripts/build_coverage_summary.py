#!/usr/bin/env python3
"""Build the bounded coverage summary document that the pull request comment uses."""
from __future__ import annotations

import argparse
import heapq
import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Final

MAX_FILES: Final[int] = 20
# Cobertura writes branch coverage as "50% (1/2)": covered and total conditions.
CONDITION_COUNTS: Final[re.Pattern[str]] = re.compile(r"\((\d+)/(\d+)\)")


def _branch_conditions(branch: ET.Element) -> tuple[int, int]:
    """Return the covered and the total conditions of one branch element."""
    match = CONDITION_COUNTS.search(branch.get("condition-coverage", ""))
    if match is None:
        return 0, 0
    return int(match.group(1)), int(match.group(2))


def _file_path(filename: str, source_prefix: str) -> str:
    """Return the path of one file with the source prefix applied at most once.

    The coverage report holds the path relative to the measured source. Some
    runs hold the path relative to the repository root already. The prefix
    never appears twice.
    """
    path = filename.replace("\\", "/")
    prefix = source_prefix.strip("/")
    if not prefix:
        return path
    if path == prefix or path.startswith(f"{prefix}/"):
        return path
    return f"{prefix}/{path}"


def _coverage_counts(element: ET.Element) -> tuple[int, int]:
    """Return the covered and the measured lines plus conditions of one class element."""
    lines = element.findall("./lines/line")
    covered = sum(1 for line in lines if int(line.get("hits", "0")) > 0)
    measured = len(lines)
    for branch in element.findall("./branches/branch"):
        branch_covered, branch_total = _branch_conditions(branch)
        covered += branch_covered
        measured += branch_total
    return covered, measured


def _file_record(element: ET.Element, source_prefix: str) -> dict[str, object]:
    """Return the path and the coverage percent of one class element."""
    covered, measured = _coverage_counts(element)
    percent = 100.0 if measured == 0 else 100.0 * covered / measured
    path = _file_path(element.get("filename") or "", source_prefix)
    return {"path": path, "percent": round(percent, 1)}


def build_summary(report: ET.Element, source_prefix: str = "") -> dict[str, object]:
    """Return the bounded summary document of one Cobertura coverage report."""
    lines_covered = int(report.get("lines-covered", "0"))
    lines_valid = int(report.get("lines-valid", "0"))
    branches_covered = int(report.get("branches-covered", "0"))
    branches_valid = int(report.get("branches-valid", "0"))
    measured = lines_valid + branches_valid
    total = 100.0 if measured == 0 else 100.0 * (lines_covered + branches_covered) / measured
    records = (_file_record(element, source_prefix) for element in report.iter("class"))
    # nsmallest returns the same list as a full sort cut to MAX_FILES, without the full sort.
    files = heapq.nsmallest(MAX_FILES, records, key=_coverage_order)
    return {"total": round(total, 1), "files": files}


def _coverage_order(record: dict[str, object]) -> tuple[object, object]:
    """Order file records by coverage percent, then by path."""
    return record["percent"], record["path"]


def read_summary(coverage_xml: Path, source_prefix: str = "") -> dict[str, object]:
    """Return the summary document of one coverage report file."""
    return build_summary(ET.parse(coverage_xml).getroot(), source_prefix)


def write_summary(coverage_xml: Path, output: Path, source_prefix: str = "") -> int:
    """Write the summary document of one coverage report file."""
    summary = read_summary(coverage_xml, source_prefix)
    output.write_text(json.dumps(summary), encoding="utf-8")
    return 0


def main() -> int:
    """Write the summary document of one coverage report file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-xml", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--source-prefix", default="")
    arguments = parser.parse_args()
    return write_summary(arguments.coverage_xml, arguments.output, arguments.source_prefix)


if __name__ == "__main__":
    raise SystemExit(main())
