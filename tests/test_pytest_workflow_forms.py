#!/usr/bin/env python3
"""The command gate routes fixed pytest forms to consent (issue #121)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_command_execution_gate import _load_gate_module
from tests.test_enforce_branch_name import REPO_ROOT

PROJECT_DIR = str(REPO_ROOT)
EXECUTABLE_FILE_MODE = 0o755

# The gate parses only commands with an argument, so a bare `pytest` stays
# unmatched. `pytest .` runs the same collection.
ACCEPTED_COMMANDS = (
    "pytest .",
    "pytest tests",
    "pytest tests/test_example.py tests/unit",
    "pytest -q tests",
    "pytest -x -v --tb=short tests",
    "pytest -k smoke tests",
    "pytest -k 'parser and not slow' tests",
    "pytest -k 'alpha or beta_2' -q",
    "python -m pytest tests",
    "python3 -m pytest -q -x tests/unit",
    "python -m pytest -k smoke",
    "python -m pytest",
)
REJECTED_COMMANDS = (
    "pytest",
    "pytest --tb=long tests",
    "pytest -p no:cacheprovider tests",
    "pytest --rootdir=/ tests",
    "pytest -c other.ini tests",
    "pytest -k",
    "pytest -k 'parser(1)' tests",
    "pytest -k 'a; b' tests",
    "pytest -k 'and' tests",
    "pytest /etc",
    "pytest ../outside",
    "pytest tests/../../outside",
    "pytest tests; echo done",
    "pytest tests | tee out",
    "pytest tests $EXTRA",
    "pytest tests # comment",
    "python -m pytest --pdb tests",
    "python -c 'import pytest' tests",
)


class PytestFormTest(unittest.TestCase):
    """Accepted pytest forms ask; every other form stays unmatched or denied."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _load_gate_module()

    def decide(self, command: str, project_dir: str = PROJECT_DIR) -> tuple:
        """Return the untrusted gate decision with PATH lookup held outside the repository."""
        with patch.object(self.gate, "_repository_program_path", return_value=""), \
                patch.object(self.gate, "_trusted_project", return_value=False):
            return self.gate.workflow_decision(command, project_dir)

    def test_accepted_forms_ask(self) -> None:
        for command in ACCEPTED_COMMANDS:
            with self.subTest(command=command):
                decision, reason = self.decide(command)
                self.assertEqual(decision, "ask")
                self.assertIn("pytest", reason)

    def test_rejected_forms_never_reach_consent(self) -> None:
        for command in REJECTED_COMMANDS:
            with self.subTest(command=command):
                self.assertIn(self.decide(command)[0], ("", "deny"))

    def test_accepted_form_is_read_only(self) -> None:
        with patch.object(self.gate, "_repository_program_path", return_value=""), \
                patch.object(self.gate, "_trusted_project", return_value=False):
            self.assertTrue(self.gate.read_only_workflow("pytest -q tests", PROJECT_DIR))

    def test_chain_with_ruff_asks(self) -> None:
        decision, _reason = self.decide("ruff check src && pytest -q tests")
        self.assertEqual(decision, "ask")

    def test_hint_names_pytest_forms(self) -> None:
        hint = self.gate._accepted_forms_hint("pytest", ["--pdb"])
        self.assertIn("pytest", hint)
        module_hint = self.gate._accepted_forms_hint("python", ["-m", "pytest", "--pdb"])
        self.assertEqual(module_hint, hint)


class PytestLookupTest(unittest.TestCase):
    """Repository content cannot stand in for pytest."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.gate = _load_gate_module()

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_root_module_file_shadows(self) -> None:
        (self.root / "pytest.py").write_text("", encoding="utf-8")
        decision, reason = self.gate.workflow_decision("python -m pytest tests", str(self.root))
        self.assertEqual(decision, "deny")
        self.assertIn("pytest.py", reason)

    def test_root_package_shadows(self) -> None:
        (self.root / "pytest").mkdir()
        decision, reason = self.gate.workflow_decision("python3 -m pytest", str(self.root))
        self.assertEqual(decision, "deny")
        self.assertIn("shadows", reason)

    @unittest.skipIf(os.name == "nt", "The executable bit selects PATH programs on POSIX only")
    def test_repository_resolved_executable_is_denied(self) -> None:
        binaries = self.root / "bin"
        binaries.mkdir()
        program = binaries / "pytest"
        program.write_text("#!/bin/sh\n", encoding="utf-8")
        program.chmod(EXECUTABLE_FILE_MODE)
        with patch.dict(os.environ, {"PATH": str(binaries)}):
            decision, reason = self.gate.workflow_decision("pytest tests", str(self.root))
        self.assertEqual(decision, "deny")
        self.assertIn("bin/pytest", reason)


if __name__ == "__main__":
    unittest.main()
