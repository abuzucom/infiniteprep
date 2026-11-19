"""The test matrix skips the upstream test copies that other jobs already run."""

from __future__ import annotations

import importlib.util
import tempfile
import textwrap
import unittest
from pathlib import Path
from types import ModuleType
from typing import Any

import yaml

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
LOADER_PATH = REPOSITORY_ROOT / "tests" / "upstream_tests.py"
CI_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "ci.yml"
EXCLUDE_FLAG = "--exclude-upstream"
MATRIX_JOB = "test-matrix"
CHECKS_JOB = "checks"
PARALLEL_RUNNER = "scripts/run_tests.py"
SERIAL_DISCOVERY = "unittest discover"
# These owned root tests must stay in the matrix on every operating system.
OWNED_ROOT_SAMPLES = (
    "test_ci_coverage_job.py",
    "test_release_workflow.py",
    "test_upstream_test_exclusion.py",
)
# These upstream copies must leave the matrix.
UPSTREAM_SAMPLES = (
    "test_review_wiring.py",
    "test_gate_parity.py",
    "test_prose_policy_backtracking.py",
)


def load_loader_module() -> ModuleType:
    """Load tests/upstream_tests.py by path, as the conftest does."""
    spec = importlib.util.spec_from_file_location("upstream_tests", LOADER_PATH)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {LOADER_PATH}; restore the loader module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def find_run_commands(job: dict[str, Any]) -> list[str]:
    """Return the run text of every step in one workflow job."""
    return [step["run"] for step in job["steps"] if "run" in step]


def write_project(root: Path, exclude_entries: list[str], test_files: list[str]) -> None:
    """Write a minimal pyproject.toml and the named test files under root."""
    (root / "tests").mkdir()
    for name in test_files:
        (root / "tests" / name).write_text("", encoding="utf-8")
    entries = "".join(f'    "{entry}",\n' for entry in exclude_entries)
    pyproject = f"[tool.ruff]\nextend-exclude = [\n{entries}]\n"
    (root / "pyproject.toml").write_text(textwrap.dedent(pyproject), encoding="utf-8")


class UpstreamLoaderTest(unittest.TestCase):
    """The loader reads the upstream list from the pyproject Ruff exclusions."""

    loader: ModuleType
    upstream_names: frozenset[str]

    @classmethod
    def setUpClass(cls) -> None:
        """Load the module and read the repository list once."""
        cls.loader = load_loader_module()
        paths = cls.loader.load_upstream_test_paths(REPOSITORY_ROOT)
        cls.upstream_names = frozenset(path.name for path in paths)

    def test_upstream_copies_are_listed(self) -> None:
        """The agents copies and the review wiring test leave the matrix."""
        for name in UPSTREAM_SAMPLES:
            with self.subTest(name=name):
                self.assertIn(name, self.upstream_names)

    def test_owned_root_tests_stay(self) -> None:
        """Owned root tests stay in the matrix."""
        for name in OWNED_ROOT_SAMPLES:
            with self.subTest(name=name):
                self.assertNotIn(name, self.upstream_names)

    def test_only_root_test_modules_are_returned(self) -> None:
        """Helper modules and directories in the Ruff list never reach the result."""
        for name in self.upstream_names:
            with self.subTest(name=name):
                self.assertTrue(name.startswith("test_") and name.endswith(".py"))

    def test_paths_sit_directly_in_the_tests_folder(self) -> None:
        """Every returned path is a direct child of tests/."""
        tests_dir = REPOSITORY_ROOT / "tests"
        for path in self.loader.load_upstream_test_paths(REPOSITORY_ROOT):
            with self.subTest(path=path.name):
                self.assertEqual(path.parent, tests_dir)


class UpstreamLoaderValidationTest(unittest.TestCase):
    """A stale or unsafe list fails loudly instead of skipping nothing."""

    loader: ModuleType

    @classmethod
    def setUpClass(cls) -> None:
        """Load the module once."""
        cls.loader = load_loader_module()

    def assert_list_rejected(self, entries: list[str], files: list[str], text: str) -> None:
        """Assert that the loader rejects a project with entries and files."""
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            write_project(root, entries, files)
            with self.assertRaisesRegex(self.loader.UpstreamTestListError, text):
                self.loader.load_upstream_test_paths(root)

    def test_missing_files_are_all_named(self) -> None:
        """Every listed file that no longer exists appears in one error."""
        entries = ["tests/test_gone_one.py", "tests/test_gone_two.py", "tests/test_here.py"]
        self.assert_list_rejected(entries, ["test_here.py"], "test_gone_one.py.*test_gone_two.py")

    def test_escaping_entry_is_rejected(self) -> None:
        """An entry that leaves the tests folder is rejected."""
        self.assert_list_rejected(["tests/../test_outside.py"], [], "outside tests")

    def test_list_without_tests_is_rejected(self) -> None:
        """A list that names no root test module would skip nothing."""
        self.assert_list_rejected(["hooks", "scripts"], [], "no upstream test")

    def test_non_string_entry_is_rejected(self) -> None:
        """A malformed list entry is rejected."""
        with tempfile.TemporaryDirectory() as temp_root:
            root = Path(temp_root)
            (root / "tests").mkdir()
            (root / "pyproject.toml").write_text(
                "[tool.ruff]\nextend-exclude = [1]\n", encoding="utf-8"
            )
            with self.assertRaisesRegex(self.loader.UpstreamTestListError, "list of strings"):
                self.loader.load_upstream_test_paths(root)


class MatrixWiringTest(unittest.TestCase):
    """The ci workflow passes the flag in the matrix and runs gates in parallel."""

    jobs: dict[str, Any]

    @classmethod
    def setUpClass(cls) -> None:
        """Parse the ci workflow once."""
        cls.jobs = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))["jobs"]

    def test_every_matrix_pytest_step_excludes_upstream(self) -> None:
        """Both the plain and the coverage pytest steps pass the flag."""
        pytest_steps = [
            command for command in find_run_commands(self.jobs[MATRIX_JOB]) if "pytest" in command
        ]
        self.assertEqual(len(pytest_steps), 2)
        for command in pytest_steps:
            with self.subTest(command=command):
                self.assertIn(EXCLUDE_FLAG, command)

    def test_checks_job_uses_the_parallel_runner(self) -> None:
        """The checks job shards the suite instead of running it serially."""
        commands = "\n".join(find_run_commands(self.jobs[CHECKS_JOB]))
        self.assertIn(PARALLEL_RUNNER, commands)
        self.assertNotIn(SERIAL_DISCOVERY, commands)


if __name__ == "__main__":
    unittest.main()
