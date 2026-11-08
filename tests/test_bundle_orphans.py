"""Behavioral checks for the orphaned Qt file filter (D313)."""

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


class OrphanedQtFileTest(unittest.TestCase):
    """Qt files that no kept binary loads leave the bundle on their platform."""

    def _assert_dropped(self, platform: str, destinations: tuple[str, ...]) -> None:
        """Each destination is an orphan on the platform."""
        for destination in destinations:
            with self.subTest(platform=platform, destination=destination):
                self.assertTrue(FILTERS.is_orphaned_qt_component(destination, platform))

    def _assert_kept(self, platform: str, destinations: tuple[str, ...]) -> None:
        """Each destination stays on the platform."""
        for destination in destinations:
            with self.subTest(platform=platform, destination=destination):
                self.assertFalse(FILTERS.is_orphaned_qt_component(destination, platform))

    def test_linux_drops_pdf_eglfs_and_unused_bindings(self) -> None:
        """QtPdf, its image plugin, the EGLFS pair, and two unused bindings go."""
        self._assert_dropped(
            "linux",
            (
                "PySide6/Qt/lib/libQt6Pdf.so.6",
                "PySide6/Qt/plugins/imageformats/libqpdf.so",
                "PySide6/Qt/lib/libQt6EglFSDeviceIntegration.so.6",
                "PySide6/Qt/lib/libQt6EglFsKmsSupport.so.6",
                "PySide6/QtConcurrent.abi3.so",
                "PySide6/QtDBus.abi3.so",
            ),
        )

    def test_linux_keeps_what_the_playback_plugin_and_qt_load(self) -> None:
        """The FFmpeg plugin loads QML and Quick on Linux, and QtMultimedia loads Concurrent."""
        self._assert_kept(
            "linux",
            (
                "PySide6/Qt/lib/libQt6Quick.so.6",
                "PySide6/Qt/lib/libQt6Qml.so.6",
                "PySide6/Qt/lib/libQt6QmlModels.so.6",
                "PySide6/Qt/lib/libQt6Concurrent.so.6",
                "PySide6/Qt/lib/libQt6DBus.so.6",
                "PySide6/Qt/lib/libQt6Svg.so.6",
                "PySide6/Qt/lib/libQt6OpenGL.so.6",
                "PySide6/Qt/plugins/imageformats/libqsvg.so",
                "PySide6/Qt/plugins/multimedia/libffmpegmediaplugin.so",
                "PySide6/QtMultimedia.abi3.so",
                "PySide6/QtNetwork.abi3.so",
            ),
        )

    def test_python_platform_names_with_a_suffix_map_to_linux(self) -> None:
        """A sys.platform value such as linux2 uses the Linux list."""
        self.assertTrue(FILTERS.is_orphaned_qt_component("libQt6Pdf.so.6", "linux2"))

    def test_macos_drops_pdf_qml_and_quick_frameworks(self) -> None:
        """Framework binaries, their resources, top-level symlinks, and the PDF plugin go."""
        self._assert_dropped(
            "darwin",
            (
                "PySide6/Qt/lib/QtPdf.framework/Versions/A/QtPdf",
                "PySide6/Qt/lib/QtQuick.framework/Versions/A/QtQuick",
                "PySide6/Qt/lib/QtQml.framework/Versions/A/Resources/Info.plist",
                "PySide6/Qt/lib/QtQmlMeta.framework/Versions/A/QtQmlMeta",
                "PySide6/Qt/lib/QtQmlModels.framework/Versions/A/QtQmlModels",
                "PySide6/Qt/lib/QtQmlWorkerScript.framework/Versions/A/QtQmlWorkerScript",
                "QtQuick",
                "PySide6/Qt/plugins/imageformats/libqpdf.dylib",
            ),
        )

    def test_macos_keeps_the_frameworks_the_app_loads(self) -> None:
        """Multimedia, Concurrent, Svg, and the FFmpeg plugin stay on macOS."""
        self._assert_kept(
            "darwin",
            (
                "PySide6/Qt/lib/QtMultimedia.framework/Versions/A/QtMultimedia",
                "PySide6/Qt/lib/QtConcurrent.framework/Versions/A/QtConcurrent",
                "PySide6/Qt/lib/QtSvg.framework/Versions/A/QtSvg",
                "PySide6/Qt/plugins/multimedia/libffmpegmediaplugin.dylib",
                "PySide6/Qt/plugins/imageformats/libqsvg.dylib",
                "QtMultimedia",
            ),
        )

    def test_windows_drops_pdf_qml_and_quick(self) -> None:
        """The Windows trial drops QtPdf, its plugin, QML, and Quick."""
        self._assert_dropped(
            "win32",
            (
                "PySide6\\Qt6Pdf.dll",
                "PySide6\\plugins\\imageformats\\qpdf.dll",
                "PySide6\\Qt6Qml.dll",
                "PySide6\\Qt6QmlMeta.dll",
                "PySide6\\Qt6QmlModels.dll",
                "PySide6\\Qt6QmlWorkerScript.dll",
                "PySide6\\Qt6Quick.dll",
            ),
        )

    def test_windows_keeps_multimedia_and_its_plugin(self) -> None:
        """QtMultimedia, its FFmpeg plugin, and the QtQuick-named widgets library stay."""
        self._assert_kept(
            "win32",
            (
                "PySide6\\Qt6Multimedia.dll",
                "PySide6\\Qt6Concurrent.dll",
                "PySide6\\plugins\\multimedia\\ffmpegmediaplugin.dll",
                "PySide6\\Qt6QuickWidgets.dll",
                "PySide6\\Qt6Svg.dll",
            ),
        )

    def test_one_platform_list_never_applies_to_another(self) -> None:
        """Each list applies to its own platform only, and an unknown platform keeps all."""
        self.assertFalse(FILTERS.is_orphaned_qt_component("libQt6Quick.so.6", "linux"))
        self.assertFalse(FILTERS.is_orphaned_qt_component("QtQuick", "win32"))
        self.assertFalse(FILTERS.is_orphaned_qt_component("PySide6\\Qt6Quick.dll", "darwin"))
        self.assertFalse(FILTERS.is_orphaned_qt_component("libQt6Pdf.so.6", "freebsd14"))

    def test_drop_entries_removes_orphans_and_reports_them(self) -> None:
        """drop_entries with the orphan filter keeps the rest in order."""
        entries = [
            ("PySide6/Qt/lib/libQt6Core.so.6", "/build/core", "BINARY"),
            ("PySide6/Qt/lib/libQt6Pdf.so.6", "/build/pdf", "BINARY"),
            ("PySide6/Qt/lib/libQt6Quick.so.6", "/build/quick", "BINARY"),
        ]

        kept, dropped = FILTERS.drop_entries(
            entries, lambda destination: FILTERS.is_orphaned_qt_component(destination, "linux")
        )

        self.assertEqual([entry[0] for entry in kept], [entries[0][0], entries[2][0]])
        self.assertEqual(dropped, ["PySide6/Qt/lib/libQt6Pdf.so.6"])


