#!/usr/bin/env python3
"""Issue and pull request comments through the trusted wrapper (issue #119)."""
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
USAGE_EXIT_CODE = 2
FAILURE_EXIT_CODE = 1
GRAPHQL_BLOCKED = "HTTP 403: GitHub GraphQL is not available from Claude Code sessions"
OVERSIZED_BODY_BYTES = trusted_gh.MAX_COMMENT_BYTES + 1
# Comment posting reads drafts only where os.open supports dir_fd and
# O_NOFOLLOW. Elsewhere, Windows included, the wrapper refuses posts.
SAFE_DRAFT_OPEN = trusted_gh._safe_draft_open_supported()
NO_SAFE_DRAFT_OPEN = "this platform lacks dir_fd and O_NOFOLLOW, so posting refuses"


class DraftRepository:
    """A temporary checkout root holding a .comment-drafts directory."""

    def __init__(self, test_case: unittest.TestCase):
        temporary = tempfile.TemporaryDirectory()
        test_case.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / ".git").mkdir()
        self.drafts = self.root / trusted_gh.COMMENT_DRAFT_DIRECTORY
        self.drafts.mkdir()

    def draft(self, name: str, content: bytes) -> Path:
        """Write one draft body and return its path."""
        path = self.drafts / name
        path.write_bytes(content)
        return path


def completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    """Return a fixed GitHub CLI result."""
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


class CommentTargetTest(unittest.TestCase):
    """Only issue or pr kinds and positive numbers name a comment thread."""

    def test_valid_targets(self):
        self.assertEqual(trusted_gh.comment_thread("issue", "12"), 12)
        self.assertEqual(trusted_gh.comment_thread("pr", "7"), 7)

    def test_invalid_targets(self):
        for kind, number in (("discussion", "1"), ("issue", "0"), ("issue", "-3"),
                             ("pr", "1e3"), ("pr", ""), ("issue", "01")):
            with self.subTest(kind=kind, number=number):
                with self.assertRaises(ValueError):
                    trusted_gh.comment_thread(kind, number)


class CommentBodyTest(unittest.TestCase):
    """A body file must be a plain ASCII draft under .comment-drafts/."""

    def setUp(self):
        self.repository = DraftRepository(self)

    def resolve(self, raw: str) -> Path:
        """Resolve one body argument from the repository root."""
        return trusted_gh.comment_body_path(self.repository.root, raw)

    @unittest.skipUnless(SAFE_DRAFT_OPEN, NO_SAFE_DRAFT_OPEN)
    def test_relative_draft_absolute(self):
        expected = self.repository.draft("reply.md", b"Line one.\nLine two.\n")
        self.assertEqual(self.resolve(".comment-drafts/reply.md"), expected)

    def test_file_outside_drafts_is_rejected(self):
        (self.repository.root / "notes.md").write_bytes(b"text\n")
        with self.assertRaises(ValueError):
            self.resolve("notes.md")

    def test_parent_escape_is_rejected(self):
        self.repository.draft("reply.md", b"text\n")
        with self.assertRaises(ValueError):
            self.resolve(".comment-drafts/../notes.md")

    @unittest.skipIf(os.name == "nt", "Symlink creation needs privileges on Windows")
    def test_symlinked_draft_is_rejected(self):
        target = self.repository.root / "outside.md"
        target.write_bytes(b"text\n")
        (self.repository.drafts / "link.md").symlink_to(target)
        with self.assertRaises(ValueError):
            self.resolve(".comment-drafts/link.md")

    def test_nested_draft_is_rejected(self):
        (self.repository.drafts / "sub").mkdir()
        (self.repository.drafts / "sub" / "reply.md").write_bytes(b"text\n")
        with self.assertRaises(ValueError):
            self.resolve(".comment-drafts/sub/reply.md")

    @unittest.skipIf(os.name == "nt", "Symlink creation needs privileges on Windows")
    def test_symlinked_drafts_directory_is_rejected(self):
        outside = self.repository.root / "outside"
        outside.mkdir()
        (outside / "reply.md").write_bytes(b"text\n")
        self.repository.drafts.rmdir()
        self.repository.drafts.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.resolve(".comment-drafts/reply.md")

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs need POSIX")
    def test_fifo_draft_rejected(self):
        os.mkfifo(self.repository.drafts / "pipe.md")
        with self.assertRaises(ValueError):
            self.resolve(".comment-drafts/pipe.md")

    @unittest.skipIf(os.name == "nt", "Symlink creation needs privileges on Windows")
    def test_link_swap_during_open(self):
        secret = self.repository.root / "secret.txt"
        secret.write_bytes(b"SECRET VALUE\n")
        draft = self.repository.draft("reply.md", b"Approved text.\n")
        real_open = os.open

        def swap_then_open(path, flags, *args, **kwargs):
            if str(path).endswith("reply.md") or path == "reply.md":
                draft.unlink()
                os.symlink(secret, draft)
            return real_open(path, flags, *args, **kwargs)

        for supported in (os.supports_dir_fd, set()):
            with self.subTest(dir_fd_path=bool(supported)):
                if draft.is_symlink():
                    draft.unlink()
                    draft.write_bytes(b"Approved text.\n")
                with patch.object(trusted_gh.os, "supports_dir_fd", supported), \
                        patch.object(trusted_gh.os, "open", side_effect=swap_then_open):
                    with self.assertRaises(ValueError):
                        trusted_gh.read_comment_draft(self.repository.root, ".comment-drafts/reply.md")

    def test_fallback_refuses_without_dir_fd(self):
        self.repository.draft("reply.md", b"Approved text.\n")
        with patch.object(trusted_gh.os, "supports_dir_fd", set()):
            with self.assertRaisesRegex(ValueError, "dir_fd"):
                trusted_gh.read_comment_draft(self.repository.root, ".comment-drafts/reply.md")

    def test_invalid_bodies_are_rejected(self):
        bodies = {
            "empty.md": b"",
            "accent.md": "caf\xe9\n".encode("utf-8"),
            "escape.md": b"first\\nsecond\n",
            "large.md": b"a" * OVERSIZED_BODY_BYTES,
        }
        for name, content in bodies.items():
            self.repository.draft(name, content)
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    self.resolve(f".comment-drafts/{name}")


