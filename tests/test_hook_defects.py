#!/usr/bin/env python3
"""Hook defect regressions: gitdir pointers, scans, aliases, routing, identity.

Each class stays small because the hook coverage runner gives every test
class one shard with a fixed time limit.
"""
import contextlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS = REPO_ROOT / "hooks"
if str(HOOKS) not in sys.path:
    sys.path.insert(0, str(HOOKS))

import _gate_core as core

POLICY_DENIAL_EXIT_CODE = 2
LONG_POINTER_CHARACTERS = 300
SHORT_SCAN_LIMIT = 1
OVER_DEEP_CHAIN = 12
SLOW_CHECKER_SECONDS = 3
SHORT_CHECKER_TIMEOUT = 0.5
GIT_CONFIG_PREFIXES = ("GIT_CONFIG_",)
GITHUB_URL = "https://github.com/example-owner/example-repo"


def load_hook(name: str):
    """Load one hook module by path under a private module name."""
    spec = importlib.util.spec_from_file_location(f"_defect_{name}", HOOKS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def clean_git_environment() -> dict:
    """Return the environment without injected Git configuration."""
    return {key: value for key, value in os.environ.items()
            if not key.startswith(GIT_CONFIG_PREFIXES)}


def make_repository(test_case: unittest.TestCase, aliases: dict) -> str:
    """Create a temporary Git repository with the given aliases."""
    temporary = tempfile.TemporaryDirectory()
    test_case.addCleanup(temporary.cleanup)
    root = temporary.name
    environment = clean_git_environment()
    subprocess.run(["git", "init", "-q", root], check=True, env=environment)
    for name, expansion in aliases.items():
        subprocess.run(["git", "-C", root, "config", f"alias.{name}", expansion],
                       check=True, env=environment)
    return os.path.realpath(root)


class GitdirPointerTest(unittest.TestCase):
    """A gitdir pointer has its own bound and fails closed past it (H1)."""

    def write_pointer(self, target: str) -> str:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name) / ".git"
        path.write_text(f"gitdir: {target}\n", encoding="utf-8")
        return str(path)

    def test_long_pointer_is_read_whole(self):
        target = "/" + "w" * LONG_POINTER_CHARACTERS
        self.assertEqual(core._read_gitdir_pointer(self.write_pointer(target), "gitdir"), target)

    def test_oversize_pointer_fails_closed(self):
        target = "/" + "w" * (core.MAX_GITDIR_POINTER_CHARACTERS + 1)
        self.assertIsNone(core._read_gitdir_pointer(self.write_pointer(target), "gitdir"))


