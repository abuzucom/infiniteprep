#!/usr/bin/env python3
"""Test canonical skill validation and client copy synchronization."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import _skill_sync
import sync

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
FIXTURE_SKILLS = ("alpha-skill", "beta-skill")
VALID_BODY = "---\nname: {name}\ndescription: Fixture skill for tests.\n---\n\nStep one.\n"


def _write_canonical(root: Path, name: str, text: str) -> Path:
    """Write one canonical skill file below a temporary root."""
    path = root / _skill_sync.canonical_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class SkillCopySyncTest(unittest.TestCase):
    """Copies match the canonical skill byte for byte."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        for name in FIXTURE_SKILLS:
            _write_canonical(self.root, name, VALID_BODY.format(name=name))

    def test_missing_copy_fails_check(self):
        problems = _skill_sync.skill_problems(self.root, FIXTURE_SKILLS)
        self.assertTrue(any("absent" in problem for problem in problems), problems)

    def test_sync_writes_every_copy(self):
        self.assertEqual(_skill_sync.sync_skills(self.root, FIXTURE_SKILLS), [])
        self.assertEqual(_skill_sync.skill_problems(self.root, FIXTURE_SKILLS), [])
        for name in FIXTURE_SKILLS:
            expected = (self.root / _skill_sync.canonical_path(name)).read_bytes()
            for relative in _skill_sync.copy_paths(name):
                self.assertEqual((self.root / relative).read_bytes(), expected)

    def test_sync_rerun_changes_nothing(self):
        _skill_sync.sync_skills(self.root, FIXTURE_SKILLS)
        copy = self.root / _skill_sync.copy_paths(FIXTURE_SKILLS[0])[0]
        before = copy.stat().st_mtime_ns
        self.assertEqual(_skill_sync.sync_skills(self.root, FIXTURE_SKILLS), [])
        self.assertEqual(copy.stat().st_mtime_ns, before)

    def test_stale_copy_fails_check(self):
        _skill_sync.sync_skills(self.root, FIXTURE_SKILLS)
        copy = self.root / _skill_sync.copy_paths(FIXTURE_SKILLS[0])[1]
        copy.write_text("stale\n", encoding="utf-8")
        problems = _skill_sync.skill_problems(self.root, FIXTURE_SKILLS)
        self.assertTrue(any("stale" in problem for problem in problems), problems)

    def test_unexpected_skill_folder_reported(self):
        _skill_sync.sync_skills(self.root, FIXTURE_SKILLS)
        for root in (_skill_sync.SKILL_SOURCE_ROOT,) + _skill_sync.SKILL_COPY_ROOTS:
            with self.subTest(root=root):
                stale = self.root / root / "stale-skill"
                stale.mkdir(parents=True)
                problems = _skill_sync.skill_problems(self.root, FIXTURE_SKILLS)
                self.assertTrue(any(f"{root}/stale-skill" in p for p in problems), problems)
                stale.rmdir()

    def test_sync_keeps_unexpected_folder(self):
        stale = self.root / ".claude" / "skills" / "stale-skill"
        stale.mkdir(parents=True)
        problems = _skill_sync.sync_skills(self.root, FIXTURE_SKILLS)
        self.assertTrue(stale.is_dir())
        self.assertTrue(any("stale-skill" in p for p in problems), problems)

    def test_symlinked_copy_parent_rejected(self):
        outside = tempfile.TemporaryDirectory()
        self.addCleanup(outside.cleanup)
        (self.root / ".claude").mkdir()
        os.symlink(outside.name, self.root / ".claude" / "skills")
        problems = _skill_sync.sync_skills(self.root, FIXTURE_SKILLS)
        self.assertTrue(problems)
        self.assertEqual(list(Path(outside.name).iterdir()), [])


class SkillFrontmatterTest(unittest.TestCase):
    """Frontmatter holds only the allowlisted keys."""

    def test_valid_frontmatter_passes(self):
        text = VALID_BODY.format(name="alpha-skill")
        self.assertEqual(_skill_sync.frontmatter_problems("alpha-skill", text), [])

    def test_allowed_tools_key_rejected(self):
        text = "---\nname: alpha-skill\ndescription: x\nallowed-tools: Bash\n---\n"
        self.assertTrue(_skill_sync.frontmatter_problems("alpha-skill", text))

    def test_mismatched_name_rejected(self):
        text = VALID_BODY.format(name="other-skill")
        self.assertTrue(_skill_sync.frontmatter_problems("alpha-skill", text))

    def test_crlf_frontmatter_passes(self):
        text = VALID_BODY.format(name="alpha-skill").replace("\n", "\r\n")
        self.assertEqual(_skill_sync.frontmatter_problems("alpha-skill", text), [])

    def test_missing_frontmatter_rejected(self):
        self.assertTrue(_skill_sync.frontmatter_problems("alpha-skill", "Step one.\n"))

    def test_multiline_description_rejected(self):
        text = "---\nname: alpha-skill\ndescription: |\n  two\n---\n"
        self.assertTrue(_skill_sync.frontmatter_problems("alpha-skill", text))

    def test_non_ascii_description_rejected(self):
        text = "---\nname: alpha-skill\ndescription: café\n---\n"
        self.assertTrue(_skill_sync.frontmatter_problems("alpha-skill", text))


class SkillNameTest(unittest.TestCase):
    """Skill names cannot steer a copy outside its folder."""

    def test_traversal_name_rejected(self):
        for name in ("../hooks", "a/b", "UPPER", "", "x" * 65):
            with self.subTest(name=name):
                with self.assertRaises(ValueError):
                    _skill_sync.canonical_path(name)


class SyncEntryPointTest(unittest.TestCase):
    """sync.py runs the skill step only where skills exist."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def test_tree_without_skills_skips(self):
        self.assertEqual(sync.sync_skill_copies(self.root, check_only=True), 0)

    def test_tree_with_stale_skill_fails(self):
        _write_canonical(self.root, "alpha-skill", "no frontmatter\n")
        self.assertEqual(sync.sync_skill_copies(self.root, check_only=True), 1)

    def test_incomplete_skill_module_fails_closed(self):
        scripts = self.root / "scripts"
        scripts.mkdir()
        (scripts / "sync.py").write_text(
            (REPOSITORY_ROOT / "scripts" / "sync.py").read_text(encoding="utf-8"),
            encoding="utf-8")
        (scripts / "_skill_sync.py").write_text("body = 1\n", encoding="utf-8")
        _write_canonical(self.root, "alpha-skill", VALID_BODY.format(name="alpha-skill"))
        result = subprocess.run([sys.executable, str(scripts / "sync.py"), "--check"],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("_skill_sync.py", result.stderr)

    def test_atomic_write_leaves_no_temp(self):
        target = self.root / "manifest.json"
        sync.write_text_atomic(target, "{}\n")
        sync.write_text_atomic(target, "{\"a\": 1}\n")
        self.assertEqual(target.read_text(encoding="utf-8"), "{\"a\": 1}\n")
        self.assertEqual([path.name for path in self.root.iterdir()], ["manifest.json"])


class LiveSkillTreeTest(unittest.TestCase):
    """The source repository carries every listed skill and copy."""

    def test_live_skills_have_no_problems(self):
        self.assertEqual(_skill_sync.skill_problems(REPOSITORY_ROOT), [])

    def test_live_list_holds_thirteen_skills(self):
        self.assertEqual(len(_skill_sync.SKILLS), 13)
        self.assertEqual(len(set(_skill_sync.SKILLS)), 13)


if __name__ == "__main__":
    unittest.main()