class CommentCommandTest(unittest.TestCase):
    """The subcommands build fixed REST calls against this repository."""

    def setUp(self):
        self.repository = DraftRepository(self)
        self.calls = []
        self.inputs = []

    def run_main(self, argv: list, account_check=None,
                 response: str = "[]\n") -> tuple[int, str, str]:
        """Run main() with a recording runner and return code and streams."""
        def record(repo_root, arguments, **keywords):
            self.calls.append(arguments)
            self.inputs.append(keywords.get("input_text"))
            return completed(stdout=response)
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch.object(sys, "argv", ["trusted_gh.py", *argv]), \
                patch.object(trusted_gh.os, "getcwd", return_value=str(self.repository.root)), \
                patch.object(trusted_gh, "repository_target", return_value=REPOSITORY), \
                patch.object(trusted_gh, "verify_account", side_effect=account_check), \
                patch.object(trusted_gh, "run_gh", side_effect=record), \
                redirect_stdout(stdout), redirect_stderr(stderr):
            code = trusted_gh.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def test_comments_read_uses_rest(self):
        code, stdout, _stderr = self.run_main(["comments", "issue", "5"])
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, [[
            "api", f"repos/{REPOSITORY}/issues/5/comments?per_page=100&page=1"]])
        self.assertEqual(stdout, "[]\n")

    def test_comments_read_single_page(self):
        code, _stdout, stderr = self.run_main(["comments", "pr", "8", "--page", "3"])
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, [[
            "api", f"repos/{REPOSITORY}/issues/8/comments?per_page=100&page=3"]])
        self.assertNotIn("--page", stderr)

    def test_full_page_next_hint(self):
        full_page = json.dumps([{"id": index} for index in range(trusted_gh.COMMENT_PAGE_SIZE)])
        code, _stdout, stderr = self.run_main(
            ["comments", "issue", "5", "--page", "2"], response=full_page)
        self.assertEqual(code, 0)
        self.assertIn("--page 3", stderr)

    def test_last_page_no_hint(self):
        full_page = json.dumps([{"id": index} for index in range(trusted_gh.COMMENT_PAGE_SIZE)])
        last = str(trusted_gh.MAX_COMMENT_PAGE)
        code, _stdout, stderr = self.run_main(
            ["comments", "issue", "5", "--page", last], response=full_page)
        self.assertEqual(code, 0)
        self.assertNotIn("--page", stderr)

    def test_invalid_pages_exit(self):
        for page in ("0", "-1", "01", "1001", "x", "2.5"):
            with self.subTest(page=page):
                code, _stdout, stderr = self.run_main(["comments", "issue", "5", "--page", page])
                self.assertEqual(code, USAGE_EXIT_CODE)
                self.assertIn("error:", stderr)
        code, _stdout, _stderr = self.run_main(["comments", "issue", "5", "--limit", "2"])
        self.assertEqual(code, USAGE_EXIT_CODE)
        self.assertEqual(self.calls, [])

    @unittest.skipUnless(SAFE_DRAFT_OPEN, NO_SAFE_DRAFT_OPEN)
    def test_post_body_on_stdin(self):
        self.repository.draft("reply.md", b"Thanks.\n")
        code, _stdout, _stderr = self.run_main(
            ["comment", "pr", "9", "--body-file", ".comment-drafts/reply.md"])
        self.assertEqual(code, 0)
        self.assertEqual(self.calls, [[
            "api", "-X", "POST", f"repos/{REPOSITORY}/issues/9/comments",
            "--input", "-"]])
        self.assertEqual([json.loads(text) for text in self.inputs], [{"body": "Thanks.\n"}])

    def test_post_without_dir_fd(self):
        self.repository.draft("reply.md", b"Thanks.\n")
        with patch.object(trusted_gh.os, "supports_dir_fd", set()):
            code, _stdout, stderr = self.run_main(
                ["comment", "pr", "9", "--body-file", ".comment-drafts/reply.md"])
        self.assertEqual(code, USAGE_EXIT_CODE)
        self.assertIn("dir_fd", stderr)
        self.assertEqual(self.calls, [])

    def test_read_without_dir_fd(self):
        with patch.object(trusted_gh.os, "supports_dir_fd", set()):
            code, _stdout, _stderr = self.run_main(["comments", "issue", "5"])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.calls), 1)

    @unittest.skipIf(SAFE_DRAFT_OPEN, "this platform supports dir_fd and O_NOFOLLOW")
    def test_post_refuses_on_this_platform(self):
        self.repository.draft("reply.md", b"Thanks.\n")
        code, _stdout, stderr = self.run_main(
            ["comment", "pr", "9", "--body-file", ".comment-drafts/reply.md"])
        self.assertEqual(code, USAGE_EXIT_CODE)
        self.assertIn("dir_fd", stderr)
        self.assertEqual(self.calls, [])

    @unittest.skipUnless(SAFE_DRAFT_OPEN, NO_SAFE_DRAFT_OPEN)
    def test_post_ignores_draft_change(self):
        draft = self.repository.draft("reply.md", b"Approved text.\n")

        def replace_draft(_repo_root):
            draft.write_bytes("Swapped \xe9 text.\n".encode("utf-8"))

        code, _stdout, _stderr = self.run_main(
            ["comment", "pr", "9", "--body-file", ".comment-drafts/reply.md"],
            account_check=replace_draft)
        self.assertEqual(code, 0)
        self.assertEqual([json.loads(text) for text in self.inputs], [{"body": "Approved text.\n"}])
        self.assertNotIn(str(draft), " ".join(self.calls[0]))

    def test_usage_errors_exit(self):
        for argv in (["comments", "issue"], ["comments", "issue", "x"],
                     ["comment", "issue", "1"], ["comment", "issue", "1", "--body", "hi"],
                     ["comment", "issue", "1", "--body-file", "notes.md"]):
            with self.subTest(argv=argv):
                code, _stdout, stderr = self.run_main(argv)
                self.assertEqual(code, USAGE_EXIT_CODE)
                self.assertIn("error:", stderr)
        self.assertEqual(self.calls, [])


