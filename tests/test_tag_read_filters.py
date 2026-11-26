#!/usr/bin/env python3
"""Check that git tag read filters with a pattern pass the tag gate."""
import unittest

from tests.test_tag_push_gate import git_decision

FILTERED_READS = (
    "git tag --contains HEAD v2.*",
    "git tag --no-contains HEAD v2.*",
    "git tag --points-at HEAD v2.27.0",
    "git tag --merged main v2.*",
    "git tag --no-merged main v2.*",
    "git tag --contains=HEAD v2.*",
)


class TagReadFilterTest(unittest.TestCase):
    def test_pattern_filters_pass(self):
        for command in FILTERED_READS:
            with self.subTest(command=command):
                self.assertEqual(git_decision(command), "")

    def test_write_flag_with_filter_denies(self):
        for command in ("git tag -d --contains HEAD v2.27.0",
                        "git tag -a --merged main v2.27.0 -m x"):
            with self.subTest(command=command):
                self.assertEqual(git_decision(command), "deny")


if __name__ == "__main__":
    unittest.main()
