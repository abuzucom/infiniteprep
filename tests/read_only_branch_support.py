"""Shared fixture for read-only primary branch and master hook tests.

RepositoryFixture owns an isolated repository, home directory, and
temporary directory. HookRunner launches the hook as a subprocess against
those roots. BranchFixture combines the two. The real home directory and
the real checkout stay untouched.
"""
import functools
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from tests.test_enforce_branch_name import HOOK_PATH, REPO_ROOT

DETACHED_HEAD = "HEAD"
BLOCKING_EXIT_CODE = 2
JSON_VERDICT_CLIENTS = ("gemini", "antigravity")
JSON_VERDICT_EXIT_CODES = (0,)
NATIVE_VERDICT_EXIT_CODES = (0, BLOCKING_EXIT_CODE)
HOOK_TIMEOUT_SECONDS = 30
# Each fixture owns a repository root, a home directory, and a temporary directory.
ISOLATED_DIRECTORY_COUNT = 3
ZERO_OBJECT_ID = "0" * 40
COPIED_SCRIPTS = (
    "run_tests.py", "read_git_state.py", "sync.py", "trusted_gh.py",
    "check_action_pins.py", "check_gate_adoption.py",
)


# Intentional process-wide cache. One test process builds many fixtures, and
# the checked-out scripts do not change during a run. The cached value is an
# immutable tuple of bytes, so no fixture can alter what another one copies.
@functools.cache
def read_copied_scripts() -> tuple[tuple[str, bytes], ...]:
    """Return each copied script name with its bytes from this checkout."""
    return tuple((name, (REPO_ROOT / "scripts" / name).read_bytes())
                 for name in COPIED_SCRIPTS)


def build_inherited_environment() -> dict[str, str]:
    """Return a new copy of the process environment without Git and branch variables.

    No cache. os.environ is mutable process state. A test can patch it before
    it builds a fixture, and each fixture reads the environment at that time.
    """
    return {key: value for key, value in os.environ.items()
            if not key.startswith("GIT_") and key != "GITHUB_HEAD_REF"}


class RepositoryFixture:
    """Build one isolated repository, home directory, and temporary directory."""

    def __init__(self, test_case, branch: str):
        directories = [tempfile.TemporaryDirectory() for _ in range(ISOLATED_DIRECTORY_COUNT)]
        for directory in directories:
            test_case.addCleanup(directory.cleanup)
        self.root, self.home, self.temp = (
            Path(directory.name).resolve() for directory in directories)
        self.admin = self.root / ".git"
        (self.admin / "objects").mkdir(parents=True)
        (self.admin / "refs" / "heads").mkdir(parents=True)
        (self.admin / "config").write_text("[core]\nbare = false\n", encoding="utf-8")
        self.set_branch(branch)
        (self.root / "README.md").write_text("fixture\n", encoding="utf-8")
        (self.root / "scripts").mkdir()
        for name, content in read_copied_scripts():
            (self.root / "scripts" / name).write_bytes(content)
        self.global_config = self.temp / "global.config"
        self.global_config.write_text("", encoding="utf-8")
        self.environment = build_inherited_environment()
        self.environment.update({
            "CLAUDE_PROJECT_DIR": str(self.root),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(self.global_config),
            "HOME": str(self.home),
            "USERPROFILE": str(self.home),
            "TMPDIR": str(self.temp),
            "TMP": str(self.temp),
            "TEMP": str(self.temp),
        })

    def set_branch(self, branch: str) -> None:
        """Point HEAD at a named branch or detach it."""
        head = ZERO_OBJECT_ID if branch == DETACHED_HEAD else "ref: refs/heads/" + branch
        (self.admin / "HEAD").write_text(head + "\n", encoding="utf-8")

    def add_local_branch(self, branch: str) -> None:
        """Create one loose local branch reference."""
        reference = self.admin / "refs" / "heads" / Path(*branch.split("/"))
        reference.parent.mkdir(parents=True, exist_ok=True)
        reference.write_text(ZERO_OBJECT_ID + "\n", encoding="utf-8")

    def write_override(self, content: bytes) -> None:
        """Write hooks/primary-branch.txt with exact bytes."""
        hooks = self.root / "hooks"
        hooks.mkdir(exist_ok=True)
        (hooks / "primary-branch.txt").write_bytes(content)


