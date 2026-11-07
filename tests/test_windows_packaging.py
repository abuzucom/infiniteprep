"""Behavioral checks for the Windows portable ZIP and NSIS installer (D83a1b)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT / ".github/workflows/build-packages.yml"
INSTALLER_PATH = ROOT / "packaging/windows/installer.nsi"
NSIS_SHA256 = "ba63dffc4410ee89193e1cb5a41989991bd77c61068da17e3156d136b7b0b3d8"
WINDOWS_CONDITION = "runner.os == 'Windows'"
ZIP_NAME = "InfinitePrep-$version-windows-x64-portable.zip"
SETUP_NAME = "InfinitePrep-$version-windows-x64-setup.exe"


class WindowsPackagingWorkflowTest(unittest.TestCase):
    """The workflow builds, verifies, tests, and uploads the Windows packages."""

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

    def test_windows_steps_run_only_on_windows(self) -> None:
        """Each packaging step is gated to the Windows runner."""
        for name in (
            "Build Windows portable ZIP",
            "Fetch pinned NSIS",
            "Build Windows installer",
            "Test Windows installer lifecycle",
            "Upload Windows packages",
        ):
            self.assertEqual(self._step(name)["if"], WINDOWS_CONDITION, name)

    def test_license_notices_join_every_bundle(self) -> None:
        """LICENSE and NOTICE are copied into the bundle on every OS before upload."""
        names = [step.get("name") for step in self.steps]
        notices = self._step("Add license notices to bundle")
        self.assertNotIn("if", notices)
        self.assertIn("LICENSE", notices["run"])
        self.assertIn("NOTICE", notices["run"])
        self.assertLess(names.index("Add license notices to bundle"), names.index("Upload bundle"))

    def test_portable_zip_carries_the_marker(self) -> None:
        """The ZIP gets portable.mode and the versioned name (D270)."""
        run = self._step("Build Windows portable ZIP")["run"]
        self.assertIn("portable.mode", run)
        self.assertIn(ZIP_NAME, run)

    def test_nsis_archive_is_pinned_and_checked(self) -> None:
        """The NSIS download is checked against the pinned SHA-256 before it unpacks (D267)."""
        step = self._step("Fetch pinned NSIS")
        self.assertEqual(step["env"]["NSIS_SHA256"], NSIS_SHA256)
        self.assertTrue(step["env"]["NSIS_URL"].startswith("https://"))
        self.assertIn("/3.13/nsis-3.13.zip", step["env"]["NSIS_URL"])
        run = step["run"]
        self.assertLess(run.index("Get-FileHash"), run.index("Expand-Archive"))
        self.assertIn("throw", run[run.index("Get-FileHash") : run.index("Expand-Archive")])

    def test_installer_build_names_the_output(self) -> None:
        """makensis gets the version, the bundle folder, and the versioned setup name."""
        run = self._step("Build Windows installer")["run"]
        for define in ("/DVERSION=", "/DSOURCE_DIR=", "/DOUTPUT_FILE="):
            self.assertIn(define, run)
        self.assertIn(SETUP_NAME, run)
        self.assertIn("packaging\\windows\\installer.nsi", run)

    def test_lifecycle_installs_checks_and_uninstalls(self) -> None:
        """The lifecycle covers install, smoke check, shortcuts, entry, and uninstall (D268)."""
        run = self._step("Test Windows installer lifecycle")["run"]
        ordered = ["'/S', \"/D=$installDir\"", "--smoke-check", "Start Menu", "Uninstall.exe"]
        positions = [run.index(fragment) for fragment in ordered]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("Desktop shortcut appeared", run)
        self.assertIn("Silent uninstall deleted user data", run)

    def test_package_upload_keeps_both_files(self) -> None:
        """Both packages upload with 3-day retention and fail when either is missing."""
        upload = self._step("Upload Windows packages")["with"]
        self.assertIn("windows-x64-portable.zip", upload["path"])
        self.assertIn("windows-x64-setup.exe", upload["path"])
        self.assertEqual(upload["retention-days"], 3)
        self.assertEqual(upload["if-no-files-found"], "error")


class InstallerScriptTest(unittest.TestCase):
    """The NSIS script installs per user and keeps data unless asked."""

    script: str

    @classmethod
    def setUpClass(cls) -> None:
        """Read the installer script once."""
        cls.script = INSTALLER_PATH.read_text(encoding="utf-8")

    def test_installs_per_user_outside_the_data_root(self) -> None:
        """No admin rights, and the program folder is apart from the data root (D264)."""
        self.assertIn("RequestExecutionLevel user", self.script)
        self.assertIn('InstallDir "$LOCALAPPDATA\\Programs\\${APP_NAME}"', self.script)
        self.assertIn('!define DATA_ROOT "$LOCALAPPDATA\\InfinitePrep"', self.script)

    def test_desktop_shortcut_starts_unchecked(self) -> None:
        """The desktop shortcut section uses /o, so it is off by default."""
        self.assertRegex(self.script, re.compile(r'^Section /o "Desktop shortcut"', re.MULTILINE))

    def test_uninstall_boxes_start_unchecked(self) -> None:
        """Both uninstall choices default to unchecked (D265, D266)."""
        self.assertIn("StrCpy $DeleteData ${BST_UNCHECKED}", self.script)
        self.assertIn("StrCpy $DeleteKeys ${BST_UNCHECKED}", self.script)
        self.assertNotIn("NSD_Check", self.script)

    def test_data_and_keys_go_only_when_checked(self) -> None:
        """The data root and the saved keys are removed only inside their checked branches."""
        data_branch = "${If} $DeleteData == ${BST_CHECKED}\n    RMDir /r \"${DATA_ROOT}\""
        keys_branch = "${If} $DeleteKeys == ${BST_CHECKED}\n    ExecWait"
        self.assertIn(data_branch, self.script)
        self.assertIn(keys_branch, self.script)
        self.assertEqual(self.script.count('RMDir /r "${DATA_ROOT}"'), 1)
        self.assertIn('!define FORGET_CREDENTIALS_FLAG "--forget-credentials"', self.script)

    def test_uninstall_refuses_a_foreign_folder(self) -> None:
        """RMDir /r runs only on a folder named InfinitePrep that holds the app."""
        guard = self.script[self.script.index("Function un.onInit") :]
        guard = guard[: guard.index("FunctionEnd")]
        self.assertIn("${GetFileName} \"$INSTDIR\" $0", guard)
        self.assertIn('IfFileExists "$INSTDIR\\${APP_EXE}"', guard)
        self.assertIn("Abort", guard)

    def test_existing_install_is_removed_first(self) -> None:
        """An earlier install runs its own uninstaller silently, keeping data (D269)."""
        on_init = self.script[self.script.index("Function .onInit") :]
        on_init = on_init[: on_init.index("FunctionEnd")]
        self.assertIn("MB_OKCANCEL", on_init)
        self.assertIn("/S _?=$0", on_init)
        self.assertNotIn("DATA_ROOT", on_init)


if __name__ == "__main__":
    unittest.main()
