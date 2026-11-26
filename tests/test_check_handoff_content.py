#!/usr/bin/env python3
"""Test the local handoff content checker."""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_handoff_content

VALID_HANDOFF = """# Handoff
- branch: docs/add-pr-steward-skill
- commits: 11e73d9
- prs: https://github.com/example/widgets/pull/7
- paths: skills/pr-steward/SKILL.md
- status: tests pass
- pending consents:
  - push docs/add-pr-steward-skill
- verification: python scripts/run_tests.py
"""


class HandoffRuleTest(unittest.TestCase):
    """Each rule rejects one unsafe content class."""

    def test_valid_handoff_passes(self):
        self.assertEqual(check_handoff_content.content_problems(VALID_HANDOFF), [])

    def test_unknown_field_rejected(self):
        problems = check_handoff_content.content_problems("# Handoff\n- notes: anything\n")
        self.assertTrue(problems)

    def test_absolute_path_rejected(self):
        for value in ("/home/someone/repo", "C:\\Users\\someone", "~/repo"):
            with self.subTest(value=value):
                text = f"# Handoff\n- paths: {value}\n"
                self.assertTrue(check_handoff_content.content_problems(text))

    def test_email_rejected(self):
        text = "# Handoff\n- status: mailed someone@example.com\n"
        self.assertTrue(check_handoff_content.content_problems(text))

    def test_foreign_url_rejected(self):
        text = "# Handoff\n- status: see https://pastebin.example/abc\n"
        self.assertTrue(check_handoff_content.content_problems(text))

    def test_lookalike_github_hosts_rejected(self):
        for url in ("https://github.com.evil.example/x", "https://github.com@evil.example/x",
                    "https://user@github.com/x", "http://github.com/x",
                    "https://github.com:8443/x"):
            with self.subTest(url=url):
                text = f"# Handoff\n- prs: {url}\n"
                self.assertTrue(check_handoff_content.content_problems(text))

    def test_misplaced_list_items_rejected(self):
        for text in ("# Handoff\n  - push x\n", "# Handoff\n- status: ok\n  - push x\n"):
            with self.subTest(text=text):
                self.assertTrue(check_handoff_content.content_problems(text))

    def test_missing_heading_rejected(self):
        self.assertTrue(check_handoff_content.content_problems("- branch: x\n"))


class HandoffFileTest(unittest.TestCase):
    """The command line checks one file."""

    def test_unsafe_file_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "HANDOFF.md"
            path.write_text("# Handoff\n- paths: /root/secret\n", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = check_handoff_content.main([str(path)])
        self.assertEqual(code, 1)
        self.assertNotIn("/root/secret", stderr.getvalue())

    def test_oversized_file_fails(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "HANDOFF.md"
            padding = "- status: ok\n" * (check_handoff_content.MAX_HANDOFF_BYTES // 13 + 1)
            path.write_text(f"# Handoff\n{padding}- paths: /root/late\n", encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = check_handoff_content.main([str(path)])
        self.assertEqual(code, 1)
        self.assertIn("size", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
