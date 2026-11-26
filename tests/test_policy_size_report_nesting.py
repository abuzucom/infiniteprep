#!/usr/bin/env python3
"""Exercise the nesting depth limit on untrusted size reports."""
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from scripts import check_policy_size

# The report file cap is 4096 bytes. One byte per bracket reaches that depth.
DEEP_NESTING = 4000
AGENTS_LIMIT = 32 * 1024
ASSEMBLED_LIMIT = 64 * 1024


def render_report_text(text: str) -> tuple:
    """Write report text to a file and run the renderer on it."""
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "report.json"
        path.write_text(text, encoding="ascii")
        stdout = StringIO()
        stderr = StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = check_policy_size.main(["--render-markdown", str(path)])
    return exit_code, stdout.getvalue(), stderr.getvalue()


class ReportNestingTests(unittest.TestCase):
    def test_deep_array_nesting_rejected(self):
        exit_code, stdout, stderr = render_report_text("[" * DEEP_NESTING)
        self.assertEqual(exit_code, 1)
        self.assertEqual(stdout, "")
        self.assertIn("invalid size report", stderr)
        self.assertIn("nesting", stderr)

    def test_deep_balanced_nesting_rejected(self):
        half = DEEP_NESTING // 2
        exit_code, _, stderr = render_report_text("[" * half + "]" * half)
        self.assertEqual(exit_code, 1)
        self.assertIn("nesting", stderr)

    def test_deep_object_nesting_is_rejected(self):
        exit_code, _, stderr = render_report_text('{"a":' * (DEEP_NESTING // 5))
        self.assertEqual(exit_code, 1)
        self.assertIn("nesting", stderr)

    def test_string_brackets_ignored(self):
        text = ('{"schema": 1, "note": "' + "[" * 50 + '", "targets": ['
                '{"name": "AGENTS.md", "bytes": 1, "limit": %d}, '
                '{"name": "assembled policy", "bytes": 1, "limit": %d}]}'
                % (AGENTS_LIMIT, ASSEMBLED_LIMIT))
        exit_code, stdout, _ = render_report_text(text)
        self.assertEqual(exit_code, 0)
        self.assertIn("Within limits", stdout)

    def test_escaped_quote_handling(self):
        text = ('{"schema": 1, "note": "\\"' + "[" * 50 + '", "targets": ['
                '{"name": "AGENTS.md", "bytes": 1, "limit": %d}, '
                '{"name": "assembled policy", "bytes": 1, "limit": %d}]}'
                % (AGENTS_LIMIT, ASSEMBLED_LIMIT))
        exit_code, _, _ = render_report_text(text)
        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
