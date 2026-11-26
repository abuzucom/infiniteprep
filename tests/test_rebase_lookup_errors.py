#!/usr/bin/env python3
"""Pin fail-closed handling of an unreadable rebase state at each hook entry point."""
import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
BRANCH_HOOK_PATH = REPO_ROOT / "hooks" / "enforce_branch_name.py"
# A clean rebase read leaves the primary branch in the read-only state. A lookup
# error must override that state and classify the branch as invalid.
PRIMARY_BRANCH = "main"
LOOKUP_FAILURE = "metadata unreadable"


def load_branch_hook():
    """Load the branch hook by path under a private module name."""
    spec = importlib.util.spec_from_file_location("branch_hook_lookup_errors", BRANCH_HOOK_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class RebaseLookupErrorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hook = load_branch_hook()

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / ".git" / "refs" / "heads").mkdir(parents=True)
        (self.root / ".git" / "HEAD").write_text(
            f"ref: refs/heads/{PRIMARY_BRANCH}\n", encoding="utf-8")
        failing_lookup = patch.object(
            self.hook, "rebase_is_active", side_effect=OSError(LOOKUP_FAILURE))
        failing_lookup.start()
        self.addCleanup(failing_lookup.stop)

    def test_failed_lookup_named_in_denial(self):
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Read",
                   "tool_input": {"file_path": str(self.root / "a.txt")}}
        captured = io.StringIO()
        with contextlib.redirect_stdout(captured), contextlib.redirect_stderr(captured):
            exit_code = self.hook._handle_failed_preflight(
                payload, str(self.root), "claude", "read-only branch", PRIMARY_BRANCH)
        self.assertNotEqual(exit_code, 0)
        self.assertIn("rebase lookup failed", captured.getvalue())
        self.assertIn(LOOKUP_FAILURE, captured.getvalue())

    def test_lifecycle_message_omits_recovery(self):
        state, message = self.hook._lifecycle_message(PRIMARY_BRANCH, str(self.root))
        self.assertEqual(state, "invalid")
        self.assertEqual(message, "")


if __name__ == "__main__":
    unittest.main()
