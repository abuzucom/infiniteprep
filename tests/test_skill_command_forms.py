#!/usr/bin/env python3
"""Test that skills never model untrusted text spliced into a command line.

Skills pass untrusted or multi-line text through files, such as
`git commit -F <file>` and `--body-file`. A skill that shows command
substitution or an inline message argument teaches the unsafe form.
"""
import re
import sys
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))
import _skill_sync

COMMAND_SUBSTITUTION = "$("
INLINE_MESSAGE = re.compile(r"(?:^|\s)(?:-m|--message|--body|--title)\s+[\"']")
FENCE = "```"


def command_form_problems(text: str) -> list:
    """Return every unsafe command form found in one skill text."""
    problems = []
    if COMMAND_SUBSTITUTION in text:
        problems.append("command substitution")
    if INLINE_MESSAGE.search(text):
        problems.append("inline quoted message argument")
    fenced = text.split(FENCE)[1::2]
    if any("`" in block for block in fenced):
        problems.append("backtick inside a fenced command block")
    return problems


class CommandFormRuleTest(unittest.TestCase):
    """Each rule rejects one unsafe form."""

    def test_substitution_rejected(self):
        self.assertTrue(command_form_problems("Run `git commit -F $(mktemp)`.\n"))

    def test_inline_message_rejected(self):
        self.assertTrue(command_form_problems("Run `git commit -m \"<summary>\"`.\n"))

    def test_fenced_backtick_rejected(self):
        self.assertTrue(command_form_problems("```\necho `id`\n```\n"))

    def test_file_forms_allowed(self):
        text = "Run `git commit -F <file>` and `--body-file .comment-drafts/<file>`.\n"
        self.assertEqual(command_form_problems(text), [])


class LiveSkillFormTest(unittest.TestCase):
    """Every canonical skill uses file-based forms only."""

    def test_live_skills_use_file_forms(self):
        for name in _skill_sync.SKILLS:
            path = REPOSITORY_ROOT / _skill_sync.canonical_path(name)
            with self.subTest(skill=name):
                self.assertEqual(command_form_problems(path.read_text(encoding="utf-8")), [])


if __name__ == "__main__":
    unittest.main()
