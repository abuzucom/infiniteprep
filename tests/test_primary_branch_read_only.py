"""The primary branch and a detached HEAD permit read-only inspection and planning.

Each class stays small because the hook coverage runner gives every test class
one shard with a fixed time limit, and every case launches the hook.
"""
import json
import unittest

from tests.read_only_branch_support import DETACHED_HEAD, BranchFixture, decision

READ_ONLY_STATES = ("main", DETACHED_HEAD)
SHA_HEX_LENGTH = 40
ALLOWED_BASH = (
    "git status", "git status --short", "git log --oneline -5", "git --no-pager log -3",
    "git diff", "git show HEAD", "git rev-parse HEAD", "git ls-files", "git blame README.md",
    "git grep fixture", "git describe --always", "git branch --show-current", "git branch -a",
    "git branch", "git worktree list", "git fetch origin", "git fetch --prune origin",
    "ls -la", "cat README.md", "head -n 1 README.md", "tail -n 1 README.md", "pwd",
    "wc -l README.md", "echo ready", "printf ready", "git log --oneline | head -5",
    "python scripts/read_git_state.py all", "python -m unittest tests.test_enforce_branch_name",
    "python scripts/check_gate_adoption.py", "rg -n fixture README.md",
)
CONSENT_BASH = (
    "python scripts/sync.py --check",
    "python scripts/sync.py --check-shared", "python scripts/sync.py --print-adoptable",
    "python scripts/run_tests.py",
    "git switch -c feat/new-work", "git checkout -b feat/new-work",
)
DENIED_BASH = (
    'git commit -m "feat: x"', "git merge feat/x", "git pull", "git push origin HEAD",
    "git branch -m feat/x", "git branch feat/x", "git branch -d feat/x", "git stash",
    "git checkout README.md", "git reset --hard", "git switch -c claude/x",
    "git switch -c feat/x extra", "touch notes.txt", "echo x > notes.txt",
    "cat README.md >> notes.txt", "python scripts/sync.py", "python scripts/sync.py --write-shared",
    "make lint PYTHON=python", "python scripts/trusted_gh.py run pr list",
    "git fetch origin main:main", "git fetch origin +refs/heads/x",
    "git fetch --upload-pack=sh origin", "git log --output=out.txt", "git grep -O fixture",
    "git diff --ext-diff", "git -c core.pager=sh log", "git -C . status", "GIT_PAGER=sh git log",
    "env git status", "cp README.md copy.md", "tee notes.txt", "rm README.md",
    "git log $(touch x)", "echo `touch x`", "git worktree add ../x", "git st", "git",
    "python scripts/run_tests.py; touch x", "cd ..", "/bin/cat README.md", "git show HEAD@{1}",
)
ALLOWED_TOOLS = ("Read", "Grep", "Glob", "AskUserQuestion", "ExitPlanMode", "EnterPlanMode",
                 "TodoWrite", "TaskCreate", "TaskUpdate", "TaskList", "TaskGet")
DENIED_TOOLS = ("WebFetch", "NotebookEdit", "mcp__github__create_pull_request", "Agent")
CMD_TOOL_NAMES = ("CMD", "Cmd", "CommandPrompt")
CMD_ALLOWED = ("type README.md", "dir", "echo ready", "more README.md", "cd", "git status")
CMD_DENIED = ("cd ..", "copy README.md copy.md", "echo x > notes.txt",
              "del README.md", "type %USERPROFILE%\\x", "cat README.md")
# Each command list is split in two. Each half runs in its own class and shard.
ALLOWED_SPLIT = len(ALLOWED_BASH) // 2
DENIED_SPLIT = len(DENIED_BASH) // 2


class ReadOnlyCase(unittest.TestCase):
    """Share fixture creation and per-state assertions across the shard classes."""

    def fixture(self, branch: str) -> BranchFixture:
        return BranchFixture(self, branch)

    def assert_shell_allowed(self, branch: str, commands: tuple) -> None:
        """Assert that each inspection command passes the hook on one read-only state."""
        # A split list half can shrink to nothing. An empty loop passes unseen.
        self.assertTrue(commands, "command list half is empty")
        fixture = self.fixture(branch)
        for command in commands:
            with self.subTest(branch=branch, command=command):
                result = fixture.shell(command)
                self.assertEqual(decision(result), "allow", result.stderr)

    def assert_writes_denied(self, branch: str, commands: tuple) -> None:
        """Assert that each write command fails the hook with the read-only message."""
        self.assertTrue(commands, "command list half is empty")
        fixture = self.fixture(branch)
        for command in commands:
            with self.subTest(branch=branch, command=command):
                result = fixture.shell(command)
                self.assertEqual(decision(result), "deny", result.stdout)
                self.assertIn("permits read-only inspection", result.stderr)


class ReadOnlyAllowedToolTest(ReadOnlyCase):
    """Read and planning tools pass."""

    def test_read_planning_tools_allowed(self):
        for branch in READ_ONLY_STATES:
            fixture = self.fixture(branch)
            for tool in ALLOWED_TOOLS:
                with self.subTest(branch=branch, tool=tool):
                    result = fixture.run(tool, {"file_path": str(fixture.root / "README.md")})
                    self.assertEqual(decision(result), "allow", result.stderr)


