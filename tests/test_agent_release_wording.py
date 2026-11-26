#!/usr/bin/env python3
"""Test the agent prose gate ban on release wording in commits and pull requests.

Humans publish releases manually. AGENTS.md forbids agent prose that calls a
version bump a release without active-human consent.
"""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
CHECKER_PATH = ROOT / "scripts" / "check_agent_prose_gate.py"
COMMIT_SHA_HEX_LENGTH = 40
AGENT_TRAILER = "\n\nAssisted-by: Claude Sonnet 5\n"


def load_checker():
    """Load the checker by path for isolated tests."""
    spec = importlib.util.spec_from_file_location(
        "check_agent_prose_gate_release", CHECKER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_commit(body: str) -> dict:
    """Return the commit shape that load_commits produces."""
    return {"sha": "a" * COMMIT_SHA_HEX_LENGTH, "body": body}


def write_event(path: Path, title: str, body: str) -> None:
    """Write a minimal pull_request event payload."""
    payload = {
        "pull_request": {"title": title, "body": body, "user": {"login": "octocat"}},
    }
    path.write_text(json.dumps(payload), encoding="utf-8")


class ReleaseWordingTest(unittest.TestCase):
    """find_release_wording flags release words outside code."""

    def test_release_word_flagged_with_location(self):
        checker = load_checker()
        findings = checker.find_release_wording(
            "Bump the version.\nPrepare the 2.34.0 release.", "pull_request.body",
        )
        self.assertEqual(len(findings), 1)
        self.assertIn("pull_request.body:2:", findings[0])
        self.assertIn("release wording", findings[0])

    def test_release_inflections_all_flagged(self):
        checker = load_checker()
        text = "Released it. Releasing more. Two releases. One Release."
        findings = checker.find_release_wording(text, "commit")
        self.assertEqual(len(findings), 4)

    def test_inline_code_release_ignored(self):
        checker = load_checker()
        findings = checker.find_release_wording(
            "Edit `scripts/release.py` and `gh release`.", "pull_request.body",
        )
        self.assertEqual(findings, [])

    def test_fenced_code_release_ignored(self):
        checker = load_checker()
        text = "Run this.\n```\npython scripts/release.py notes\n```\n"
        self.assertEqual(checker.find_release_wording(text, "commit"), [])

    def test_embedded_release_substring_ignored(self):
        checker = load_checker()
        findings = checker.find_release_wording("Keep releasers apart.", "commit")
        self.assertEqual(findings, [])

    def test_version_bump_wording_passes(self):
        checker = load_checker()
        findings = checker.find_release_wording(
            "Advance the version to 2.34.0.", "pull_request.body",
        )
        self.assertEqual(findings, [])


class PrReleaseFindingsTest(unittest.TestCase):
    """find_pr_findings reports release wording in the title and body."""

    def test_pr_title_release_flagged(self):
        checker = load_checker()
        findings = checker.find_pr_findings(
            "chore: release 2.34.0", "", "octocat", checker.load_denylist(),
        )
        self.assertTrue(any(
            "pull_request.title" in finding and "release wording" in finding
            for finding in findings
        ))

    def test_pr_body_release_flagged(self):
        checker = load_checker()
        findings = checker.find_pr_findings(
            "chore: bump version", "Ship the 2.34.0 release.", "octocat",
            checker.load_denylist(),
        )
        self.assertTrue(any(
            "pull_request.body" in finding and "release wording" in finding
            for finding in findings
        ))


class CommitReleaseFindingsTest(unittest.TestCase):
    """find_commit_release_findings scans agent-labeled commits only."""

    def test_agent_commit_release_flagged(self):
        checker = load_checker()
        agent_commit = build_commit("chore: release 2.34.0" + AGENT_TRAILER)
        findings = checker.find_commit_release_findings([agent_commit])
        self.assertEqual(len(findings), 1)
        self.assertIn("commit " + "a" * COMMIT_SHA_HEX_LENGTH, findings[0])

    def test_human_commit_release_ignored(self):
        checker = load_checker()
        human_commit = build_commit("chore: release 2.34.0\n")
        self.assertEqual(checker.find_commit_release_findings([human_commit]), [])


class CheckReleaseWordingTest(unittest.TestCase):
    """check() blocks agent-disclosed work that calls a bump a release."""

    def run_check(self, commits: list, title: str, body: str):
        """Run check() with a fixed commit range and no document scan."""
        checker = load_checker()
        with tempfile.TemporaryDirectory() as tmp:
            event_path = Path(tmp) / "event.json"
            write_event(event_path, title, body)
            with mock.patch.object(
                checker, "load_commits", return_value=commits,
            ), mock.patch.object(
                checker, "find_doc_findings", return_value=[],
            ), mock.patch("builtins.print"):
                return checker.check("base", "head", tmp, str(event_path))

    def test_agent_release_commit_blocks(self):
        agent_commit = build_commit("chore: release 2.34.0" + AGENT_TRAILER)
        result = self.run_check([agent_commit], "chore: bump version", "")
        self.assertEqual(result, 1)

    def test_agent_bump_commit_passes(self):
        agent_commit = build_commit("chore: bump version to 2.34.0" + AGENT_TRAILER)
        result = self.run_check([agent_commit], "chore: bump version", "")
        self.assertEqual(result, 0)

    def test_human_release_pr_passes(self):
        human_commit = build_commit("chore: release 2.34.0\n")
        result = self.run_check(
            [human_commit], "chore: release 2.34.0", "Ship the 2.34.0 release.",
        )
        self.assertEqual(result, 0)

    def test_disclosed_release_pr_blocks(self):
        human_commit = build_commit("chore: bump version\n")
        result = self.run_check(
            [human_commit], "chore: release 2.34.0", "Assisted-by: Claude Sonnet 5",
        )
        self.assertEqual(result, 1)


if __name__ == "__main__":
    unittest.main()
