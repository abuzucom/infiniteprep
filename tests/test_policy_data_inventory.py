#!/usr/bin/env python3
"""Policy data files that checkers read must be present and adopted.

A missing branch ban list fails branch validation closed. The adoption
inventory names the ban list and the shared YAML module, so an adopter that
omits either one fails the gate adoption check.
"""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"
COMPLIANT_BRANCH = "fix/branch-name-validation"
INVENTORIED_FILES = ("scripts/branch_name_bans.txt", "scripts/_yaml_lines.py",
                     "scripts/_commit_trailers.py", "scripts/_pr_event.py")
# check_branch_name.py omits these two stopwords on purpose. See the comment
# beside FOREIGN_TOKENS.
ENGLISH_COLLISIONS = {"mit", "uma"}


def load_script(name: str):
    """Import one script module from its repository path."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class BanListFailClosedTest(unittest.TestCase):
    """An unreadable ban list makes every branch invalid with a recovery step."""

    @classmethod
    def setUpClass(cls):
        cls.branch = load_script("check_branch_name")

    def test_present_list_accepts_compliant_branch(self):
        self.assertEqual(self.branch.find_violations(COMPLIANT_BRANCH), [])

    def test_missing_list_rejects_compliant_branch(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "branch_name_bans.txt"
            with patch.object(self.branch, "BRANCH_BANNED_TOKENS_PATH", missing):
                findings = self.branch.find_violations(COMPLIANT_BRANCH)
        self.assertEqual(len(findings), 1)
        self.assertIn("branch_name_bans.txt", findings[0])
        self.assertIn("Recovery:", findings[0])

    def test_non_ascii_list_rejects_branch(self):
        with tempfile.TemporaryDirectory() as temporary:
            unreadable = Path(temporary) / "branch_name_bans.txt"
            unreadable.write_bytes("caf\xe9\n".encode("utf-8"))
            with patch.object(self.branch, "BRANCH_BANNED_TOKENS_PATH", unreadable):
                findings = self.branch.find_violations(COMPLIANT_BRANCH)
        self.assertEqual(len(findings), 1)
        self.assertIn("Recovery:", findings[0])


class ForeignTokenParityTest(unittest.TestCase):
    """The branch stopword list tracks the English-only checker list."""

    def test_branch_tokens_match_stopwords(self):
        branch = load_script("check_branch_name")
        english = load_script("check_english_only")
        self.assertEqual(set(branch.FOREIGN_TOKENS),
                         set(english.FOREIGN_STOPWORDS) - ENGLISH_COLLISIONS)


class AdoptionInventoryTest(unittest.TestCase):
    """An adopted tree without a checker data file fails adoption."""

    @classmethod
    def setUpClass(cls):
        cls.adoption = load_script("check_gate_adoption")

    def test_each_data_file_is_required(self):
        with tempfile.TemporaryDirectory() as temporary:
            findings = self.adoption.check_required_files(Path(temporary))
        for relative in INVENTORIED_FILES:
            with self.subTest(relative=relative):
                self.assertIn(f"{relative} is absent from the adopted tree", findings)

    def test_source_tree_data_files(self):
        findings = self.adoption.check_required_files(REPO_ROOT)
        for relative in INVENTORIED_FILES:
            with self.subTest(relative=relative):
                self.assertNotIn(f"{relative} is absent from the adopted tree", findings)


if __name__ == "__main__":
    unittest.main()