class ReadOnlyToolTest(ReadOnlyCase):
    """Tools outside the read and planning set fail."""

    def test_other_tools_are_denied(self):
        for branch in READ_ONLY_STATES:
            fixture = self.fixture(branch)
            for tool in DENIED_TOOLS:
                with self.subTest(branch=branch, tool=tool):
                    result = fixture.run(tool, {"url": "https://example.com"})
                    self.assertEqual(decision(result), "deny")
                    self.assertIn("permits read-only inspection", result.stderr)


class ReadOnlyMalformedCallTest(ReadOnlyCase):
    """Tool calls without a name or with a malformed input fail."""

    def test_malformed_tool_calls_are_denied(self):
        fixture = self.fixture("main")
        missing_name = fixture.run_payload({"hook_event_name": "PreToolUse",
                                            "tool_input": {"command": "git status"}})
        self.assertEqual(decision(missing_name), "deny")
        missing_input = fixture.run_payload({"hook_event_name": "PreToolUse",
                                             "tool_name": "Bash", "tool_input": "git status"})
        self.assertEqual(decision(missing_input), "deny")


class ReadOnlyShellMainTest(ReadOnlyCase):
    """The first half of the inspection commands pass on main."""

    def test_read_only_shell_allowed(self):
        self.assert_shell_allowed("main", ALLOWED_BASH[:ALLOWED_SPLIT])


class ReadOnlyShellMainRestTest(ReadOnlyCase):
    """The second half of the inspection commands pass on main."""

    def test_read_only_shell_allowed(self):
        self.assert_shell_allowed("main", ALLOWED_BASH[ALLOWED_SPLIT:])


class ReadOnlyShellDetachedTest(ReadOnlyCase):
    """The first half of the inspection commands pass on a detached HEAD."""

    def test_read_only_shell_allowed(self):
        self.assert_shell_allowed(DETACHED_HEAD, ALLOWED_BASH[:ALLOWED_SPLIT])


class ReadOnlyShellDetachedRestTest(ReadOnlyCase):
    """The second half of the inspection commands pass on a detached HEAD."""

    def test_read_only_shell_allowed(self):
        self.assert_shell_allowed(DETACHED_HEAD, ALLOWED_BASH[ALLOWED_SPLIT:])


class ReadOnlyConsentTest(ReadOnlyCase):
    """Workflows and branch creation ask, and fail without an interactive session."""

    def test_workflows_request_consent(self):
        for branch in READ_ONLY_STATES:
            fixture = self.fixture(branch)
            for command in CONSENT_BASH:
                with self.subTest(branch=branch, command=command):
                    result = fixture.shell(command)
                    self.assertEqual(decision(result), "ask", result.stderr)

    def test_unattended_consent_denied(self):
        fixture = self.fixture("main")
        result = fixture.run("Bash", {"command": "git switch -c feat/new-work"}, mode="dontAsk")
        self.assertEqual(decision(result), "deny")


class ReadOnlyBranchSwitchTest(ReadOnlyCase):
    """Switches to existing loose or packed branches pass. Other switches fail."""

    def test_existing_branch_switch_is_allowed(self):
        fixture = self.fixture("main")
        fixture.add_local_branch("feat/existing")
        self.assertEqual(decision(fixture.shell("git switch feat/existing")), "allow")
        packed = fixture.admin / "packed-refs"
        packed.write_text("# pack-refs with: peeled\n" + "0" * SHA_HEX_LENGTH
                          + " refs/heads/fix/packed\n", encoding="utf-8")
        self.assertEqual(decision(fixture.shell("git switch fix/packed")), "allow")
        for command in ("git switch feat/missing", "git switch ../outside", "git switch -"):
            with self.subTest(command=command):
                self.assertEqual(decision(fixture.shell(command)), "deny")


class ReadOnlyDenyMainTest(ReadOnlyCase):
    """The first half of the write commands fail on main."""

    def test_write_operations_are_denied(self):
        self.assert_writes_denied("main", DENIED_BASH[:DENIED_SPLIT])


class ReadOnlyDenyMainRestTest(ReadOnlyCase):
    """The second half of the write commands fail on main."""

    def test_write_operations_are_denied(self):
        self.assert_writes_denied("main", DENIED_BASH[DENIED_SPLIT:])


class ReadOnlyDenyDetachedTest(ReadOnlyCase):
    """The first half of the write commands fail on a detached HEAD."""

    def test_write_operations_are_denied(self):
        self.assert_writes_denied(DETACHED_HEAD, DENIED_BASH[:DENIED_SPLIT])


class ReadOnlyDenyDetachedRestTest(ReadOnlyCase):
    """The second half of the write commands fail on a detached HEAD."""

    def test_write_operations_are_denied(self):
        self.assert_writes_denied(DETACHED_HEAD, DENIED_BASH[DENIED_SPLIT:])


