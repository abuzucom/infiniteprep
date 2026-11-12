#!/usr/bin/env python3
"""The command gate allows inspecting workflows and routes writing ones to consent."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_command_execution_gate import GATE_NAME, _load_gate_module
from tests.test_enforce_branch_name import HOOK_PATH, REPO_ROOT

PROJECT_DIR = str(REPO_ROOT)
SCRIPT_LIST_NAME = "workflow-scripts.txt"
TIMEOUT_SECONDS = 15
BLOCKING_EXIT_CODE = 2
EXECUTABLE_FILE_MODE = 0o755
MAX_CHAIN_PARTS = 8
MAX_SCRIPT_LIST_BYTES = 64 * 1024
OPAQUE_REASON = "Opaque command execution requires an inspectable operation"

ALLOWED_COMMANDS = (
    "ruff check src tests",
    "ruff check",
    "ruff check --no-fix --statistics src",
    "ruff check --diff src",
    "ruff format --check src tests",
    "ruff format --diff",
    "python -m ruff check src tests",
    "python3 -m ruff format --check src",
    "python -m unittest discover -s tests",
    "python -m unittest discover",
    "python -m unittest discover -s tests -t . -p test_*.py -v",
    "python -m unittest discover --start-directory tests --pattern test_gate*.py",
    "python -m unittest tests.test_gate_parity -v",
    "python scripts/check_changelog.py",
    "python scripts/check_changelog.py --staged",
    "python scripts/check_changelog.py --base origin/main --head HEAD",
    "python scripts/check_branch_name.py --strict-agent-preflight",
    "python scripts/check_ascii.py README.md CHANGELOG.md",
    "python scripts/check_persist_credentials.py .github/workflows/*.yml",
    "python scripts/check_git_identity.py --strict --unpushed",
    "python scripts/check_git_identity.py --advise",
    "python -I scripts/check_local_paths.py --unpushed",
    "python scripts/check_local_paths.py --report",
    "python scripts/lint_style.py",
    "python scripts/read_git_state.py all",
    "python scripts/check_action_pins.py",
    "python scripts/check_gate_adoption.py",
    "rg -n branch README.md",
)
CONSENT_COMMANDS = (
    "ruff format src",
    "ruff format",
    "ruff check --fix src",
    "ruff check --fix --no-fix src",
    "python -m ruff format src",
    "python scripts/run_tests.py",
    "python scripts/sync.py --check",
    "python scripts/sync.py",
    "make lint PYTHON=python",
    "rg -n token /etc/hosts",
    "rg -n token ../other",
    "rg -n token ~/notes",
)
UNMATCHED_COMMANDS = (
    "ruff",
    "ruff rule E501",
    "ruff check --config other.toml src",
    "ruff check --unsafe-fixes src",
    "ruff check /etc",
    "ruff check ../other",
    "ruff format --config=other.toml",
    "python -m ruff server",
    "python -m unittest discover tests",
    "python -m unittest discover -s /tmp",
    "python -m unittest discover -s ../other",
    "python -m unittest discover -s",
    "python -m unittest discover -k name",
    "python -m unittest discover -p -v",
    "python scripts/check_changelog.py --repo /tmp",
    "python scripts/check_changelog.py --base",
    "python scripts/check_changelog.py --base -x --head HEAD",
    "python scripts/check_ascii.py /etc/passwd",
    "python scripts/check_ascii.py ../other.md",
    "python scripts/check_ascii.py --flag",
    "python scripts/complete_gate_adoption.py --candidate .gate-staging/candidate",
    "python scripts/check_gate_pr_integrity.py",
)
# shlex ends a word at `#` and Bash does not, so `#` could hide a second command.
# A run of operator characters such as `|&` is one token that names no command.
HIDDEN_COMMANDS = (
    "rg token#x ; sh other.sh",
    "ruff check src #x",
    "rg token |& sh",
    "rg token ;; sh",
    "rg token & sh",
)
READ_ONLY_COMMANDS = (
    "ruff check src",
    "ruff format --check src",
    "python scripts/check_changelog.py",
    "python scripts/run_tests.py",
    "python -m unittest discover -s tests",
    "ruff check src && python -m unittest discover -s tests",
)
WRITING_COMMANDS = (
    "ruff format src",
    "ruff check --fix src",
    "python scripts/sync.py",
    "ruff check src && ruff format src",
)


class AllowVerdictModuleTest(unittest.TestCase):
    """Classify single invocations through the gate module."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _load_gate_module()

    def decide(self, command: str, project_dir: str = PROJECT_DIR) -> tuple:
        """Return the gate decision for one command."""
        return self.gate.workflow_decision(command, project_dir)

    def test_inspecting_forms_pass_without_consent(self) -> None:
        for command in ALLOWED_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(self.decide(command), ("allow", ""))

    def test_writing_and_unbounded_forms_route_to_consent(self) -> None:
        for command in CONSENT_COMMANDS:
            with self.subTest(command=command):
                decision, reason = self.decide(command)
                self.assertEqual(decision, "ask")
                self.assertIn("execution consent", reason)

    def test_other_shapes_stay_unmatched(self) -> None:
        for command in UNMATCHED_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(self.decide(command), ("", ""))

    def test_hidden_second_command_stays_unmatched(self) -> None:
        for command in HIDDEN_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(self.decide(command), ("", ""))

    def test_allowed_forms_never_need_consent(self) -> None:
        for command in ALLOWED_COMMANDS:
            with self.subTest(command=command):
                self.assertFalse(self.gate.workflow_needs_consent(command, PROJECT_DIR))

    def test_read_only_classification(self) -> None:
        for command in READ_ONLY_COMMANDS:
            with self.subTest(command=command):
                self.assertTrue(self.gate.read_only_workflow(command, PROJECT_DIR))
        for command in WRITING_COMMANDS:
            with self.subTest(command=command):
                self.assertFalse(self.gate.read_only_workflow(command, PROJECT_DIR))


