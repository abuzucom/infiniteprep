#!/usr/bin/env python3
"""The SessionStart hook creates this session's zone folders and refuses links."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOK_PATH = Path(__file__).resolve().parent.parent / "hooks" / "prepare_session_workspace.py"
SESSION = "session-abc_123"
HOOK_TIMEOUT_SECONDS = 15
PRIVATE_MODE = 0o700
SUPPORTED = (hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY")
             and os.mkdir in os.supports_dir_fd and os.open in os.supports_dir_fd)


def run_hook(root: Path, payload) -> subprocess.CompletedProcess:
    """Run the hook with one payload against a temporary project."""
    environment = {**os.environ, "CLAUDE_PROJECT_DIR": str(root)}
    text = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([sys.executable, str(HOOK_PATH)], input=text, capture_output=True,
                          text=True, env=environment, check=False, timeout=HOOK_TIMEOUT_SECONDS)


def start_payload(session_id=SESSION) -> dict:
    return {"hook_event_name": "SessionStart", "source": "startup", "session_id": session_id}


@unittest.skipUnless(SUPPORTED, "dir_fd and O_NOFOLLOW are required to create zone folders")
class PrepareWorkspaceTest(unittest.TestCase):
    """Folders appear once, private, and never through a link."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()

    def test_creates_both_folders(self) -> None:
        result = run_hook(self.root, start_payload())
        self.assertEqual(result.returncode, 0, result.stderr)
        for zone in (".scratch", ".worktrees"):
            folder = self.root / zone / SESSION
            with self.subTest(zone=zone):
                self.assertTrue(folder.is_dir())
                self.assertEqual(stat.S_IMODE(folder.stat().st_mode) & PRIVATE_MODE, PRIVATE_MODE)

    def test_rerun_keeps_folders(self) -> None:
        run_hook(self.root, start_payload())
        (self.root / ".scratch" / SESSION / "keep.txt").write_text("x", encoding="utf-8")
        result = run_hook(self.root, start_payload())
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((self.root / ".scratch" / SESSION / "keep.txt").is_file())

    def test_linked_zone_refused(self) -> None:
        outside = self.root.parent / f"{self.root.name}-outside"
        outside.mkdir()
        self.addCleanup(outside.rmdir)
        (self.root / ".scratch").symlink_to(outside)
        result = run_hook(self.root, start_payload())
        self.assertEqual(result.returncode, 0)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertIn("not created", result.stderr)

    def test_invalid_input_creates_nothing(self) -> None:
        for payload in (start_payload(""), start_payload("../x"), start_payload(7),
                        {"hook_event_name": "SessionStart"}, "not json"):
            with self.subTest(payload=payload):
                result = run_hook(self.root, payload)
                self.assertEqual(result.returncode, 0)
                self.assertFalse((self.root / ".scratch").exists())


if __name__ == "__main__":
    unittest.main()
