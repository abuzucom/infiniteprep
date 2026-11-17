#!/usr/bin/env python3
"""Build the bounded security scan summary document that the pull request comment uses."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Final

MAX_TOOLS: Final[int] = 20
UNNAMED_TOOL: Final[str] = "unnamed"


def _tool_name(run: dict[str, object]) -> str:
    """Return the driver name of one SARIF run, or the fallback name."""
    tool = run.get("tool")
    driver = tool.get("driver") if isinstance(tool, dict) else None
    name = driver.get("name") if isinstance(driver, dict) else None
    if isinstance(name, str) and name:
        return name
    return UNNAMED_TOOL


def _result_count(run: dict[str, object]) -> int:
    """Return the number of results of one SARIF run."""
    results = run.get("results", [])
    if not isinstance(results, list):
        raise ValueError("the results of a SARIF run are not a list. Inspect the scan output.")
    return len(results)


def build_summary(report: object) -> dict[str, object]:
    """Return the bounded summary document of one SARIF report.

    The total counts every result. The tool list holds the tools with the most
    findings, up to the cap.
    """
    if not isinstance(report, dict):
        raise ValueError("the SARIF report is not an object. Inspect the scan output.")
    runs = report.get("runs", [])
    if not isinstance(runs, list):
        raise ValueError("the SARIF run list is not a list. Inspect the scan output.")
    findings: Counter[str] = Counter()
    for run in runs:
        if not isinstance(run, dict):
            raise ValueError("a SARIF run is not an object. Inspect the scan output.")
        findings[_tool_name(run)] += _result_count(run)
    tools = [{"name": name, "findings": count} for name, count in findings.items()]
    tools.sort(key=lambda item: (-int(item["findings"]), str(item["name"])))
    return {"total": sum(findings.values()), "tools": tools[:MAX_TOOLS]}


def read_summary(sarif: Path) -> dict[str, object]:
    """Return the summary document of one SARIF report file."""
    return build_summary(json.loads(sarif.read_text(encoding="utf-8")))


def write_summary(sarif: Path, output: Path) -> int:
    """Write the summary document of one SARIF report file."""
    output.write_text(json.dumps(read_summary(sarif)), encoding="utf-8")
    return 0


def main() -> int:
    """Write the summary document of one SARIF report file."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sarif", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    return write_summary(arguments.sarif, arguments.output)


if __name__ == "__main__":
    raise SystemExit(main())
