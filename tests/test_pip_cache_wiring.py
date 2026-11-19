"""Owned workflows cache pip downloads, pin Python, and keep the bundled pip."""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

import yaml

WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"
# Each owned workflow and the jobs whose setup-python step must cache pip.
CACHED_JOBS: dict[str, tuple[str, ...]] = {
    "ci.yml": ("checks", "lint-and-style", "test-matrix"),
    "os-tests.yml": ("tests-windows", "tests-macos"),
}
SETUP_PYTHON_ACTION = "actions/setup-python@"
PIP_CACHE = "pip"
PINNED_PYTHON = "3.12"
PIP_SELF_UPGRADE = "--upgrade pip"
REQUIREMENTS_FILE = re.compile(r"requirements-[a-z]+\.txt")


def find_setup_python_steps(job: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every setup-python step of one job."""
    return [
        step for step in job["steps"] if str(step.get("uses", "")).startswith(SETUP_PYTHON_ACTION)
    ]


def find_installed_requirements(job: dict[str, Any]) -> set[str]:
    """Return every requirements file that a run step of the job names."""
    names: set[str] = set()
    for step in job["steps"]:
        names.update(REQUIREMENTS_FILE.findall(step.get("run", "")))
    return names


class PipCacheTest(unittest.TestCase):
    """Each listed job restores pip downloads keyed on the files it installs."""

    workflows: dict[str, dict[str, Any]]

    @classmethod
    def setUpClass(cls) -> None:
        """Parse each owned workflow once."""
        cls.workflows = {
            name: yaml.safe_load((WORKFLOWS / name).read_text(encoding="utf-8"))
            for name in CACHED_JOBS
        }

    def test_each_setup_python_step_caches_pip(self) -> None:
        """The cache input names pip in every listed job."""
        for workflow_name, job_names in CACHED_JOBS.items():
            for job_name in job_names:
                job = self.workflows[workflow_name]["jobs"][job_name]
                for step in find_setup_python_steps(job):
                    with self.subTest(workflow=workflow_name, job=job_name):
                        self.assertEqual(step["with"].get("cache"), PIP_CACHE)

    def test_cache_key_names_every_installed_file(self) -> None:
        """A change to any installed requirements file invalidates the cache."""
        for workflow_name, job_names in CACHED_JOBS.items():
            for job_name in job_names:
                job = self.workflows[workflow_name]["jobs"][job_name]
                installed = find_installed_requirements(job)
                for step in find_setup_python_steps(job):
                    with self.subTest(workflow=workflow_name, job=job_name):
                        key_files = set(str(step["with"].get("cache-dependency-path", "")).split())
                        self.assertTrue(installed)
                        self.assertEqual(key_files, installed)

    def test_os_tests_pin_the_matrix_python(self) -> None:
        """The platform gate suite runs on the same Python as the test matrix."""
        jobs = self.workflows["os-tests.yml"]["jobs"]
        for job_name in CACHED_JOBS["os-tests.yml"]:
            for step in find_setup_python_steps(jobs[job_name]):
                with self.subTest(job=job_name):
                    self.assertEqual(str(step["with"]["python-version"]), PINNED_PYTHON)

    def test_ci_keeps_the_bundled_pip(self) -> None:
        """No ci step downloads an unpinned pip over the pinned interpreter."""
        for job_name, job in self.workflows["ci.yml"]["jobs"].items():
            for step in job.get("steps", []):
                with self.subTest(job=job_name, step=step.get("name")):
                    self.assertNotIn(PIP_SELF_UPGRADE, step.get("run", ""))


if __name__ == "__main__":
    unittest.main()
