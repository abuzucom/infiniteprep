#!/usr/bin/env python3
"""Bound prose policy regex time on whitespace runs and keep the same findings."""

import itertools
import re
import subprocess
import sys
import unittest
from pathlib import Path

from scripts import prose_policy

ROOT = Path(__file__).resolve().parent.parent
# The child process fails the test instead of hanging the suite on a slow pattern.
CHILD_TIMEOUT_SECONDS = 10
# A cubic or quadratic pattern takes minutes on these sizes. A linear pattern takes milliseconds.
BLANK_LINE_COUNT = 3000
WHITESPACE_RUN_LENGTH = 100_000
TUTORIAL_MAX_TOKENS = 6
ENUMERATION_MAX_TOKENS = 7
KEYWORD_LINE_NUMBER = 4

# The previous patterns serve as the reference for which text each check flags.
REFERENCE_TUTORIAL_PATTERN = re.compile(
    r"^\s*(?:#|//)?\s*(?:First|Next|Finally),",
    re.IGNORECASE | re.MULTILINE,
)
REFERENCE_INLINE_ENUMERATION_PATTERN = re.compile(
    r":[^\n.!?,]*(?:,[^\n.!?,]*){2,}\s+(?:and|or)\b[^\n.!?]*[.!?]",
    re.IGNORECASE,
)
TUTORIAL_TOKENS = ("First,", "#", "//", " ", "\n", "\r", "x")
ENUMERATION_TOKENS = (":", ",", " ", "\n", "and", ".")


def run_child(statement: str) -> None:
    """Run one prose policy call in a child process under the time bound."""
    script = "from scripts.prose_policy import find_violations\n" + statement
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT_SECONDS,
    )


def build_samples(tokens: tuple[str, ...], max_tokens: int) -> list[str]:
    """Return every sequence of the tokens up to max_tokens long."""
    return [
        "".join(sequence)
        for length in range(max_tokens + 1)
        for sequence in itertools.product(tokens, repeat=length)
    ]


class TutorialPatternTest(unittest.TestCase):
    """The tutorial check stays linear and flags the same keywords."""

    def test_blank_line_run_finishes_quickly(self):
        run_child(f"find_violations('\\n' * {BLANK_LINE_COUNT} + 'x', 'sample.md')\n")

    def test_flags_the_same_keywords_as_the_reference(self):
        for text in build_samples(TUTORIAL_TOKENS, TUTORIAL_MAX_TOKENS):
            expected = [match.end() for match in REFERENCE_TUTORIAL_PATTERN.finditer(text)]
            actual = [match.end() for match in prose_policy.TUTORIAL_PATTERN.finditer(text)]
            self.assertEqual(actual, expected, repr(text))

    def test_reports_the_keyword_line(self):
        text = "Intro text.\n\n\nFirst, run the check.\n"
        found = prose_policy.find_violations(text, "sample.md")
        tutorial = [line for line in found if "tutorial narration" in line]
        self.assertEqual(len(tutorial), 1)
        self.assertIn(f"sample.md:{KEYWORD_LINE_NUMBER}:1:", tutorial[0])


class InlineEnumerationPatternTest(unittest.TestCase):
    """The inline enumeration check stays linear and matches the same spans."""

    def test_whitespace_run_finishes_quickly(self):
        run_child(
            "find_violations("
            f"'Values: alpha, beta, gamma' + ' ' * {WHITESPACE_RUN_LENGTH} + 'x', 'sample.md')\n"
        )

    def test_matches_the_same_spans_as_the_reference(self):
        reference = REFERENCE_INLINE_ENUMERATION_PATTERN
        for text in build_samples(ENUMERATION_TOKENS, ENUMERATION_MAX_TOKENS):
            expected = [match.span() for match in reference.finditer(text)]
            actual = [
                match.span() for match in prose_policy.INLINE_ENUMERATION_PATTERN.finditer(text)
            ]
            self.assertEqual(actual, expected, repr(text))

    def test_and_after_a_line_break_still_warns(self):
        text = "Values: alpha, beta, gamma\nand delta.\n"
        found = prose_policy.find_violations(text, "sample.md")
        self.assertTrue(any("inline enumeration" in line for line in found), found)


if __name__ == "__main__":
    unittest.main()
