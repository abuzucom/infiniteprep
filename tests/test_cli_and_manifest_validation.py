#!/usr/bin/env python3
"""Command-line and manifest validation for sync.py and check_hook_coverage.py.

An unknown or conflicting flag exits 2 before any work. A manifest or
baseline that is not a JSON object fails with a recovery message instead of
an AttributeError traceback.
"""
import contextlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
USAGE_EXIT_CODE = 2
FAILURE_EXIT_CODE = 1


def load_script(name: str):
    """Import one script module from its repository path."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def forbid(*_arguments, **_keywords):
    """Fail the test when a guarded action runs."""
    raise AssertionError("a guarded action ran after an invalid argument")


def exit_code(function, argv: list) -> int:
    """Return the SystemExit code of `function(argv)` with stderr captured."""
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            function(argv)
        except SystemExit as stop:
            return stop.code
    raise AssertionError("expected a usage exit")


class SyncArgumentTest(unittest.TestCase):
    """sync.py accepts one known mode flag or none."""

    @classmethod
    def setUpClass(cls):
        cls.sync = load_script("sync")

    def test_unknown_flag_exits_before_writing(self):
        with patch.object(self.sync, "sync_copies", forbid):
            self.assertEqual(exit_code(self.sync.main, ["--chek"]), USAGE_EXIT_CODE)

    def test_abbreviated_flag_exits_before_writing(self):
        with patch.object(self.sync, "sync_copies", forbid):
            self.assertEqual(exit_code(self.sync.main, ["--chec"]), USAGE_EXIT_CODE)

    def test_conflicting_modes_exit_before_writing(self):
        with patch.object(self.sync, "sync_copies", forbid), \
                patch.object(self.sync, "write_shared", forbid):
            self.assertEqual(exit_code(self.sync.main, ["--check", "--write-shared"]),
                             USAGE_EXIT_CODE)

    def test_check_flag_runs_check_mode(self):
        calls = []
        with patch.object(self.sync, "sync_copies",
                          lambda check_only: calls.append(check_only) or 0), \
                patch.object(self.sync, "sync_digest_index", lambda root, check_only: 0):
            self.assertEqual(self.sync.main(["--check"]), 0)
        self.assertEqual(calls, [True])

    def test_no_flag_runs_write_mode(self):
        calls = []
        with patch.object(self.sync, "sync_copies",
                          lambda check_only: calls.append(check_only) or 0), \
                patch.object(self.sync, "sync_digest_index", lambda root, check_only: 0):
            self.assertEqual(self.sync.main([]), 0)
        self.assertEqual(calls, [False])


class SyncManifestTest(unittest.TestCase):
    """check_shared rejects a manifest that is not a JSON object."""

    @classmethod
    def setUpClass(cls):
        cls.sync = load_script("sync")

    def check_manifest(self, content: str) -> tuple[int, str]:
        """Run check_shared against a temporary manifest body."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / self.sync.SHARED_MANIFEST).write_text(content, encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = self.sync.check_shared(root)
        return code, stderr.getvalue()

    def test_array_manifest_fails_with_recovery(self):
        code, stderr = self.check_manifest("[1]")
        self.assertEqual(code, FAILURE_EXIT_CODE)
        self.assertIn("JSON object", stderr)
        self.assertIn("--write-shared", stderr)

    def test_non_object_shared_entry_fails(self):
        code, stderr = self.check_manifest(json.dumps({"shared": ["hooks/x.py"]}))
        self.assertEqual(code, FAILURE_EXIT_CODE)
        self.assertIn("JSON object", stderr)


class IdentityAllowPatternTest(unittest.TestCase):
    """check_git_identity.py treats an invalid --allow pattern as usage."""

    @classmethod
    def setUpClass(cls):
        cls.identity = load_script("check_git_identity")

    def run_main(self, argv: list) -> tuple[int, str]:
        """Run main() with `argv` and return its exit code and stderr."""
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["check_git_identity.py", *argv]), \
                patch.object(self.identity, "select_identities", forbid), \
                contextlib.redirect_stderr(stderr):
            try:
                code = self.identity.main()
            except SystemExit as stop:
                code = stop.code
        return code, stderr.getvalue()

    def test_invalid_pattern_exits_2(self):
        code, stderr = self.run_main(["--allow", "(["])
        self.assertEqual(code, USAGE_EXIT_CODE)
        self.assertIn("--allow", stderr)
        self.assertNotIn("Traceback", stderr)


class HookCoverageArgumentTest(unittest.TestCase):
    """check_hook_coverage.py accepts only --write-baseline."""

    @classmethod
    def setUpClass(cls):
        cls.coverage = load_script("check_hook_coverage")

    def test_unknown_flag_exits_before_measuring(self):
        with patch.object(self.coverage, "measure", forbid):
            self.assertEqual(exit_code(self.coverage.main, ["--write-baselin"]),
                             USAGE_EXIT_CODE)


class HookCoverageBaselineTest(unittest.TestCase):
    """A baseline that is not a JSON object fails with a recovery message."""

    @classmethod
    def setUpClass(cls):
        cls.coverage = load_script("check_hook_coverage")

    def read(self, content: str):
        """Call read_baseline on a temporary baseline body."""
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, self.coverage.BASELINE).write_text(content, encoding="utf-8")
            return self.coverage.read_baseline(temporary)

    def test_array_baseline_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            self.read("[1]")
        self.assertIn("JSON object", str(caught.exception))

    def test_non_object_unreached_is_rejected(self):
        with self.assertRaises(ValueError):
            self.read(json.dumps({"unreached": [1]}))

    def test_main_reports_malformed_baseline(self):
        def malformed(_root):
            raise ValueError("hook-coverage-baseline.json must hold a JSON object")
        stderr = io.StringIO()
        with patch.object(self.coverage, "measure", lambda root: {}), \
                patch.object(self.coverage, "read_baseline", malformed), \
                contextlib.redirect_stderr(stderr):
            code = self.coverage.main([])
        self.assertEqual(code, FAILURE_EXIT_CODE)
        self.assertIn("Recovery:", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
