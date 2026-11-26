#!/usr/bin/env python3
"""Tests for trusted_gh output stream encoding resilience."""
import io
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))

import trusted_gh


class RestrictedEncodingStream(io.TextIOBase):
    """Text stream that simulates a restricted terminal encoding such as cp1252."""

    def __init__(self, encoding: str = "cp1252", with_buffer: bool = True) -> None:
        self._encoding = encoding
        if with_buffer:
            self.buffer = io.BytesIO()

    @property
    def encoding(self) -> str:
        return self._encoding

    def write(self, s: str) -> int:
        encoded = s.encode(self._encoding)
        if hasattr(self, "buffer"):
            self.buffer.write(encoded)
        return len(s)


class TrustedGhEncodingTest(unittest.TestCase):
    """Verify trusted_gh output handling on streams with restricted encodings."""

    def test_run_unencodable_output(self) -> None:
        fake_result = subprocess.CompletedProcess(
            args=["gh", "run", "view"],
            returncode=0,
            stdout="run log with BOM \ufeff and symbol \u2713\n",
            stderr="",
        )
        fake_stdout = RestrictedEncodingStream("cp1252", with_buffer=True)
        fake_stderr = RestrictedEncodingStream("cp1252", with_buffer=True)

        with patch.object(trusted_gh, "authenticated_account", return_value={"id": 1, "login": "test"}):
            with patch.object(trusted_gh, "run_gh", return_value=fake_result):
                with patch.object(sys, "stdout", fake_stdout), patch.object(sys, "stderr", fake_stderr):
                    ret = trusted_gh._run_requested_command(REPOSITORY_ROOT, ["run", "view", "123"])
                    self.assertEqual(ret, 0)

    def test_run_unbuffered_stream(self) -> None:
        fake_result = subprocess.CompletedProcess(
            args=["gh", "run", "view"],
            returncode=0,
            stdout="run log with BOM \ufeff and symbol \u2713\n",
            stderr="",
        )
        fake_stdout = RestrictedEncodingStream("cp1252", with_buffer=False)
        fake_stderr = RestrictedEncodingStream("cp1252", with_buffer=False)

        with patch.object(trusted_gh, "authenticated_account", return_value={"id": 1, "login": "test"}):
            with patch.object(trusted_gh, "run_gh", return_value=fake_result):
                with patch.object(sys, "stdout", fake_stdout), patch.object(sys, "stderr", fake_stderr):
                    ret = trusted_gh._run_requested_command(REPOSITORY_ROOT, ["run", "view", "123"])
                    self.assertEqual(ret, 0)


if __name__ == "__main__":
    unittest.main()