class ChainVerdictTest(unittest.TestCase):
    """An && chain takes the strictest verdict of its parts."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _load_gate_module()

    def decision(self, command: str) -> str:
        """Return only the decision word for one command."""
        return self.gate.workflow_decision(command, PROJECT_DIR)[0]

    def test_chain_of_allowed_forms_is_allowed(self) -> None:
        self.assertEqual(
            self.decision("ruff check src && ruff format --check src && python -m unittest discover -s tests"),
            "allow")

    def test_chain_with_a_consent_form_asks(self) -> None:
        self.assertEqual(self.decision("ruff check src && ruff format src"), "ask")
        self.assertEqual(self.decision("python scripts/run_tests.py && ruff check src"), "ask")

    def test_chain_with_an_unaccepted_part_stays_unmatched(self) -> None:
        for command in ("ruff check src && node script.js",
                        "ruff check src && git status",
                        "ruff check src && ruff rule E501",
                        "ruff check src &&",
                        "&& ruff check src"):
            with self.subTest(command=command):
                self.assertEqual(self.decision(command), "")

    def test_other_operators_stay_unmatched(self) -> None:
        for operator in (";", "||", "|", "&"):
            with self.subTest(operator=operator):
                self.assertEqual(self.decision(f"ruff check src {operator} ruff format --check src"), "")

    def test_chain_length_is_bounded(self) -> None:
        longest = " && ".join(["ruff check src"] * MAX_CHAIN_PARTS)
        self.assertEqual(self.decision(longest), "allow")
        self.assertEqual(self.decision(longest + " && ruff check src"), "")


class RuffLookupTest(unittest.TestCase):
    """Repository content cannot stand in for Ruff."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _load_gate_module()

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_root_module_file_shadows_the_module_spelling(self) -> None:
        (self.root / "ruff.py").write_text("", encoding="utf-8")
        decision, reason = self.gate.workflow_decision("python -m ruff check src", str(self.root))
        self.assertEqual(decision, "deny")
        self.assertIn("ruff.py", reason)
        self.assertEqual(self.gate.workflow_decision("ruff check src", str(self.root)), ("allow", ""))

    def test_root_package_directory_shadows_the_module_spelling(self) -> None:
        (self.root / "ruff").mkdir()
        decision, reason = self.gate.workflow_decision("python -m ruff format --check", str(self.root))
        self.assertEqual(decision, "deny")
        self.assertIn("shadows", reason)

    @unittest.skipIf(os.name == "nt", "The executable bit selects PATH programs on POSIX only")
    def test_repository_resolved_executable_is_denied(self) -> None:
        binaries = self.root / "bin"
        binaries.mkdir()
        program = binaries / "ruff"
        program.write_text("#!/bin/sh\n", encoding="utf-8")
        program.chmod(EXECUTABLE_FILE_MODE)
        with patch.dict(os.environ, {"PATH": str(binaries)}):
            decision, reason = self.gate.workflow_decision("ruff check src", str(self.root))
        self.assertEqual(decision, "deny")
        self.assertIn("inside the repository", reason)


