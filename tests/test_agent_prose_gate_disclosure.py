#!/usr/bin/env python3
"""Exercise the pull request disclosure exemption in the agent prose gate."""
import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CHECKER_PATH = ROOT / "scripts" / "check_agent_prose_gate.py"
CLEAN_TITLE = "feat: add a size report"
CLEAN_SUMMARY = "Adds a size report."
# "Opus" ends in "s" and reads as a verb to the colon clause pattern.
PLAIN_DISCLOSURE = "Assisted-by: Claude Opus 5.5"
# Placeholder identifier. The shape matches a real session link.
SESSION_URL = "https://claude.ai/code/session_00000000EXAMPLE00000000"


def load_checker():
    """Load the checker by path for isolated pure-function tests."""
    spec = importlib.util.spec_from_file_location(
        "check_agent_prose_gate_disclosure", CHECKER_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def find_body_findings(body: str) -> list[str]:
    """Return gate findings for a clean title and the given body."""
    checker = load_checker()
    return checker.find_pr_findings(
        CLEAN_TITLE, body, "octocat", checker.load_denylist(),
    )


class DisclosureExemptionTest(unittest.TestCase):
    def test_plain_disclosure_no_finding(self):
        body = f"{CLEAN_SUMMARY}\n\n{PLAIN_DISCLOSURE}\n"
        self.assertEqual(find_body_findings(body), [])

    def test_disclosure_variants_raise_no_finding(self):
        for line in (
            "**Assisted-by:** Claude Opus 5.5",
            "- Assisted-by: Claude Opus 5.5",
            "Assisted by: Claude Opus 5.5",
            "Co-authored-by: Claude",
            "Co-Authored-By: Claude Opus 5.5",
        ):
            with self.subTest(line=line):
                body = f"{CLEAN_SUMMARY}\n\n{line}\n"
                self.assertEqual(find_body_findings(body), [])

    def test_disclosure_marks_agent_attribution(self):
        checker = load_checker()
        body = f"{CLEAN_SUMMARY}\n\n{PLAIN_DISCLOSURE}\n"
        self.assertTrue(checker.is_agent_attributed([], body))

    def test_prose_after_disclosure_name(self):
        body = (
            f"{CLEAN_SUMMARY}\n\n"
            "Assisted-by: Claude Opus 5.5. They wrote this summary.\n"
        )
        findings = find_body_findings(body)
        self.assertTrue(any("personal pronoun" in item for item in findings))

    def test_prose_inside_disclosure_value(self):
        for line in (
            "Assisted-by: Claude they wrote this summary",
            "**Assisted-by:** Claude we wrote this summary",
            "Co-authored-by: Claude and they wrote it",
            "Assisted-by: Claude.They wrote this summary.",
            "Assisted-by: Claude!They wrote this summary",
        ):
            with self.subTest(line=line):
                findings = find_body_findings(f"{CLEAN_SUMMARY}\n\n{line}\n")
                self.assertTrue(any("personal pronoun" in item for item in findings))

    def test_value_finding_column(self):
        line = "Assisted-by: Claude they wrote this summary"
        findings = find_body_findings(f"{line}\n")
        column = line.index("they") + 1
        self.assertTrue(any(
            f"pull_request.body:1:{column}:" in item and "personal pronoun" in item
            for item in findings
        ))

    def test_bold_value_finding_column(self):
        for line in ("**Assisted-by:** Claude they wrote this summary",
                     "- **Assisted-by:** Claude they wrote this summary"):
            with self.subTest(line=line):
                findings = find_body_findings(f"{line}\n")
                column = line.index("they") + 1
                self.assertTrue(any(
                    f"pull_request.body:1:{column}:" in item
                    and "personal pronoun" in item
                    for item in findings
                ), findings)

    def test_masking_keeps_positions(self):
        checker = load_checker()
        for line in ("**Assisted-by:** Claude Opus 5.5",
                     "Assisted-by: Claude Opus 5.5",
                     "- **Co-authored-by:** Claude"):
            with self.subTest(line=line):
                masked = checker.mask_disclosure_lines(line)
                self.assertEqual(len(masked), len(line))
                value_start = line.index("Claude")
                self.assertEqual(masked[value_start:], line[value_start:])
                self.assertEqual(masked[:value_start].strip(), "")

    def test_other_lines_keep_findings(self):
        body = f"{PLAIN_DISCLOSURE}\n\nThey wrote this summary.\n"
        findings = find_body_findings(body)
        self.assertTrue(any(
            "pull_request.body:3:" in item and "personal pronoun" in item
            for item in findings
        ))

    def test_session_link_no_finding(self):
        for line in (
            f"Claude-Session: {SESSION_URL}",
            f"**Claude-Session:** {SESSION_URL}",
            f"- Claude-Session: {SESSION_URL}",
            SESSION_URL,
        ):
            with self.subTest(line=line):
                body = f"{CLEAN_SUMMARY}\n\n{PLAIN_DISCLOSURE}\n{line}\n"
                self.assertEqual(find_body_findings(body), [])

    def test_session_link_not_attribution(self):
        checker = load_checker()
        body = f"{CLEAN_SUMMARY}\n\nClaude-Session: {SESSION_URL}\n"
        self.assertFalse(checker.is_agent_attributed([], body))

    def test_prose_after_session_link(self):
        body = f"Claude-Session: {SESSION_URL} They wrote this summary.\n"
        findings = find_body_findings(body)
        self.assertTrue(any("personal pronoun" in item for item in findings))

    def test_other_hosts_not_session_links(self):
        line = "Claude-Session: https://example.com/code/session_01X They are here."
        findings = find_body_findings(f"{line}\n")
        self.assertTrue(any("personal pronoun" in item for item in findings))

    def test_disclosure_word_in_sentence(self):
        body = "The note says Assisted-by: The tool writes prose.\n"
        findings = find_body_findings(body)
        self.assertTrue(any("clause join" in item for item in findings))


if __name__ == "__main__":
    unittest.main()
