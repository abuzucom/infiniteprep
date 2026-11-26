#!/usr/bin/env python3
"""Test runners allow without a prompt only for a trusted origin owner."""
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_command_execution_gate import _load_gate_module
from tests.test_npm_workflow_gate import NpmFixture

# A hostile owner file must parse well inside one second.
HOSTILE_PARSE_SECONDS = 1.0
OWNER_FILE_LIMIT_BYTES = 4096
TRUSTED_OWNER = "trusted-org"
OTHER_OWNER = "other-org"
GIT_TIMEOUT_SECONDS = 15
RUNNER_COMMANDS = (
    "pytest tests",
    "pytest -q tests",
    "python -m pytest tests",
    "python scripts/run_tests.py",
    "make test",
    "make test PYTHON=python",
)


def _load_trust_module():
    """Import hooks/_trust.py after the gate loader puts hooks/ on sys.path."""
    _load_gate_module()
    import _trust
    return _trust


def _write_owner_list(directory: Path, text: str) -> Path:
    """Write one owner list file and return its path."""
    path = directory / "trusted-owners.txt"
    path.write_bytes(text.encode("utf-8"))
    return path


class TrustedOwnerListTest(unittest.TestCase):
    """The owner list trusts exactly its listed owners and fails closed."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.trust = _load_trust_module()

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)

    def read(self, text: str) -> frozenset:
        return self.trust.read_trusted_owners(str(_write_owner_list(self.directory, text)))

    def test_listed_owner_trusted(self) -> None:
        owners = self.read(f"# comment\n{TRUSTED_OWNER}\n\n")
        self.assertEqual(owners, frozenset({TRUSTED_OWNER}))

    def test_owner_case_folds(self) -> None:
        owners = self.read("Trusted-Org\n")
        self.assertTrue(self.trust.owner_in(owners, "TRUSTED-org"))

    def test_unlisted_and_empty_untrusted(self) -> None:
        owners = self.read(f"{TRUSTED_OWNER}\n")
        self.assertFalse(self.trust.owner_in(owners, OTHER_OWNER))
        self.assertFalse(self.trust.owner_in(owners, ""))

    def test_bad_files_trust_nobody(self) -> None:
        cases = {
            "malformed": f"{TRUSTED_OWNER} extra\n",
            "leading hyphen": "-org\n",
            "too long": "a" * 40 + "\n",
            "oversized": "a\n" * (OWNER_FILE_LIMIT_BYTES // 2 + 1),
        }
        for label, text in cases.items():
            with self.subTest(case=label):
                self.assertEqual(self.read(text), frozenset())
        path = self.directory / "trusted-owners.txt"
        path.write_bytes(b"caf\xc3\xa9\n")
        self.assertEqual(self.trust.read_trusted_owners(str(path)), frozenset())
        self.assertEqual(self.trust.read_trusted_owners(str(self.directory / "absent.txt")),
                         frozenset())

    def test_hostile_line_parses_fast(self) -> None:
        started = time.monotonic()
        self.read("a" + "-" * (OWNER_FILE_LIMIT_BYTES - 3) + "!\n")
        self.assertLess(time.monotonic() - started, HOSTILE_PARSE_SECONDS)


class TrustedRunnerVerdictTest(unittest.TestCase):
    """Runner forms allow for a trusted project and ask otherwise."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _load_gate_module()

    def decide(self, command: str, trusted: bool, project_dir: str = None) -> tuple:
        directory = project_dir or str(Path(__file__).resolve().parent.parent)
        with patch.object(self.gate, "_repository_program_path", return_value=""), \
                patch.object(self.gate, "_trusted_project", return_value=trusted):
            return self.gate.workflow_decision(command, directory)

    def test_trusted_runners_allow(self) -> None:
        for command in RUNNER_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(self.decide(command, True), ("allow", ""))

    def test_untrusted_runners_ask(self) -> None:
        for command in RUNNER_COMMANDS:
            with self.subTest(command=command):
                self.assertEqual(self.decide(command, False)[0], "ask")

    def test_other_make_targets_ask(self) -> None:
        self.assertEqual(self.decide("make lint PYTHON=python", True)[0], "ask")

    def test_argument_files_rejected(self) -> None:
        for command in ("pytest @args.txt", "pytest tests @args.txt", "python -m pytest @x"):
            with self.subTest(command=command):
                self.assertIn(self.decide(command, True)[0], ("", "deny"))

    def test_trusted_shadow_still_denies(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            (Path(temporary) / "pytest.py").write_text("", encoding="utf-8")
            decision, reason = self.decide("python -m pytest tests", True, temporary)
        self.assertEqual(decision, "deny", reason)

    def decide_repository_program(self, command: str) -> str:
        with patch.object(self.gate, "_repository_program_path", return_value="bin/tool"), \
                patch.object(self.gate, "_trusted_project", return_value=True):
            return self.gate.workflow_decision(command, str(Path.cwd()))[0]

    def test_repository_pytest_denies(self) -> None:
        self.assertEqual(self.decide_repository_program("pytest tests"), "deny")

    def test_repository_make_asks(self) -> None:
        self.assertEqual(self.decide_repository_program("make test"), "ask")

    def test_trusted_read_verdict_parses(self) -> None:
        entries = self.gate._parse_workflow_scripts("trusted-read scripts/x.py\n")
        self.assertEqual(entries, (("scripts/x.py", (), "trusted-read"),))
        with self.assertRaises(ValueError):
            self.gate._parse_workflow_scripts("maybe scripts/x.py\n")


class TrustedNpmTestTest(NpmFixture):
    """npm test allows for a trusted project after every npm check."""

    def decide_trusted(self, command: str, trusted: bool) -> tuple:
        with patch.object(self.gate, "_trusted_project", return_value=trusted):
            return self.decide(command)

    def test_trusted_npm_test_allows(self) -> None:
        self.write_manifest({"test": "vitest run"})
        self.assertEqual(self.decide_trusted("npm test", True), ("allow", ""))
        self.assertEqual(self.decide_trusted("npm test", False)[0], "ask")

    def test_trusted_npm_denials_stay(self) -> None:
        self.write_manifest({"lint": "eslint ."})
        self.assertEqual(self.decide_trusted("npm test", True)[0], "deny")
        self.write_manifest({"test": "vitest run"})
        self.write_file(".npmrc", "script-shell=/bin/sh\n")
        self.assertEqual(self.decide_trusted("npm test", True)[0], "deny")

    def test_trusted_npm_ci_asks(self) -> None:
        self.write_manifest({"test": "vitest run"})
        self.write_file("package-lock.json", "{}")
        self.assertEqual(self.decide_trusted("npm ci", True)[0], "ask")


class OriginOwnerIntegrationTest(unittest.TestCase):
    """The origin owner of a real checkout decides trust."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _load_gate_module()
        cls.trust = _load_trust_module()

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.root = self.base / "repository"
        self.root.mkdir()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True,
                       timeout=GIT_TIMEOUT_SECONDS)
        owner_list = _write_owner_list(self.base, f"{TRUSTED_OWNER}\n")
        path_patch = patch.object(self.trust, "TRUSTED_OWNERS_PATH", str(owner_list))
        path_patch.start()
        self.addCleanup(path_patch.stop)
        self.trust.load_trusted_owners.cache_clear()
        self.addCleanup(self.trust.load_trusted_owners.cache_clear)

    def decide_with_origin(self, owner: str) -> str:
        subprocess.run(["git", "-C", str(self.root), "remote", "add", "origin",
                        f"https://github.com/{owner}/project.git"],
                       check=True, timeout=GIT_TIMEOUT_SECONDS)
        self.gate.core.reset_invocation_caches()
        with patch.object(self.gate, "_repository_program_path", return_value=""):
            return self.gate.workflow_decision("pytest tests", str(self.root))[0]

    def test_listed_origin_allows(self) -> None:
        self.assertEqual(self.decide_with_origin(TRUSTED_OWNER), "allow")

    def test_unlisted_origin_asks(self) -> None:
        self.assertEqual(self.decide_with_origin(OTHER_OWNER), "ask")


if __name__ == "__main__":
    sys.exit(unittest.main())
