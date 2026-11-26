#!/usr/bin/env python3
"""Gemini hook commands pass the project path to Python as one argument."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
GEMINI_SETTINGS = REPO_ROOT / ".gemini" / "settings.json"
PROJECT_VARIABLE = "$GEMINI_PROJECT_DIR"
# A space splits an unquoted path. The semicolons run the middle word as a command.
HOSTILE_DIRECTORY = "project dir;touch injected;x"
SHELL_TIMEOUT_SECONDS = 15
FAKE_PYTHON = """#!/bin/sh
printf '%s\\n' "$1" > "$ARGUMENT_LOG"
"""


def gemini_commands() -> list:
    """Return every hook command string in the Gemini settings file."""
    document = json.loads(GEMINI_SETTINGS.read_text(encoding="utf-8"))
    commands = []
    pending = [document]
    while pending:
        value = pending.pop()
        if isinstance(value, dict):
            commands.extend(child for key, child in value.items()
                            if key == "command" and isinstance(child, str))
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    return [command for command in commands if PROJECT_VARIABLE in command]


@unittest.skipIf(os.name == "nt" or shutil.which("bash") is None, "Needs a POSIX bash shell")
class GeminiCommandQuotingTest(unittest.TestCase):
    """Each command keeps a hostile project path intact under bash."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.work = Path(temporary.name)
        self.project = self.work / HOSTILE_DIRECTORY
        self.project.mkdir()
        bin_dir = self.work / "bin"
        bin_dir.mkdir()
        fake = bin_dir / "python"
        fake.write_text(FAKE_PYTHON, encoding="utf-8")
        fake.chmod(0o755)
        self.log = self.work / "argument.log"
        self.environment = {"PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                            "GEMINI_PROJECT_DIR": str(self.project),
                            "ARGUMENT_LOG": str(self.log)}

    def test_commands_exist(self) -> None:
        self.assertTrue(gemini_commands())

    def test_project_path_stays_one_argument(self) -> None:
        for command in gemini_commands():
            with self.subTest(command=command):
                script = command.split("/hooks/", 1)[1].split('"', 1)[0].split(" ", 1)[0]
                subprocess.run(["bash", "-c", command], cwd=self.work, env=self.environment,
                               check=False, capture_output=True, timeout=SHELL_TIMEOUT_SECONDS)
                recorded = self.log.read_text(encoding="utf-8").strip()
                self.assertEqual(recorded, f"{self.project}/hooks/{script}")
                self.assertFalse((self.work / "injected").exists())


if __name__ == "__main__":
    unittest.main()
