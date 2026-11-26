#!/usr/bin/env python3
"""Test the AGENTS.md line length limit in lint_style.py."""
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import lint_style


class PolicyLineLengthTest(unittest.TestCase):
    """Every canonical policy line stays within the code quality limit."""

    def test_policy_line_limit_value(self) -> None:
        self.assertEqual(lint_style.MAX_LINE_LENGTH, 120)

    def test_long_line_reports_number(self) -> None:
        long_line = "x" * (lint_style.MAX_LINE_LENGTH + 1)
        self.assertEqual(
            lint_style.find_line_violations(f"short\n{long_line}\n"),
            [f"AGENTS.md:2: line has {lint_style.MAX_LINE_LENGTH + 1} characters, "
             f"limit {lint_style.MAX_LINE_LENGTH}"])

    def test_limit_length_line_passes(self) -> None:
        text = "x" * lint_style.MAX_LINE_LENGTH + "\n"
        self.assertEqual(lint_style.find_line_violations(text), [])

    def test_canonical_policy_lint_passes(self) -> None:
        stdout, stderr = StringIO(), StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exit_code = lint_style.lint()
        self.assertEqual((exit_code, stderr.getvalue()), (0, ""))


if __name__ == "__main__":
    unittest.main()