class HookRunner:
    """Send payloads to the installed hook for one repository root.

    The runner keeps the caller's environment dict. A test that edits the
    dict changes the environment of every later hook launch.
    """

    def __init__(self, root: Path, environment: dict):
        self.root = root
        self.environment = environment

    def run(self, tool_name: str, tool_input: dict, client: str = "claude",
            event: str = "PreToolUse", mode: str = "default") -> subprocess.CompletedProcess:
        """Send one tool call through the hook for the selected client."""
        if client == "antigravity":
            return self.run_payload({"workspacePaths": [str(self.root)],
                                     "toolCall": {"name": tool_name, "args": tool_input}},
                                    client)
        payload = {"hook_event_name": event, "permission_mode": mode,
                   "tool_name": tool_name, "tool_input": tool_input,
                   "cwd": str(self.root)}
        return self.run_payload(payload, client)

    def run_payload(self, payload: dict, client: str = "claude") -> subprocess.CompletedProcess:
        """Send one raw payload through the hook."""
        return subprocess.run(
            [sys.executable, str(HOOK_PATH), "--client", client],
            input=json.dumps(payload), env=self.environment, capture_output=True,
            text=True, timeout=HOOK_TIMEOUT_SECONDS, check=False,
        )

    def shell(self, command: str, tool_name: str = "Bash", client: str = "claude"):
        """Send one shell command through the hook."""
        command_key = "CommandLine" if client == "antigravity" else "command"
        return self.run(tool_name, {command_key: command}, client)

    def lifecycle(self, event: str) -> subprocess.CompletedProcess:
        """Send one lifecycle event through the hook."""
        return self.run_payload({"hook_event_name": event, "cwd": str(self.root)})


class BranchFixture:
    """Compose one repository fixture with a hook runner for that repository.

    The attribute and method names stay the same for every test that uses them.
    """

    def __init__(self, test_case, branch: str):
        self.repository = RepositoryFixture(test_case, branch)
        self.hook = HookRunner(self.repository.root, self.repository.environment)
        self.root = self.repository.root
        self.home = self.repository.home
        self.temp = self.repository.temp
        self.admin = self.repository.admin
        self.global_config = self.repository.global_config
        self.environment = self.repository.environment

    def set_branch(self, branch: str) -> None:
        """Point HEAD at a named branch or detach it."""
        self.repository.set_branch(branch)

    def add_local_branch(self, branch: str) -> None:
        """Create one loose local branch reference."""
        self.repository.add_local_branch(branch)

    def write_override(self, content: bytes) -> None:
        """Write hooks/primary-branch.txt with exact bytes."""
        self.repository.write_override(content)

    def run(self, tool_name: str, tool_input: dict, client: str = "claude",
            event: str = "PreToolUse", mode: str = "default") -> subprocess.CompletedProcess:
        """Send one tool call through the hook for the selected client."""
        return self.hook.run(tool_name, tool_input, client, event, mode)

    def run_payload(self, payload: dict, client: str = "claude") -> subprocess.CompletedProcess:
        """Send one raw payload through the hook."""
        return self.hook.run_payload(payload, client)

    def shell(self, command: str, tool_name: str = "Bash", client: str = "claude"):
        """Send one shell command through the hook."""
        return self.hook.shell(command, tool_name, client)

    def lifecycle(self, event: str) -> subprocess.CompletedProcess:
        """Send one lifecycle event through the hook."""
        return self.hook.lifecycle(event)


def decision(result: subprocess.CompletedProcess, client: str = "claude") -> str:
    """Return allow, ask, or deny from one hook result.

    Gemini and Antigravity verdicts always exit 0 with the verdict on stdout.
    Other clients exit 0, or exit 2 to deny. Any other exit code is a hook
    failure. An empty stdout from a failed hook must not read as allow.
    """
    expected_codes = JSON_VERDICT_EXIT_CODES if client in JSON_VERDICT_CLIENTS \
        else NATIVE_VERDICT_EXIT_CODES
    if result.returncode not in expected_codes:
        raise AssertionError(
            f"hook exited {result.returncode} for {client}: {result.stderr}")
    if client in JSON_VERDICT_CLIENTS:
        if not result.stdout.strip():
            return "allow"
        return json.loads(result.stdout)["decision"]
    if result.returncode == BLOCKING_EXIT_CODE:
        return "deny"
    if not result.stdout.strip():
        return "allow"
    return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]