class ReadOnlyRepositoryWriteTest(ReadOnlyCase):
    """File tools cannot write inside the repository or its metadata."""

    def test_repository_file_writes_are_denied(self):
        for branch in READ_ONLY_STATES:
            fixture = self.fixture(branch)
            target = str(fixture.root / "README.md")
            for tool, tool_input in (
                    ("Write", {"file_path": target, "content": "x"}),
                    ("Edit", {"file_path": target, "old_string": "a", "new_string": "b"}),
                    ("MultiEdit", {"file_path": target, "edits": []}),
                    ("Write", {"file_path": "README.md", "content": "x"}),
                    ("Write", {"file_path": str(fixture.admin / "HEAD"), "content": "x"}),
                    ("Write", {"content": "x"})):
                with self.subTest(branch=branch, tool=tool, tool_input=tool_input):
                    self.assertEqual(decision(fixture.run(tool, tool_input)), "deny")


class ReadOnlyPowerShellTest(ReadOnlyCase):
    """PowerShell inspection forms pass and write forms fail."""

    def test_powershell_forms(self):
        fixture = self.fixture("main")
        for command in ("Get-Content README.md", "Get-ChildItem", "Get-Location",
                        "Select-String fixture README.md", "Write-Output ready", "git status"):
            with self.subTest(command=command):
                result = fixture.shell(command, tool_name="PowerShell")
                self.assertEqual(decision(result), "allow", result.stderr)
        for command in ("Set-Content notes.txt x", "Get-Content README.md > out.txt",
                        "Remove-Item README.md", "Copy-Item README.md copy.md",
                        "Get-ChildItem | ForEach-Object { Remove-Item $_ }"):
            with self.subTest(command=command):
                self.assertEqual(decision(fixture.shell(command, tool_name="PowerShell")), "deny")


class ReadOnlyCmdTest(ReadOnlyCase):
    """CMD inspection forms pass."""

    def test_cmd_forms(self):
        fixture = self.fixture("main")
        for tool in CMD_TOOL_NAMES:
            for command in CMD_ALLOWED:
                with self.subTest(tool=tool, command=command):
                    result = fixture.shell(command, tool_name=tool)
                    self.assertEqual(decision(result), "allow", result.stderr)


class ReadOnlyCmdDenyTest(ReadOnlyCase):
    """CMD write forms fail."""

    def test_cmd_forms(self):
        fixture = self.fixture("main")
        for tool in CMD_TOOL_NAMES:
            for command in CMD_DENIED:
                with self.subTest(tool=tool, command=command):
                    self.assertEqual(decision(fixture.shell(command, tool_name=tool)), "deny")


class ReadOnlyClientTest(ReadOnlyCase):
    """Gemini and Antigravity read tools pass and their write commands fail."""

    def test_gemini_and_antigravity_read_tools(self):
        fixture = self.fixture("main")
        for tool in ("read_file", "read_many_files", "list_directory", "glob",
                     "search_file_content"):
            with self.subTest(tool=tool):
                result = fixture.run(tool, {"path": str(fixture.root)}, client="gemini")
                self.assertEqual(decision(result, "gemini"), "allow", result.stdout)
        for tool in ("view_file", "list_dir", "find_by_name", "grep_search"):
            with self.subTest(tool=tool):
                result = fixture.run(tool, {"AbsolutePath": str(fixture.root)}, client="antigravity")
                self.assertEqual(decision(result, "antigravity"), "allow", result.stdout)
        result = fixture.shell("git status", tool_name="run_command", client="antigravity")
        self.assertEqual(decision(result, "antigravity"), "allow", result.stdout)
        result = fixture.shell("touch x", tool_name="run_command", client="antigravity")
        self.assertEqual(decision(result, "antigravity"), "deny")


class ReadOnlyRebaseTest(ReadOnlyCase):
    """An active rebase keeps strict recovery on a detached HEAD."""

    def test_active_rebase_keeps_strict_recovery(self):
        fixture = self.fixture(DETACHED_HEAD)
        (fixture.admin / "rebase-merge").mkdir()
        for tool, tool_input in (("Read", {"file_path": str(fixture.root / "README.md")}),
                                 ("Bash", {"command": "git status"})):
            with self.subTest(tool=tool):
                result = fixture.run(tool, tool_input)
                self.assertEqual(decision(result), "deny")
                self.assertIn("rebase", result.stderr)


class ReadOnlyLifecycleTest(ReadOnlyCase):
    """Lifecycle events show only the read-only note."""

    def test_lifecycle_note_only(self):
        for branch, label in (("main", "`main`"), (DETACHED_HEAD, "detached HEAD")):
            fixture = self.fixture(branch)
            start = fixture.lifecycle("SessionStart")
            self.assertEqual(start.returncode, 0, start.stderr)
            context = json.loads(start.stdout)["hookSpecificOutput"]["additionalContext"]
            self.assertEqual(
                context, f"{label} is read-only; create a feature branch before writing.")
            for event in ("Stop", "SubagentStop", "UserPromptSubmit"):
                with self.subTest(branch=branch, event=event):
                    result = fixture.lifecycle(event)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
