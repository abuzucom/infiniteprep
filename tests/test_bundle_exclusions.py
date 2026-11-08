"""Behavioral checks for the bundle exclusions and Linux library pruning (D302 to D304)."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
FILTERS_PATH = ROOT / "packaging" / "bundle_filters.py"
SPEC_PATH = ROOT / "infiniteprep.spec"
WORKFLOW_PATH = ROOT / ".github/workflows/build-packages.yml"


def _load_filters() -> ModuleType:
    """Import packaging/bundle_filters.py the way the spec does, by file path."""
    spec = importlib.util.spec_from_file_location("bundle_filters", FILTERS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FILTERS = _load_filters()


def _entry(destination: str, typecode: str = "BINARY") -> tuple[str, str, str]:
    """Return a TOC entry whose source path names the destination."""
    return (destination, f"/build/{destination}", typecode)


class Gpl3ExclusionTest(unittest.TestCase):
    """Qt Virtual Keyboard and GNU Readline leave every bundle (D302)."""

    def test_virtual_keyboard_files_are_dropped_on_every_os(self) -> None:
        """Libraries, frameworks, plugins, and symlinks for Virtual Keyboard all go."""
        for destination in (
            "libQt6VirtualKeyboard.so.6",
            "PySide6/Qt/lib/libQt6VirtualKeyboardQml.so.6",
            "PySide6/Qt/plugins/platforminputcontexts/libqtvirtualkeyboardplugin.so",
            "PySide6/Qt/lib/QtVirtualKeyboard.framework/Versions/A/QtVirtualKeyboard",
            "QtVirtualKeyboardQml",
            "PySide6\\Qt6VirtualKeyboard.dll",
            "PySide6\\plugins\\platforminputcontexts\\qtvirtualkeyboardplugin.dll",
        ):
            self.assertTrue(FILTERS.is_gpl3_component(destination), destination)

    def test_readline_library_is_dropped(self) -> None:
        """GNU Readline goes. Libraries with similar names stay."""
        self.assertTrue(FILTERS.is_gpl3_component("libreadline.so.8"))
        self.assertFalse(FILTERS.is_gpl3_component("libtinfo.so.6"))
        self.assertFalse(FILTERS.is_gpl3_component("libQt6Widgets.so.6"))
        self.assertFalse(FILTERS.is_gpl3_component("PySide6/Qt/lib/libQt6Quick.so.6"))

    def test_drop_entries_reports_each_removed_destination(self) -> None:
        """drop_entries keeps the rest in order and names what it removed."""
        entries = [_entry("libQt6Core.so.6"), _entry("libQt6VirtualKeyboard.so.6")]

        kept, dropped = FILTERS.drop_entries(entries, FILTERS.is_gpl3_component)

        self.assertEqual(kept, [_entry("libQt6Core.so.6")])
        self.assertEqual(dropped, ["libQt6VirtualKeyboard.so.6"])


class LinuxPluginTrimTest(unittest.TestCase):
    """Unused Linux Qt plugins leave the bundle, and the needed ones stay (D303)."""

    def test_unused_plugins_are_dropped(self) -> None:
        """The GTK 3 theme, network information, EGLFS, VNC, and the others go."""
        for name in (
            "platformthemes/libqgtk3.so",
            "networkinformation/libqnetworkmanager.so",
            "egldeviceintegrations/libqeglfs-x11-integration.so",
            "generic/libqevdevmouseplugin.so",
            "platforms/libqeglfs.so",
            "platforms/libqlinuxfb.so",
            "platforms/libqvnc.so",
            "platforms/libqvkkhrdisplay.so",
            "platforms/libqminimalegl.so",
            "wayland-decoration-client/libadwaita.so",
        ):
            self.assertTrue(FILTERS.is_unused_linux_plugin(f"PySide6/Qt/plugins/{name}"), name)

    def test_needed_plugins_stay(self) -> None:
        """X11, Wayland, offscreen, multimedia, images, and TLS stay."""
        for name in (
            "platforms/libqxcb.so",
            "platforms/libqwayland.so",
            "platforms/libqoffscreen.so",
            "platforms/libqminimal.so",
            "multimedia/libffmpegmediaplugin.so",
            "imageformats/libqsvg.so",
            "tls/libqopensslbackend.so",
            "wayland-decoration-client/libbradient.so",
            "platformthemes/libqxdgdesktopportal.so",
        ):
            self.assertFalse(FILTERS.is_unused_linux_plugin(f"PySide6/Qt/plugins/{name}"), name)


class LibraryPruningTest(unittest.TestCase):
    """Top-level libraries that no kept binary links against leave (D304)."""

    links: dict[str, set[str]]

    def setUp(self) -> None:
        """Describe a small dependency graph by source path."""
        self.links = {
            "/build/PySide6/QtWidgets.abi3.so": {"libQt6Widgets.so.6"},
            "/build/libQt6Widgets.so.6": {"libQt6Gui.so.6"},
            "/build/libQt6Gui.so.6": {"libfreetype.so.6"},
            "/build/libfreetype.so.6": set(),
            "/build/libgtk-3.so.0": {"libcairo.so.2"},
            "/build/libcairo.so.2": set(),
            "/build/libpython3.12.so.1.0": set(),
            "/build/libgcc_s.so.1": set(),
        }

    def _needed(self, source: str) -> set[str]:
        """Return the libraries the given source links against."""
        return self.links[source]

    def test_orphaned_chain_is_removed_until_nothing_changes(self) -> None:
        """GTK goes first, then cairo, which only GTK needed. The used chain stays."""
        entries = [_entry(destination.removeprefix("/build/")) for destination in self.links]

        kept, dropped = FILTERS.prune_unneeded_libraries(entries, self._needed)

        kept_names = {entry[0] for entry in kept}
        self.assertEqual(dropped, ["libgtk-3.so.0", "libcairo.so.2"])
        for name in ("libQt6Widgets.so.6", "libQt6Gui.so.6", "libfreetype.so.6"):
            self.assertIn(name, kept_names)

    def test_protected_and_nested_files_are_never_pruned(self) -> None:
        """libpython, libgcc_s, and files below a folder stay without any link."""
        for destination in (
            "libpython3.12.so.1.0",
            "libgcc_s.so.1",
            "PySide6/Qt/lib/libQt6Core.so.6",
            "_soundfile_data/libsndfile_x86_64.so",
            "base_library.zip",
        ):
            self.assertFalse(FILTERS.is_prunable_library(destination), destination)

    def test_symlinks_add_no_link_names(self) -> None:
        """A symlink entry holds a link target, so the pruner never reads it as a binary."""
        entries = [
            _entry("libQt6Gui.so.6", FILTERS.SYMLINK_TYPECODE),
            _entry("libfreetype.so.6"),
        ]

        kept, dropped = FILTERS.prune_unneeded_libraries(entries, self._needed)

        self.assertEqual(sorted(dropped), ["libQt6Gui.so.6", "libfreetype.so.6"])
        self.assertEqual(kept, [])


class SpecAndWorkflowTest(unittest.TestCase):
    """The spec applies the filters, and CI starts the Linux app on X11."""

    steps: list[dict[str, Any]]
    spec: str

    @classmethod
    def setUpClass(cls) -> None:
        """Read the spec and the workflow once."""
        cls.spec = SPEC_PATH.read_text(encoding="utf-8")
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        cls.steps = workflow["jobs"]["bundle"]["steps"]

    def _step(self, name: str) -> dict[str, Any]:
        """Return the one step with this name."""
        [step] = [step for step in self.steps if step.get("name") == name]
        return step

    def test_spec_excludes_readline_and_applies_every_filter(self) -> None:
        """The spec loads the filters by exact path, excludes readline, and runs each filter."""
        self.assertNotIn("sys.path.insert", self.spec)
        self.assertIn('spec_from_file_location("bundle_filters", filters_path)', self.spec)
        self.assertIn('excludes=["readline"]', self.spec)
        for call in (
            "drop_entries(a.binaries, is_gpl3_component)",
            "drop_entries(a.datas, is_gpl3_component)",
            "drop_entries(a.binaries, is_unused_linux_plugin)",
            "prune_unneeded_libraries(a.binaries, linked_library_names)",
        ):
            self.assertIn(call, self.spec)
        linux = self.spec.index('if sys.platform.startswith("linux"):')
        self.assertLess(linux, self.spec.index("is_unused_linux_plugin)"))
        prune = self.spec.index("prune_unneeded_libraries(a.binaries")
        self.assertLess(prune, self.spec.index("PYZ("))

    def test_linux_app_starts_on_x11_after_the_offscreen_check(self) -> None:
        """Xvfb is installed, and the X11 start runs on Linux after the smoke check."""
        names = [step.get("name") for step in self.steps]
        self.assertIn("xvfb", self._step("Install Linux Qt headless system libraries")["run"])
        x11 = self._step("Start built app on X11")
        self.assertEqual(x11["if"], "runner.os == 'Linux'")
        self.assertIn("QT_QPA_PLATFORM=xcb xvfb-run", x11["run"])
        self.assertIn("--smoke-check", x11["run"])
        self.assertLess(names.index("Smoke-check built app"), names.index("Start built app on X11"))


if __name__ == "__main__":
    unittest.main()
