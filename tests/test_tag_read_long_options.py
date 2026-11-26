#!/usr/bin/env python3
"""Check that long listing options with a tag operand pass the tag gate."""
import unittest

from tests.test_tag_push_gate import git_decision

LONG_OPTION_READS = (
    "git tag --list v2.*",
    "git tag --verify v2.27.0",
    "git tag --sort=-v:refname --list v2.*",
)


class TagLongReadOptionTest(unittest.TestCase):
    def test_long_listing_options_pass(self):
        for command in LONG_OPTION_READS:
            with self.subTest(command=command):
                self.assertEqual(git_decision(command), "")

    def test_unrelated_long_option_denies(self):
        self.assertEqual(git_decision("git tag --no-sign v2.27.0"), "deny")


if __name__ == "__main__":
    unittest.main()
