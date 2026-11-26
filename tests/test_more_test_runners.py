#!/usr/bin/env python3
"""pnpm, yarn, go, and cargo test runners and the per-adopter script list."""
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_command_execution_gate import _load_gate_module

LOCAL_LIST_NAME = "workflow-scripts.local.txt"
MAX_SCRIPT_LIST_BYTES = 64 * 1024
COMPILED_CASES = (("go", "go.mod"), ("cargo", "Cargo.toml"))
JS_CASES = (("pnpm", "pnpm-lock.yaml", ".npmrc"), ("yarn", "yarn.lock", ".yarnrc.yml"))


def _write_programs(directory: Path, names: tuple) -> None:
    """Place inert launchers where shutil.which finds them."""
    directory.mkdir(parents=True, exist_ok=True)
    for name in names:
        for launcher_name in (name, f"{name}.cmd"):
            launcher = directory / launcher_name
            launcher.write_text("exit 0\n", encoding="utf-8")
            launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


class RunnerFixture(unittest.TestCase):
    """One temporary repository with runner launchers outside it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _load_gate_module()

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "repository"
        self.root.mkdir()
        self.tools = self.base / "tools"
        _write_programs(self.tools, ("go", "cargo", "pnpm", "yarn"))
        path_patch = patch.dict(os.environ, {"PATH": str(self.tools)})
        path_patch.start()
        self.addCleanup(path_patch.stop)

    def write(self, relative: str, content: str = "") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def decide(self, command: str, trusted: bool) -> tuple:
        with patch.object(self.gate, "_trusted_project", return_value=trusted):
            return self.gate.workflow_decision(command, str(self.root))


class CompiledRunnerTest(RunnerFixture):
    """go test and cargo test need their manifest and a fixed argument shape."""

    def test_trusted_runners_allow(self) -> None:
        commands = {"go": ("go test ./...", "go test -v -count=1 -run TestParse ./pkg/..."),
                    "cargo": ("cargo test", "cargo test -q --no-fail-fast parse_header")}
        for program, manifest in COMPILED_CASES:
            self.write(manifest)
            for command in commands[program]:
                with self.subTest(command=command):
                    self.assertEqual(self.decide(command, True), ("allow", ""))
                    self.assertEqual(self.decide(command, False)[0], "ask")

    def test_missing_manifest_unmatched(self) -> None:
        self.assertEqual(self.decide("go test ./...", True), ("", ""))
        self.assertEqual(self.decide("cargo test", True), ("", ""))

    @unittest.skipIf(os.name == "nt", "Symlink creation needs privileges on Windows")
    def test_linked_manifest_unmatched(self) -> None:
        outside = self.base / "outside.mod"
        outside.write_text("module x\n", encoding="utf-8")
        (self.root / "go.mod").symlink_to(outside)
        self.assertEqual(self.decide("go test ./...", True), ("", ""))

    def test_other_shapes_unmatched(self) -> None:
        self.write("go.mod")
        self.write("Cargo.toml")
        for command in ("go test -exec x ./...", "go test -run A -run B ./...",
                        "go test -run 'A|B' ./...", "go test ../outside", "go build ./...",
                        "cargo test --config x", "cargo test a b", "cargo bench",
                        "cargo test -- --nocapture"):
            with self.subTest(command=command):
                self.assertIn(self.decide(command, True)[0], ("", "deny"))

    def test_repository_program_denies(self) -> None:
        self.write("go.mod")
        _write_programs(self.root / "bin", ("go",))
        with patch.dict(os.environ, {"PATH": str(self.root / "bin")}):
            decision, reason = self.decide("go test ./...", True)
        self.assertEqual(decision, "deny", reason)
        self.assertIn("inside the repository", reason)


class JsRunnerTest(RunnerFixture):
    """pnpm test and yarn test follow the npm checks with their own lockfile."""

    def write_manifest(self, scripts: dict) -> None:
        self.write("package.json", json.dumps({"name": "fixture", "scripts": scripts}))

    def test_trusted_allow_untrusted_lists(self) -> None:
        self.write_manifest({"test": "vitest run", "pretest": "echo setup"})
        for program, lockfile, _config in JS_CASES:
            self.write(lockfile)
            with self.subTest(program=program):
                self.assertEqual(self.decide(f"{program} test", True), ("allow", ""))
                decision, reason = self.decide(f"{program} test", False)
                self.assertEqual(decision, "ask")
                self.assertIn('test="vitest run"', reason)

    def test_missing_lockfile_denies(self) -> None:
        self.write_manifest({"test": "vitest run"})
        for program, lockfile, _config in JS_CASES:
            with self.subTest(program=program):
                decision, reason = self.decide(f"{program} test", True)
                self.assertEqual(decision, "deny")
                self.assertIn(lockfile, reason)

    def test_missing_script_denies(self) -> None:
        self.write_manifest({"lint": "eslint ."})
        for program, lockfile, _config in JS_CASES:
            self.write(lockfile)
            with self.subTest(program=program):
                self.assertEqual(self.decide(f"{program} test", True)[0], "deny")

    def test_project_config_denies(self) -> None:
        self.write_manifest({"test": "vitest run"})
        for program, lockfile, config in JS_CASES:
            self.write(lockfile)
            self.write(config, "x\n")
            with self.subTest(program=program):
                decision, reason = self.decide(f"{program} test", True)
                self.assertEqual(decision, "deny")
                self.assertIn(config, reason)
        self.write(".yarnrc", "x\n")
        (self.root / ".yarnrc.yml").unlink()
        self.assertEqual(self.decide("yarn test", True)[0], "deny")

    def test_unusable_inputs_deny(self) -> None:
        self.write("pnpm-lock.yaml")
        decision, reason = self.decide("pnpm test", True)
        self.assertEqual(decision, "deny")
        self.assertIn("package.json", reason)
        self.write_manifest({"test": "vitest run\u0007"})
        self.assertEqual(self.decide("pnpm test", True)[0], "deny")

    def test_runner_off_path_denies(self) -> None:
        self.write_manifest({"test": "vitest run"})
        self.write("yarn.lock")
        with patch.dict(os.environ, {"PATH": str(self.base / "empty")}):
            decision, reason = self.decide("yarn test", True)
        self.assertEqual(decision, "deny")
        self.assertIn("not on PATH", reason)

    def test_other_subcommands_unmatched(self) -> None:
        self.write_manifest({"test": "vitest run"})
        for command in ("pnpm install", "yarn test --watch", "yarn add x"):
            with self.subTest(command=command):
                self.assertEqual(self.decide(command, True), ("", ""))

    def test_repository_program_denies(self) -> None:
        self.write_manifest({"test": "vitest run"})
        self.write("pnpm-lock.yaml")
        _write_programs(self.root / "bin", ("pnpm",))
        with patch.dict(os.environ, {"PATH": str(self.root / "bin")}):
            decision, reason = self.decide("pnpm test", True)
        self.assertEqual(decision, "deny", reason)
        self.assertIn("inside the repository", reason)


class LocalScriptListTest(unittest.TestCase):
    """hooks/workflow-scripts.local.txt adds adopter entries to the shared list."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "repository"
        (self.root / "scripts").mkdir(parents=True)
        (self.root / "scripts" / "local_check.py").write_text("", encoding="utf-8")
        (self.root / "scripts" / "check_changelog.py").write_text("", encoding="utf-8")
        self.gate = _load_gate_module()
        self.local = self.base / LOCAL_LIST_NAME

    def decide(self, command: str, content=None) -> tuple:
        if content is not None:
            self.local.write_bytes(content if isinstance(content, bytes) else content.encode())
        with patch.object(self.gate, "LOCAL_WORKFLOW_SCRIPT_LIST_PATH", str(self.local)):
            self.gate._load_workflow_scripts.cache_clear()
            try:
                return self.gate.workflow_decision(command, str(self.root))
            finally:
                self.gate._load_workflow_scripts.cache_clear()

    def test_absent_list_adds_nothing(self) -> None:
        self.assertEqual(self.decide("python scripts/local_check.py"), ("", ""))
        self.assertEqual(self.decide("python scripts/check_changelog.py")[0], "allow")

    def test_local_entry_applies(self) -> None:
        decision, _reason = self.decide("python scripts/local_check.py",
                                        "# adopter scripts\nallow scripts/local_check.py\n")
        self.assertEqual(decision, "allow")

    def test_unreadable_local_list_denies(self) -> None:
        self.local.mkdir()
        decision, reason = self.decide("python scripts/check_changelog.py")
        self.assertEqual(decision, "deny")
        self.assertIn(LOCAL_LIST_NAME, reason)

    def test_bad_local_list_denies(self) -> None:
        oversized = b"# filler\n" * (MAX_SCRIPT_LIST_BYTES // len(b"# filler\n") + 1)
        cases = (("duplicate", "allow scripts/check_changelog.py\n"),
                 ("malformed", "permit scripts/local_check.py\n"),
                 ("oversized", oversized))
        for label, content in cases:
            with self.subTest(case=label):
                decision, reason = self.decide("python scripts/check_changelog.py", content)
                self.assertEqual(decision, "deny")
                self.assertIn(LOCAL_LIST_NAME, reason)


if __name__ == "__main__":
    unittest.main()
