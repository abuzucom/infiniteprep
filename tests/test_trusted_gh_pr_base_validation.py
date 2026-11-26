#!/usr/bin/env python3
"""The REST pull request form validates every base and reads the override safely."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tests.test_trusted_gh_rest_write_forms as forms

trusted_gh = forms.trusted_gh


@unittest.skipUnless(forms.SAFE_DRAFT_OPEN, forms.NO_SAFE_DRAFT_OPEN)
class ExplicitBaseTest(forms.RestWriteHarness):
    """An explicit `--base` passes the same rules as the default base."""

    def test_rejected_branch_names_are_refused(self):
        for base in ("master", "HEAD"):
            with self.subTest(base=base):
                self.calls.clear()
                self.assert_refused([*forms.PR_CREATE, "--base", base])

    def test_empty_base_is_refused(self):
        self.assert_refused([*forms.PR_CREATE, "--base", ""])

    def test_valid_explicit_base_still_maps(self):
        code, _stdout, _stderr = self.run_wrapper([*forms.PR_CREATE, "--base", "main"])
        self.assertEqual(code, 0)
        self.assertEqual(self.sent_payloads()[0]["base"], "main")


class MissingBodyTest(forms.RestWriteHarness):
    """A create command without `--body-file` reaches gh unchanged."""

    def test_create_without_body_file(self):
        self.assert_unmapped(["pr", "create", "--draft", "--title", "T"])


@unittest.skipIf(os.name == "nt", "Symlink creation needs privileges on Windows")
class PrimaryBranchReadRaceTest(unittest.TestCase):
    """The override file opens once without following a link."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "hooks").mkdir()
        self.override = self.root / "hooks" / "primary-branch.txt"
        self.override.write_text("trunk\n", encoding="utf-8")
        self.other = self.root / "other.txt"
        self.other.write_text("release-line\n", encoding="utf-8")

    def test_link_swap_refused(self):
        real_open = os.open

        def swap_then_open(path, flags, *args, **kwargs):
            if str(path).endswith("primary-branch.txt"):
                self.override.unlink()
                self.override.symlink_to(self.other)
            return real_open(path, flags, *args, **kwargs)

        with patch.object(trusted_gh.os, "open", side_effect=swap_then_open):
            with self.assertRaises(ValueError):
                trusted_gh.primary_branch_name(self.root)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFOs need POSIX")
    def test_fifo_override_refused(self):
        self.override.unlink()
        os.mkfifo(self.override)
        with self.assertRaises(ValueError):
            trusted_gh.primary_branch_name(self.root)

    def test_regular_override_is_read(self):
        self.assertEqual(trusted_gh.primary_branch_name(self.root), "trunk")


if __name__ == "__main__":
    unittest.main()
