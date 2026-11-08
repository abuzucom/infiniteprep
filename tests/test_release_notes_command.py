"""Checks that the notes subcommand of packaging/release.py runs through its own branch."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
RELEASE_PATH = ROOT / "packaging" / "release.py"


def _load_release() -> ModuleType:
    """Import packaging/release.py by file path, as CI runs it."""
    spec = importlib.util.spec_from_file_location("release_notes_target", RELEASE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RELEASE = _load_release()


class NotesCommandTest(unittest.TestCase):
    """main(["notes", ...]) writes the notes of the top CHANGELOG entry."""

    def test_notes_writes_the_top_changelog_section(self) -> None:
        """The notes file holds the download block, and main returns 0."""
        version = RELEASE.read_versions(ROOT)["CHANGELOG.md"]
        output_text = io.StringIO()
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder) / "notes.md"
            with contextlib.redirect_stdout(output_text):
                exit_code = RELEASE.main(["notes", version, str(output)])
            notes = output.read_text(encoding="utf-8")
        self.assertEqual(exit_code, 0)
        self.assertIn("wrote", output_text.getvalue())
        self.assertIn("## Downloads", notes)


if __name__ == "__main__":
    unittest.main()
