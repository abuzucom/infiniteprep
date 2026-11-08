"""Behavioral checks that each required notice in a bundle folder is a real file."""

from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parent.parent
CHECKER_PATH = ROOT / "packaging" / "check_bundle.py"
NOTICES = ("LICENSE", "NOTICE", "ICU-LICENSE.txt")


def _load_checker() -> ModuleType:
    """Import packaging/check_bundle.py by file path, as CI runs it."""
    spec = importlib.util.spec_from_file_location("check_bundle_notices_target", CHECKER_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CHECKER = _load_checker()


class NoticeFileTest(unittest.TestCase):
    """A notice name alone is not enough. The notice must resolve to a file."""

    def test_dangling_notice_links_are_missing_notices(self) -> None:
        """Three broken notice links fail as missing, and stay in the inventory."""
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / "InfinitePrep"
            bundle.mkdir()
            (bundle / "InfinitePrep").write_bytes(b"")
            for notice in NOTICES:
                os.symlink(bundle / f"absent-{notice}", bundle / notice)

            report = CHECKER.check_bundle(bundle, "linux")
            files = CHECKER.bundle_files(bundle)

        self.assertEqual(report.missing_notices, list(NOTICES))
        self.assertFalse(report.passed)
        for notice in NOTICES:
            self.assertIn(notice, files)

    def test_notice_links_to_real_files_count(self) -> None:
        """A notice link that resolves to a file satisfies the notice check."""
        with tempfile.TemporaryDirectory() as temp:
            bundle = Path(temp) / "InfinitePrep"
            texts = Path(temp) / "texts"
            bundle.mkdir()
            texts.mkdir()
            (bundle / "InfinitePrep").write_bytes(b"")
            for notice in NOTICES:
                (texts / notice).write_text("license text", encoding="utf-8")
                os.symlink(texts / notice, bundle / notice)

            report = CHECKER.check_bundle(bundle, "linux")

        self.assertEqual(report.missing_notices, [])
        self.assertTrue(report.passed)


if __name__ == "__main__":
    unittest.main()
