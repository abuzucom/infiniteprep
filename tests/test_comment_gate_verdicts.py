#!/usr/bin/env python3
"""Gate verdicts for the trusted wrapper comment subcommands (issue #119).

Each class stays small because the hook coverage runner gives every test
class one shard with a fixed time limit.
"""
import unittest

from tests.read_only_branch_support import DETACHED_HEAD, BranchFixture, decision
from tests.test_command_execution_gate import _load_gate_module
from tests.test_enforce_branch_name import REPO_ROOT

PROJECT_DIR = str(REPO_ROOT)
READ_COMMAND = "python scripts/trusted_gh.py comments issue 5"
POST_COMMAND = "python scripts/trusted_gh.py comment pr 9 --body-file .comment-drafts/reply.md"
UNMATCHED_COMMANDS = (
    "python scripts/trusted_gh.py comments discussion 5",
    "python scripts/trusted_gh.py comments issue five",
    "python scripts/trusted_gh.py comments issue 5 extra",
    "python scripts/trusted_gh.py comment pr 9 --body-file notes.md",
    "python scripts/trusted_gh.py comment pr 9 --body-file /tmp/reply.md",
    "python scripts/trusted_gh.py comment pr 9 --body-file .comment-drafts/../x.md",
    "python scripts/trusted_gh.py comment pr 9 --body hi",
)
OPAQUE_REASON = "Opaque command execution requires an inspectable operation"


class CommentWorkflowVerdictTest(unittest.TestCase):
    """Reads pass, posts ask, and other shapes stay unmatched."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _load_gate_module()

    def test_read_allowed(self) -> None:
        self.assertEqual(self.gate.workflow_decision(READ_COMMAND, PROJECT_DIR), ("allow", ""))
        self.assertTrue(self.gate.read_only_workflow(READ_COMMAND, PROJECT_DIR))

    def test_post_asks_and_writes(self) -> None:
        self.assertEqual(self.gate.workflow_decision(POST_COMMAND, PROJECT_DIR)[0], "ask")
        self.assertFalse(self.gate.read_only_workflow(POST_COMMAND, PROJECT_DIR))

    def test_other_shapes_stay_unmatched(self) -> None:
        for command in UNMATCHED_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(self.gate.workflow_decision(command, PROJECT_DIR)[0], "")


class CommentPageVerdictTest(unittest.TestCase):
    """A comment read may name one bounded page."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _load_gate_module()

    def test_bounded_page_allowed(self) -> None:
        for page in ("1", "42", "1000"):
            command = f"{READ_COMMAND} --page {page}"
            with self.subTest(page=page):
                self.assertEqual(self.gate.workflow_decision(command, PROJECT_DIR), ("allow", ""))
                self.assertTrue(self.gate.read_only_workflow(command, PROJECT_DIR))

    def test_other_page_shapes_stay_unmatched(self) -> None:
        for suffix in ("--page 0", "--page 01", "--page 1001", "--page x", "--page",
                       "--page 2 --page 3", "--limit 5"):
            command = f"{READ_COMMAND} {suffix}"
            with self.subTest(suffix=suffix):
                self.assertEqual(self.gate.workflow_decision(command, PROJECT_DIR)[0], "")


class OpaqueReasonNamesProgramTest(unittest.TestCase):
    """An opaque refusal names the rejected program."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _load_gate_module()

    def test_reason_names_program(self) -> None:
        reason = self.gate.segment_program_reason(["node", "script.js"])
        self.assertIn(OPAQUE_REASON, reason)
        self.assertIn("(rejected: node)", reason)


class DetachedHeadCommentTest(unittest.TestCase):
    """A detached HEAD allows comment reads and refuses comment posts."""

    def test_read_allowed_post_denied(self) -> None:
        fixture = BranchFixture(self, DETACHED_HEAD)
        read = fixture.shell(READ_COMMAND)
        self.assertEqual(decision(read), "allow", read.stderr)
        post = fixture.shell(POST_COMMAND)
        self.assertEqual(decision(post), "deny", post.stderr)


if __name__ == "__main__":
    unittest.main()
