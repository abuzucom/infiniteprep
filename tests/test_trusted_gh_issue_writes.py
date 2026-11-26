#!/usr/bin/env python3
"""Issue body writes through the trusted wrapper over REST."""
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

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

import trusted_gh

REPOSITORY = "example-owner/example-repo"
ISSUE_URL = f"https://github.com/{REPOSITORY}/issues/5"
ISSUE_RESPONSE = json.dumps({"number": 5, "html_url": ISSUE_URL, "title": "Kept"})
DRAFT_ARGUMENT = ".comment-drafts/body.md"
DRAFT_TEXT = "First line.\nSecond line.\n"
USAGE_EXIT_CODE = 2
FAILURE_EXIT_CODE = 1
OVERSIZED_BODY_BYTES = trusted_gh.MAX_COMMENT_BYTES + 1
# Body reads use the comment draft reader. That reader needs os.open dir_fd
# and O_NOFOLLOW support and refuses elsewhere, Windows included.
SAFE_DRAFT_OPEN = trusted_gh._safe_draft_open_supported()
NO_SAFE_DRAFT_OPEN = "this platform lacks dir_fd and O_NOFOLLOW, so body reads refuse"
EDIT_ENDPOINT = ["api", "-X", "PATCH", f"repos/{REPOSITORY}/issues/5", "--input", "-"]
CREATE_ENDPOINT = ["api", "-X", "POST", f"repos/{REPOSITORY}/issues", "--input", "-"]


def completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Return a fixed GitHub CLI result."""
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class IssueWriteHarness(unittest.TestCase):
    """Run `trusted_gh.py run` against a temporary checkout with a recording CLI."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / ".git").mkdir()
        self.drafts = self.root / trusted_gh.COMMENT_DRAFT_DIRECTORY
        self.drafts.mkdir()
        (self.drafts / "body.md").write_bytes(DRAFT_TEXT.encode("ascii"))
        self.calls = []
        self.inputs = []
        self.account_checks = 0

    def run_wrapper(self, argv: list, result=None,
                    before_account_check=None) -> tuple[int, str, str]:
        """Run main() with `run` arguments and return code and both streams."""
        response = result or completed(stdout=ISSUE_RESPONSE)

        def record(_repo_root, arguments, **keywords):
            self.calls.append(list(arguments))
            self.inputs.append(keywords.get("input_text"))
            return response

        def count_account_check(_repo_root):
            if before_account_check is not None:
                before_account_check()
            self.account_checks += 1

        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["trusted_gh.py", "run", *argv]), \
                patch.object(trusted_gh.os, "getcwd", return_value=str(self.root)), \
                patch.object(trusted_gh, "repository_target", return_value=REPOSITORY), \
                patch.object(trusted_gh, "verify_account", side_effect=count_account_check), \
                patch.object(trusted_gh, "run_gh", side_effect=record), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = trusted_gh.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def sent_payloads(self) -> list:
        """Return every JSON request body sent on stdin."""
        return [json.loads(text) for text in self.inputs if text is not None]


