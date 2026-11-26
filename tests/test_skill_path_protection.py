#!/usr/bin/env python3
"""Test that skill files route to consent and the adoption hook runs fast."""
import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
HOOKS = REPOSITORY_ROOT / "hooks"
sys.path.insert(0, str(HOOKS))
import _gate_core as core

ARGUMENT_RECORDER = (
    "import sys\n"
    "from pathlib import Path\n"
    "Path(sys.argv[0]).with_name('arguments.txt').write_text('\\n'.join(sys.argv[1:]))\n"
)


def _load(name: str):
    """Load one hook module from its checked-in path."""
    spec = importlib.util.spec_from_file_location(name, HOOKS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SkillProtectionTest(unittest.TestCase):
    """Writes below skills/ meet the same gate in both tools."""

    def test_file_tool_protects_skills(self):
        consent = _load("require_consent")
        target = REPOSITORY_ROOT / "skills" / "pr-steward" / "SKILL.md"
        self.assertTrue(consent.is_protected_path(str(target), str(REPOSITORY_ROOT)))

    def test_shell_write_to_skills_asks(self):
        decision, _reason = core.protected_write_verdict(
            "tee", ["skills/pr-steward/SKILL.md"], [], str(REPOSITORY_ROOT))
        self.assertEqual(decision, "ask")

    def test_protected_part_lists_match(self):
        consent = _load("require_consent")
        self.assertEqual(set(consent.PROTECTED_PARTS), set(core.PROTECTED_PATH_PARTS))


class SkillIntegrityLabelTest(unittest.TestCase):
    """A pull request that edits a skill needs the gate-change label."""

    def test_skill_edit_requires_label(self):
        sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))
        import check_gate_pr_integrity
        self.assertTrue(check_gate_pr_integrity.requires_approval(["skills/pr-steward/SKILL.md"]))
        self.assertFalse(check_gate_pr_integrity.requires_approval(["README.md"]))


class AdoptionHookModeTest(unittest.TestCase):
    """The per-call adoption hook runs the checker in fast mode."""

    def test_hook_passes_fast_flag(self):
        hook = _load("enforce_gate_adoption")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checker = root / "scripts" / "check_gate_adoption.py"
            checker.parent.mkdir()
            checker.write_text(ARGUMENT_RECORDER, encoding="utf-8")
            # hook.core is the shared _gate_core module, so restore it for later tests.
            with patch.object(hook.core, "policy_root", lambda: str(root)):
                self.assertEqual(hook._check_complete_set(), "")
            arguments = (checker.parent / "arguments.txt").read_text().splitlines()
        self.assertIn("--fast", arguments)


if __name__ == "__main__":
    unittest.main()
