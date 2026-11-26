#!/usr/bin/env python3
"""Cover consolidated prose policy checks and category ownership."""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REDOS_TIMEOUT_SECONDS = 5
REDOS_TOKEN_COUNT = 28
PRONOUN_FINDING_COUNT = 3
NEWLINE_ESCAPE_FINDING_COUNT = 2
CARRIAGE_RETURN_AND_TAB_FINDING_COUNT = 2

sys.path.insert(0, str(ROOT / "scripts"))
import prose_policy


def findings(text: str, path: str = "sample.md") -> list:
    """Return policy findings for fixed sample input."""
    return prose_policy.find_violations(text, path)


class VocabularyTest(unittest.TestCase):
    """Exact vocabulary checks scan raw configured content."""

    def test_configured_entry_scopes(self):
        entries = prose_policy.load_denylist()
        self.assertEqual(
            [entry.text for entry in entries],
            [
                "worth noting",
                "worst",
                "serious",
                "testament",
                "serves as",
                "stands as",
                "functions as",
                "has the ability to",
                "utilize",
                "never held",
                "never holds",
                "never ran",
                "never delete",
                "never deletes",
                "never follows",
                "never saw",
                "never reach",
                "never reaches",
                "never runs",
                "never read",
                "never reads",
                "the owner",
                "that owner",
                "the origin owner",
                "does not run",
                "pull-request",
                "banned-phrase",
                "arguably",
                "innocuous",
                "rather than",
                "not parse",
                "must reach",
                ", never",
                "never write",
                "summarizes",
                "file-transfer",
                "never excuse",
                "blocks every",
                "target-directory",
                "editor-tool",
                "hard-link",
                "hard-linked",
                "protected-path",
                "input-error",
                "repository-discovery",
                "empty-segment",
                "permission-mode",
                "process-entrypoint",
                "source-repository",
                "default-branch",
                "detatched-head",
                "detached-head",
                "waiving",
                "whose",
                "live here",
                "cannot parse",
                "denies before",
                "asked about",
                "tokenize",
                "waved through",
                "speaks for",
                "restated here",
                "narrows the window",
                "of raising",
                "than raising",
                "then raising",
                ", instead",
                "feeding it",
                "instead of here",
                "raises no exception",
                "re-entry",
                "assume-unchanged",
                "tool-agnostic",
                "names the cause",
                "skip-worktree",
                "path-generic",
                "textual",
                "textually",
                "mid-file",
                "meanwhile",
                "comes too late",
                "is synced",
                "carve-out",
                "verbatim",
                "marker-led",
                "invokes",
                "happily",
                "genuine",
                "genuinely",
                "honestly",
                "essentially",
                "effectively",
                "simply",
                "fundamentally",
                "note that",
                "in order to",
                "in practice",
                "leverage",
                "leverages",
                "leveraged",
                "leveraging",
                "seamless",
                "seamlessly",
                "delve",
                "delves",
                "delved",
                "delving",
                "load-bearing",
                "best-effort",
                "silently",
                "deliberate",
                "deliberately",
                "comprehensive",
                "dual-valid",
                "avant-garde",
                "captivating",
                "crucial",
                "bustling",
                "distinguished",
                "esteemed",
                "exquisite",
                "formidable",
                "game-changing",
                "groundbreaking",
                "holistic",
                "iconic",
                "indomitable",
                "irrefutable",
                "meticulous",
                "multifaceted",
                "omniscient",
                "paradigm-shifting",
                "paramount",
                "pioneering",
                "pivotal",
                "predominant",
                "profound",
                "prominent",
                "quintessential",
                "revolutionary",
                "tangible",
                "trailblazing",
                "ubiquitous",
                "unassailable",
                "unblemished",
                "unequaled",
                "unparalleled",
                "unprecedented",
                "unrivaled",
                "unsurpassed",
                "unwavering",
                "unyielding",
                "visionary",
                "apogee",
                "epitome",
                "facet",
                "gusto",
                "journey",
                "landscape",
                "nestled",
                "nexus",
                "odyssey",
                "panorama",
                "paradigm",
                "pinnacle",
                "spectrum",
                "symphony",
                "tapestry",
                "trajectory",
                "dive",
                "elevate",
                "embark",
                "embrace",
                "emerge",
                "ensure",
                "envision",
                "foster",
                "galvanize",
                "orchestrate",
                "redefine",
                "reinforce",
                "streamline",
                "transcend",
                "unleash",
                "usher",
                "agent-attributed",
                "agent-authored",
                "agent-generated",
                "agent-initiated",
                "human-authored",
                "human-approved",
                "user-approved",
                "harness-assigned",
                "hook-delivered",
                "signer-supplied",
                "consent-required",
                "consent-gated",
                "consent-routed",
                "label-gated",
                "non-denied",
                "non-banned",
                "branch-exempt",
                "handoff-exempt",
                "name-only",
                "regex-only",
                "repository-only",
                "source-only",
                "adopter-only",
                "docs-only",
                "example-only",
                "warning-only",
                "push-only",
                "repository-bound",
                "repository-controlled",
                "repository-configured",
                "repository-contained",
                "repository-defined",
                "repository-resolved",
                "schema-safe",
                "injection-safe",
                "value-free",
                "name-free",
                "shell-free",
                "length-bounded",
                "size-bound",
                "target-scoped",
                "outward-facing",
                "state-changing",
                "privilege-changing",
                "email-bearing",
                "NUL-bearing",
                "test-shaped",
                "test-controlled",
                "base-defined",
                "root-targeted",
                "root-targeting",
                "commit-writing",
                "draft-skipping",
                "line-marking",
                "class-sharded",
                "tracer-unreached",
                "host-qualified",
                "license-required",
                "placeholder-named",
                "stop-and-ask",
                "line-ending-normalized",
                "non-pre-release",
                "non-dry-run",
                "writeability",
                "raises before",
                "raises after",
                "as no",
                "suffixed",
                "unpositioned",
                "active-human",
                "active-user",
                "act-specific",
                "rule-specific",
                "covered-act",
                "generated-name",
                "list-introduction",
                "shared-subject",
                "temporary-work",
                "conversational-provenance",
                "escape-sequence",
                "public-contract",
                "code-quality",
                "prose-style",
                "context-evident",
                "mass-refactor",
                "automatic-identity",
                "workspace-path",
                "argument-array",
                "hook-based",
                "task-specific",
                "client-specific",
                "adopter-specific",
                "shared-file",
                "commit-message",
                "event-stream",
                "login-correlation",
                "client-hook",
                "repository-local",
                "non-registration",
                "base-branch",
                "credential-manager",
                "opaque-command",
                "designed-denial",
                "parity-test",
                "self-scan",
                "hosted-resource",
                "proxy-placeholder",
                "one-use",
                "file-tool",
                "re-read",
                "re-verify",
                "re-test",
                "re-adopt",
                "pre-compaction",
                "post-disclosure",
                "non-hidden",
                "non-force",
                "non-security",
                "this session",
                "a round",
                "could not carry",
                "denied outright",
                "Denied:",
                "the user",
            ],
        )

    def test_matching_ignores_case(self):
        found = findings("A SERIOUS failure occurred.\n")
        self.assertEqual(len(found), 1)
        self.assertIn("controlled vocabulary", found[0])

    def test_matching_uses_exact_forms(self):
        self.assertEqual(findings("The failure was seriously disruptive.\n"), [])
        self.assertEqual(findings("The helper utilized an existing API.\n"), [])

    def test_matching_includes_code(self):
        inline = findings("Call `utilize(resource)` here.\n")
        fenced = findings("```text\nworst\n```\n")
        self.assertEqual(len(inline), 1)
        self.assertEqual(len(fenced), 1)

    def test_denylist_source_skips_self_scanning(self):
        text = (Path(prose_policy.DENYLIST_PATH)
                .read_text(encoding="utf-8"))
        self.assertEqual(findings(text, "scripts/prose_bans.txt"), [])

    def test_handoff_skips_only_scoped_vocabulary(self):
        path = "plan/HANDOFF.md.example"
        self.assertEqual(findings("This session records state.\n", path), [])
        found = findings("A serious failure occurred.\n", path)
        self.assertEqual(len(found), 1)


