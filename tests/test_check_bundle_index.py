"""Checks for the allowlist index, the notice source, the walk, and overlap rejection."""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
CHECKER_PATH = ROOT / "packaging" / "check_bundle.py"
ALLOWLIST_DIR = ROOT / "packaging" / "bundle-allowlist"
NOTICES = ("LICENSE", "NOTICE", "ICU-LICENSE.txt")
# Each wildcard shape becomes a concrete sample path part for the equivalence check.
SAMPLE_REPLACEMENTS = (("**/", "deep/er/"), ("**", "any/where"), ("*", "star"), ("?", "q"))
NON_MATCHING_NAMES = ("unlisted.bin", "zz/none.so", "_internal/zz/none.unknown")
OVERLAPPING_PATTERNS = ("**/**", "_internal/**/**/x.so", "*?*", "_internal/lib*??*.so")
SEPARATED_PATTERNS = ("_internal/lib*.so.?", "?*.dll", "*?.pyd", "_internal/**/*.qm")


def load_checker_module() -> ModuleType:
    """Import packaging/check_bundle.py by file path, as CI runs it."""
    spec = importlib.util.spec_from_file_location("check_bundle_index_target", CHECKER_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {CHECKER_PATH}. Check that the file exists.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECKER = load_checker_module()


def read_allowlist_entries(operating_system: str) -> list[str]:
    """Return the non-comment entries of one shipped allowlist."""
    text = (ALLOWLIST_DIR / f"{operating_system}.txt").read_text(encoding="utf-8")
    entries = [line.strip() for line in text.splitlines()]
    return [entry for entry in entries if entry and not entry.startswith("#")]


def build_sample_name(entry: str) -> str:
    """Return one concrete path that the allowlist entry is meant to match."""
    sample = entry
    for wildcard, replacement in SAMPLE_REPLACEMENTS:
        sample = sample.replace(wildcard, replacement)
    return sample


class AllowlistIndexTest(unittest.TestCase):
    """The index matches exactly the names that the linear pattern scan matches."""

    def test_index_matches_linear_scan(self) -> None:
        """Every sample and non-matching name gets the same verdict from both forms."""
        for operating_system in CHECKER.OPERATING_SYSTEMS:
            entries = read_allowlist_entries(operating_system)
            patterns = CHECKER.read_allowlist(entries)
            index = CHECKER.index_allowlist(entries)
            names = [build_sample_name(entry) for entry in entries] + list(NON_MATCHING_NAMES)
            for name in names:
                with self.subTest(operating_system=operating_system, name=name):
                    linear = any(pattern.fullmatch(name) for pattern in patterns)
                    self.assertEqual(index.matches(name), linear)

    def test_index_keeps_exact_paths_separate(self) -> None:
        """A wildcard-free entry lands in the exact set only."""
        index = CHECKER.index_allowlist(["LICENSE", "_internal/base_library.zip", "_internal/*.so"])
        self.assertEqual(index.exact_paths, frozenset({"LICENSE", "_internal/base_library.zip"}))
        self.assertEqual(set(index.patterns_by_first_part), {"_internal"})
        self.assertEqual(index.unanchored_patterns, ())

    def test_indexed_check_matches_pattern_check(self) -> None:
        """Both allowlist forms yield the same report."""
        entries = ["LICENSE", "NOTICE", "ICU-LICENSE.txt", "_internal/*.so", "**/*.qm"]
        files = [*NOTICES, "_internal/a.so", "_internal/b.dll", "x/y/de.qm", "other.bin"]
        by_patterns = CHECKER.check_files(files, CHECKER.read_allowlist(entries))
        by_index = CHECKER.check_files(files, (), allowlist_index=CHECKER.index_allowlist(entries))
        self.assertEqual(by_index, by_patterns)
        self.assertEqual(by_index.unlisted, ["_internal/b.dll", "other.bin"])


class RegularFileNoticeTest(unittest.TestCase):
    """The notice rule reads one source of regular files."""

    def test_non_regular_notice_is_missing(self) -> None:
        """A listed NOTICE that is not a regular file is a missing notice."""
        patterns = CHECKER.read_allowlist(["**"])
        report = CHECKER.check_files(
            list(NOTICES), patterns, regular_files=frozenset({"LICENSE", "ICU-LICENSE.txt"})
        )
        self.assertEqual(report.missing_notices, ["NOTICE"])

    def test_dangling_notice_link_reported_once(self) -> None:
        """A dangling NOTICE link stays in the inventory and counts as a missing notice."""
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / "InfinitePrep"
            bundle.mkdir()
            for name in ("LICENSE", "ICU-LICENSE.txt"):
                (bundle / name).write_text("text", encoding="utf-8")
            os.symlink(bundle / "absent", bundle / "NOTICE")
            report = CHECKER.check_bundle(bundle, "linux")
        self.assertEqual(report.missing_notices, ["NOTICE"])


class BundleWalkTest(unittest.TestCase):
    """The walk lists files and file links and never descends into folder links."""

    def test_walk_lists_nested_entries_sorted(self) -> None:
        """Nested files, a file link, and a dangling link appear. A folder link does not."""
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / "InfinitePrep"
            nested = bundle / "_internal" / "sub"
            nested.mkdir(parents=True)
            (nested / "b.so").write_bytes(b"")
            (bundle / "a.txt").write_bytes(b"")
            os.symlink(nested / "b.so", bundle / "_internal" / "link.so")
            os.symlink(bundle / "absent", bundle / "_internal" / "dangling.so")
            os.symlink(nested, bundle / "_internal" / "folder_link", target_is_directory=True)
            files = CHECKER.bundle_files(bundle)
        self.assertEqual(
            files,
            ["_internal/dangling.so", "_internal/link.so", "_internal/sub/b.so", "a.txt"],
        )


class OverlapRejectionTest(unittest.TestCase):
    """Pattern shapes with adjacent overlapping wildcards raise ValueError."""

    def test_overlapping_wildcards_raise(self) -> None:
        """Adjacent '**' parts and two '*' joined only by '?' fail to compile."""
        for pattern in OVERLAPPING_PATTERNS:
            with self.subTest(pattern=pattern), self.assertRaises(ValueError):
                CHECKER.compile_pattern(pattern)

    def test_separated_wildcards_still_compile(self) -> None:
        """A '*' next to a literal, a '/', or a lone '?' stays allowed."""
        for pattern in SEPARATED_PATTERNS:
            with self.subTest(pattern=pattern):
                CHECKER.compile_pattern(pattern)

    def test_every_shipped_allowlist_still_compiles(self) -> None:
        """No shipped entry has an overlapping shape."""
        for operating_system in CHECKER.OPERATING_SYSTEMS:
            with self.subTest(operating_system=operating_system):
                CHECKER.read_allowlist(read_allowlist_entries(operating_system))


if __name__ == "__main__":
    unittest.main()