class InfrastructureScanStatusTest(unittest.TestCase):
    """The scan separates protected, limit, and unreadable outcomes (H2)."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = temporary.name
        for name in ("one.py", "two.py", "three.py"):
            Path(self.root, name).write_text("print('safe')\n", encoding="utf-8")
        core.reset_invocation_caches()
        self.addCleanup(core.reset_invocation_caches)

    def test_clear_tree(self):
        self.assertEqual(core.infrastructure_scan_status(self.root), core.SCAN_CLEAR)
        self.assertEqual(core.infrastructure_path_verdict("ls", ["."], [], self.root), ("", ""))

    def test_protected_tree(self):
        Path(self.root, "main.tf").write_text("terraform {}\n", encoding="utf-8")
        self.assertEqual(core.infrastructure_scan_status(self.root), core.SCAN_PROTECTED)
        decision, reason = core.infrastructure_path_verdict("ls", ["."], [], self.root)
        self.assertEqual(decision, "deny")
        self.assertIn("includes protected infrastructure files", reason)

    def test_limit_denial_recovery(self):
        with patch.object(core, "MAX_INFRASTRUCTURE_SCAN_FILES", SHORT_SCAN_LIMIT):
            status = core.infrastructure_scan_status(self.root)
            decision, reason = core.infrastructure_path_verdict("ls", ["."], [], self.root)
        self.assertEqual(status, core.SCAN_LIMIT)
        self.assertEqual(decision, "deny")
        self.assertIn("Narrow the search root", reason)
        self.assertNotIn("includes protected infrastructure files", reason)

    def test_unreadable_denial_recovery(self):
        with patch.object(core.os, "walk", side_effect=OSError("synthetic")):
            status = core.infrastructure_scan_status(self.root)
            decision, reason = core.infrastructure_path_verdict("ls", ["."], [], self.root)
        self.assertEqual(status, core.SCAN_UNREADABLE)
        self.assertEqual(decision, "deny")
        self.assertIn("could not be read", reason)

    def test_bool_contract_is_kept(self):
        with patch.object(core, "MAX_INFRASTRUCTURE_SCAN_FILES", SHORT_SCAN_LIMIT):
            self.assertTrue(core.tree_contains_protected_infrastructure(self.root))
        core.reset_invocation_caches()
        self.assertFalse(core.tree_contains_protected_infrastructure(self.root))


class UnreadableDescendantTest(unittest.TestCase):
    """A subdirectory the scan cannot list denies as unreadable (H2 review)."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = temporary.name
        self.locked = os.path.join(self.root, "locked")
        os.mkdir(self.locked)
        Path(self.locked, "main.tf").write_text("terraform {}\n", encoding="utf-8")
        core.reset_invocation_caches()
        self.addCleanup(core.reset_invocation_caches)

    def lock_descendant(self):
        """Make the subdirectory unlistable for real, or by refusing its listing as root."""
        if os.name != "nt" and os.geteuid() != 0:
            os.chmod(self.locked, 0)
            self.addCleanup(os.chmod, self.locked, 0o700)
            return contextlib.nullcontext()
        real_scandir = os.scandir

        def refuse_locked(path="."):
            if os.path.realpath(path) == os.path.realpath(self.locked):
                raise PermissionError(f"synthetic refusal: {path}")
            return real_scandir(path)

        return patch.object(core.os, "scandir", side_effect=refuse_locked)

    def test_unlistable_descendant_is_unreadable(self):
        with self.lock_descendant():
            status = core.infrastructure_scan_status(self.root)
            decision, reason = core.infrastructure_path_verdict("ls", ["."], [], self.root)
        self.assertEqual(status, core.SCAN_UNREADABLE)
        self.assertEqual(decision, "deny")
        self.assertIn("could not be read", reason)

    def test_missing_or_file_root_clear(self):
        missing = os.path.join(self.root, "absent")
        single_file = os.path.join(self.root, "notes.txt")
        Path(single_file).write_text("safe\n", encoding="utf-8")
        self.assertEqual(core.infrastructure_scan_status(missing), core.SCAN_CLEAR)
        self.assertEqual(core.infrastructure_scan_status(single_file), core.SCAN_CLEAR)


class ManifestReadCacheTest(unittest.TestCase):
    """The manifest prefix comes from the file that the gate reads now."""

    def setUp(self):
        core.reset_invocation_caches()
        self.addCleanup(core.reset_invocation_caches)

    def test_replaced_file_is_read_again(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "manifest.yaml")
            path.write_text("plain: value\n", encoding="utf-8")
            self.assertFalse(core.is_protected_infrastructure_path(str(path), root))
            path.write_text("apiVersion: v1\nkind: ConfigMap\n", encoding="utf-8")
            self.assertTrue(core.is_protected_infrastructure_path(str(path), root))

    def test_binary_manifest_is_not_read(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root, "manifest.yaml")
            path.write_bytes(b"\xff\xfe\x00binary")
            self.assertFalse(core.is_protected_infrastructure_path(str(path), root))


class FileToolScanReasonTest(unittest.TestCase):
    """The file-tool gate names the scan outcome it denies on (H2)."""

    def test_search_reason_names_the_limit(self):
        module = load_hook("block_infrastructure_access")
        with tempfile.TemporaryDirectory() as root:
            for name in ("one.py", "two.py"):
                Path(root, name).write_text("print('safe')\n", encoding="utf-8")
            core.reset_invocation_caches()
            with patch.object(module.core, "MAX_INFRASTRUCTURE_SCAN_FILES", SHORT_SCAN_LIMIT):
                reason = module._search_denied("Grep", {"path": root}, root)
            core.reset_invocation_caches()
            clear = module._search_denied("Grep", {"path": root}, root)
        self.assertIn("Narrow the search root", reason)
        self.assertEqual(clear, "")