class RunReportingTest(unittest.TestCase):
    """run names the policy reason and the GraphQL block."""

    def run_main(self, argv: list, result=None) -> tuple[int, str]:
        """Run `trusted_gh.py run` with a fixed CLI result."""
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["trusted_gh.py", "run", *argv]), \
                patch.object(trusted_gh, "with_repository_context",
                             side_effect=lambda root, arguments: arguments), \
                patch.object(trusted_gh, "verify_account", return_value=None), \
                patch.object(trusted_gh, "run_gh", return_value=result or completed()), \
                redirect_stdout(io.StringIO()), redirect_stderr(stderr):
            code = trusted_gh.main()
        return code, stderr.getvalue()

    def test_denial_names_the_policy_reason(self):
        code, stderr = self.run_main(
            ["api", "-X", "POST", f"repos/{REPOSITORY}/issues/1/comments"])
        self.assertEqual(code, USAGE_EXIT_CODE)
        self.assertIn("gh api can mutate hosted GitHub resources", stderr)

    def test_run_gh_stdin_delivery(self):
        echo = ["-c", "import sys; sys.stdout.write(sys.stdin.read())"]
        with patch.object(trusted_gh, "resolve_gh", return_value=sys.executable), \
                patch.object(trusted_gh, "_safe_directory",
                             return_value=Path(tempfile.gettempdir())):
            result = trusted_gh.run_gh(Path(tempfile.gettempdir()), echo,
                                       input_text='{"body": "Thanks.\\n"}')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), {"body": "Thanks.\n"})

    def test_graphql_block_rest_path(self):
        code, stderr = self.run_main(
            ["issue", "view", "1"], completed(stderr=GRAPHQL_BLOCKED, returncode=1))
        self.assertEqual(code, FAILURE_EXIT_CODE)
        self.assertIn("trusted_gh.py comments", stderr)


if __name__ == "__main__":
    unittest.main()
