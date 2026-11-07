"""Behavioral checks for the macOS app bundle and DMG (D83a3c, D297, D298)."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT / ".github/workflows/build-packages.yml"
SPEC_PATH = ROOT / "infiniteprep.spec"
MACOS_CONDITION = "runner.os == 'macOS'"
MACOS_STEPS = ("Sign macOS app", "Build macOS DMG", "Test macOS DMG", "Upload macOS DMG")
BUNDLE_IDENTIFIER = "dev.infiniteprep.app"
MINIMUM_VERSION = "14.4"
DMG_NAME = "-macos-arm64.dmg"
RETENTION_DAYS = 3


class MacosPackagingWorkflowTest(unittest.TestCase):
    """The workflow signs, packages, tests, and uploads the macOS DMG."""

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

    def test_macos_steps_run_only_on_macos_in_order(self) -> None:
        """Each macOS step is gated to macOS and runs after the bundle build."""
        names = [step.get("name") for step in self.steps]
        positions = [names.index(name) for name in MACOS_STEPS]
        self.assertEqual(positions, sorted(positions))
        self.assertGreater(positions[0], names.index("Build bundle"))
        for name in MACOS_STEPS:
            self.assertEqual(self._step(name)["if"], MACOS_CONDITION, name)

    def test_app_is_signed_after_the_notices_are_added(self) -> None:
        """The ad-hoc signature covers LICENSE and NOTICE inside the bundle."""
        run = self._step("Sign macOS app")["run"]
        self.assertIn("Contents/Resources", run)
        self.assertLess(run.index("cp LICENSE NOTICE"), run.index("codesign --force"))
        self.assertIn("--sign -", run)
        self.assertIn("codesign --verify --deep --strict", run)

    def test_dmg_holds_the_app_shortcut_and_notices(self) -> None:
        """The DMG has the app, an Applications shortcut, and both notices (D298)."""
        run = self._step("Build macOS DMG")["run"]
        for fragment in ("InfinitePrep.app", "ln -s /Applications", "cp LICENSE NOTICE"):
            self.assertIn(fragment, run)
        self.assertIn("hdiutil create", run)
        self.assertIn(DMG_NAME, run)
        self.assertNotIn("portable.mode", run)

    def test_dmg_test_checks_contents_signature_plist_and_launch(self) -> None:
        """The test mounts the DMG read-only and checks what users get."""
        run = self._step("Test macOS DMG")["run"]
        for fragment in (
            "hdiutil attach -readonly",
            "hdiutil detach",
            "codesign --verify --deep --strict",
            BUNDLE_IDENTIFIER,
            MINIMUM_VERSION,
            "CFBundleShortVersionString",
            "--smoke-check",
        ):
            self.assertIn(fragment, run)

    def test_upload_keeps_the_dmg(self) -> None:
        """The DMG uploads with 3-day retention and fails when it is missing."""
        upload = self._step("Upload macOS DMG")["with"]
        self.assertIn(DMG_NAME, upload["path"])
        self.assertEqual(upload["retention-days"], RETENTION_DAYS)
        self.assertEqual(upload["if-no-files-found"], "error")


class MacosBundleSpecTest(unittest.TestCase):
    """The PyInstaller spec builds the .app on macOS only."""

    spec: str

    @classmethod
    def setUpClass(cls) -> None:
        """Read the spec once."""
        cls.spec = SPEC_PATH.read_text(encoding="utf-8")

    def test_bundle_step_runs_only_on_macos(self) -> None:
        """BUNDLE sits under a darwin check, so Windows and Linux builds stay the same."""
        darwin = self.spec.index('if sys.platform == "darwin":')
        self.assertLess(darwin, self.spec.index("BUNDLE("))
        self.assertIn('name="InfinitePrep.app"', self.spec)

    def test_bundle_identifier_and_minimum_version(self) -> None:
        """The bundle uses the fixed identifier and the macOS 14.4 floor (D297)."""
        self.assertIn(f'MACOS_BUNDLE_IDENTIFIER = "{BUNDLE_IDENTIFIER}"', self.spec)
        self.assertIn(f'MACOS_MINIMUM_VERSION = "{MINIMUM_VERSION}"', self.spec)
        self.assertIn('"LSMinimumSystemVersion": MACOS_MINIMUM_VERSION', self.spec)
        self.assertIn('"CFBundleShortVersionString": APP_VERSION', self.spec)


if __name__ == "__main__":
    unittest.main()
