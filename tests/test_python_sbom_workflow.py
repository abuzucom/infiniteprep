"""Behavioral checks for Python SBOM generation in bundle jobs."""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

import yaml

WORKFLOW_PATH = Path(__file__).resolve().parent.parent / ".github/workflows/build-packages.yml"
REQUIREMENTS_PATH = Path(__file__).resolve().parent.parent / "requirements-sbom.txt"
UPLOAD_ARTIFACT = "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"


class PythonSbomWorkflowTest(unittest.TestCase):
    """The bundle workflow inventories its app environment outside the bundle."""

    workflow: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the workflow once for every check."""
        cls.workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))

    def _steps(self) -> list[dict[str, Any]]:
        """Return every step of the bundle job."""
        return self.workflow["jobs"]["bundle"]["steps"]

    def test_sbom_tool_scans_the_app_python_environment(self) -> None:
        """The isolated tool inspects the package environment, not its own venv."""
        steps = self._steps()
        sbom_step = next(step for step in steps if step.get("name") == "Generate Python SBOM")
        command = sbom_step["run"]

        self.assertIn("python -m venv", command)
        self.assertIn("$RUNNER_TEMP/cyclonedx", command)
        self.assertIn("requirements-sbom.txt", command)
        self.assertIn("--require-hashes", command)
        self.assertIn("--only-binary=:all:", command)
        self.assertNotIn("dist/InfinitePrep", command)
        self.assertIn("cyclonedx-py", command)
        self.assertIn("environment", command)
        self.assertIn("app_python=\"$(python -c 'import sys; print(sys.executable)')\"", command)
        self.assertIn("--output-format JSON", command)
        self.assertIn("$RUNNER_TEMP/InfinitePrep-python-$RUNNER_OS.cdx.json", command)
        build_index = next(
            index for index, step in enumerate(steps) if "pyinstaller" in step.get("run", "")
        )
        check_index = next(
            index for index, step in enumerate(steps)
            if step.get("name") == "Check installed requirements"
        )
        self.assertLess(check_index, steps.index(sbom_step))
        self.assertLess(steps.index(sbom_step), build_index)

    def test_sbom_uses_a_separate_short_lived_artifact(self) -> None:
        """The SBOM upload stays separate from the application bundle upload."""
        uploads = [step for step in self._steps() if step.get("uses", "") == UPLOAD_ARTIFACT]
        sbom_uploads = [step for step in uploads if "python-sbom" in step["with"]["name"]]

        self.assertEqual(len(sbom_uploads), 1)
        self.assertEqual(
            sbom_uploads[0]["with"]["path"],
            "${{ runner.temp }}/InfinitePrep-python-${{ runner.os }}.cdx.json",
        )
        self.assertEqual(
            sbom_uploads[0]["with"]["name"],
            "InfinitePrep-python-sbom-${{ matrix.os }}",
        )
        self.assertEqual(sbom_uploads[0]["with"]["retention-days"], 3)
        self.assertEqual(sbom_uploads[0]["with"]["if-no-files-found"], "error")
        bundle_uploads = [
            step for step in uploads
            if step.get("with", {}).get("path") == "dist/InfinitePrep"
        ]
        self.assertEqual(len(bundle_uploads), 1)

    def test_sbom_tool_requirements_pin_every_package_with_hashes(self) -> None:
        """Every SBOM tool package has an exact version and SHA-256 pin."""
        requirements = REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines()
        package_entries: list[str] = []
        pending_lines: list[str] = []

        for raw_line in requirements:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            pending_lines.append(line.rstrip("\\"))
            if line.endswith("\\"):
                continue
            entry = " ".join(pending_lines)
            pending_lines.clear()
            self.assertRegex(entry, r"^[A-Za-z0-9_.-]+==[^ ]+ ")
            hashes = re.findall(r"--hash=sha256:([0-9a-f]{64})", entry)
            self.assertTrue(hashes, entry)
            package_entries.append(entry)

        self.assertFalse(pending_lines)
        self.assertEqual(len(package_entries), 33)
        self.assertTrue(any(entry.startswith("cyclonedx-bom==8.0.1 ") for entry in package_entries))
        self.assertTrue(any(entry.startswith("chardet==7.7.0 ") for entry in package_entries))


if __name__ == "__main__":
    unittest.main()