class SpecOrphanWiringTest(unittest.TestCase):
    """The spec applies the orphan filter before the Linux prune and excludes setuptools."""

    spec: str

    @classmethod
    def setUpClass(cls) -> None:
        """Read the spec once."""
        cls.spec = SPEC_PATH.read_text(encoding="utf-8")

    def test_orphan_filter_runs_on_binaries_and_datas_before_the_prune(self) -> None:
        """Both collection lists pass through the filter, and the Linux prune comes after."""
        binaries = self.spec.index("drop_entries(a.binaries, is_orphan_on_this_platform)")
        datas = self.spec.index("drop_entries(a.datas, is_orphan_on_this_platform)")
        prune = self.spec.index("prune_unneeded_libraries(a.binaries")
        self.assertLess(binaries, prune)
        self.assertLess(datas, prune)
        self.assertIn("is_orphaned_qt_component(destination, sys.platform)", self.spec)

    def test_setuptools_and_pkg_resources_are_excluded(self) -> None:
        """The Analysis excludes keep readline and add setuptools and pkg_resources."""
        self.assertIn('UNUSED_PYTHON_PACKAGES = ["setuptools", "pkg_resources"]', self.spec)
        self.assertIn('excludes=["readline"] + UNUSED_PYTHON_PACKAGES', self.spec)


if __name__ == "__main__":
    unittest.main()