@unittest.skipUnless(SAFE_DRAFT_OPEN, NO_SAFE_DRAFT_OPEN)
class IssueRestMappingTest(IssueWriteHarness):
    """The two approved issue shapes always run as REST requests."""

    def test_edit_body_maps_to_patch(self):
        code, stdout, _stderr = self.run_wrapper(
            ["issue", "edit", "5", "--body-file", DRAFT_ARGUMENT])
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, [EDIT_ENDPOINT])
        self.assertEqual(self.sent_payloads(), [{"body": DRAFT_TEXT}])
        self.assertEqual(stdout, ISSUE_URL + "\n")

    def test_edit_body_title_patch(self):
        code, _stdout, _stderr = self.run_wrapper(
            ["issue", "edit", "5", "--body-file", DRAFT_ARGUMENT, "--title", "New title"])
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, [EDIT_ENDPOINT])
        self.assertEqual(self.sent_payloads(), [{"title": "New title", "body": DRAFT_TEXT}])

    def test_edit_title_first_patch(self):
        code, _stdout, _stderr = self.run_wrapper(
            ["issue", "edit", "5", "--title", "New title", "--body-file", DRAFT_ARGUMENT])
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, [EDIT_ENDPOINT])
        self.assertEqual(self.sent_payloads(), [{"title": "New title", "body": DRAFT_TEXT}])

    def test_create_maps_to_post(self):
        code, stdout, _stderr = self.run_wrapper(
            ["issue", "create", "--title", "Fresh issue", "--body-file", DRAFT_ARGUMENT])
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, [CREATE_ENDPOINT])
        self.assertEqual(self.sent_payloads(), [{"title": "Fresh issue", "body": DRAFT_TEXT}])
        self.assertEqual(stdout, ISSUE_URL + "\n")

    def test_mapping_still_verifies_the_account(self):
        self.run_wrapper(["issue", "edit", "5", "--body-file", DRAFT_ARGUMENT])
        self.assertEqual(self.account_checks, 1)

    def test_rest_failure_relayed(self):
        code, stdout, stderr = self.run_wrapper(
            ["issue", "edit", "5", "--body-file", DRAFT_ARGUMENT],
            completed(stderr="HTTP 404: Not Found", returncode=1))
        self.assertEqual(code, FAILURE_EXIT_CODE)
        self.assertEqual(stdout, "")
        self.assertIn("HTTP 404", stderr)

    def test_draft_path_hidden_from_cli(self):
        self.run_wrapper(["issue", "edit", "5", "--body-file", DRAFT_ARGUMENT])
        self.assertNotIn(DRAFT_ARGUMENT, " ".join(self.calls[0]))
        self.assertNotIn("--body-file", self.calls[0])


class IssueRestExclusionTest(IssueWriteHarness):
    """Any option outside the approved shapes sends the command to gh unchanged."""

    def assert_unmapped(self, argv: list) -> None:
        """Assert one command reaches gh as an issue command with no REST mapping."""
        code, _stdout, _stderr = self.run_wrapper(argv)
        self.assertEqual(code, 0)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][:2], argv[:2])
        self.assertNotIn("api", self.calls[0])
        self.assertEqual(self.inputs, [None])

    def test_excluded_edit_options_passthrough(self):
        extras = (
            ["--add-assignee", "someone"], ["--remove-assignee", "someone"],
            ["--milestone", "v1"], ["--remove-milestone"],
            ["--add-project", "Board"], ["--remove-project", "Board"],
            ["--body", "inline"],
        )
        for extra in extras:
            with self.subTest(extra=extra):
                self.calls.clear()
                self.inputs.clear()
                self.assert_unmapped(["issue", "edit", "5", "--body-file", DRAFT_ARGUMENT, *extra])

    def test_excluded_create_options_passthrough(self):
        extras = (
            ["--assignee", "someone"], ["--milestone", "v1"], ["--project", "Board"],
            ["--template", "bug"], ["--web"],
        )
        for extra in extras:
            with self.subTest(extra=extra):
                self.calls.clear()
                self.inputs.clear()
                self.assert_unmapped(
                    ["issue", "create", "--title", "T", "--body-file", DRAFT_ARGUMENT, *extra])

    def test_stdin_body_passthrough(self):
        self.assert_unmapped(["issue", "edit", "5", "--body-file", "-"])

    def test_untitled_create_passthrough(self):
        self.assert_unmapped(["issue", "create", "--body-file", DRAFT_ARGUMENT])

    def test_explicit_repository_reaches_gh_unchanged(self):
        self.assert_unmapped(
            ["issue", "edit", "5", "--body-file", DRAFT_ARGUMENT, "--repo", REPOSITORY])

    def test_other_commands_passthrough(self):
        self.assert_unmapped(["issue", "view", "5"])


