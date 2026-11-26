#!/usr/bin/env python3
"""One trailer parser serves the attribution and banned-agent checkers (S14)."""
import inspect
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import _commit_trailers
import check_banned_agents
import check_commit_attribution

BODIES = (
    "Subject\n\nBody text.\n\nCo-authored-by: Claude Code\nAssisted-by: Model 1\n",
    "Subject\n\nSigned-off-by: Ada <ada@example.com>\n  continued value\n\n\n",
    "Subject\n\nCo-authored-by: Grok <grok@x.ai>   \n",
    "Subject only",
    "",
)


class SharedParserTest(unittest.TestCase):
    """Both checkers read trailers through the shared module."""

    def test_checkers_share_one_parser(self):
        shared = Path(inspect.getsourcefile(_commit_trailers.terminal_trailers)).resolve()
        for function in (check_commit_attribution.terminal_trailers,
                         check_banned_agents._terminal_trailers,
                         check_banned_agents.parse_coauthor):
            with self.subTest(function=function.__name__):
                self.assertEqual(Path(inspect.getsourcefile(function)).resolve(), shared)
        self.assertEqual(check_commit_attribution.COAUTHOR_PATTERN.pattern,
                         _commit_trailers.COAUTHOR_PATTERN.pattern)

    def test_terminal_trailers(self):
        self.assertEqual(_commit_trailers.terminal_trailers(BODIES[0]),
                         [("Co-authored-by", "Claude Code"), ("Assisted-by", "Model 1")])
        self.assertEqual(_commit_trailers.terminal_trailers(BODIES[1]),
                         [("Signed-off-by", "Ada <ada@example.com>\ncontinued value")])
        self.assertEqual(_commit_trailers.terminal_trailers(BODIES[3]), [])
        self.assertEqual(_commit_trailers.terminal_trailers(BODIES[4]), [])

    def test_terminal_paragraph(self):
        self.assertEqual(_commit_trailers.terminal_paragraph(BODIES[1]),
                         ["Signed-off-by: Ada <ada@example.com>", "  continued value"])


class CoauthorParsingTest(unittest.TestCase):
    """The shared co-author parser strips the value before its strict match."""

    def test_parse_coauthor(self):
        match = _commit_trailers.parse_coauthor("  Grok <grok@x.ai>   ")
        self.assertEqual((match.group("name"), match.group("email")), ("Grok", "grok@x.ai"))
        self.assertEqual(_commit_trailers.parse_coauthor("Claude Code").group("email"), None)
        self.assertIsNone(_commit_trailers.parse_coauthor("<only@example.com>"))

    def test_padded_banned_coauthor_flagged(self):
        commit = {"sha": "a" * 40, "author_name": "Ada", "author_email": "ada@example.com",
                  "committer_name": "Ada", "committer_email": "ada@example.com",
                  "body": BODIES[2]}
        violations = check_banned_agents.find_violations([commit])
        self.assertTrue(any("co-author" in violation for violation in violations), violations)


if __name__ == "__main__":
    unittest.main()
