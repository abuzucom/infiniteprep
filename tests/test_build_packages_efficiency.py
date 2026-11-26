"""Checks that build-packages reads each value once and keeps shell steps flat (D314 to D354)."""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "build-packages.yml"
VERSION_IMPORT = "import infiniteprep; print(infiniteprep.__version__)"
VERSION_VARIABLE = "APP_VERSION"
RECORD_STEP_NAME = "Record app version"
SMOKE_STEP_NAME = "Smoke-check built app"
SAVE_STEP_NAME = "Save package attestation bundles"
UPLOAD_STEP_NAME = "Upload to draft release"
SIZE_SCRIPT = "packaging/bundle_size.py"
RELEASE_VIEW = "gh release view"
CAPTURE_IN_CONDITION = re.compile(r'^\s*if [a-z_]+="\$\(', re.MULTILINE)


def load_jobs() -> dict[str, Any]:
    """Return the jobs mapping of build-packages.yml."""
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))["jobs"]


def find_step(steps: list[dict[str, Any]], name: str) -> dict[str, Any]:
    """Return the one step with the given name."""
    [step] = [step for step in steps if step.get("name") == name]
    return step


class VersionReadTest(unittest.TestCase):
    """The bundle job starts Python once to read the app version."""

    steps: list[dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the workflow once."""
        cls.steps = load_jobs()["bundle"]["steps"]

    def test_only_record_step_imports_package(self) -> None:
        """One step reads the version and writes it to GITHUB_ENV."""
        readers = [step["name"] for step in self.steps if VERSION_IMPORT in step.get("run", "")]
        self.assertEqual(readers, [RECORD_STEP_NAME])
        record = find_step(self.steps, RECORD_STEP_NAME)
        self.assertIn(f"{VERSION_VARIABLE}=", record["run"])
        self.assertIn("GITHUB_ENV", record["run"])

    def test_version_recorded_before_first_use(self) -> None:
        """The record step runs before the smoke check, the first step that needs it."""
        names = [step.get("name") for step in self.steps]
        self.assertLess(names.index(RECORD_STEP_NAME), names.index(SMOKE_STEP_NAME))


class SizeStepTest(unittest.TestCase):
    """One bundle size step covers every operating system."""

    def test_one_step_runs_size_report(self) -> None:
        """Exactly one bundle step calls packaging/bundle_size.py."""
        steps = load_jobs()["bundle"]["steps"]
        size_steps = [step for step in steps if SIZE_SCRIPT in step.get("run", "")]
        self.assertEqual(len(size_steps), 1)
        self.assertNotIn("if", size_steps[0])


class ReleaseStepTest(unittest.TestCase):
    """The release steps view the release once and test sets with a hash lookup."""

    steps: list[dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the workflow once."""
        cls.steps = load_jobs()["release"]["steps"]

    def test_release_steps_view_release_once(self) -> None:
        """The save and upload steps each call gh release view one time."""
        for name in (SAVE_STEP_NAME, UPLOAD_STEP_NAME):
            with self.subTest(step=name):
                self.assertEqual(find_step(self.steps, name)["run"].count(RELEASE_VIEW), 1)

    def test_asset_names_use_associative_array(self) -> None:
        """The steps look up draft asset names in a bash associative array."""
        for name in (SAVE_STEP_NAME, UPLOAD_STEP_NAME):
            run = find_step(self.steps, name)["run"]
            with self.subTest(step=name):
                self.assertIn("declare -A", run)
                self.assertNotIn("grep -Fxq", run)

    def test_no_capture_tested_inside_if(self) -> None:
        """Each command substitution runs before the status test, not inside it."""
        for name in (SAVE_STEP_NAME, UPLOAD_STEP_NAME):
            with self.subTest(step=name):
                self.assertIsNone(CAPTURE_IN_CONDITION.search(find_step(self.steps, name)["run"]))

    def test_bundle_reuse_in_named_function(self) -> None:
        """The save step defines reuse_draft_bundle and calls it."""
        run = find_step(self.steps, SAVE_STEP_NAME)["run"]
        self.assertIn("reuse_draft_bundle() {", run)
        self.assertGreater(run.count("reuse_draft_bundle"), 1)


if __name__ == "__main__":
    unittest.main()
