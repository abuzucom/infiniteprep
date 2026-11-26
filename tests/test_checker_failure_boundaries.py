#!/usr/bin/env python3
"""Checkers report malformed input and internal failures differently.

A parse failure stays a "malformed ... cannot be checked" finding. A defect in
the analysis code must not hide behind that message: find_violations raises,
and main() reports an internal checker failure with exit code 1.
"""
import contextlib
import importlib.util
import io
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
FAILURE_EXIT_CODE = 1
MALFORMED_YAML = "key: [unclosed\n"
MALFORMED_PYTHON = "def broken(:\n"
VALID_COMPOSE = "services:\n  app:\n    image: example\n    user: app\n"
VALID_WORKFLOW = (
    "jobs:\n  build:\n    steps:\n      - uses: actions/checkout@v4\n"
    "        with:\n          persist-credentials: false\n"
)
VALID_PYTHON = "import hashlib\nhashlib.sha256(b'x')\n"
INJECTED_DEFECT = AttributeError("injected analysis defect")


def load_script(name: str):
    """Import one script module from its repository path."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def raise_defect(*_arguments, **_keywords):
    """Simulate a programming defect inside checker analysis code."""
    raise INJECTED_DEFECT


def run_main(module, file_name: str, text: str) -> tuple[int, str]:
    """Run one checker main() on a temporary file and capture stderr."""
    with tempfile.TemporaryDirectory() as temporary:
        path = Path(temporary) / file_name
        path.write_text(text, encoding="utf-8")
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["checker", str(path)]), \
                contextlib.redirect_stderr(stderr):
            code = module.main()
    return code, stderr.getvalue()


class CheckerBoundaryCase:
    """Shared assertions. Subclasses set the checker and its fixtures."""

    module_name = ""
    file_name = ""
    malformed_text = ""
    malformed_marker = ""
    valid_text = ""
    defect_target = ""

    @classmethod
    def setUpClass(cls):
        cls.module = load_script(cls.module_name)

    def test_malformed_input_reported(self):
        findings = self.module.find_violations(self.malformed_text, self.file_name)
        self.assertEqual(len(findings), 1)
        self.assertIn(self.malformed_marker, findings[0])

    def test_analysis_defect_not_malformed(self):
        with patch.object(self.module, self.defect_target, raise_defect):
            with self.assertRaises(AttributeError):
                self.module.find_violations(self.valid_text, self.file_name)

    def test_main_names_internal_failure(self):
        with patch.object(self.module, self.defect_target, raise_defect):
            code, stderr = run_main(self.module, self.file_name, self.valid_text)
        self.assertEqual(code, FAILURE_EXIT_CODE)
        self.assertIn("internal checker failure (AttributeError)", stderr)
        self.assertIn("Recovery:", stderr)
        self.assertNotIn("malformed", stderr)


class DockerfileRootBoundaryTest(CheckerBoundaryCase, unittest.TestCase):
    module_name = "check_dockerfile_root"
    file_name = "compose.yml"
    malformed_text = MALFORMED_YAML
    malformed_marker = "malformed YAML cannot be checked (Rule 12)"
    valid_text = VALID_COMPOSE
    defect_target = "_compose_violations"


class PersistCredentialsBoundaryTest(CheckerBoundaryCase, unittest.TestCase):
    module_name = "check_persist_credentials"
    file_name = "workflow.yml"
    malformed_text = MALFORMED_YAML
    malformed_marker = "malformed YAML cannot be checked (Rule 11)"
    valid_text = VALID_WORKFLOW
    defect_target = "_checkout_violation"


class WeakHashingBoundaryTest(CheckerBoundaryCase, unittest.TestCase):
    module_name = "check_weak_hashing"
    file_name = "sample.py"
    malformed_text = MALFORMED_PYTHON
    malformed_marker = "malformed Python cannot be checked (Rule 7)"
    valid_text = VALID_PYTHON
    defect_target = "_python_comments"


class ComplianceTreeWorkflowYamlTest(unittest.TestCase):
    """The compliance tree catches only YAML errors around workflow loading."""

    @classmethod
    def setUpClass(cls):
        cls.tree = load_script("check_compliance_tree")
        cls.persist = load_script("check_persist_credentials")

    def test_malformed_workflow_yaml_is_reported(self):
        findings = self.tree._workflow_violations(
            MALFORMED_YAML, "w.yml", self.persist)
        self.assertEqual(findings, ["w.yml: malformed workflow YAML cannot be checked"])

    def test_loader_defect_not_malformed(self):
        broken = SimpleNamespace(
            load_documents=raise_defect,
            yaml=SimpleNamespace(YAMLError=self.persist.yaml.YAMLError))
        with self.assertRaises(AttributeError):
            self.tree._workflow_violations(VALID_WORKFLOW, "w.yml", broken)


if __name__ == "__main__":
    unittest.main()