class ForgeProgramFirstTest(unittest.TestCase):
    """forge_verdict reads the `origin` repository owner only for forge programs (H3)."""

    def setUp(self):
        core.reset_invocation_caches()
        self.addCleanup(core.reset_invocation_caches)

    def test_non_forge_skips_lookup(self):
        calls = []
        with patch.object(core, "_origin_owner", side_effect=lambda cwd: calls.append(cwd) or ""):
            self.assertEqual(core.forge_verdict("ls", ["-l"], str(REPO_ROOT)), ("", ""))
        self.assertEqual(calls, [])

    def test_owner_lookup_cached(self):
        calls = []
        real_parse = core.parse_git_config

        def counting_parse(cwd):
            calls.append(cwd)
            return real_parse(cwd)

        with patch.object(core, "parse_git_config", side_effect=counting_parse):
            core._origin_owner(str(REPO_ROOT))
            core._origin_owner(str(REPO_ROOT))
            self.assertEqual(len(calls), 1)
            core.reset_invocation_caches()
            core._origin_owner(str(REPO_ROOT))
        self.assertEqual(len(calls), 2)


class AliasCycleTest(unittest.TestCase):
    """A cyclic or over-deep git alias denies with a named reason (H7)."""

    def test_self_cycle_denies(self):
        repository = make_repository(self, {"loop": "loop"})
        decision, reason = core.git_verdict(["loop"], repository)
        self.assertEqual(decision, "deny")
        self.assertIn("cycle", reason)

    def test_two_alias_cycle_denies(self):
        repository = make_repository(self, {"first": "second", "second": "first"})
        decision, reason = core.git_verdict(["first"], repository)
        self.assertEqual(decision, "deny")
        self.assertIn("cycle", reason)

    def test_over_deep_chain_denies(self):
        aliases = {f"step{index}": f"step{index + 1}" for index in range(OVER_DEEP_CHAIN)}
        aliases[f"step{OVER_DEEP_CHAIN}"] = "status"
        repository = make_repository(self, aliases)
        decision, reason = core.git_verdict(["step0"], repository)
        self.assertEqual(decision, "deny")
        self.assertIn(str(core.MAX_GIT_ALIAS_DEPTH), reason)

    def test_short_chain_still_resolves(self):
        repository = make_repository(self, {"st": "status"})
        self.assertEqual(core.git_verdict(["st"], repository), ("", ""))


class FilterRepoTest(unittest.TestCase):
    """git filter-repo reaches the history-rewrite rule (H8)."""

    def test_filter_repo_history_reason(self):
        repository = make_repository(self, {})
        self.assertEqual(core.git_verdict(["filter-repo", "--force"], repository),
                         ("ask", "git filter-repo: this rewrites history"))


class RoutingSubcommandTest(unittest.TestCase):
    """The routing gate skips every global option value (V-19)."""

    def test_namespace_value_is_skipped(self):
        args = ["--namespace", "scratch", "remote", "add", "origin", GITHUB_URL]
        decision, _reason = core.github_routing_verdict("git", args, str(REPO_ROOT))
        self.assertEqual(decision, "deny")

    def test_upper_c_value_skipped(self):
        args = ["-C", "elsewhere", "remote", "add", "origin", GITHUB_URL]
        decision, _reason = core.github_routing_verdict("git", args, str(REPO_ROOT))
        self.assertEqual(decision, "deny")


class DottedSubsectionTest(unittest.TestCase):
    """A dotted config subsection still names an exec-capable key (S-06)."""

    def test_dotted_subsections_are_detected(self):
        for key in ("filter.a.b.clean", "diff.a.b.textconv", "filter.ab.clean"):
            with self.subTest(key=key):
                self.assertEqual(core._exec_capable_key({key: "program"}), key)

    def test_unrelated_dotted_key_is_ignored(self):
        self.assertEqual(core._exec_capable_key({"branch.a.b.merge": "refs/heads/main"}), "")


