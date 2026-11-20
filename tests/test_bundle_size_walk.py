"""Checks for the single bundle walk and the prune loop name cache (D354, D304, D313)."""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
SIZE_PATH = ROOT / "packaging" / "bundle_size.py"
FILTERS_PATH = ROOT / "packaging" / "bundle_filters.py"
MACOS_FILES = {
    "Contents/MacOS/InfinitePrep": 40,
    "Contents/Frameworks/QtCore.framework/QtCore": 300,
    "Contents/Frameworks/libz.dylib": 25,
    "Contents/Resources/icon.icns": 10,
    "Contents/Info.plist": 5,
}
FLAT_FILES = {"app.exe": 100, "_internal/lib.dll": 250, "_internal/deep/b.dll": 50}
SYMLINK_TYPECODE = "SYMLINK"
BINARY_TYPECODE = "BINARY"


def load_module(name: str, path: Path) -> ModuleType:
    """Import one packaging script by file path, as CI and the spec do."""
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}. Check that the file exists.")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SIZE = load_module("bundle_size_walk_target", SIZE_PATH)
FILTERS = load_module("bundle_filters_walk_target", FILTERS_PATH)


def make_tree(root: Path, files: dict[str, int]) -> Path:
    """Create a bundle folder with files of the given sizes and one empty folder."""
    bundle = root / "InfinitePrep.app"
    for name, size in files.items():
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
    return bundle


def count_folders(bundle: Path) -> int:
    """Return the number of real folders in the tree, the root included."""
    return 1 + sum(len(folders) for _root, folders, _files in os.walk(bundle))


class SingleWalkTest(unittest.TestCase):
    """build_report walks each folder of the bundle once."""

    def assert_single_walk(self, bundle: Path, folders_root: Path) -> None:
        """Check the report values and the scandir call count for one layout."""
        expected_total = SIZE.directory_bytes(bundle)
        expected_folders = SIZE.largest_folders(folders_root)
        with mock.patch.object(SIZE.os, "scandir", wraps=os.scandir) as scandir:
            report = SIZE.build_report("macos", bundle, folders_root, (), None)
        self.assertEqual(report.bundle_bytes, expected_total)
        self.assertEqual(report.folders, expected_folders)
        self.assertEqual(scandir.call_count, count_folders(bundle))

    def test_macos_contents_layout_uses_one_walk(self) -> None:
        """A folders root below the bundle groups folders without a second walk."""
        with tempfile.TemporaryDirectory() as directory:
            bundle = make_tree(Path(directory), MACOS_FILES)
            (bundle / "Contents" / "PlugIns").mkdir()
            self.assert_single_walk(bundle, bundle / "Contents")

    def test_flat_layout_uses_one_walk(self) -> None:
        """A folders root equal to the bundle groups the top-level folders."""
        with tempfile.TemporaryDirectory() as directory:
            bundle = make_tree(Path(directory), FLAT_FILES)
            self.assert_single_walk(bundle, bundle)

    def test_folders_root_outside_the_bundle_keeps_its_own_folders(self) -> None:
        """A folders root outside the bundle still lists its own folders."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = make_tree(root, FLAT_FILES)
            other = root / "other"
            (other / "big").mkdir(parents=True)
            (other / "big" / "file.bin").write_bytes(b"x" * 7)
            report = SIZE.build_report("linux", bundle, other, (), None)
        self.assertEqual(report.bundle_bytes, sum(FLAT_FILES.values()))
        self.assertEqual([(folder.name, folder.bytes) for folder in report.folders], [("big", 7)])


class PruneNameCacheTest(unittest.TestCase):
    """The prune loop classifies each destination once across every round."""

    def test_each_destination_is_classified_once(self) -> None:
        """Two prune rounds still call is_prunable_library once per entry."""
        entries = [
            ("app", "/src/app", BINARY_TYPECODE),
            ("liba.so.1", "/src/liba.so.1", BINARY_TYPECODE),
            ("libb.so.1", "/src/libb.so.1", BINARY_TYPECODE),
            ("libc.so.1", "/src/libc.so.1", SYMLINK_TYPECODE),
        ]
        # liba links libb. Round one drops liba and the unlinked libc. Round two drops libb.
        links = {"/src/app": set(), "/src/liba.so.1": {"libb.so.1"}, "/src/libb.so.1": set()}
        original = FILTERS.is_prunable_library
        with mock.patch.object(FILTERS, "is_prunable_library", wraps=original) as classify:
            kept, dropped = FILTERS.prune_unneeded_libraries(entries, links.__getitem__)
        self.assertEqual(dropped, ["liba.so.1", "libc.so.1", "libb.so.1"])
        self.assertEqual([entry[0] for entry in kept], ["app"])
        self.assertEqual(classify.call_count, len(entries))

    def test_macos_framework_parts_are_a_module_constant(self) -> None:
        """The orphan framework folder names live in one module constant."""
        expected = frozenset(f"{name}.framework" for name in FILTERS.MACOS_ORPHAN_FRAMEWORKS)
        self.assertEqual(FILTERS.MACOS_ORPHAN_FRAMEWORK_PARTS, expected)


if __name__ == "__main__":
    unittest.main()
