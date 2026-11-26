#!/usr/bin/env python3
"""Characterize conflict-marker decoding and tracked-file listing."""
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
CONFLICT = "<<<<<<< HEAD\n"


def load_script(name: str):
    """Import one script module from its repository path."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


checker = load_script("check_conflict_markers")


class DecodeContentTest(unittest.TestCase):
    """Each decode path keeps its result."""

    def test_unknown_codec_fallback(self):
        self.assertEqual(checker.decode_content(CONFLICT.encode(), "no-such-codec"),
                         (CONFLICT, None))

    def test_declared_codec_decodes_with_replacement(self):
        self.assertEqual(checker.decode_content("caf\xe9".encode("latin-1"), "latin-1"),
                         ("caf\xe9", None))

    def test_invalid_utf8_nul_binary(self):
        self.assertEqual(checker.decode_content(b"\xff\x00\xfe"), (None, None))

    def test_invalid_utf8_latin1_fallback(self):
        self.assertEqual(checker.decode_content(b"\xffabc"), ("\xffabc", None))

    def test_utf8_bom_is_stripped(self):
        self.assertEqual(checker.decode_content(b"\xef\xbb\xbf" + CONFLICT.encode()),
                         (CONFLICT, None))


EMPTY_BLOB_ID = "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"


def run_git(root: Path, *arguments: str) -> None:
    """Run one git command in the fixture repository."""
    subprocess.run(["git", "-C", str(root), *arguments], check=True,
                   capture_output=True)


class TrackedFilesTest(unittest.TestCase):
    """The tracked listing keeps regular files and drops symlinks."""

    def test_symlink_entries_are_excluded(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            run_git(root, "init", "-q")
            (root / "plain.txt").write_text("text\n", encoding="utf-8")
            run_git(root, "add", "plain.txt")
            run_git(root, "update-index", "--add", "--cacheinfo",
                    f"120000,{EMPTY_BLOB_ID},link.txt")
            self.assertEqual(checker.get_tracked_regular_files(str(root)), ["plain.txt"])


if __name__ == "__main__":
    unittest.main()
