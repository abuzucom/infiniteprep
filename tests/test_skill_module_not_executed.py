#!/usr/bin/env python3
"""Test that adoption checks never run the checked tree's skill module."""
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_gate_adoption

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
CHECKER = REPOSITORY_ROOT / "scripts" / "check_gate_adoption.py"
MARKER_NAME = "module-ran.txt"
# The planted module records any import through a marker file next to the
# candidate tree. It also defines every name the checker could look up.
PLANTED_MODULE = (
    "from pathlib import Path\n"
    "Path(__file__).resolve().parent.parent.parent.joinpath({marker!r}).write_text('ran')\n"
    "SKILLS = ()\n"
    "def skill_problems(root, names=()):\n"
    "    return []\n"
    "def is_regular_file(root, relative):\n"
    "    return True\n"
)


class CandidateSkillModuleTest(unittest.TestCase):
    """A candidate tree's skill module stays unexecuted in every mode."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temporary.name)
        cls.root = cls.base / "tree"
        shutil.copytree(REPOSITORY_ROOT, cls.root, symlinks=True,
                        ignore=shutil.ignore_patterns(".git", ".scratch", "__pycache__"))
        module = cls.root / "scripts" / "_skill_sync.py"
        module.write_text(PLANTED_MODULE.format(marker=MARKER_NAME), encoding="utf-8")
        cls.marker = cls.base / MARKER_NAME

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def tearDown(self):
        self.assertFalse(self.marker.exists(), "the checker ran the candidate skill module")

    def test_skill_functions_skip_candidate_module(self):
        check_gate_adoption.check_skill_files(self.root)
        check_gate_adoption.check_skill_content(self.root)

    def test_checker_modes_skip_candidate_module(self):
        for mode in ((), ("--fast",)):
            with self.subTest(mode=mode):
                subprocess.run([sys.executable, str(CHECKER), "--root", str(self.root), *mode],
                               capture_output=True, text=True, check=False, timeout=120)

    def test_planted_list_keeps_requirements(self):
        absent = self.root / "skills" / "pr-steward" / "SKILL.md"
        saved = absent.read_bytes()
        absent.unlink()
        try:
            findings = check_gate_adoption.check_skill_files(self.root)
        finally:
            absent.write_bytes(saved)
        self.assertTrue(any("skills/pr-steward/SKILL.md" in item for item in findings), findings)

    def test_symlinked_candidate_module_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "tree"
            (root / "scripts").mkdir(parents=True)
            outside = Path(temporary) / "outside.py"
            outside.write_text("SKILLS = ()\n", encoding="utf-8")
            (root / "scripts" / "_skill_sync.py").symlink_to(outside)
            findings = check_gate_adoption.check_skill_files(root)
        self.assertTrue(any("_skill_sync.py" in item for item in findings), findings)


if __name__ == "__main__":
    unittest.main()
