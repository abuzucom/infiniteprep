"""Behavioral checks for module-stem markers and symlink entries in the bundle check."""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
CHECKER_PATH = ROOT / "packaging" / "check_bundle.py"


def _load_checker() -> ModuleType:
    """Import packaging/check_bundle.py by file path, as CI runs it."""
    spec = importlib.util.spec_from_file_location("check_bundle_links_target", CHECKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECKER = _load_checker()


class ModuleStemTest(unittest.TestCase):
    """GPL-3-only module libraries and their QML plugins fail under every name."""

    def test_lottie_bodymovin_libraries_are_denied(self) -> None:
        """Qt Lottie Animation's Bodymovin library fails on Linux, Windows, and macOS."""
        for name in (
            "_internal/libQt6Bodymovin.so.6",
            "_internal/PySide6/Qt6Bodymovin.dll",
            "_internal/PySide6/Qt/lib/QtBodymovin.framework/Versions/A/QtBodymovin",
            "_internal/PySide6/Qt/qml/Qt/labs/lottieqt/liblottieqtplugin.so",
        ):
            with self.subTest(name=name):
                self.assertTrue(CHECKER.is_denied(name))

    def test_qml_plugins_without_the_qt_prefix_are_denied(self) -> None:
        """QML plugins of the GPL-3-only families fail without a 'qt' prefix too."""
        for name in (
            "_internal/PySide6/qml/QtQuick3D/libqquick3dplugin.so",
            "_internal/PySide6/qml/QtDataVisualization/libdatavisualizationqmlplugin.so",
            "_internal/PySide6/qml/QtCharts/chartsqml2plugin.dll",
            "_internal/PySide6/qml/QtGraphs/libgraphsplugin.so",
        ):
            with self.subTest(name=name):
                self.assertTrue(CHECKER.is_denied(name))

    def test_every_allowlist_entry_stays_clear_of_the_wider_markers(self) -> None:
        """No allowlist entry matches a module marker."""
        for operating_system in CHECKER.OPERATING_SYSTEMS:
            path = ROOT / "packaging" / "bundle-allowlist" / f"{operating_system}.txt"
            entries = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
            plain = [entry for entry in entries if entry and not entry.startswith("#")]
            with self.subTest(operating_system=operating_system):
                self.assertEqual([entry for entry in plain if CHECKER.is_denied(entry)], [])


class SymlinkEntryTest(unittest.TestCase):
    """Dangling symlinks join the inventory, and links to folders stay out."""

    def test_dangling_symlink_is_listed_and_denied(self) -> None:
        """A broken link named like a denied library shows up as unlisted and denied."""
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / "InfinitePrep"
            internal = bundle / "_internal"
            internal.mkdir(parents=True)
            os.symlink(internal / "missing.so", internal / "libQt6Charts.so.6")
            patterns = CHECKER.read_allowlist(["**"])

            report = CHECKER.check_files(CHECKER.bundle_files(bundle), patterns)

        self.assertEqual(report.denied, ["_internal/libQt6Charts.so.6"])

    def test_symlink_to_a_folder_is_not_a_file(self) -> None:
        """A framework-style link to a folder adds no entry, and its target files count once."""
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / "InfinitePrep"
            versions = bundle / "QtCore.framework" / "Versions"
            (versions / "A").mkdir(parents=True)
            (versions / "A" / "QtCore").write_bytes(b"")
            os.symlink(versions / "A", versions / "Current", target_is_directory=True)

            files = CHECKER.bundle_files(bundle)

        self.assertEqual(files, ["QtCore.framework/Versions/A/QtCore"])


if __name__ == "__main__":
    unittest.main()