class WorkflowScriptListTest(unittest.TestCase):
    """The script list parses strictly and fails closed."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _load_gate_module()

    def test_valid_lines_parse_into_entries(self) -> None:
        entries = self.gate._parse_workflow_scripts(
            "# comment\n\nallow scripts/a.py --base <revision> <paths>\nconsent-write scripts/b.py\n")
        self.assertEqual(entries, (
            ("scripts/a.py", ("--base", "<revision>", "<paths>"), "allow"),
            ("scripts/b.py", (), "consent-write"),
        ))

    def test_malformed_lines_raise(self) -> None:
        for text in ("permit scripts/a.py\n",
                     "allow\n",
                     "allow ../a.py\n",
                     "allow /abs/a.py\n",
                     "allow scripts/a.sh\n",
                     "allow scripts/a.py <unknown>\n",
                     "allow scripts/a.py <paths> --flag\n"):
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    self.gate._parse_workflow_scripts(text)

    def test_every_listed_script_exists(self) -> None:
        entries = self.gate._load_workflow_scripts()
        self.assertTrue(entries)
        for script, _pattern, _verdict in entries:
            with self.subTest(script=script):
                self.assertTrue((REPO_ROOT / script).is_file())

    def test_missing_list_denies_repository_scripts(self) -> None:
        gate = _load_gate_module()
        missing = str(REPO_ROOT / "hooks" / "absent-workflow-scripts.txt")
        with patch.object(gate, "WORKFLOW_SCRIPT_LIST_PATH", missing):
            gate._load_workflow_scripts.cache_clear()
            decision, reason = gate.workflow_decision("python scripts/check_changelog.py", PROJECT_DIR)
        self.assertEqual(decision, "deny")
        self.assertIn(SCRIPT_LIST_NAME, reason)

    def test_missing_list_leaves_the_literal_tables_empty(self) -> None:
        gate = _load_gate_module()
        missing = str(REPO_ROOT / "hooks" / "absent-workflow-scripts.txt")
        with patch.object(gate, "WORKFLOW_SCRIPT_LIST_PATH", missing):
            gate._load_workflow_scripts.cache_clear()
            self.assertEqual(gate._literal_script_arguments(gate.SCRIPT_VERDICTS), {})

    def test_unusable_list_content_names_the_cause(self) -> None:
        oversized = b"# filler\n" * (MAX_SCRIPT_LIST_BYTES // len(b"# filler\n") + 1)
        cases = ((oversized, "exceeds 64 KiB"),
                 (b"allow scripts/caf\xc3\xa9.py\n", "non-ASCII"),
                 (b"permit scripts/a.py\n", "line 1"))
        with tempfile.TemporaryDirectory() as temporary:
            for index, (content, cause) in enumerate(cases):
                with self.subTest(cause=cause):
                    path = Path(temporary) / f"list-{index}.txt"
                    path.write_bytes(content)
                    gate = _load_gate_module()
                    with patch.object(gate, "WORKFLOW_SCRIPT_LIST_PATH", str(path)):
                        gate._load_workflow_scripts.cache_clear()
                        with self.assertRaises(ValueError) as raised:
                            gate._load_workflow_scripts()
                    self.assertIn(cause, str(raised.exception))
                    self.assertIn(SCRIPT_LIST_NAME, str(raised.exception))

    def test_single_path_placeholder_accepts_one_repository_path(self) -> None:
        pattern = ("--file", "<path>")
        self.assertTrue(self.gate._pattern_matches(pattern, ("--file", "docs/guide.md")))
        self.assertFalse(self.gate._pattern_matches(pattern, ("--file", "/etc/passwd")))
        self.assertFalse(self.gate._pattern_matches(pattern, ("--file", "docs/guide.md", "extra.md")))

    def test_literal_tokens_reject_chains(self) -> None:
        self.assertEqual(self.gate._literal_tokens("ruff check src"), ["ruff", "check", "src"])
        self.assertEqual(self.gate._literal_tokens("ruff check src && ruff check tests"), [])


class AcceptedFormHintTest(unittest.TestCase):
    """A denied known tool names the forms the gate accepts."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _load_gate_module()

    def test_ruff_denial_lists_ruff_forms(self) -> None:
        for segment in (["ruff", "rule", "E501"], ["python", "-m", "ruff", "server"]):
            with self.subTest(segment=segment):
                reason = self.gate.segment_program_reason(segment)
                self.assertIn(OPAQUE_REASON, reason)
                self.assertIn("ruff check", reason)
                self.assertIn("ruff format", reason)

    def test_unittest_denial_lists_unittest_forms(self) -> None:
        reason = self.gate.segment_program_reason(["python", "-m", "unittest", "discover", "/tmp"])
        self.assertIn(OPAQUE_REASON, reason)
        self.assertIn("python -m unittest discover", reason)

    def test_script_denial_names_the_script_list(self) -> None:
        reason = self.gate.segment_program_reason(["python", "scripts/other.py"])
        self.assertIn(OPAQUE_REASON, reason)
        self.assertIn(f"hooks/{SCRIPT_LIST_NAME}", reason)

    def test_other_programs_keep_the_plain_reason(self) -> None:
        for segment in (["node", "script.js"], ["python", "-c", "pass"]):
            with self.subTest(segment=segment):
                self.assertEqual(self.gate.segment_program_reason(segment), OPAQUE_REASON)