class VoiceTest(unittest.TestCase):
    """Voice checks own personal pronouns and common passive forms."""

    def test_personal_pronouns_share_one_category(self):
        found = findings("I sent your report after they approved it.\n")
        self.assertEqual(len(found), PRONOUN_FINDING_COUNT)
        self.assertTrue(all("personal pronoun" in item for item in found))

    def test_neutral_object_pronouns_pass(self):
        text = "It stores its state by itself. It's stable. It'll persist. It'd recover.\n"
        self.assertEqual(findings(text), [])

    def test_pronouns_inside_examples_pass(self):
        text = "Bad: `I sent your report after they approved it.`\n"
        self.assertEqual(findings(text), [])

    def test_common_passive_form_warns(self):
        found = findings("The request was rejected by the validator.\n")
        self.assertEqual(len(found), 1)
        self.assertIn("passive voice", found[0])

    def test_active_form_passes(self):
        self.assertEqual(findings("The validator rejected the request.\n"), [])


class SentenceFormTest(unittest.TestCase):
    """Sentence checks cover high-confidence clause and punctuation patterns."""

    def test_comma_so_warns(self):
        found = findings("The cache was stale, so the build failed.\n")
        self.assertEqual(len(found), 1)
        self.assertIn("comma-led tail", found[0])

    def test_comma_which_warns(self):
        found = findings("The parser rejects input, which prevents bad records.\n")
        self.assertEqual(len(found), 1)
        self.assertIn("comma-led tail", found[0])

    def test_clause_semicolon_warns(self):
        found = findings("The gate rejected input; the parser logged the result.\n")
        self.assertEqual(len(found), 1)
        self.assertIn("clause join", found[0])

    def test_clause_colon_warns(self):
        found = findings("Newlines remain: the parser preserves each line.\n")
        self.assertEqual(len(found), 1)
        self.assertIn("clause join", found[0])

    def test_rhetorical_contrast_warns(self):
        found = findings("The change is not broad, but focused.\n")
        self.assertEqual(len(found), 1)
        self.assertIn("rhetorical contrast", found[0])

    def test_technical_contrast_passes(self):
        self.assertEqual(findings("Use argument arrays, not shell strings.\n"), [])

    def test_shared_subject_predicate_passes(self):
        self.assertEqual(findings("The checker reads files and reports warnings.\n"), [])

    def test_inline_enumeration_warns(self):
        found = findings("Values: alpha, beta, and gamma.\n")
        self.assertEqual(len(found), 1)
        self.assertIn("punctuation chain", found[0])

    def test_inline_enumeration_pathological_input(self):
        script = (
            "from scripts.prose_policy import find_violations\n"
            f"text = 'Values: ' + ', '.join(['token'] * {REDOS_TOKEN_COUNT}) + '.\\n'\n"
            "find_violations(text, 'sample.md')\n"
        )
        subprocess.run(
            [sys.executable, "-c", script],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=REDOS_TIMEOUT_SECONDS,
        )


