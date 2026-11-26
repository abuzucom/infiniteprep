#!/usr/bin/env python3
"""Exercise the assembled policy size check and the pull request report."""
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import sync

from scripts import check_policy_size

AGENTS_LIMIT = 32 * 1024
COMBINED_LIMIT = 64 * 1024
AGENTS_FIXTURE_BYTES = 1000
SUPPORTING_FIXTURE_BYTES = 10
# Six supporting files at this size leave an AGENTS.md below its own limit.
AT_LIMIT_SUPPORTING_BYTES = 6000
DIRECTIVE_PHRASE = (
    "Agents must not make an independent decision about policy reduction."
)


def _write_fixture(root: Path, agents_bytes: int, supporting_bytes: int) -> None:
    """Create a synthetic repository with exact policy file sizes."""
    (root / "docs" / "agent-policy").mkdir(parents=True)
    body = "\n".join(check_policy_size.REQUIRED_TEXT).encode("ascii") + b"\n"
    padding = b"x" * (agents_bytes - len(body) - 1)
    (root / "AGENTS.md").write_bytes(body + padding + b"\n")
    for relative_name in sync.ADOPTER_POLICY_FILES:
        content = b"y" * (supporting_bytes - 1) + b"\n"
        (root / relative_name).write_bytes(content)


def _compute_combined_size(agents_bytes: int, supporting_bytes: int) -> int:
    """Return the assembled size of a fixture built by _write_fixture."""
    return agents_bytes + supporting_bytes * len(sync.ADOPTER_POLICY_FILES)


def _run_main(argv: list) -> tuple:
    """Run the checker entry point and capture its exit code and streams."""
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = check_policy_size.main(argv)
    return exit_code, stdout.getvalue(), stderr.getvalue()


class CombinedPolicySizeTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

    def test_combined_size_matches_sync_assembly(self):
        report = check_policy_size.measure_policy(ROOT / "AGENTS.md")
        assembled = report["targets"][1]
        self.assertEqual(assembled["name"], "assembled policy")
        self.assertEqual(
            assembled["bytes"], len(sync.assemble_policy_bytes(ROOT)))
        self.assertEqual(assembled["limit"], sync.MAX_POLICY_BYTES)

    def test_combined_at_limit_passes(self):
        supporting = AT_LIMIT_SUPPORTING_BYTES
        agents = COMBINED_LIMIT - supporting * len(sync.ADOPTER_POLICY_FILES)
        self.assertLessEqual(agents, AGENTS_LIMIT)
        _write_fixture(self.root, agents, supporting)
        self.assertEqual(_compute_combined_size(agents, supporting), COMBINED_LIMIT)
        self.assertEqual(
            check_policy_size.find_combined_violations(self.root), [])

    def test_combined_over_limit_fails(self):
        supporting = COMBINED_LIMIT // len(sync.ADOPTER_POLICY_FILES)
        _write_fixture(self.root, AGENTS_FIXTURE_BYTES, supporting)
        total = _compute_combined_size(AGENTS_FIXTURE_BYTES, supporting)
        violations = check_policy_size.find_combined_violations(self.root)
        self.assertEqual(len(violations), 1)
        self.assertIn(f"{total} bytes exceeds {COMBINED_LIMIT} bytes", violations[0])
        self.assertIn(f"by {total - COMBINED_LIMIT}", violations[0])

    def test_missing_supporting_file_reports_violation(self):
        _write_fixture(self.root, AGENTS_FIXTURE_BYTES, SUPPORTING_FIXTURE_BYTES)
        (self.root / sync.ADOPTER_POLICY_FILES[0]).unlink()
        violations = check_policy_size.find_combined_violations(self.root)
        self.assertTrue(any("cannot assemble" in item for item in violations))

    def test_failure_output_carries_agent_directive(self):
        supporting = COMBINED_LIMIT // len(sync.ADOPTER_POLICY_FILES)
        _write_fixture(self.root, AGENTS_FIXTURE_BYTES, supporting)
        exit_code, _, stderr = _run_main([str(self.root / "AGENTS.md")])
        self.assertEqual(exit_code, 1)
        self.assertIn("AGENT DIRECTIVE", stderr)
        self.assertIn(DIRECTIVE_PHRASE, stderr)
        self.assertIn("Change policy safely", stderr)

    def test_passing_run_prints_nothing(self):
        _write_fixture(self.root, AGENTS_FIXTURE_BYTES, SUPPORTING_FIXTURE_BYTES)
        exit_code, stdout, stderr = _run_main([str(self.root / "AGENTS.md")])
        self.assertEqual((exit_code, stdout, stderr), (0, "", ""))

    def test_default_path_working_directory(self):
        _write_fixture(self.root, AGENTS_FIXTURE_BYTES, SUPPORTING_FIXTURE_BYTES)
        original_directory = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, original_directory)
        exit_code, stdout, stderr = _run_main([])
        self.assertEqual((exit_code, stdout, stderr), (0, "", ""))
        exit_code, stdout, _ = _run_main(["--json"])
        self.assertEqual(exit_code, 0)
        self.assertEqual(
            json.loads(stdout)["targets"][1]["bytes"],
            _compute_combined_size(AGENTS_FIXTURE_BYTES, SUPPORTING_FIXTURE_BYTES))

    def test_json_report_lists_both_targets(self):
        _write_fixture(self.root, AGENTS_FIXTURE_BYTES, SUPPORTING_FIXTURE_BYTES)
        exit_code, stdout, _ = _run_main(
            [str(self.root / "AGENTS.md"), "--json"])
        self.assertEqual(exit_code, 0)
        report = json.loads(stdout)
        self.assertEqual(report["schema"], 1)
        self.assertEqual(report["targets"], [
            {"name": "AGENTS.md", "bytes": AGENTS_FIXTURE_BYTES,
             "limit": AGENTS_LIMIT},
            {"name": "assembled policy",
             "bytes": _compute_combined_size(
                 AGENTS_FIXTURE_BYTES, SUPPORTING_FIXTURE_BYTES),
             "limit": COMBINED_LIMIT},
        ])

    def test_json_report_over_limit(self):
        supporting = COMBINED_LIMIT // len(sync.ADOPTER_POLICY_FILES)
        _write_fixture(self.root, AGENTS_FIXTURE_BYTES, supporting)
        exit_code, stdout, _ = _run_main(
            [str(self.root / "AGENTS.md"), "--json"])
        self.assertEqual(exit_code, 0)
        report = json.loads(stdout)
        self.assertGreater(report["targets"][1]["bytes"], COMBINED_LIMIT)


class PolicySizeReportRenderTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.report_path = Path(self.temporary_directory.name) / "report.json"

    def _build_report(self, agents_bytes: int, combined_bytes: int) -> dict:
        """Return a size report with the checker limits and the given sizes."""
        return {"schema": 1, "targets": [
            {"name": "AGENTS.md", "bytes": agents_bytes, "limit": AGENTS_LIMIT},
            {"name": "assembled policy", "bytes": combined_bytes,
             "limit": COMBINED_LIMIT},
        ]}

    def _render(self, report: object) -> tuple:
        """Write the report as JSON and return the renderer exit code and streams."""
        self.report_path.write_text(json.dumps(report), encoding="ascii")
        return _run_main(["--render-markdown", str(self.report_path)])

    def test_render_sizes_headroom(self):
        exit_code, stdout, _ = self._render(self._build_report(32602, 65532))
        self.assertEqual(exit_code, 0)
        self.assertTrue(stdout.startswith("<!-- policy-size-report -->"))
        self.assertIn("| `AGENTS.md` | 32,602 | 32,768 | 166 | 99.5% |", stdout)
        self.assertIn("| Assembled policy | 65,532 | 65,536 | 4 | 100.0% |", stdout)
        self.assertIn("Within limits", stdout)
        self.assertIn(DIRECTIVE_PHRASE, stdout)

    def test_render_marks_over_limit(self):
        exit_code, stdout, _ = self._render(self._build_report(32602, 65600))
        self.assertEqual(exit_code, 0)
        self.assertIn("| Assembled policy | 65,600 | 65,536 | -64 | 100.1% |", stdout)
        self.assertIn("Over limit", stdout)
        self.assertIn(DIRECTIVE_PHRASE, stdout)

    def test_render_rejects_untrusted_shapes(self):
        bad_reports = [
            [],
            {"schema": 2, "targets": []},
            self._build_report(True, 1),
            self._build_report(-1, 1),
            self._build_report("1", 1),
            {"schema": 1, "targets": [
                {"name": "`injected`", "bytes": 1, "limit": 1},
                {"name": "assembled policy", "bytes": 1, "limit": 1}]},
            {"schema": 1, "targets": [
                {"name": "AGENTS.md", "bytes": 1, "limit": 0},
                {"name": "assembled policy", "bytes": 1, "limit": 1}]},
        ]
        for report in bad_reports:
            with self.subTest(report=report):
                exit_code, stdout, stderr = self._render(report)
                self.assertEqual(exit_code, 1)
                self.assertEqual(stdout, "")
                self.assertIn("invalid size report", stderr)


if __name__ == "__main__":
    unittest.main()
