#!/usr/bin/env python3
"""Hook cleanup regressions: shared helpers, stricter matching, accurate reasons.

Each class stays small because the hook coverage runner gives every test
class one shard with a fixed time limit.
"""
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS = REPO_ROOT / "hooks"
SCRIPTS = REPO_ROOT / "scripts"
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))

import _gate_core as core

HOST_SAMPLES = {
    "github.com": True,
    "GitHub.com": True,
    "github.com.": True,
    "github.com:443": True,
    "api.github.com": True,
    "raw.githubusercontent.com": True,
    "evilgithub.com": False,
    "github.com.example": False,
    "": False,
}


def load_module(path: Path, name: str):
    """Load one module by path under a private name."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScriptsConsentTest(unittest.TestCase):
    """An Edit-tool write under scripts/ needs consent (V-11)."""

    def test_script_edit_asks(self):
        with tempfile.TemporaryDirectory() as project:
            target = Path(project, "scripts", "check_example.py")
            target.parent.mkdir()
            target.write_text("VALUE = 1\n", encoding="utf-8")
            payload = {"hook_event_name": "PreToolUse", "tool_name": "Edit",
                       "permission_mode": "default",
                       "tool_input": {"file_path": str(target), "old_string": "1",
                                      "new_string": "2"}}
            environment = dict(os.environ, CLAUDE_PROJECT_DIR=project)
            result = subprocess.run([sys.executable, str(HOOKS / "require_consent.py")],
                                    input=json.dumps(payload), capture_output=True,
                                    text=True, check=False, env=environment)
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["permissionDecision"], "ask")


class CmdBusyboxTest(unittest.TestCase):
    """The CMD gate treats busybox as an interpreter hop (Decision 49)."""

    def test_busybox_payload_denies(self):
        cmd_gate = load_module(HOOKS / "block_destructive_cmd.py", "_consolidation_cmd")
        decision, _reason = cmd_gate.classify_cmd_command("busybox sh -c whoami")
        self.assertEqual(decision, "deny")


class BranchAliasCaseTest(unittest.TestCase):
    """The branch alias resolver matches alias names like git, ignoring case."""

    def test_mixed_case_alias_resolves(self):
        entries = {"alias.br": "branch -m feature/example"}
        subcommand, arguments, error = core.resolve_branch_alias("Br", [], entries)
        self.assertEqual((subcommand, arguments, error),
                         ("branch", ["-m", "feature/example"], ""))


class HostPredicateTest(unittest.TestCase):
    """One GitHub host predicate, mirrored by the external reference checker."""

    def test_core_predicate(self):
        for host, expected in HOST_SAMPLES.items():
            with self.subTest(host=host):
                self.assertEqual(core.is_github_hostname(host), expected)

    def test_script_copy_agrees(self):
        script = load_module(SCRIPTS / "check_external_pr_refs.py", "_consolidation_refs")
        for host in HOST_SAMPLES:
            with self.subTest(host=host):
                self.assertEqual(script.is_github_host(host), core.is_github_hostname(host))


class EmitHelpersTest(unittest.TestCase):
    """Core emit takes a prefix, and emit_context prints both context fields."""

    def test_emit_prefix(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = core.emit("example.py", "ask", "a reason", prefix="gated by")
        reason = json.loads(stdout.getvalue())["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertEqual(code, 0)
        self.assertEqual(reason, "gated by hooks/example.py: a reason")

    def test_emit_context(self):
        stdout = io.StringIO()
        with redirect_stdout(stdout):
            code = core.emit_context("SessionStart", "context text")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stdout.getvalue()), {
            "hookSpecificOutput": {"hookEventName": "SessionStart",
                                   "additionalContext": "context text"},
            "systemMessage": "context text",
        })


class PowerShellOperandTest(unittest.TestCase):
    """One PowerShell write-operand helper serves both gates (Decision 50)."""

    def test_empty_value_dropped_by_default(self):
        targets, _operands = core.powershell_write_groups("set-content", [["-Path:"], ["x"]])
        self.assertEqual(targets, [])

    def test_empty_value_kept_on_request(self):
        targets, _operands = core.powershell_write_groups(
            "set-content", [["-Path:"], ["x"]], keep_empty=True)
        self.assertEqual(targets, [""])

    def test_attached_value_keeps_case(self):
        targets, _operands = core.powershell_write_groups("set-content", [["-Path:Link/File"]])
        self.assertEqual(targets, ["Link/File"])

    def test_ungrouped_array_raises(self):
        with self.assertRaises(ValueError):
            core.powershell_write_groups("copy-item", [["-Destination", "a", "b"]])


class WriteContentTest(unittest.TestCase):
    """One write-content helper serves the branch and infrastructure hooks."""

    def test_fields_edits_joined(self):
        tool_input = {"content": "alpha", "new_source": "beta",
                      "edits": [{"new_string": "gamma"}, {"old_string": "only"}, "bad"]}
        self.assertEqual(core.write_content(tool_input), "alpha beta gamma")

    def test_cap_trims_each_value(self):
        tool_input = {"new_string": "abcdef", "edits": [{"new_string": "uvwxyz"}]}
        self.assertEqual(core.write_content(tool_input, 3), "abc uvw")


class EnvSplitTest(unittest.TestCase):
    """env -S with a missing or incomplete payload leaves the prefix unresolved."""

    def test_incomplete_split_payloads(self):
        import _bash_parser
        for tokens in (["env", "-S"], ["env", "-S", "'unfinished"]):
            with self.subTest(tokens=tokens):
                _executable, _assignments, complete = _bash_parser.strip_prefixes(tokens)
                self.assertFalse(complete)


class RecoveryCommandTest(unittest.TestCase):
    """One helper chooses the branch recovery command (Decision 52)."""

    def test_three_cases(self):
        branch_hook = load_module(HOOKS / "enforce_branch_name.py", "_consolidation_branch")
        self.assertEqual(branch_hook.recovery_command("bad-name", False), "git branch -m")
        self.assertEqual(branch_hook.recovery_command("main", False), "git switch -c")
        self.assertIn("git rebase --abort", branch_hook.recovery_command("bad-name", True))


class ReasonTextTest(unittest.TestCase):
    """Deny reasons describe what each command does (H10)."""

    def test_dd_reason_names_raw_writes(self):
        decision, reason = core.device_write_verdict("dd", ["if=a", "of=b"])
        self.assertEqual(decision, "deny")
        self.assertNotIn("filesystem", reason)
        self.assertIn("raw", reason)

    def test_root_mode_reason(self):
        decision, reason = core.mode_change_verdict("chown", ["nobody", "/etc"])
        self.assertEqual(decision, "deny")
        self.assertNotIn("recursive", reason)


if __name__ == "__main__":
    unittest.main()