class RebaseLookupFailureTest(unittest.TestCase):
    """An unreadable rebase lookup yields rebase-only recovery text (H13)."""

    def test_stop_reason_keeps_cause(self):
        module = load_hook("enforce_branch_name")
        stdout = _Capture()
        with patch.object(module, "rebase_is_active", side_effect=OSError("synthetic")), \
                patch.object(module, "read_branch_preflight",
                             return_value=("bad-name", "branch lookup failed: synthetic")), \
                patch.object(sys, "stdout", stdout):
            code = module.handle_stop_event({}, str(REPO_ROOT))
        reason = json.loads(stdout.text)["reason"]
        self.assertEqual(code, 0)
        self.assertIn(module.recovery_authorization_reason("bad-name", rebase_active=True), reason)
        self.assertIn("branch lookup failed", reason)
        for command in ("git switch -c", "git branch -m"):
            self.assertNotIn(command, reason)

    def test_invalid_branch_keeps_violation(self):
        module = load_hook("enforce_branch_name")
        payload = {"tool_name": "Read", "tool_input": {"file_path": "README.md"}}
        stderr = _Capture()
        with patch.object(module, "read_branch_preflight", return_value=("", "second lookup")), \
                patch.object(module, "rebase_is_active", return_value=False), \
                patch.object(sys, "stderr", stderr):
            code = module._handle_invalid_branch(payload, str(REPO_ROOT), "claude", "first lookup")
        self.assertEqual(code, POLICY_DENIAL_EXIT_CODE)
        self.assertIn("first lookup", stderr.text)
        self.assertNotIn("second lookup", stderr.text)


class IdentityCommandTypeTest(unittest.TestCase):
    """The identity hook denies a command that is not a string (S-08)."""

    def test_non_string_commands_deny(self):
        hook = HOOKS / "enforce_git_identity.py"
        environment = dict(os.environ, CLAUDE_PROJECT_DIR=str(REPO_ROOT))
        for command in (["git", "commit"], 7, None):
            payload = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                       "tool_input": {"command": command}}
            with self.subTest(command=command):
                result = subprocess.run([sys.executable, str(hook)], input=json.dumps(payload),
                                        capture_output=True, text=True, check=False,
                                        env=environment)
                self.assertEqual(result.returncode, POLICY_DENIAL_EXIT_CODE)
                self.assertIn("malformed", result.stderr)


class IdentityCheckerTimeoutTest(unittest.TestCase):
    """A slow identity checker denies with a timeout reason (S-09)."""

    def setUp(self):
        self.module = load_hook("enforce_git_identity")
        policy = tempfile.TemporaryDirectory()
        project = tempfile.TemporaryDirectory()
        self.addCleanup(policy.cleanup)
        self.addCleanup(project.cleanup)
        self.project = project.name
        for root in (policy.name, project.name):
            checker = Path(root, "scripts", "check_git_identity.py")
            checker.parent.mkdir()
            checker.write_text(f"import time\ntime.sleep({SLOW_CHECKER_SECONDS})\n",
                               encoding="utf-8")
        for patcher in (patch.object(self.module.core, "policy_root", return_value=policy.name),
                        patch.object(self.module, "CHECKER_TIMEOUT_SECONDS",
                                     SHORT_CHECKER_TIMEOUT, create=True)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_timeout_is_reported(self):
        self.assertIn("timed out", self.module.find_violation(self.project, []))

    def test_session_start_reports_the_timeout(self):
        stdout = _Capture()
        with patch.object(sys, "stdout", stdout):
            code = self.module._handle_session_start(self.project)
        self.assertEqual(code, 0)
        self.assertIn("timed out", json.loads(stdout.text)["systemMessage"])


class _Capture:
    """A minimal text stream that keeps everything written to it."""

    def __init__(self) -> None:
        self.text = ""

    def write(self, value: str) -> int:
        self.text += value
        return len(value)

    def flush(self) -> None:
        """Satisfy the stream protocol; the buffer needs no flushing."""


if __name__ == "__main__":
    unittest.main()
