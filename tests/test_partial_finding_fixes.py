#!/usr/bin/env python3
"""Pin the issue 122 follow-up fix that reads the rebase state once per call."""
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
BRANCH_HOOK_PATH = REPO_ROOT / "hooks" / "enforce_branch_name.py"
INVALID_BRANCH = "not-a-valid-branch"


def load_branch_hook():
    """Load the branch hook by path under a private module name."""
    spec = importlib.util.spec_from_file_location("branch_hook_followups", BRANCH_HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RebaseLookupCountTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hook = load_branch_hook()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / ".git" / "refs" / "heads").mkdir(parents=True)
        (self.root / ".git" / "HEAD").write_text(
            f"ref: refs/heads/{INVALID_BRANCH}\n", encoding="utf-8")

    def count_rebase_lookups(self, action) -> int:
        """Run one hook action and return how often it read the rebase state."""
        calls = []
        original = self.hook.rebase_is_active

        def counting_lookup(project_dir):
            calls.append(project_dir)
            return original(project_dir)

        with patch.object(self.hook, "rebase_is_active", side_effect=counting_lookup):
            action()
        return len(calls)

    def test_denied_call_rebase_read_once(self):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Write",
                   "tool_input": {"file_path": str(self.root / "a.txt"), "content": "x"}}
        count = self.count_rebase_lookups(lambda: self.hook._handle_failed_preflight(
            payload, str(self.root), "claude", "invalid branch", INVALID_BRANCH))
        self.assertEqual(count, 1)

    def test_lifecycle_rebase_read_once(self):
        count = self.count_rebase_lookups(
            lambda: self.hook._lifecycle_message(INVALID_BRANCH, str(self.root)))
        self.assertEqual(count, 1)

    def test_unreadable_rebase_state_denies(self):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Write",
                   "tool_input": {"file_path": str(self.root / "a.txt"), "content": "x"}}
        with patch.object(self.hook, "rebase_is_active", side_effect=OSError("unreadable")):
            code = self.hook._handle_invalid_branch(
                payload, str(self.root), "claude", "invalid branch", INVALID_BRANCH)
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
