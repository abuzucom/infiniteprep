#!/usr/bin/env python3
"""Zone deletes route to consent through the descriptor helper only."""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_command_execution_gate import _load_gate_module
from tests.test_enforce_branch_name import bash_payload, run_hook

SESSION = "session-abc_123"
OTHER_SESSION = "session-other"
GIT_TIMEOUT_SECONDS = 15
FEATURE_BRANCH = "feat/zone-test"
HELPER = "scripts/delete_zone_path.py"


def _init_repository(root: Path) -> None:
    """Create a real Git repository with the helper script in place."""
    subprocess.run(["git", "init", "-q", str(root)], check=True, timeout=GIT_TIMEOUT_SECONDS)
    (root / "scripts").mkdir()
    (root / HELPER).write_text("", encoding="utf-8")


def helper_command(operands: str, session_id: str = SESSION) -> str:
    """Return the helper command line for the given operands."""
    return f"python {HELPER} --session {session_id} {operands}"


class ZoneVerdictTest(unittest.TestCase):
    """workflow_decision asks for own-zone helper deletes and leaves every rm unmatched."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _load_gate_module()

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        _init_repository(self.root)
        for zone in (".scratch", ".worktrees"):
            (self.root / zone / SESSION / "run1").mkdir(parents=True)
            (self.root / zone / SESSION / "a.txt").write_text("x", encoding="utf-8")
            (self.root / zone / OTHER_SESSION).mkdir(parents=True)

    def decide(self, command: str, session_id: str = SESSION) -> str:
        return self.gate.workflow_decision(command, str(self.root), session_id=session_id)[0]

    def test_own_zone_helper_asks(self) -> None:
        for operands in (f".scratch/{SESSION}/a.txt", f".scratch/{SESSION}/run1",
                         f".worktrees/{SESSION}/run1", f".worktrees/{SESSION}/a.txt",
                         f".scratch/{SESSION}/run1 .worktrees/{SESSION}/run1"):
            with self.subTest(operands=operands):
                self.assertEqual(self.decide(helper_command(operands)), "ask")

    def test_shell_rm_unmatched(self) -> None:
        for command in (f"rm .scratch/{SESSION}/a.txt", f"rm -rf .scratch/{SESSION}/run1",
                        f"rm -r .worktrees/{SESSION}/run1", f"rm -f .worktrees/{SESSION}/a.txt"):
            with self.subTest(command=command):
                self.assertEqual(self.decide(command), "")

    def test_other_targets_unmatched(self) -> None:
        for operands in (".scratch", f".scratch/{SESSION}", f".scratch/{SESSION}/run1/",
                         f".scratch/{OTHER_SESSION}/x", f".scratch/{SESSION}/../{OTHER_SESSION}",
                         f".scratch/{SESSION}/*", "hooks", f".scratch/{SESSION}/run1 hooks",
                         f"-v .scratch/{SESSION}/a.txt", f"/tmp/{SESSION}", ""):
            with self.subTest(operands=operands):
                self.assertEqual(self.decide(helper_command(operands)), "")

    def test_session_argument_must_match(self) -> None:
        operands = f".scratch/{SESSION}/run1"
        for command in (helper_command(f".scratch/{OTHER_SESSION}/x", OTHER_SESSION),
                        f"python {HELPER} {operands}",
                        f"python {HELPER} --session {operands}"):
            with self.subTest(command=command):
                self.assertEqual(self.decide(command), "")

    def test_missing_or_bad_session_unmatched(self) -> None:
        command = helper_command(f".scratch/{SESSION}/run1")
        for session_id in ("", "../x", "a b", "x" * 129):
            with self.subTest(session_id=session_id):
                self.assertEqual(self.decide(command, session_id), "")
        self.assertEqual(self.gate.workflow_decision(command, str(self.root))[0], "")

    @unittest.skipIf(os.name == "nt", "Symlink creation needs privileges on Windows")
    def test_link_leaving_zone_unmatched(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / ".scratch" / SESSION / "link").symlink_to(outside)
        self.assertEqual(self.decide(helper_command(f".scratch/{SESSION}/link/x")), "")

    def test_tracked_file_unmatched(self) -> None:
        subprocess.run(["git", "-C", str(self.root), "add", "-f", f".scratch/{SESSION}/a.txt"],
                       check=True, timeout=GIT_TIMEOUT_SECONDS)
        self.assertEqual(self.decide(helper_command(f".scratch/{SESSION}/a.txt")), "")

    def test_dot_and_empty_components_unmatched(self) -> None:
        for operands in (f".scratch/{SESSION}/./a.txt", f".scratch/{SESSION}//a.txt"):
            with self.subTest(operands=operands):
                self.assertEqual(self.decide(helper_command(operands)), "")

    def test_git_tree_unmatched(self) -> None:
        (self.root / ".scratch" / SESSION / "run1" / "sub" / ".git").mkdir(parents=True)
        for operands in (f".scratch/{SESSION}/run1", f".scratch/{SESSION}/run1/sub/.git"):
            with self.subTest(operands=operands):
                self.assertEqual(self.decide(helper_command(operands)), "")

    def test_git_entry_check_fails_closed(self) -> None:
        zones = self.gate._zones
        self.assertTrue(zones.holds_git_entry(str(self.root / ".git")))
        target = str(self.root / ".scratch" / SESSION / "run1")
        with patch.object(zones.os, "scandir", side_effect=PermissionError("denied")):
            self.assertTrue(zones.holds_git_entry(target))
        self.assertFalse(zones.holds_git_entry(target))

    def test_zone_check_refuses_empty_input(self) -> None:
        check = self.gate._zones.zone_delete_allowed
        self.assertFalse(check([], str(self.root), SESSION))
        self.assertFalse(check([f".scratch/{SESSION}/a.txt"], str(self.root), "../x"))

    def test_zone_delete_not_read_only(self) -> None:
        command = helper_command(f".scratch/{SESSION}/a.txt")
        self.assertFalse(self.gate.read_only_workflow(command, str(self.root)))


class ZoneBranchHookTest(unittest.TestCase):
    """The branch hook asks for an own-zone helper delete and denies every rm."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        _init_repository(self.root)
        (self.root / ".scratch" / SESSION / "run1").mkdir(parents=True)

    def run_command(self, command: str, branch: str = FEATURE_BRANCH, session_id=SESSION):
        payload = bash_payload(command)
        if session_id is not None:
            payload["session_id"] = session_id
        result = run_hook(payload, branch, project_dir=self.root)
        return result, json.loads(result.stdout or "{}")

    def test_own_zone_helper_asks(self) -> None:
        result, output = self.run_command(helper_command(f".scratch/{SESSION}/run1"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(output["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertIn("zone", output["hookSpecificOutput"]["permissionDecisionReason"])

    def test_other_deletes_deny(self) -> None:
        for command, session_id in ((helper_command(f".scratch/{OTHER_SESSION}"), SESSION),
                                    (helper_command(f".scratch/{SESSION}/run1"), None),
                                    (f"rm -rf .scratch/{SESSION}/run1", SESSION)):
            with self.subTest(command=command, session_id=session_id):
                result, _output = self.run_command(command, session_id=session_id)
                self.assertEqual(result.returncode, 2, result.stdout)

    def test_primary_branch_denies(self) -> None:
        command = helper_command(f".scratch/{SESSION}/run1")
        result, _output = self.run_command(command, branch="main")
        self.assertEqual(result.returncode, 2, result.stdout)


if __name__ == "__main__":
    unittest.main()
