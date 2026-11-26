#!/usr/bin/env python3
"""Characterize upward .git discovery in hooks/_gate_core.py.

A missing .git entry continues the search. A present entry is returned. Any
other inspection error fails closed with a reason.
"""
import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOKS = REPO_ROOT / "hooks"
UNREADABLE_REASON = "a parent repository entry could not be inspected"


def load_core():
    """Import the shared gate core from its repository path."""
    if str(HOOKS) not in sys.path:
        sys.path.insert(0, str(HOOKS))
    spec = importlib.util.spec_from_file_location("_gate_core", HOOKS / "_gate_core.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["_gate_core"] = module
    spec.loader.exec_module(module)
    return module


core = load_core()


class DiscoverDotGitTest(unittest.TestCase):
    """Present, absent, and unreadable .git entries keep their verdicts."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(os.path.realpath(self.temporary.name))
        self.nested = self.root / "repo" / "sub"
        self.nested.mkdir(parents=True)

    def tearDown(self):
        self.temporary.cleanup()

    def test_entry_in_parent_is_found(self):
        dot_git = self.root / "repo" / ".git"
        dot_git.mkdir()
        self.assertEqual(core._discover_dot_git(str(self.nested)), (str(dot_git), ""))

    def test_pointer_file_is_found(self):
        dot_git = self.nested / ".git"
        dot_git.write_text("gitdir: elsewhere\n", encoding="utf-8")
        self.assertEqual(core._discover_dot_git(str(self.nested)), (str(dot_git), ""))

    def test_unreadable_entry_fails_closed(self):
        real_stat = os.stat
        blocked = str(self.nested / ".git")

        def stat_with_denied_entry(path, *arguments, **keywords):
            """Deny inspection of one .git path and pass the rest through."""
            if os.fspath(path) == blocked:
                raise PermissionError(13, "denied", blocked)
            return real_stat(path, *arguments, **keywords)

        with patch.object(core.os, "stat", stat_with_denied_entry):
            self.assertEqual(core._discover_dot_git(str(self.nested)),
                             (None, UNREADABLE_REASON))

    def test_missing_entries_continue_upward(self):
        found, reason = core._discover_dot_git(str(self.nested))
        self.assertEqual(reason, "")
        if found:
            self.assertFalse(Path(found).is_relative_to(self.nested))


if __name__ == "__main__":
    unittest.main()
