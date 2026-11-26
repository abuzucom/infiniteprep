#!/usr/bin/env python3
"""Draft pull request creation, pull request edits, and issue state changes over REST."""
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from tests.test_command_execution_gate import _load_gate_module

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

import trusted_gh

GATE = _load_gate_module()
REPOSITORY = "example-owner/example-repo"
HEAD_BRANCH = "feat/example-change"
PULL_URL = f"https://github.com/{REPOSITORY}/pull/7"
ISSUE_URL = f"https://github.com/{REPOSITORY}/issues/5"
DRAFT_ARGUMENT = ".comment-drafts/body.md"
DRAFT_TEXT = "Summary line.\nDetail line.\n"
USAGE_EXIT_CODE = 2
METADATA_FAILURE_EXIT_CODE = 1
# Body reads use the comment draft reader. That reader needs os.open dir_fd
# and O_NOFOLLOW support and refuses elsewhere, Windows included.
SAFE_DRAFT_OPEN = trusted_gh._safe_draft_open_supported()
NO_SAFE_DRAFT_OPEN = "this platform lacks dir_fd and O_NOFOLLOW, so body reads refuse"
PULLS_ENDPOINT = f"repos/{REPOSITORY}/pulls"
CREATE_PULL = ["api", "-X", "POST", PULLS_ENDPOINT, "--input", "-"]
EDIT_PULL = ["api", "-X", "PATCH", f"{PULLS_ENDPOINT}/7", "--input", "-"]
EDIT_ISSUE = ["api", "-X", "PATCH", f"repos/{REPOSITORY}/issues/5", "--input", "-"]
PR_CREATE = ["pr", "create", "--draft", "--title", "Add feature", "--body-file", DRAFT_ARGUMENT]


def completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Return a fixed GitHub CLI result."""
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class RestWriteHarness(unittest.TestCase):
    """Run `trusted_gh.py run` in a temporary checkout with a recording CLI."""

    response_url = PULL_URL

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        git_dir = self.root / ".git"
        git_dir.mkdir()
        (git_dir / "HEAD").write_text(f"ref: refs/heads/{HEAD_BRANCH}\n", encoding="utf-8")
        self.drafts = self.root / trusted_gh.COMMENT_DRAFT_DIRECTORY
        self.drafts.mkdir()
        (self.drafts / "body.md").write_bytes(DRAFT_TEXT.encode("ascii"))
        self.calls = []
        self.inputs = []

    def write_primary_branch(self, content: str) -> None:
        """Write a hooks/primary-branch.txt override."""
        hooks = self.root / "hooks"
        hooks.mkdir(exist_ok=True)
        (hooks / "primary-branch.txt").write_text(content, encoding="utf-8")

    def run_wrapper(self, argv: list, result=None) -> tuple[int, str, str]:
        """Run main() with `run` arguments and return code and both streams."""
        response = result or completed(stdout=json.dumps({"html_url": self.response_url}))

        def record(_repo_root, arguments, **keywords):
            self.calls.append(list(arguments))
            self.inputs.append(keywords.get("input_text"))
            return response

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["trusted_gh.py", "run", *argv]), \
                patch.object(trusted_gh.os, "getcwd", return_value=str(self.root)), \
                patch.object(trusted_gh, "repository_target", return_value=REPOSITORY), \
                patch.object(trusted_gh, "verify_account", return_value=None), \
                patch.object(trusted_gh, "run_gh", side_effect=record), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = trusted_gh.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def sent_payloads(self) -> list:
        """Return every JSON request body sent on stdin."""
        return [json.loads(text) for text in self.inputs if text is not None]

    def assert_unmapped(self, argv: list) -> None:
        """Assert one command reaches gh as written, with no REST mapping."""
        code, _stdout, _stderr = self.run_wrapper(argv)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][:2], argv[:2])
        self.assertNotIn("api", self.calls[0])
        self.assertEqual(self.inputs, [None])

    def assert_refused(self, argv: list) -> None:
        """Assert one command exits 2 with no CLI call."""
        code, _stdout, stderr = self.run_wrapper(argv)
        self.assertEqual(code, USAGE_EXIT_CODE)
        self.assertIn("error:", stderr)
        self.assertEqual(self.calls, [])


@unittest.skipUnless(SAFE_DRAFT_OPEN, NO_SAFE_DRAFT_OPEN)
class DraftPullCreateTest(RestWriteHarness):
    """`pr create --draft` with a title and a body file runs as a REST POST."""

    def test_create_default_base(self):
        code, stdout, _stderr = self.run_wrapper(PR_CREATE)
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, [CREATE_PULL])
        self.assertEqual(self.sent_payloads(), [{
            "title": "Add feature", "body": DRAFT_TEXT, "head": HEAD_BRANCH,
            "base": "main", "draft": True}])
        self.assertEqual(stdout, PULL_URL + "\n")

    def test_explicit_base_is_used(self):
        self.assert_refused([*PR_CREATE, "--base", "release-line"])

    def test_option_order_does_not_matter(self):
        argv = ["pr", "create", "--body-file", DRAFT_ARGUMENT, "--base", "main",
                "--title", "Add feature", "--draft"]
        code, _stdout, _stderr = self.run_wrapper(argv)
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, [CREATE_PULL])

    def test_primary_branch_file_base(self):
        self.write_primary_branch("trunk\n")
        code, _stdout, _stderr = self.run_wrapper(PR_CREATE)
        self.assertEqual(code, 0)
        self.assertEqual(self.sent_payloads()[0]["base"], "trunk")

    def test_invalid_primary_branch_file(self):
        for content in ("master\n", "HEAD\n", "one\ntwo\n", "bad name\n", ""):
            with self.subTest(content=content):
                self.calls.clear()
                self.write_primary_branch(content)
                self.assert_refused(PR_CREATE)

    def test_invalid_base_is_refused(self):
        for base in ("../main", "bad name", "-main", "a//b"):
            with self.subTest(base=base):
                self.calls.clear()
                self.assert_refused([*PR_CREATE, "--base", base])

    def test_detached_head_is_refused(self):
        # Repository context for any `pr create` reads the branch first and
        # fails there, before the REST mapping runs.
        (self.root / ".git" / "HEAD").write_text("0" * 40 + "\n", encoding="utf-8")
        code, _stdout, stderr = self.run_wrapper(PR_CREATE)
        self.assertEqual(code, METADATA_FAILURE_EXIT_CODE)
        self.assertIn("error:", stderr)
        self.assertEqual(self.calls, [])

    def test_refused_body_file_exits_2(self):
        self.assert_refused(["pr", "create", "--draft", "--title", "T",
                             "--body-file", "notes.md"])


class DraftPullCreateExclusionTest(RestWriteHarness):
    """Other `pr create` shapes reach gh unchanged or stay denied."""

    def test_non_draft_passthrough(self):
        self.assert_unmapped(["pr", "create", "--title", "T", "--body-file", DRAFT_ARGUMENT])

    def test_untitled_passthrough(self):
        self.assert_unmapped(["pr", "create", "--draft", "--body-file", DRAFT_ARGUMENT])

    def test_other_options_passthrough(self):
        for extra in (["--fill"], ["--web"], ["--reviewer", "someone"],
                      ["--assignee", "someone"], ["--head", "other"], ["--body", "x"]):
            with self.subTest(extra=extra):
                self.calls.clear()
                self.inputs.clear()
                self.assert_unmapped([*PR_CREATE, *extra])

    def test_stdin_body_passthrough(self):
        self.assert_unmapped(["pr", "create", "--draft", "--title", "T", "--body-file", "-"])

    def test_create_with_label_stays_denied(self):
        code, _stdout, stderr = self.run_wrapper([*PR_CREATE, "--label", "bug"])
        self.assertEqual(code, USAGE_EXIT_CODE)
        self.assertIn("Rule 23", stderr)
        self.assertEqual(self.calls, [])


@unittest.skipUnless(SAFE_DRAFT_OPEN, NO_SAFE_DRAFT_OPEN)
class PullEditTest(RestWriteHarness):
    """`pr edit <n>` with a body file and an optional title runs as a REST PATCH."""

    def test_edit_body_maps_to_patch(self):
        code, stdout, _stderr = self.run_wrapper(
            ["pr", "edit", "7", "--body-file", DRAFT_ARGUMENT])
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, [EDIT_PULL])
        self.assertEqual(self.sent_payloads(), [{"body": DRAFT_TEXT}])
        self.assertEqual(stdout, PULL_URL + "\n")

    def test_edit_body_and_title(self):
        code, _stdout, _stderr = self.run_wrapper(
            ["pr", "edit", "7", "--title", "New title", "--body-file", DRAFT_ARGUMENT])
        self.assertEqual(code, 0)
        self.assertEqual(self.sent_payloads(), [{"title": "New title", "body": DRAFT_TEXT}])

    def test_refused_body_file_exits_2(self):
        self.assert_refused(["pr", "edit", "7", "--body-file", "notes.md"])


class PullEditExclusionTest(RestWriteHarness):
    """Other `pr edit` shapes reach gh unchanged or stay denied."""

    def test_other_options_passthrough(self):
        for extra in (["--base", "main"], ["--add-reviewer", "someone"],
                      ["--milestone", "v1"], ["--add-assignee", "someone"]):
            with self.subTest(extra=extra):
                self.calls.clear()
                self.inputs.clear()
                self.assert_unmapped(["pr", "edit", "7", "--body-file", DRAFT_ARGUMENT, *extra])

    def test_bodyless_edit_passthrough(self):
        self.assert_unmapped(["pr", "edit", "7", "--title", "New title"])

    def test_edit_with_label_stays_denied(self):
        code, _stdout, stderr = self.run_wrapper(
            ["pr", "edit", "7", "--body-file", DRAFT_ARGUMENT, "--add-label", "bug"])
        self.assertEqual(code, USAGE_EXIT_CODE)
        self.assertIn("Rule 23", stderr)
        self.assertEqual(self.calls, [])


class IssueStateTest(RestWriteHarness):
    """`issue close` and `issue reopen` run as REST PATCH requests."""

    response_url = ISSUE_URL

    def test_close_maps_to_patch(self):
        code, stdout, _stderr = self.run_wrapper(["issue", "close", "5"])
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, [EDIT_ISSUE])
        self.assertEqual(self.sent_payloads(), [{"state": "closed"}])
        self.assertEqual(stdout, ISSUE_URL + "\n")

    def test_close_reason_mapping(self):
        for reason in ("completed", "not_planned"):
            with self.subTest(reason=reason):
                self.calls.clear()
                self.inputs.clear()
                self.run_wrapper(["issue", "close", "5", "--reason", reason])
                self.assertEqual(self.sent_payloads(),
                                 [{"state": "closed", "state_reason": reason}])

    def test_reopen_maps_to_patch(self):
        code, _stdout, _stderr = self.run_wrapper(["issue", "reopen", "5"])
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, [EDIT_ISSUE])
        self.assertEqual(self.sent_payloads(), [{"state": "open"}])

    def test_other_state_shapes_passthrough(self):
        for argv in (["issue", "close", "5", "--reason", "duplicate"],
                     ["issue", "close", "5", "--comment", "done"],
                     ["issue", "close", "5", "--reason"],
                     ["issue", "reopen", "5", "--comment", "again"],
                     ["issue", "close", "01"]):
            with self.subTest(argv=argv):
                self.calls.clear()
                self.inputs.clear()
                self.assert_unmapped(argv)


class RestWriteGateVerdictTest(unittest.TestCase):
    """The command gate keeps its ask verdict for every mapped form."""

    def test_mapped_forms_ask(self):
        project = str(REPOSITORY_ROOT)
        for argv in (PR_CREATE,
                     ["pr", "edit", "7", "--body-file", DRAFT_ARGUMENT],
                     ["issue", "close", "5", "--reason", "completed"],
                     ["issue", "reopen", "5"]):
            command = "python scripts/trusted_gh.py run " + " ".join(argv)
            with self.subTest(command=command):
                decision, _reason = GATE.workflow_decision(command, project)
                self.assertEqual(decision, "ask")


class PrimaryBranchReaderTest(unittest.TestCase):
    """The wrapper reads the primary branch override like the branch gate."""

    def test_reader_follows_the_override_rules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual(trusted_gh.primary_branch_name(root), "main")
            (root / "hooks").mkdir()
            override = root / "hooks" / "primary-branch.txt"
            override.write_text("trunk\n", encoding="utf-8")
            self.assertEqual(trusted_gh.primary_branch_name(root), "trunk")
            override.write_text("master\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                trusted_gh.primary_branch_name(root)

    @unittest.skipIf(os.name == "nt", "Symlink creation needs privileges on Windows")
    def test_linked_override_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "hooks").mkdir()
            target = root / "elsewhere.txt"
            target.write_text("trunk\n", encoding="utf-8")
            (root / "hooks" / "primary-branch.txt").symlink_to(target)
            with self.assertRaises(ValueError):
                trusted_gh.primary_branch_name(root)


if __name__ == "__main__":
    unittest.main()
