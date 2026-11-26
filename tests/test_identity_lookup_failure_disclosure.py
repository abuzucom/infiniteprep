#!/usr/bin/env python3
"""The identity advisory names a failed authenticated lookup by type only."""
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
FAILURE_PREFIX = "authenticated lookup failed ({}); "
SENSITIVE_DETAIL = "ghp_example_detail_value"
LOOKUP_TIMEOUT_SECONDS = 5
HISTORY_CANDIDATE = ("Example Person", "1+example@users.noreply.github.com")


def load_script(name: str):
    """Import one script module from its repository path."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve annotations through sys.modules during class creation.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class LookupFailureDisclosureTest(unittest.TestCase):
    """A failed gh lookup is disclosed before the history fallback."""

    @classmethod
    def setUpClass(cls):
        cls.checker = load_script("check_git_identity")

    def advisory(self, error: BaseException, candidates: list) -> str:
        """Return the advisory when the authenticated lookup raises `error`."""
        def failing_lookup(_repository):
            raise error
        with tempfile.TemporaryDirectory() as temporary:
            with patch.object(self.checker, "authenticated_account", failing_lookup), \
                    patch.object(self.checker, "history_identity_candidates",
                                 return_value=candidates):
                return self.checker.bootstrap_identity_advisory(temporary)

    def test_timeout_named_first(self):
        error = subprocess.TimeoutExpired(["gh"], LOOKUP_TIMEOUT_SECONDS)
        advisory = self.advisory(error, [])
        self.assertTrue(advisory.startswith(FAILURE_PREFIX.format("TimeoutExpired")))
        self.assertIn("identity candidate unavailable", advisory)

    def test_failure_named_before_candidates(self):
        advisory = self.advisory(OSError("network down"), [HISTORY_CANDIDATE])
        self.assertTrue(advisory.startswith(FAILURE_PREFIX.format("OSError")))
        self.assertIn("never auto-select", advisory)

    def test_exception_message_never_appears(self):
        advisory = self.advisory(ValueError(SENSITIVE_DETAIL), [])
        self.assertTrue(advisory.startswith(FAILURE_PREFIX.format("ValueError")))
        self.assertNotIn(SENSITIVE_DETAIL, advisory)


class RepositoryRelativePathTest(unittest.TestCase):
    """prose_policy keeps paths outside the repository unchanged."""

    @classmethod
    def setUpClass(cls):
        cls.prose_policy = load_script("prose_policy")

    def test_absolute_inside_path_becomes_relative(self):
        inside = REPO_ROOT / "docs" / "agent-policy" / "clients.md"
        self.assertEqual(self.prose_policy._normalize_path(inside),
                         "docs/agent-policy/clients.md")

    def test_absolute_outside_path_stays_absolute(self):
        with tempfile.TemporaryDirectory() as temporary:
            outside = Path(temporary).resolve() / "notes.md"
            self.assertEqual(self.prose_policy._normalize_path(outside),
                             str(outside).replace("\\", "/"))

    def test_relative_path_drops_dot_prefix(self):
        self.assertEqual(self.prose_policy._normalize_path("./plan/HANDOFF.md.example"),
                         "plan/HANDOFF.md.example")


if __name__ == "__main__":
    unittest.main()
