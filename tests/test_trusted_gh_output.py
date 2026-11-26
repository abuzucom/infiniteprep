#!/usr/bin/env python3
"""trusted_gh relays gh output without terminal control characters (S-15)."""
import io
import subprocess
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import trusted_gh

CONTROLLED = "ok\x1b[31mred\x07\x7f\x85\u202ebidi\u2066x\ttab\nline\r\n"
EXPECTED = "ok?[31mred????bidi?x\ttab\nline\r\n"


class ControlStrippingTest(unittest.TestCase):
    """C0 except tab, newline, and CR, plus DEL, C1, and bidi controls become '?'."""

    def test_strip_terminal_controls(self):
        self.assertEqual(trusted_gh.strip_terminal_controls(CONTROLLED), EXPECTED)

    def test_plain_json_is_unchanged(self):
        text = '{"body": "caf\u00e9 \\u001b escaped", "n": 1}\n'
        self.assertEqual(trusted_gh.strip_terminal_controls(text), text)

    def test_relay_strips_both_streams(self):
        result = subprocess.CompletedProcess([], 0, CONTROLLED, CONTROLLED)
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = trusted_gh._relay_result(result)
        self.assertEqual(code, 0)
        self.assertEqual(stdout.getvalue(), EXPECTED)
        self.assertEqual(stderr.getvalue(), EXPECTED)


if __name__ == "__main__":
    unittest.main()
