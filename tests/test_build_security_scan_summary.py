#!/usr/bin/env python3
"""Cover the bounded security scan summary document that the pull request comment uses."""

import importlib.util
import json
import shutil
import tempfile
import unittest
from pathlib import Path

BUILDER_PATH = Path(__file__).resolve().parent.parent / "scripts" / "build_security_scan_summary.py"


def _load_builder():
    spec = importlib.util.spec_from_file_location("build_security_scan_summary", BUILDER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


builder = _load_builder()


def make_run(tool_name: str, result_count: int) -> dict:
    """Return one SARIF run of one tool with the given number of results."""
    return {
        "tool": {"driver": {"name": tool_name}},
        "results": [{"ruleId": f"R{index}"} for index in range(result_count)],
    }


class BuildSecurityScanSummaryTest(unittest.TestCase):
    """Cover the summary document built from one SARIF report."""

    def setUp(self) -> None:
        self.folder = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.folder, True)

    def write_report(self, document: object) -> Path:
        """Write one SARIF report file and return its path."""
        path = self.folder / "msdo.sarif"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_a_report_without_runs_holds_no_finding(self) -> None:
        """A clean scan with no run yields a zero total and no tool."""
        summary = builder.read_summary(self.write_report({"version": "2.1.0", "runs": []}))

        self.assertEqual(summary, {"total": 0, "tools": []})

    def test_a_clean_tool_run_reports_zero_findings(self) -> None:
        """A tool that ran clean still appears with a zero count."""
        summary = builder.read_summary(self.write_report({"runs": [make_run("bandit", 0)]}))

        self.assertEqual(summary, {"total": 0, "tools": [{"name": "bandit", "findings": 0}]})

    def test_the_total_counts_every_result(self) -> None:
        """The total holds the results of every run."""
        report = {"runs": [make_run("bandit", 2), make_run("checkov", 3)]}

        summary = builder.read_summary(self.write_report(report))

        self.assertEqual(summary["total"], 5)

    def test_two_runs_of_one_tool_share_one_record(self) -> None:
        """The runs of one tool add up in one record."""
        report = {"runs": [make_run("checkov", 1), make_run("checkov", 2)]}

        summary = builder.read_summary(self.write_report(report))

        self.assertEqual(summary["tools"], [{"name": "checkov", "findings": 3}])

    def test_the_tool_with_the_most_findings_comes_first(self) -> None:
        """The tools sort by the finding count and then by the name."""
        report = {"runs": [make_run("bandit", 1), make_run("checkov", 4), make_run("alpha", 1)]}

        summary = builder.read_summary(self.write_report(report))

        names = [tool["name"] for tool in summary["tools"]]
        self.assertEqual(names, ["checkov", "alpha", "bandit"])

    def test_the_tool_list_stays_bounded(self) -> None:
        """The document holds no more than the capped number of tools."""
        report = {"runs": [make_run(f"tool{index}", 1) for index in range(30)]}

        summary = builder.read_summary(self.write_report(report))

        self.assertEqual(len(summary["tools"]), builder.MAX_TOOLS)
        self.assertEqual(summary["total"], 30)

    def test_unnamed_tool_run_gets_fallback_name(self) -> None:
        """A run that names no tool still counts under the fallback name."""
        summary = builder.read_summary(self.write_report({"runs": [{"results": [{}]}]}))

        self.assertEqual(summary["tools"], [{"name": builder.UNNAMED_TOOL, "findings": 1}])

    def test_a_report_that_is_not_an_object_is_rejected(self) -> None:
        """A report with the wrong shape raises an error."""
        with self.assertRaises(ValueError):
            builder.read_summary(self.write_report(["runs"]))

    def test_a_run_list_with_the_wrong_type_is_rejected(self) -> None:
        """A run list that is not a list raises an error."""
        with self.assertRaises(ValueError):
            builder.read_summary(self.write_report({"runs": "none"}))

    def test_the_summary_writes_json(self) -> None:
        """The command writes the summary document as JSON."""
        report_path = self.write_report({"runs": [make_run("bandit", 1)]})
        output_path = self.folder / "security-scan-summary.json"

        code = builder.write_summary(report_path, output_path)

        self.assertEqual(code, 0)
        document = json.loads(output_path.read_text(encoding="utf-8"))
        self.assertEqual(document, {"total": 1, "tools": [{"name": "bandit", "findings": 1}]})


if __name__ == "__main__":
    unittest.main()
