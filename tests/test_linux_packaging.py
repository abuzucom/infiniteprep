"""Behavioral checks for the Linux AppImage and tarballs (D83a2b, D284 to D288)."""

from __future__ import annotations

import configparser
import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT / ".github/workflows/build-packages.yml"
DESKTOP_PATH = ROOT / "packaging/linux/infiniteprep.desktop"
NOTICE_PATH = ROOT / "NOTICE"
LINUX_CONDITION = "runner.os == 'Linux'"
LINUX_STEPS = (
    "Build Linux tarballs",
    "Fetch pinned AppImage tools",
    "Build Linux AppImage",
    "Test Linux packages",
    "Upload Linux packages",
)
PINNED_TOOLS = {
    "LINUXDEPLOY": (
        "/1-alpha-20251107-1/linuxdeploy-x86_64.AppImage",
        "c20cd71e3a4e3b80c3483cef793cda3f4e990aca14014d23c544ca3ce1270b4d",
    ),
    "APPIMAGE_PLUGIN": (
        "/1-alpha-20250213-1/linuxdeploy-plugin-appimage-x86_64.AppImage",
        "992d502a248e14ab185448ddf6f6e7d25558cb84d4623c354c3af350c25fccb3",
    ),
    "RUNTIME": (
        "/20251108/runtime-x86_64",
        "2fca8b443c92510f1483a883f60061ad09b46b978b2631c807cd873a47ec260d",
    ),
}
TARBALL = "-linux-x86_64.tar.gz"
PORTABLE_TARBALL = "-linux-x86_64-portable.tar.gz"
APPIMAGE = "-linux-x86_64.AppImage"
RETENTION_DAYS = 3


class LinuxPackagingWorkflowTest(unittest.TestCase):
    """The workflow builds, checks, tests, and uploads the Linux packages."""

    steps: list[dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the workflow once."""
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        cls.steps = workflow["jobs"]["bundle"]["steps"]

    def _step(self, name: str) -> dict[str, Any]:
        """Return the one step with this name."""
        [step] = [step for step in self.steps if step.get("name") == name]
        return step

    def test_linux_steps_run_only_on_linux_after_the_notices(self) -> None:
        """Each Linux step runs only on Linux, after LICENSE and NOTICE join the bundle."""
        names = [step.get("name") for step in self.steps]
        notices_index = names.index("Add license notices to bundle")
        for name in LINUX_STEPS:
            self.assertEqual(self._step(name)["if"], LINUX_CONDITION, name)
            self.assertGreater(names.index(name), notices_index, name)

    def test_tools_are_pinned_and_checked_before_use(self) -> None:
        """Every tool matches its pinned SHA-256 before anything runs it (D286)."""
        step = self._step("Fetch pinned AppImage tools")
        for prefix, (url_tail, digest) in PINNED_TOOLS.items():
            url = step["env"][f"{prefix}_URL"]
            self.assertTrue(url.startswith("https://github.com/"), prefix)
            self.assertTrue(url.endswith(url_tail), prefix)
            self.assertEqual(step["env"][f"{prefix}_SHA256"], digest, prefix)
        run = step["run"]
        self.assertIn("sha256sum --check --strict", run)
        self.assertLess(run.index("sha256sum --check"), run.index("chmod +x"))
        self.assertNotIn("plugin-qt", run)

    def test_appimage_uses_the_pinned_runtime_without_the_qt_plugin(self) -> None:
        """The build points at the pinned runtime and fails on a download (D284, D285)."""
        run = self._step("Build Linux AppImage")["run"]
        self.assertIn('LDAI_RUNTIME_FILE="$tools/runtime-x86_64"', run)
        self.assertIn("grep -q 'Downloading runtime'", run)
        self.assertIn("--output appimage", run)
        self.assertNotIn("--plugin qt", run)
        self.assertIn("packaging/linux/infiniteprep.desktop", run)
        self.assertIn(APPIMAGE, run)

    def test_tarballs_differ_only_by_the_marker(self) -> None:
        """The plain tarball is built before the marker is added for the portable one (D288)."""
        run = self._step("Build Linux tarballs")["run"]
        plain = run.index(TARBALL)
        marker = run.index("portable.mode")
        portable = run.index(PORTABLE_TARBALL)
        self.assertLess(plain, marker)
        self.assertLess(marker, portable)

    def test_package_test_covers_every_package(self) -> None:
        """Both tarballs and the AppImage are unpacked or run and smoke-checked."""
        run = self._step("Test Linux packages")["run"]
        for fragment in ("--smoke-check", "LICENSE", "NOTICE", "portable.mode", APPIMAGE):
            self.assertIn(fragment, run)
        self.assertIn("The plain tarball carries portable.mode.", run)

    def test_upload_keeps_all_three_packages(self) -> None:
        """All three packages upload with 3-day retention and fail when any is missing."""
        upload = self._step("Upload Linux packages")["with"]
        for suffix in (TARBALL, PORTABLE_TARBALL, APPIMAGE):
            self.assertIn(suffix, upload["path"])
        self.assertEqual(upload["retention-days"], RETENTION_DAYS)
        self.assertEqual(upload["if-no-files-found"], "error")


class LinuxPackagingFilesTest(unittest.TestCase):
    """The desktop entry and NOTICE describe the AppImage."""

    def test_desktop_entry_launches_the_bundle(self) -> None:
        """The desktop entry names the bundled executable and the placeholder icon."""
        # ConfigParser lowercases keys on read and on lookup alike.
        parser = configparser.ConfigParser(interpolation=None)
        parser.read_string(DESKTOP_PATH.read_text(encoding="utf-8"))
        entry = parser["Desktop Entry"]
        self.assertEqual(entry["Type"], "Application")
        self.assertEqual(entry["Name"], "InfinitePrep")
        self.assertEqual(entry["Exec"], "InfinitePrep")
        self.assertEqual(entry["Icon"], "infiniteprep")
        self.assertEqual(entry["Terminal"], "false")

    def test_notice_lists_the_static_runtime_libraries(self) -> None:
        """NOTICE names the runtime, its static libraries, and where to get their source."""
        notice = NOTICE_PATH.read_text(encoding="utf-8")
        for fragment in (
            "AppImage type2-runtime 20251108: MIT",
            "libfuse 3.15.0: LGPL-2.1",
            "squashfuse 0.5.2: BSD-2-Clause",
            "https://github.com/AppImage/type2-runtime/tree/20251108",
            "https://github.com/libfuse/libfuse/releases/tag/fuse-3.15.0",
        ):
            self.assertIn(fragment, notice)


if __name__ == "__main__":
    unittest.main()
