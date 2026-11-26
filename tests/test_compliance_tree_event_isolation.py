#!/usr/bin/env python3
"""Regression test for compliance tree scanning in push-event CI environments."""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SHA_HEX_LENGTH = 40
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "tests"))

try:
    from tests.retrying_temp_directory import RetryingTemporaryDirectory
except ImportError:
    from retrying_temp_directory import RetryingTemporaryDirectory

import test_immutable_compliance


def _git_environment() -> dict[str, str]:
    """Return isolated test-only Git author and configuration state."""
    environment = dict(os.environ)
    environment.update({
        "GIT_AUTHOR_NAME": "compliance test",
        "GIT_AUTHOR_EMAIL": "1234567+compliance-test@users.noreply.github.com",
        "GIT_COMMITTER_NAME": "compliance test",
        "GIT_COMMITTER_EMAIL": "1234567+compliance-test@users.noreply.github.com",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
    })
    return environment


def _run_git(repo: Path, *args: str) -> str:
    """Run Git with fixed argument boundaries and return stripped stdout."""
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
        env=_git_environment(),
    )
    return result.stdout.strip()


class ComplianceTreeEventIsolationTest(unittest.TestCase):
    """Compliance tree scanner does not fail when GITHUB_EVENT_PATH is a push event."""

    def test_scanner_push_event_payload(self) -> None:
        with RetryingTemporaryDirectory() as temporary:
            repo = Path(temporary)
            _run_git(repo, "init", "-q", "-b", "main")
            target_file = repo / ".github" / "workflows" / "unsafe.yml"
            target_file.parent.mkdir(parents=True, exist_ok=True)
            target_file.write_text(
                "on:\n  pull_request_target:\njobs:\n  unsafe:\n"
                "    permissions:\n      contents: read\n    steps:\n"
                "      - run: bash pr-head/payload.sh\n",
                encoding="utf-8",
            )
            _run_git(repo, "add", "--all")
            _run_git(repo, "commit", "-qm", "test: add unsafe workflow")
            tree_sha = _run_git(repo, "rev-parse", "HEAD")

            event_path = repo / "push_event.json"
            event_path.write_text(
                json.dumps({"ref": "refs/heads/main", "before": "0" * SHA_HEX_LENGTH}),
                encoding="utf-8",
            )

            old_event_path = os.environ.get("GITHUB_EVENT_PATH")
            old_event_name = os.environ.get("GITHUB_EVENT_NAME")
            try:
                os.environ["GITHUB_EVENT_PATH"] = str(event_path)
                os.environ["GITHUB_EVENT_NAME"] = "push"

                result = test_immutable_compliance._scan_tree(repo, tree_sha)
                output = test_immutable_compliance._scan_output(result)
                self.assertNotIn("event payload lacks pull request data", output)
                self.assertEqual(result.returncode, 1, output)
                self.assertIn(".github/workflows/unsafe.yml", output)
            finally:
                if old_event_path is None:
                    os.environ.pop("GITHUB_EVENT_PATH", None)
                else:
                    os.environ["GITHUB_EVENT_PATH"] = old_event_path
                if old_event_name is None:
                    os.environ.pop("GITHUB_EVENT_NAME", None)
                else:
                    os.environ["GITHUB_EVENT_NAME"] = old_event_name


if __name__ == "__main__":
    unittest.main()
