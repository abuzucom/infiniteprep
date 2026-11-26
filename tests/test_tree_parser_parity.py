#!/usr/bin/env python3
"""Pin the two standalone ls-tree parsers to the same record handling.

scripts/check_compliance_tree.py and scripts/check_conflict_markers.py each
keep a separate _parse_tree_entries so each file stays standalone. This test
fails when one copy changes record parsing and the other does not.
"""
import unittest

from tests.test_tree_checker_parity import load_script, outcome

REGULAR_ID = "a" * 40
EXECUTABLE_ID = "b" * 64
SYMLINK_ID = "c" * 40
SUBMODULE_ID = "d" * 40


def build_record(mode: bytes, object_type: bytes, object_id: str, path: bytes) -> bytes:
    """Return one NUL-terminated ls-tree record."""
    return mode + b" " + object_type + b" " + object_id.encode("ascii") + b"\t" + path + b"\x00"


REGULAR_RECORDS = (
    build_record(b"100644", b"blob", REGULAR_ID, b"docs/readme.md")
    + build_record(b"100755", b"blob", EXECUTABLE_ID, b"scripts/run.py")
)
REJECTED_OUTPUTS = (
    build_record(b"100644", b"blob", REGULAR_ID, b"a")[:-1],
    b"100644 blob " + REGULAR_ID.encode("ascii") + b" no-tab\x00",
    build_record(b"100644", b"blob", "g" * 40, b"bad-id"),
    build_record(b"100644", b"blob", REGULAR_ID, b"dup")
    + build_record(b"100644", b"blob", REGULAR_ID, b"dup"),
    build_record(b"100644", b"blob", REGULAR_ID, b"a/../b"),
    build_record(b"040000", b"tree", REGULAR_ID, b"subdir"),
)


class TreeParserParityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = load_script("check_compliance_tree")
        cls.markers = load_script("check_conflict_markers")

    def test_regular_blobs_parse_equal(self):
        tree_entries = self.tree._parse_tree_entries(REGULAR_RECORDS)
        marker_entries = self.markers._parse_tree_entries(REGULAR_RECORDS)
        self.assertEqual([(path, object_id) for path, object_id, _mode in tree_entries],
                         marker_entries)

    def test_parsers_reject_malformed_output(self):
        for raw in REJECTED_OUTPUTS:
            with self.subTest(raw=raw):
                self.assertEqual(outcome(self.tree._parse_tree_entries, raw)[0], "error")
                self.assertEqual(outcome(self.markers._parse_tree_entries, raw)[0], "error")

    def test_empty_output_yields_no_entries(self):
        self.assertEqual(self.tree._parse_tree_entries(b""), [])
        self.assertEqual(self.markers._parse_tree_entries(b""), [])

    def test_symlink_submodule_differences(self):
        symlink = build_record(b"120000", b"blob", SYMLINK_ID, b"link")
        submodule = build_record(b"160000", b"commit", SUBMODULE_ID, b"vendor/lib")
        self.assertEqual(self.tree._parse_tree_entries(symlink), [("link", SYMLINK_ID, "120000")])
        self.assertEqual(self.markers._parse_tree_entries(symlink), [])
        self.assertEqual(outcome(self.tree._parse_tree_entries, submodule)[0], "error")
        self.assertEqual(self.markers._parse_tree_entries(submodule), [])


if __name__ == "__main__":
    unittest.main()
