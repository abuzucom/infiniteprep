"""Exercise failure paths with real metadata, processes, and native config reads."""
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_enforce_branch_name import REPO_ROOT, hook

core = hook.core
BLOCKING_EXIT_CODE = 2
PROCESS_EXIT_TIMEOUT_SECONDS = 5


class BranchFailureTest(unittest.TestCase):
    """Keep malformed input and unavailable trusted resources fail closed."""

    def test_malformed_file_destinations_fail_closed(self):
        for value in (None, [], "", "invalid\0path"):
            with self.subTest(value=value):
                self.assertTrue(hook._file_metadata_reason({"file_path": value}, str(REPO_ROOT)))

    def test_malformed_tool_names_fail_closed(self):
        for value in ([], {}, None):
            with self.subTest(value=value):
                payload = {"tool_name": value, "tool_input": {}}
                self.assertEqual(
                    hook._handle_pre_tool_use(payload, str(REPO_ROOT), "claude"), BLOCKING_EXIT_CODE)
                self.assertEqual(hook._handle_invalid_branch(
                    payload, str(REPO_ROOT), "claude", "invalid", "claude/x"), BLOCKING_EXIT_CODE)

    def test_invalid_recovery_and_wrapper_paths(self):
        for command in ("git branch -m 'unfinished", "git branch -m fix/x\n", "git branch -m main"):
            with self.subTest(command=command):
                self.assertFalse(
                    hook._valid_recovery(command, "claude/x", str(REPO_ROOT)))
        for command in ("env -S 'unfinished", "env -u GIT_DIR git status"):
            with self.subTest(command=command):
                self.assertTrue(hook.command_execution_violation(command, str(REPO_ROOT), "Bash")[1])
        self.assertFalse(hook._segment_names_prohibited_metadata([">", "output"]))
        self.assertEqual(hook._segment_execution_reason([">", "output"], str(REPO_ROOT)), "")

    def test_parser_literal_targets(self):
        for command in ("env -S 'unfinished", "git status"):
            with self.subTest(command=command):
                self.assertNotIn("prohibited", hook.command_execution_violation(command, str(REPO_ROOT), "Bash")[1])

    def test_environment_alias_and_depth_bound(self):
        environment = {
            "GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "alias.branchtest",
            "GIT_CONFIG_VALUE_0": "branch claude/x",
        }
        with patch.dict(os.environ, environment):
            context = hook.core.git_branch_context(["branchtest"], str(REPO_ROOT), [])
            self.assertTrue(hook._context_names_prohibited_branch(context))
        entries = {f"alias.step{index}": f"step{index + 1}"
                   for index in range(hook.core.MAX_GIT_ALIAS_DEPTH)}
        self.assertIn("limit", hook.core.resolve_branch_alias("step0", [], entries)[2])

    def test_git_directory_metadata_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "HEAD").write_text("ref: refs/heads/claude/x\n", encoding="utf-8")
            self.assertTrue(hook.find_violation(str(REPO_ROOT), {"cwd": str(REPO_ROOT), "git_dir": directory}))

    def test_explicit_git_directory_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            (root / "HEAD").write_text("ref: refs/heads/fix/example\n", encoding="utf-8")
            (root / "objects").mkdir()
            (root / "refs").mkdir()
            (root / "config").write_text("[core]\nbare = true\n", encoding="utf-8")
            context = core.git_branch_context(["--git-dir", directory, "status"], str(REPO_ROOT), [])
            self.assertEqual(context["error"], "")
            self.assertEqual(context["subcommand"], "status")

    def test_config_failure_not_allowed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            config = root / "invalid.config"
            config.write_text("[unterminated\n", encoding="utf-8")
            context = core.git_branch_context(
                ["status"], directory, [["GIT_CONFIG_GLOBAL", str(config)]])
            self.assertIn("inspection failed", context["error"])

    def test_config_reader_closed_descriptor(self):
        with subprocess.Popen([sys.executable, "-c", "print('config')"], stdout=subprocess.PIPE) as process:
            process.wait(timeout=PROCESS_EXIT_TIMEOUT_SECONDS)
            os.close(process.stdout.fileno())
            result = []
            core._collect_config_output(process, result)
            self.assertEqual(result, [None])
            with self.assertRaises(OSError):
                process.stdout.close()

    def test_config_program_outside_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            name = "git.exe" if os.name == "nt" else "git"
            executable = root / name
            executable.write_bytes(b"untrusted executable placeholder")
            executable.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
            with patch.dict(os.environ, {"PATH": str(root) + os.pathsep + os.environ.get("PATH", "")}):
                with self.assertRaises(OSError):
                    core._trusted_config_executable(directory)

    def test_missing_target_directory_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(OSError):
                core._trusted_config_executable(str(Path(directory) / "absent"))

    def test_checker_timeout_returns_a_violation(self):
        with patch.object(hook, "CHECKER_TIMEOUT_SECONDS", 0):
            self.assertTrue(hook.check_branch("fix/example"))

    def test_valid_branch_bootstrap_passes(self):
        payload = {
            "tool_name": "Bash", "tool_input": {"command": "python scripts/read_git_state.py branch"},
        }
        self.assertEqual(hook._handle_pre_tool_use(payload, str(REPO_ROOT), "claude"), 0)


class AdditionalBoundaryTest(unittest.TestCase):
    """Cover option boundaries and literal shell operator data."""

    def test_recovered_wrappers_and_dynamic_arguments(self) -> None:
        self.assertIn("wrapper", hook._segment_execution_reason(["env", "-S", "'"], str(REPO_ROOT)))
        self.assertIn("unresolved", hook.command_execution_violation("echo $value", str(REPO_ROOT), "Bash")[1])
        self.assertFalse(hook.bash_parser.has_quoted_redirects(r"echo a\b"))

    def test_empty_common_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            git_dir = root / ".git"
            git_dir.mkdir()
            (git_dir / "commondir").write_text("\n", encoding="utf-8")
            self.assertEqual(hook._metadata_roots(directory), (str(git_dir),))
            reason = hook._file_metadata_reason(
                {"file_path": ".git/HEAD", "content": "ref: refs/heads/claude/x\n"}, directory)
            self.assertIn("prohibited claude/", reason)

    def test_missing_metadata_file_write(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(hook._metadata_roots(directory), ())
            self.assertEqual(hook._file_metadata_reason(
                {"file_path": "notes.txt", "content": "refs/heads/claude/x"}, directory), "")

    def test_push_operand_boundaries(self):
        self.assertFalse(hook._push_has_literal_target(["--repo"]))
        self.assertTrue(hook._push_has_literal_target(["--", "origin", "HEAD"]))

    def test_quoted_redirect_data(self):
        for command in (
            "printf '%s' '>' .git/HEAD refs/heads/claude/x",
            "printf '%s' \\> .git/HEAD refs/heads/claude/x",
        ):
            with self.subTest(command=command):
                reason = hook.command_execution_violation(command, str(REPO_ROOT), "Bash")[1]
                self.assertNotIn("prohibited", reason)
                self.assertNotIn("Git", reason)


if __name__ == "__main__":
    unittest.main()
