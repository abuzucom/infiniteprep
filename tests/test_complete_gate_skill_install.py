#!/usr/bin/env python3
"""Test skill copy installation and rollback in complete_gate_adoption.py."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "complete_gate_adoption.py"


def load_script():
    """Load the transaction installer from its checked-in path."""
    spec = importlib.util.spec_from_file_location("complete_gate_skill_install", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def fail_validation() -> None:
    """Stand in for a failed post-install validation."""
    raise ValueError("candidate fails check_gate_adoption.py")


class CompleteGateSkillInstallTest(unittest.TestCase):
    """The installer copies every required skill and rolls back cleanly."""

    def setUp(self) -> None:
        self.script = load_script()

    def test_candidate_paths_include_skills(self) -> None:
        required = self.script.adoption.required_skill_files()
        self.assertTrue(required)
        with tempfile.TemporaryDirectory() as temporary:
            candidate = Path(temporary)
            (candidate / "shared-files.json").write_text(
                json.dumps({"shared": {}}), encoding="utf-8")
            paths = self.script._candidate_paths(candidate)
        for relative in required:
            self.assertIn(relative, paths)
        self.assertIn(".claude/skills/pr-steward/SKILL.md", paths)
        self.assertIn(".agents/skills/pr-steward/SKILL.md", paths)

    def test_failed_install_removes_created_directories(self) -> None:
        paths = ["skills/alpha/SKILL.md", "skills/beta/SKILL.md"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            candidate = Path(temporary) / "candidate"
            root.mkdir()
            for relative in paths:
                source = candidate / relative
                source.parent.mkdir(parents=True)
                source.write_text("skill\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                self.script._install_paths(root, candidate, paths, fail_validation)
            remaining = sorted(
                entry.name for entry in root.iterdir()
                if not entry.name.startswith(".gate-adoption-"))
        self.assertEqual(remaining, [])

    def test_failed_install_keeps_existing_directory(self) -> None:
        paths = ["skills/alpha/SKILL.md"]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "root"
            candidate = Path(temporary) / "candidate"
            (root / "skills").mkdir(parents=True)
            prior = root / "skills" / "README.md"
            prior.write_text("prior\n", encoding="utf-8")
            source = candidate / paths[0]
            source.parent.mkdir(parents=True)
            source.write_text("skill\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                self.script._install_paths(root, candidate, paths, fail_validation)
            self.assertEqual(prior.read_text(encoding="utf-8"), "prior\n")
            self.assertEqual(sorted(entry.name for entry in (root / "skills").iterdir()),
                             ["README.md"])


if __name__ == "__main__":
    unittest.main()
