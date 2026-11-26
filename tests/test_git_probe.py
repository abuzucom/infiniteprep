#!/usr/bin/env python3
"""The hardened Git probe reports tracked paths or fails closed."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "hooks"))
import _git_probe

GIT_TIMEOUT_SECONDS = 15
KILLED_RETURN_CODE = -9
SHORT_READ_BYTES = 3


class FakeStream:
    """Serve stdout bytes on request and record each requested size."""

    def __init__(self, payload: bytes, endless: bool = False, chunk_limit=None) -> None:
        self.payload = payload
        self.endless = endless
        self.chunk_limit = chunk_limit
        self.requested = []

    def read(self, size: int) -> bytes:
        """Return up to size bytes, or exactly size bytes when endless."""
        self.requested.append(size)
        if self.endless:
            return b"a" * size
        if self.chunk_limit is not None:
            size = min(size, self.chunk_limit)
        chunk, self.payload = self.payload[:size], self.payload[size:]
        return chunk


class FakeProcess:
    """Stand in for a Git child process with fixed output and exit behavior."""

    def __init__(self, stream: FakeStream, hang: bool = False) -> None:
        self.stdout = stream
        self.hang = hang
        self.returncode = None

    def __enter__(self):
        return self

    def __exit__(self, *exc_info) -> None:
        return None

    def poll(self):
        """Report the exit code, or None while the process still runs."""
        return self.returncode

    def kill(self) -> None:
        """Record a kill as a nonzero exit."""
        self.returncode = KILLED_RETURN_CODE

    def wait(self, timeout=None) -> int:
        """Finish at once, or time out first when the process hangs."""
        if self.hang and timeout is not None and self.returncode is None:
            raise subprocess.TimeoutExpired(["git"], timeout)
        if self.returncode is None:
            self.returncode = 0
        return self.returncode


class TrackedPathsTest(unittest.TestCase):
    """tracked_paths names tracked paths and returns None on any failure."""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        subprocess.run(["git", "init", "-q", str(self.root)], check=True,
                       timeout=GIT_TIMEOUT_SECONDS)
        (self.root / "tracked.txt").write_text("x", encoding="utf-8")
        (self.root / "loose.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "tracked.txt"], check=True,
                       timeout=GIT_TIMEOUT_SECONDS)

    def test_tracked_and_untracked(self) -> None:
        tracked = _git_probe.tracked_paths(str(self.root), ["tracked.txt", "loose.txt"])
        self.assertEqual(tracked, frozenset({"tracked.txt"}))

    def test_directory_reports_contents(self) -> None:
        (self.root / "sub").mkdir()
        (self.root / "sub" / "kept.txt").write_text("x", encoding="utf-8")
        subprocess.run(["git", "-C", str(self.root), "add", "sub/kept.txt"], check=True,
                       timeout=GIT_TIMEOUT_SECONDS)
        self.assertEqual(_git_probe.tracked_paths(str(self.root), ["sub"]),
                         frozenset({"sub/kept.txt"}))

    def test_not_a_repository_fails(self) -> None:
        with tempfile.TemporaryDirectory() as other:
            self.assertIsNone(_git_probe.tracked_paths(other, ["x"]))

    def test_output_read_stays_bounded(self) -> None:
        stream = FakeStream(b"", endless=True)
        with patch.object(_git_probe.subprocess, "Popen", return_value=FakeProcess(stream)):
            self.assertIsNone(_git_probe.tracked_paths(str(self.root), ["tracked.txt"]))
        self.assertTrue(stream.requested)
        self.assertLessEqual(sum(stream.requested), _git_probe.MAX_OUTPUT_BYTES + 1)

    def test_read_error_fails(self) -> None:
        stream = FakeStream(b"")
        with patch.object(stream, "read", side_effect=OSError("pipe")), \
                patch.object(_git_probe.subprocess, "Popen", return_value=FakeProcess(stream)):
            self.assertIsNone(_git_probe.tracked_paths(str(self.root), ["tracked.txt"]))

    def test_short_reads_collect_all(self) -> None:
        stream = FakeStream(b"a.txt\0b.txt\0", chunk_limit=SHORT_READ_BYTES)
        with patch.object(_git_probe.subprocess, "Popen", return_value=FakeProcess(stream)):
            tracked = _git_probe.tracked_paths(str(self.root), ["a.txt", "b.txt"])
        self.assertEqual(tracked, frozenset({"a.txt", "b.txt"}))

    def test_timeout_fails(self) -> None:
        hanging = FakeProcess(FakeStream(b"tracked.txt\0"), hang=True)
        with patch.object(_git_probe.subprocess, "Popen", return_value=hanging):
            self.assertIsNone(_git_probe.tracked_paths(str(self.root), ["tracked.txt"]))

    @unittest.skipIf(os.name == "nt", "The fake Git is a POSIX shell script")
    def test_repository_git_ignored(self) -> None:
        fake_bin = self.root / "bin"
        fake_bin.mkdir()
        fake_git = fake_bin / "git"
        fake_git.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        fake_git.chmod(0o755)
        path = f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}"
        with patch.dict(_git_probe.os.environ, {"PATH": path}):
            tracked = _git_probe.tracked_paths(str(self.root), ["tracked.txt", "loose.txt"])
        self.assertEqual(tracked, frozenset({"tracked.txt"}))

    def test_missing_trusted_git_fails(self) -> None:
        with patch.object(_git_probe, "_trusted_git", return_value=""):
            self.assertIsNone(_git_probe.tracked_paths(str(self.root), ["tracked.txt"]))

    def test_unloadable_resolver_fails(self) -> None:
        _git_probe._load_resolver.cache_clear()
        self.addCleanup(_git_probe._load_resolver.cache_clear)
        with patch.object(_git_probe.importlib.util, "spec_from_file_location", return_value=None):
            self.assertEqual(_git_probe._trusted_git(str(self.root)), "")

    def test_broken_resolver_fails(self) -> None:
        for error in (SyntaxError("bad resolver"), AttributeError("no resolve_git")):
            with self.subTest(error=type(error).__name__), \
                    patch.object(_git_probe, "_load_resolver", side_effect=error):
                self.assertEqual(_git_probe._trusted_git(str(self.root)), "")

    def test_unresolved_git_fails(self) -> None:
        class Resolver:
            """Stand in for a resolver that finds no trusted Git."""

            @staticmethod
            def resolve_git(root: str) -> str:
                raise FileNotFoundError(f"no trusted Git for {root}")

        with patch.object(_git_probe, "_load_resolver", return_value=Resolver):
            self.assertEqual(_git_probe._trusted_git(str(self.root)), "")

    def test_missing_git_fails(self) -> None:
        with patch.object(_git_probe.subprocess, "Popen", side_effect=FileNotFoundError("git")):
            self.assertIsNone(_git_probe.tracked_paths(str(self.root), ["tracked.txt"]))

    def test_undecodable_output_fails(self) -> None:
        undecodable = FakeProcess(FakeStream(b"caf\xe9\0"))
        with patch.object(_git_probe.subprocess, "Popen", return_value=undecodable):
            self.assertIsNone(_git_probe.tracked_paths(str(self.root), ["tracked.txt"]))

    def test_oversized_output_fails(self) -> None:
        large = FakeProcess(FakeStream(b"a\0" * _git_probe.MAX_OUTPUT_BYTES))
        with patch.object(_git_probe.subprocess, "Popen", return_value=large):
            self.assertIsNone(_git_probe.tracked_paths(str(self.root), ["tracked.txt"]))


if __name__ == "__main__":
    unittest.main()
