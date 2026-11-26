#!/usr/bin/env python3
"""Test delegation detection in agent output."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_anti_delegation as checker


class AntiDelegationTest(unittest.TestCase):
    """Detect and block delegating recovery commands or manual actions."""

    def test_delegating_shell_command_detected(self) -> None:
        cases = (
            "Please run the following command in the terminal: npm test",
            "You should run the build script in bash.",
            "Please type the following in the command prompt.",
        )
        for case in cases:
            with self.subTest(case=case):
                findings = checker.scan_text_for_delegation(case)
                self.assertTrue(findings, f"Failed to detect delegation: {case}")
                self.assertEqual(findings[0].category, "command_delegation")

    def test_delegating_git_recovery_detected(self) -> None:
        cases = (
            "Please run git merge --abort to resolve the conflict.",
            "Run git rebase --abort for the active human.",
            "Please execute `git branch -m fix/compliant-branch`.",
            "Ask the active human to run git checkout main.",
        )
        for case in cases:
            with self.subTest(case=case):
                findings = checker.scan_text_for_delegation(case)
                self.assertTrue(findings, f"Failed to detect git delegation: {case}")
                self.assertEqual(findings[0].category, "git_recovery_delegation")

    def test_delegating_manual_recovery_detected(self) -> None:
        cases = (
            "Please perform manual recovery on the corrupted repository.",
            "The user must manually recover the git state.",
            "Delegate the recovery action to the human.",
            "Ask the user to run recovery manually.",
        )
        for case in cases:
            with self.subTest(case=case):
                findings = checker.scan_text_for_delegation(case)
                self.assertTrue(findings, f"Failed to detect manual recovery: {case}")
                self.assertEqual(findings[0].category, "manual_recovery_delegation")

    def test_policy_statements_pass(self) -> None:
        benign_cases = (
            "Never ask the user to run recovery commands.",
            "Do not delegate recovery commands to the human.",
            "Policy prohibits delegating recovery actions to humans.",
            "# The agent must run git merge --abort directly if authorized.",
            "Strict branch preflight passed on branch fix/compaction-transcript-bug.",
            "The script executes git commands autonomously after consent.",
            "Run the focused test once in an elevated PowerShell.",
            "You can review the diff and report the result.",
        )
        for case in benign_cases:
            with self.subTest(case=case):
                findings = checker.scan_text_for_delegation(case)
                self.assertEqual(findings, [], f"False positive for benign text: {case}")

    def test_directive_without_payload_passes(self) -> None:
        findings = checker.scan_text_for_delegation(
            "Please review the shell script before the next step."
        )
        self.assertEqual(findings, [])

    def test_cli_reports_findings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sample = Path(directory) / "delegation.txt"
            sample.write_text(
                "Please run git merge --abort to clear the conflict.\n",
                encoding="utf-8",
            )
            self.assertEqual(checker.main([str(sample)]), 1)
            self.assertEqual(checker.main([str(sample), "--json"]), 1)

    def test_cli_reports_clean_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sample = Path(directory) / "clean.txt"
            sample.write_text(
                "Policy forbids delegating commands to humans.\n"
                "Strict branch preflight passed.\n",
                encoding="utf-8",
            )
            self.assertEqual(checker.main([str(sample)]), 0)

    def test_cli_reports_missing_file(self) -> None:
        self.assertEqual(checker.main(["no_such_file.txt"]), 2)

    def test_cli_scans_every_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            clean = Path(directory) / "clean.txt"
            clean.write_text("Strict branch preflight passed.\n", encoding="utf-8")
            flagged = Path(directory) / "flagged.txt"
            flagged.write_text("Please run git merge --abort now.\n", encoding="utf-8")
            self.assertEqual(checker.main([str(clean), str(flagged)]), 1)
            self.assertEqual(checker.main([str(clean)]), 0)

    def test_json_output_shape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            sample = Path(directory) / "delegation.txt"
            sample.write_text("Please run git rebase --abort now.\n", encoding="utf-8")
            findings = checker.scan_text_for_delegation(
                sample.read_text(encoding="utf-8")
            )
            payload = [vars(finding) for finding in findings]
            self.assertEqual(payload[0]["category"], "git_recovery_delegation")
            self.assertEqual(payload[0]["line_number"], 1)
            self.assertIsInstance(json.dumps(payload), str)


if __name__ == "__main__":
    unittest.main()
