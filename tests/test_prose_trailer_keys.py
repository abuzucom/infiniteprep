#!/usr/bin/env python3
"""Pin the prose checker allowance for attribution trailer keys."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import prose_policy

COLON_JOIN = "clause join (colon)"
TRAILER_BLOCK = (
    "Co-authored-by: Claude\n"
    "Assisted-by: Claude Opus 5.5\n"
    "Claude-Session: https://claude.ai/code/session_example\n"
    "Session: https://claude.ai/code/session_example\n"
)


def colon_findings(text: str) -> list[str]:
    """Return the colon clause-join findings for fixed sample input."""
    return [finding for finding in prose_policy.find_violations(text, "sample.md")
            if COLON_JOIN in finding]


class TrailerKeyAllowanceTest(unittest.TestCase):
    """Each trailer key and the colon after it skip prose analysis. Values stay checked."""

    def test_trailer_keys_no_colon_finding(self):
        self.assertEqual(colon_findings(TRAILER_BLOCK), [])

    def test_trailer_keys_case_insensitive(self):
        self.assertEqual(colon_findings("Co-Authored-By: Claude Opus 5.5\n"), [])

    def test_trailer_value_stays_checked(self):
        self.assertEqual(
            len(colon_findings("Assisted-by: Model note: the tool runs.\n")), 1)

    def test_other_keys_stay_checked(self):
        for line in ("Result: the check runs.\n",
                     "Assisted-by-bot: the tool runs.\n",
                     "Sessions: the tool runs.\n"):
            with self.subTest(line=line):
                self.assertEqual(len(colon_findings(line)), 1)

    def test_sentence_key_checked(self):
        self.assertEqual(
            len(colon_findings("The line reads Assisted-by: the tool runs.\n")), 1)

    def test_finding_columns_aligned(self):
        text = "Assisted-by: Claude\nResult: the check runs.\n"
        self.assertEqual(colon_findings(text),
                         ["warning: sample.md:2:1: clause join (colon)"])


if __name__ == "__main__":
    unittest.main()
