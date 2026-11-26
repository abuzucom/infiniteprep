#!/usr/bin/env python3
"""Commit message scans fail closed past their commit cap (S-14)."""
import io
import subprocess
import sys
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_commit_message
import check_external_pr_refs

CAP = check_commit_message.MAX_COMMIT_COUNT


def git_log_output(count: int) -> str:
    """Return git log output in the loaders' record format for `count` commits."""
    field = check_commit_message.FIELD_SEPARATOR
    record = check_commit_message.RECORD_SEPARATOR
    return "".join(f"{index:040x}{field}fix: change {index}{field}{record}\n"
                   for index in range(1, count + 1))


def fake_run_git(count: int, calls: list):
    """Return a run_git stand-in that answers with `count` commits."""
    def run(repo, arguments, **_options):
        calls.append(arguments)
        stdout = "origin\n" if arguments == ["remote"] else git_log_output(count)
        return subprocess.CompletedProcess(arguments, 0, stdout, "")
    return run


class CommitCapTest(unittest.TestCase):
    """The loaders request one commit past the cap and refuse a larger range."""

    def test_range_at_cap_loads(self):
        calls = []
        with patch.object(check_commit_message, "run_git", side_effect=fake_run_git(CAP, calls)):
            messages = check_commit_message.load_commit_messages("a" * 40, "b" * 40, ".")
        self.assertEqual(len(messages), CAP)
        self.assertIn(f"--max-count={CAP + 1}", calls[0])

    def test_range_past_cap_fails_closed(self):
        with patch.object(check_commit_message, "run_git",
                          side_effect=fake_run_git(CAP + 1, [])):
            with self.assertRaisesRegex(ValueError, f"{CAP}-commit scan limit"):
                check_commit_message.load_commit_messages("a" * 40, "b" * 40, ".")
            with self.assertRaisesRegex(ValueError, f"{CAP}-commit scan limit"):
                check_commit_message.load_unpushed_messages(".")

    def test_reference_checker_past_cap(self):
        stderr = io.StringIO()
        # The reference checker may import check_commit_message through the
        # scripts package, so patch the module object it actually holds.
        loader_module = check_external_pr_refs.check_commit_message
        with patch.object(loader_module, "run_git",
                          side_effect=fake_run_git(CAP + 1, [])), redirect_stderr(stderr):
            code = check_external_pr_refs.check_range("a" * 40, "b" * 40, "example-owner", ".")
        self.assertEqual(code, 1)
        self.assertIn("scan limit", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