class IssueRestGateTest(IssueWriteHarness):
    """The mapping runs only after the existing gate checks pass."""

    def test_label_options_denied(self):
        for argv in (
            ["issue", "edit", "5", "--body-file", DRAFT_ARGUMENT, "--add-label", "bug"],
            ["issue", "edit", "5", "--body-file", DRAFT_ARGUMENT, "--remove-label", "bug"],
            ["issue", "create", "--title", "T", "--body-file", DRAFT_ARGUMENT, "--label", "bug"],
        ):
            with self.subTest(argv=argv):
                code, _stdout, stderr = self.run_wrapper(argv)
                self.assertEqual(code, USAGE_EXIT_CODE)
                self.assertIn("Rule 23", stderr)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.account_checks, 0)

    def test_direct_patch_denied(self):
        code, _stdout, stderr = self.run_wrapper(
            ["api", "-X", "PATCH", f"repos/{REPOSITORY}/issues/5", "--input", "-"])
        self.assertEqual(code, USAGE_EXIT_CODE)
        self.assertIn("denied by policy", stderr)
        self.assertEqual(self.calls, [])

    def test_direct_post_denied(self):
        code, _stdout, stderr = self.run_wrapper(
            ["api", "-X", "POST", f"repos/{REPOSITORY}/issues", "--input", "-"])
        self.assertEqual(code, USAGE_EXIT_CODE)
        self.assertIn("denied by policy", stderr)
        self.assertEqual(self.calls, [])


@unittest.skipUnless(SAFE_DRAFT_OPEN, NO_SAFE_DRAFT_OPEN)
class IssueBodyBoundsTest(IssueWriteHarness):
    """A mapped body follows the comment draft rules and fails closed."""

    def assert_refused(self, body_argument: str) -> None:
        """Assert one mapped edit exits 2 with no CLI call."""
        code, _stdout, stderr = self.run_wrapper(
            ["issue", "edit", "5", "--body-file", body_argument])
        self.assertEqual(code, USAGE_EXIT_CODE)
        self.assertIn("error:", stderr)
        self.assertEqual(self.calls, [])

    def test_invalid_bodies_are_refused(self):
        bodies = {
            "empty.md": b"",
            "accent.md": "café\n".encode("utf-8"),
            "escape.md": b"first\\nsecond\n",
            "large.md": b"a" * OVERSIZED_BODY_BYTES,
        }
        for name, content in bodies.items():
            (self.drafts / name).write_bytes(content)
            with self.subTest(name=name):
                self.assert_refused(f".comment-drafts/{name}")

    def test_largest_body_is_accepted(self):
        (self.drafts / "max.md").write_bytes(b"a" * trusted_gh.MAX_COMMENT_BYTES)
        code, _stdout, _stderr = self.run_wrapper(
            ["issue", "edit", "5", "--body-file", ".comment-drafts/max.md"])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.sent_payloads()[0]["body"]), trusted_gh.MAX_COMMENT_BYTES)

    def test_paths_outside_drafts_are_refused(self):
        (self.root / "notes.md").write_bytes(b"text\n")
        (self.drafts / "sub").mkdir()
        (self.drafts / "sub" / "nested.md").write_bytes(b"text\n")
        for argument in ("notes.md", ".comment-drafts/../notes.md",
                         ".comment-drafts/sub/nested.md", ".comment-drafts/missing.md"):
            with self.subTest(argument=argument):
                self.assert_refused(argument)

    @unittest.skipIf(os.name == "nt", "Symlink creation needs privileges on Windows")
    def test_symlinked_body_is_refused(self):
        target = self.root / "outside.md"
        target.write_bytes(b"text\n")
        (self.drafts / "link.md").symlink_to(target)
        self.assert_refused(".comment-drafts/link.md")

    def test_body_swap_after_validation(self):
        draft = self.drafts / "body.md"
        code, _stdout, _stderr = self.run_wrapper(
            ["issue", "edit", "5", "--body-file", DRAFT_ARGUMENT],
            before_account_check=lambda: draft.write_bytes(b"Swapped text.\n"))
        self.assertEqual(code, 0)
        self.assertEqual(self.sent_payloads(), [{"body": DRAFT_TEXT}])


if __name__ == "__main__":
    unittest.main()