class DiscourseTest(unittest.TestCase):
    """Discourse checks own pronoun-free framing patterns."""

    def test_conversational_provenance_warns(self):
        found = findings("The earlier chat established the approach.\n")
        self.assertEqual(len(found), 1)
        self.assertIn("conversational provenance", found[0])

    def test_handoff_skips_provenance_only(self):
        path = "plan\\HANDOFF.md.example"
        self.assertEqual(
            findings("The earlier chat established the approach.\n", path),
            [],
        )
        found = findings("A scalable solution handles edge cases.\n", path)
        self.assertGreaterEqual(len(found), 1)

    def test_ownership_deflection_warns(self):
        found = findings("The defect belongs to another team.\n")
        self.assertEqual(len(found), 1)
        self.assertIn("ownership deflection", found[0])

    def test_pronoun_ownership_warning_only(self):
        found = findings("The defect is not mine to fix.\n")
        self.assertEqual(len(found), 1)
        self.assertIn("personal pronoun", found[0])

    def test_pronoun_intent_warning_only(self):
        found = findings("This matches what you want.\n")
        self.assertEqual(len(found), 1)
        self.assertTrue(all("personal pronoun" in item for item in found))

    def test_pronoun_free_intent_warns(self):
        found = findings("The requester prefers strict validation.\n")
        self.assertEqual(len(found), 1)
        self.assertIn("intent attribution", found[0])


class LiteralEscapeTest(unittest.TestCase):
    """Literal escape sequences in prose trigger advisory warnings."""

    def test_literal_newline_escape_warns(self):
        found = findings("Summary\\n- Allow rebase recovery.\\n")
        self.assertEqual(len(found), NEWLINE_ESCAPE_FINDING_COUNT)
        self.assertTrue(all("literal escape sequence" in item for item in found))

    def test_carriage_return_and_tab_warn(self):
        found = findings("Header\\r\\tDetails.\n")
        self.assertEqual(len(found), CARRIAGE_RETURN_AND_TAB_FINDING_COUNT)
        self.assertTrue(all("literal escape sequence" in item for item in found))

    def test_code_span_skips_escape_sequences(self):
        self.assertEqual(findings("Use `\\n` to represent newlines.\n"), [])

    def test_fenced_code_skips_escapes(self):
        text = "```text\nSummary\\n- item\n```\n"
        self.assertEqual(findings(text), [])


if __name__ == "__main__":
    unittest.main()