class AllowVerdictHookTest(unittest.TestCase):
    """Run the registered branch hook as Claude and as Codex."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        admin = self.root / ".git"
        (admin / "objects").mkdir(parents=True)
        (admin / "refs" / "heads").mkdir(parents=True)
        (admin / "config").write_text("[core]\nbare = false\n", encoding="utf-8")
        self.set_branch("fix/example")
        global_config = self.root / "global.config"
        global_config.write_text("", encoding="utf-8")
        self.environment = {key: value for key, value in os.environ.items()
                            if not key.startswith("GIT_")}
        self.environment.update({"CLAUDE_PROJECT_DIR": str(self.root), "GIT_CONFIG_NOSYSTEM": "1",
                                 "GIT_CONFIG_GLOBAL": str(global_config)})
        (self.root / "scripts").mkdir()
        for name in ("check_changelog.py", "sync.py"):
            (self.root / "scripts" / name).write_text("", encoding="utf-8")

    def set_branch(self, name: str) -> None:
        """Point the fixture HEAD at one branch."""
        (self.root / ".git" / "HEAD").write_text(f"ref: refs/heads/{name}\n", encoding="utf-8")

    def run_candidate(self, command: str, client: str,
                      permission_mode: str = "default") -> subprocess.CompletedProcess:
        """Send one command payload to the hook without executing the command."""
        payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                   "tool_input": {"command": command}}
        if permission_mode:
            payload["permission_mode"] = permission_mode
        return subprocess.run(
            [sys.executable, str(HOOK_PATH), "--client", client], input=json.dumps(payload),
            env=self.environment, capture_output=True, text=True,
            timeout=TIMEOUT_SECONDS, check=False,
        )

    def test_inspecting_forms_pass_for_every_client_and_mode(self) -> None:
        commands = ("ruff check src tests", "ruff format --check src tests",
                    "python -m ruff check src tests", "python -m unittest discover -s tests",
                    "python scripts/check_changelog.py",
                    "ruff check src && python -m unittest discover -s tests",
                    "rg -n branch README.md")
        for command in commands:
            for client, mode in (("claude", "default"), ("claude", ""), ("codex", "")):
                with self.subTest(command=command, client=client, mode=mode):
                    result = self.run_candidate(command, client, mode)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), "")

    def test_writing_form_asks_an_interactive_claude_session(self) -> None:
        result = self.run_candidate("ruff format src", "claude")
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "ask")
        self.assertIn(f"blocked by {GATE_NAME}:", output["permissionDecisionReason"])

    def test_writing_form_is_denied_without_a_consent_path(self) -> None:
        for client, mode in (("claude", ""), ("codex", "")):
            with self.subTest(client=client):
                result = self.run_candidate("ruff format src", client, mode)
                self.assertEqual(result.returncode, BLOCKING_EXIT_CODE, result.stdout)
                self.assertIn(f"blocked by {GATE_NAME}:", result.stderr)
                self.assertIn("execution consent", result.stderr)

    def test_unaccepted_ruff_form_denial_lists_accepted_forms(self) -> None:
        for client in ("claude", "codex"):
            with self.subTest(client=client):
                result = self.run_candidate("ruff check --config other.toml src", client)
                self.assertEqual(result.returncode, BLOCKING_EXIT_CODE, result.stdout)
                self.assertIn(f"blocked by {GATE_NAME}: {OPAQUE_REASON}", result.stderr)
                self.assertIn("ruff format", result.stderr)

    def test_mixed_chain_is_denied(self) -> None:
        result = self.run_candidate("ruff check src && node script.js", "claude")
        self.assertEqual(result.returncode, BLOCKING_EXIT_CODE, result.stdout)
        self.assertIn(OPAQUE_REASON, result.stderr)

    def test_primary_branch_allows_inspection_and_denies_writes(self) -> None:
        self.set_branch("main")
        for client in ("claude", "codex"):
            with self.subTest(client=client):
                allowed = self.run_candidate("ruff check src", client)
                self.assertEqual(allowed.returncode, 0, allowed.stderr)
                self.assertEqual(allowed.stdout.strip(), "")
                denied = self.run_candidate("ruff format src", client)
                self.assertEqual(denied.returncode, BLOCKING_EXIT_CODE, denied.stdout)


if __name__ == "__main__":
    unittest.main()
