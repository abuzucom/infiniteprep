"""Behavioral checks for the build stop when a kept binary links a dropped Qt file (D313)."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
FILTERS_PATH = ROOT / "packaging" / "bundle_filters.py"
SPEC_PATH = ROOT / "infiniteprep.spec"


def _load_filters() -> ModuleType:
    """Import packaging/bundle_filters.py the way the spec does, by file path."""
    spec = importlib.util.spec_from_file_location("bundle_filters", FILTERS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FILTERS = _load_filters()


class LoadedOrphanTest(unittest.TestCase):
    """find_loaded_orphans names every kept binary that still links a dropped file."""

    links: dict[str, set[str]]

    def setUp(self) -> None:
        """Describe the links of a small kept set by source path."""
        self.links = {
            "/build/ffmpeg-linux": {"libQt6Quick.so.6", "libQt6Multimedia.so.6"},
            "/build/future-plugin": {"libQt6Pdf.so.6"},
            "/build/ffmpeg-macos": {"@rpath/QtQuick.framework/Versions/A/QtQuick"},
            "/build/ffmpeg-windows": {"QT6QUICK.DLL"},
            "/build/widgets": {"libQt6Widgets.so.6"},
        }

    def _needed(self, source: str) -> set[str]:
        """Return the libraries the given source links against."""
        return self.links[source]

    def test_no_conflict_when_no_kept_binary_links_a_dropped_file(self) -> None:
        """Dropping QtPdf alone is safe while nothing kept links it."""
        kept = [("PySide6/QtWidgets.abi3.so", "/build/widgets", "BINARY")]

        conflicts = FILTERS.find_loaded_orphans(
            kept, ["PySide6/Qt/lib/libQt6Pdf.so.6"], self._needed
        )

        self.assertEqual(conflicts, [])

    def test_linux_conflict_names_the_binary_and_the_link(self) -> None:
        """A new plugin that links QtPdf shows up with its destination and link name."""
        kept = [
            ("plugins/new/libnew.so", "/build/future-plugin", "BINARY"),
            ("PySide6/QtWidgets.abi3.so", "/build/widgets", "BINARY"),
        ]

        conflicts = FILTERS.find_loaded_orphans(
            kept, ["PySide6/Qt/lib/libQt6Pdf.so.6"], self._needed
        )

        self.assertEqual(conflicts, ["plugins/new/libnew.so loads libQt6Pdf.so.6"])

    def test_macos_framework_link_matches_the_dropped_framework_binary(self) -> None:
        """An @rpath framework link matches the framework binary by file name."""
        kept = [("plugins/libffmpegmediaplugin.dylib", "/build/ffmpeg-macos", "BINARY")]
        dropped = ["PySide6/Qt/lib/QtQuick.framework/Versions/A/QtQuick"]

        conflicts = FILTERS.find_loaded_orphans(kept, dropped, self._needed)

        self.assertEqual(len(conflicts), 1)
        self.assertIn("QtQuick.framework", conflicts[0])

    def test_windows_link_matches_regardless_of_case(self) -> None:
        """Windows link names ignore case, so QT6QUICK.DLL matches a dropped Qt6Quick.dll."""
        kept = [("PySide6\\plugins\\ffmpegmediaplugin.dll", "/build/ffmpeg-windows", "BINARY")]

        conflicts = FILTERS.find_loaded_orphans(kept, ["PySide6\\Qt6Quick.dll"], self._needed)

        self.assertEqual(len(conflicts), 1)

    def test_symlink_entries_are_not_read_as_binaries(self) -> None:
        """A symlink holds a link target, so its links are never read."""
        kept = [("libQt6Quick.so.6", "/build/ffmpeg-linux", FILTERS.SYMLINK_TYPECODE)]

        conflicts = FILTERS.find_loaded_orphans(kept, ["libQt6Quick.so.6"], self._needed)

        self.assertEqual(conflicts, [])


class SpecLoadedOrphanWiringTest(unittest.TestCase):
    """The spec checks the dropped binaries on every OS and stops the build on a conflict."""

    def test_spec_stops_the_build_before_the_linux_block(self) -> None:
        """The check runs on the kept binaries and dropped orphans, outside any OS branch."""
        spec = SPEC_PATH.read_text(encoding="utf-8")
        check = spec.index(
            "find_loaded_orphans(a.binaries, orphan_binaries, linked_library_names)"
        )
        stop = spec.index("raise SystemExit(", check)
        self.assertLess(stop, spec.index('if sys.platform.startswith("linux"):'))
        self.assertIn("from PyInstaller.depend import bindepend", spec)


if __name__ == "__main__":
    unittest.main()
