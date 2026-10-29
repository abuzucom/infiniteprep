#!/usr/bin/env python3
"""The command gate routes every spelling of the trusted scripts to consent."""
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

from tests.test_command_execution_gate import REPO_ROOT, _load_gate_module

sys.path.insert(0, str(REPO_ROOT / "scripts"))

import trusted_git

GATE = _load_gate_module()
PROJECT_DIR = str(REPO_ROOT)

CONSENT_COMMANDS = (
    "python scripts/trusted_git.py fetch . origin main",
    "python3 scripts/trusted_git.py fetch .",
    "python scripts/trusted_git.py clone https://github.com/abuzucom/euler euler-copy",
    "python scripts/trusted_gh.py run pr list",
    "python ./scripts/trusted_gh.py run pr list",
    "python -I scripts/trusted_gh.py run pr list",
    "python3.12 scripts/trusted_gh.py run pr list",
    "py scripts/trusted_gh.py run pr list",
    "python ./scripts/read_git_state.py branch",
    "python3 -I ./scripts/trusted_git.py fetch .",
    "python scripts/../scripts/trusted_git.py fetch .",
)
UNMATCHED_COMMANDS = (
    "python scripts/trusted_git.py push origin main",
    "python scripts/trusted_git.py",
    "python -c pass scripts/trusted_gh.py run pr list",
    "python -m trusted_gh run pr list",
    "python scripts/trusted_gh.py run repo delete OWNER/REPO",
    "python3.x scripts/trusted_gh.py run pr list",
    "python ../scripts/trusted_gh.py run pr list",
    "python -I -u",
)
READ_ONLY_COMMANDS = (
    "python ./scripts/read_git_state.py branch",
    "python -I scripts/read_git_state.py status",
)


class TrustedScriptRecognitionTest(unittest.TestCase):
    """Equivalent invocations of one trusted script get one verdict."""

    def test_equivalent_spellings_route_to_consent(self) -> None:
        for command in CONSENT_COMMANDS:
            with self.subTest(command=command):
                decision, _reason = GATE.workflow_decision(command, PROJECT_DIR)
                self.assertEqual(decision, "ask")

    def test_other_shapes_stay_unmatched(self) -> None:
        for command in UNMATCHED_COMMANDS:
            with self.subTest(command=command):
                decision, _reason = GATE.workflow_decision(command, PROJECT_DIR)
                self.assertEqual(decision, "")

    def test_read_only_spellings_stay_read_only(self) -> None:
        for command in READ_ONLY_COMMANDS:
            with self.subTest(command=command):
                self.assertTrue(GATE.read_only_workflow(command, PROJECT_DIR))

    def test_trusted_git_fetch_is_not_read_only(self) -> None:
        self.assertFalse(GATE.read_only_workflow(
            "python scripts/trusted_git.py fetch . origin", PROJECT_DIR))


def _can_symlink() -> bool:
    """Return whether the current platform and user can create symlinks."""
    try:
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary) / "target"
            target.write_text("x", encoding="utf-8")
            (Path(temporary) / "link").symlink_to(target)
    except (OSError, NotImplementedError):
        return False
    return True


CAN_SYMLINK = _can_symlink()


class TrustedScriptSymlinkTest(unittest.TestCase):
    """A trusted script name that resolves outside the project never matches."""

    @unittest.skipUnless(CAN_SYMLINK, "Symlinks require elevated privileges on Windows")
    def test_symlink_to_outside_script_stays_unmatched(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project = root / "project"
            (project / "scripts").mkdir(parents=True)
            outside = root / "outside.py"
            outside.write_text("", encoding="utf-8")
            (project / "scripts" / "trusted_git.py").symlink_to(outside)
            for command in ("python scripts/trusted_git.py fetch .",
                            "python scripts/trusted_gh.py run pr list"):
                with self.subTest(command=command):
                    decision, _reason = GATE.workflow_decision(command, str(project))
                    self.assertEqual(decision, "")

    @unittest.skipUnless(CAN_SYMLINK, "Symlinks require elevated privileges on Windows")
    def test_symlinked_project_root_still_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            project = root / "project"
            (project / "scripts").mkdir(parents=True)
            (project / "scripts" / "trusted_git.py").write_text("", encoding="utf-8")
            linked = root / "linked"
            linked.symlink_to(project, target_is_directory=True)
            decision, _reason = GATE.workflow_decision(
                "python scripts/trusted_git.py fetch .", str(linked))
            self.assertEqual(decision, "ask")


class TrustedGitMainErrorTest(unittest.TestCase):
    """Expected runner failures end in a message and exit code, not a traceback."""

    def _run_main_with(self, error: Exception) -> tuple[int, str]:
        stderr = io.StringIO()
        with patch.object(trusted_git, "run_git", side_effect=error):
            with patch.object(trusted_git.Path, "cwd", return_value=REPO_ROOT):
                with redirect_stderr(stderr):
                    exit_code = trusted_git.main(["fetch", "."])
        return exit_code, stderr.getvalue()

    def test_missing_git_reports_failure(self) -> None:
        exit_code, message = self._run_main_with(
            FileNotFoundError("trusted Git executable was not found on PATH"))
        self.assertEqual(exit_code, trusted_git.RUNNER_FAILURE)
        self.assertIn("trusted Git executable was not found on PATH", message)

    def test_missing_safe_directory_reports_failure(self) -> None:
        exit_code, message = self._run_main_with(
            OSError("no safe external directory is available for Git execution"))
        self.assertEqual(exit_code, trusted_git.RUNNER_FAILURE)
        self.assertIn("no safe external directory", message)


if __name__ == "__main__":
    unittest.main()
