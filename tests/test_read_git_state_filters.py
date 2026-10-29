#!/usr/bin/env python3
"""Tests that the status query never runs repository-configured programs."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import read_git_state
import trusted_git

TRACKED_FILE = "data.txt"


def _git(repository: Path, *arguments: str) -> None:
    """Run one fixture Git command and fail loudly on error."""
    subprocess.run(["git", "-C", str(repository), *arguments],
                   check=True, capture_output=True, text=True)


def _build_filtered_repository(root: Path, attributes_path: str,
                               driver: str) -> tuple[Path, Path]:
    """Return a repository whose dirty tracked file maps to a marker filter."""
    repository = root / "repository"
    repository.mkdir()
    marker = root / "filter-ran"
    helper = root / "clean_filter.py"
    helper.write_text(
        "import pathlib, sys\n"
        f"pathlib.Path({str(marker)!r}).write_text('ran')\n"
        "sys.stdout.write(sys.stdin.read())\n",
        encoding="utf-8",
    )
    _git(repository, "init", "--quiet")
    _git(repository, "config", "user.name", "Fixture")
    _git(repository, "config", "user.email", "fixture@example.invalid")
    (repository / TRACKED_FILE).write_text("one\n", encoding="utf-8")
    _git(repository, "add", TRACKED_FILE)
    _git(repository, "commit", "--quiet", "--message", "fixture")
    command = f'"{sys.executable}" "{helper}"'
    _git(repository, "config", f"filter.{driver}.clean", command)
    _git(repository, "config", f"filter.{driver}.required", "true")
    attributes = repository / attributes_path
    attributes.parent.mkdir(parents=True, exist_ok=True)
    attributes.write_text(f"*.txt filter={driver}\n", encoding="utf-8")
    (repository / TRACKED_FILE).write_text("two\n", encoding="utf-8")
    return repository, marker


class StatusFilterDriverTest(unittest.TestCase):
    """read_state("status") must not run clean or process filter drivers."""

    def _assert_filter_never_runs(self, attributes_path: str, driver: str):
        with tempfile.TemporaryDirectory() as temporary:
            repository, marker = _build_filtered_repository(
                Path(temporary), attributes_path, driver)
            state = read_git_state.read_state("status", repository)
            self.assertFalse(marker.exists(), "status ran a configured filter")
            self.assertTrue(state["tracked_changes"])

    def test_worktree_attributes_filter_never_runs(self):
        self._assert_filter_never_runs(".gitattributes", "probe")

    def test_info_attributes_filter_never_runs(self):
        self._assert_filter_never_runs(".git/info/attributes", "probe")

    def test_driver_name_with_dots_and_equals_never_runs(self):
        self._assert_filter_never_runs(".git/info/attributes", "a.b=c")


class StatusConfigOverrideTest(unittest.TestCase):
    """Driver overrides reach Git without replacing identity context."""

    def test_overrides_extend_forwarded_identity_pairs(self):
        captured = {}

        def runner(command, **kwargs):
            captured["env"] = kwargs["env"]
            return subprocess.CompletedProcess(command, 0, "", "")

        with tempfile.TemporaryDirectory() as temporary:
            context = {"GIT_CONFIG_COUNT": "1",
                       "GIT_CONFIG_KEY_0": "user.name",
                       "GIT_CONFIG_VALUE_0": "Fixture"}
            trusted_git.run_git(
                Path(temporary), ["status"], runner=runner,
                git_context=context,
                config_overrides=(("filter.probe.clean", ""),))
        environment = captured["env"]
        self.assertEqual(environment["GIT_CONFIG_COUNT"], "2")
        self.assertEqual(environment["GIT_CONFIG_KEY_0"], "user.name")
        self.assertEqual(environment["GIT_CONFIG_KEY_1"], "filter.probe.clean")
        self.assertEqual(environment["GIT_CONFIG_VALUE_1"], "")


class StatusTimeoutTest(unittest.TestCase):
    """A Git timeout ends in the bounded error, not a traceback."""

    def test_timeout_returns_bounded_error(self):
        def expire(*_arguments, **_keywords):
            raise subprocess.TimeoutExpired("git", read_git_state.GIT_TIMEOUT_SECONDS)

        with patch.object(read_git_state, "run_git", side_effect=expire):
            with patch("sys.stderr") as stderr:
                exit_code = read_git_state.main(["status"])
        self.assertEqual(exit_code, 1)
        written = "".join(call.args[0] for call in stderr.write.call_args_list)
        self.assertIn("Git state read failed", written)


if __name__ == "__main__":
    unittest.main()
