#!/usr/bin/env python3
"""Check that an oversized assembled policy names the assembled policy."""
import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = ROOT / "hooks" / "reinject_agents_policy.py"
SYNC_PATH = ROOT / "scripts" / "sync.py"
ASSEMBLED_MESSAGE = "assembled policy exceeds the policy size limit"


def load_module(path: Path, name: str):
    """Load one module by path under a private name."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_overflowing_policy(root: Path, files: tuple, limit: int) -> None:
    """Write files each under the limit with an assembly that exceeds it."""
    part_bytes = limit // 2 + 1
    (root / "AGENTS.md").write_bytes(b"a" * (part_bytes - 1) + b"\n")
    for relative_name in files:
        path = root / relative_name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"d\n")
    (root / files[0]).write_bytes(b"s" * (part_bytes - 1) + b"\n")


class AssembledSizeMessageTest(unittest.TestCase):
    def test_reinjection_hook_names_policy(self):
        hook = load_module(HOOK_PATH, "reinject_size_message")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_overflowing_policy(root, hook.ADOPTER_POLICY_FILES, hook.MAX_POLICY_BYTES)
            with self.assertRaises(ValueError) as raised:
                hook.load_policy(root)
        self.assertEqual(str(raised.exception), ASSEMBLED_MESSAGE)

    def test_sync_names_the_assembled_policy(self):
        sync = load_module(SYNC_PATH, "sync_size_message")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_overflowing_policy(root, sync.ADOPTER_POLICY_FILES, sync.MAX_POLICY_BYTES)
            with self.assertRaises(ValueError) as raised:
                sync.policy_bytes(root)
        self.assertEqual(str(raised.exception), ASSEMBLED_MESSAGE)


if __name__ == "__main__":
    unittest.main()
