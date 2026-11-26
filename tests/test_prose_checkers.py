#!/usr/bin/env python3
"""Direct tests for the four prose checkers (S16, S18).

Each class stays small because the hook coverage runner gives every test
class one shard with a fixed time limit.
"""
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_english_only
import check_hedging
import check_us_spelling
import lint_style

FOREIGN_LINE = "el gato de la casa que come para la familia"
BRITISH_LINE = "The colour of the organisation changed."


def run_main(module, paths: list) -> tuple[int, str, str]:
    """Run one checker main() with file arguments and return code and streams."""
    stdout, stderr = io.StringIO(), io.StringIO()
    with patch.object(sys, "argv", [module.__name__, *paths]), \
            redirect_stdout(stdout), redirect_stderr(stderr):
        code = module.main()
    return code, stdout.getvalue(), stderr.getvalue()


def write_file(test_case: unittest.TestCase, text: str) -> str:
    """Write text to a temporary Markdown file and return its path."""
    temporary = tempfile.TemporaryDirectory()
    test_case.addCleanup(temporary.cleanup)
    path = Path(temporary.name) / "sample.md"
    path.write_text(text, encoding="utf-8")
    return str(path)


class EnglishOnlyTest(unittest.TestCase):
    """check_english_only flags foreign prose outside every code form."""

    def test_foreign_prose_is_flagged(self):
        violations = check_english_only.find_violations(FOREIGN_LINE + "\n", "doc.md")
        self.assertEqual(len(violations), 1)
        self.assertIn("doc.md:1", violations[0])

    def test_code_forms_are_skipped(self):
        for text in (f"```\n{FOREIGN_LINE}\n```\n", f"~~~\n{FOREIGN_LINE}\n~~~\n",
                     f"`{FOREIGN_LINE}`\n"):
            with self.subTest(text=text):
                self.assertEqual(check_english_only.find_violations(text, "doc.md"), [])

    def test_main_is_advisory(self):
        code, stdout, _stderr = run_main(check_english_only, [write_file(self, FOREIGN_LINE)])
        self.assertEqual(code, 0)
        self.assertIn("looks like non-English text", stdout)


class UsSpellingTest(unittest.TestCase):
    """check_us_spelling flags British spellings outside every code form."""

    def test_british_spellings_are_flagged(self):
        violations = check_us_spelling.find_violations(BRITISH_LINE + "\n", "doc.md")
        self.assertEqual(len(violations), 2)
        self.assertIn("use 'color'", violations[0])

    def test_code_forms_are_skipped(self):
        for text in (f"```\n{BRITISH_LINE}\n```\n", f"~~~\n{BRITISH_LINE}\n~~~\n",
                     f"`{BRITISH_LINE}`\n"):
            with self.subTest(text=text):
                self.assertEqual(check_us_spelling.find_violations(text, "doc.md"), [])

    def test_main_is_advisory(self):
        code, stdout, _stderr = run_main(check_us_spelling, [write_file(self, BRITISH_LINE)])
        self.assertEqual(code, 0)
        self.assertIn("British spelling", stdout)


class LintStyleTest(unittest.TestCase):
    """lint_style reports dash findings under the canonical source name."""

    def test_em_dash_is_reported(self):
        violations = lint_style.find_violations("One clause \u2014 another clause.\n")
        self.assertTrue(violations)
        self.assertTrue(all(message.startswith("AGENTS.md") for message in violations))

    def test_clean_text_passes(self):
        self.assertEqual(lint_style.find_violations("One clause. Another clause.\n"), [])

    def test_repository_policy_is_clean(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(io.StringIO()):
            self.assertEqual(lint_style.lint(), 0)
        self.assertIn("style clean", stdout.getvalue())


class HedgingTest(unittest.TestCase):
    """check_hedging warns with exit 0 and fails only on unreadable input."""

    def test_findings_exit_0(self):
        code, stdout, _stderr = run_main(check_hedging, [write_file(self, "We think it works.\n")])
        self.assertEqual(code, 0)
        self.assertIn("warning:", stdout)

    def test_unreadable_file_exits_1(self):
        missing = str(Path(tempfile.gettempdir()) / "missing-prose-file.md")
        code, _stdout, stderr = run_main(check_hedging, [missing])
        self.assertEqual(code, 1)
        self.assertIn("prose check failed", stderr)

    def test_no_arguments_prints_usage(self):
        code, _stdout, stderr = run_main(check_hedging, [])
        self.assertEqual(code, 0)
        self.assertIn("usage:", stderr)


if __name__ == "__main__":
    unittest.main()
