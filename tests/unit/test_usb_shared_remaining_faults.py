"""Behavioral tests for the remaining shared USB fault paths."""

from __future__ import annotations

import ctypes
import errno
import os
import stat
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import infiniteprep.services.usb._shared as shared


class FakeKernel32:
    """Return configured Win32 API results."""

    def __init__(self, *, info_result: int = 1, path_length: int = 0) -> None:
        self.info_result = info_result
        self.path_length = path_length
        self.closed_handles: list[int] = []

    def GetFileInformationByHandle(self, _handle: int, pointer: Any) -> int:
        """Populate the file attributes and return the configured result."""
        info = ctypes.cast(pointer, ctypes.POINTER(shared._Win32HandleFileInfo)).contents
        info.dwFileAttributes = shared._WIN32_ATTRIBUTE_DIRECTORY
        return self.info_result

    def GetFinalPathNameByHandleW(
        self,
        _handle: int,
        path_buffer: Any,
        _buffer_length: int,
        _flags: int,
    ) -> int:
        """Return the configured final path length."""
        path_buffer.value = "C:\\target"
        return self.path_length

    def CloseHandle(self, handle: int) -> int:
        """Record one closed handle."""
        self.closed_handles.append(int(handle.value))
        return 1


def regular_stat(*, device: int = 1, inode: int = 1, size: int = 0) -> SimpleNamespace:
    """Return a regular stat record."""
    return SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600,
        st_dev=device,
        st_ino=inode,
        st_size=size,
    )


def link_stat() -> SimpleNamespace:
    """Return a symbolic link stat record."""
    return SimpleNamespace(st_mode=stat.S_IFLNK, st_dev=1, st_ino=1, st_size=0)


def test_validate_win32_handle_reports_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed file information query becomes a Win32 error."""
    fake_kernel = FakeKernel32(info_result=0)
    monkeypatch.setattr(shared, "_win32_kernel32", lambda: fake_kernel)

    with pytest.raises(OSError):
        shared._validate_win32_handle(42, Path("target"), expect_dir=True, check_parent=False)


def test_validate_win32_handle_reports_a(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed final path query becomes a Win32 error."""
    fake_kernel = FakeKernel32(path_length=0)
    monkeypatch.setattr(shared, "_win32_kernel32", lambda: fake_kernel)

    with pytest.raises(OSError):
        shared._validate_win32_handle(42, Path("target"), expect_dir=True, check_parent=False)


def test_validate_win32_handle_reports_an(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A final path beyond the buffer becomes a bounded path error."""
    fake_kernel = FakeKernel32(path_length=shared._WIN32_PATH_BUFFER_LENGTH)
    monkeypatch.setattr(shared, "_win32_kernel32", lambda: fake_kernel)

    with pytest.raises(OSError, match="exceeds the path buffer"):
        shared._validate_win32_handle(42, Path("target"), expect_dir=True, check_parent=False)


def test_open_win32_locked_dir_reports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing Win32 directory has no handle to return."""
    monkeypatch.setattr(shared, "_open_win32_handle", lambda *_args: None)

    assert shared.open_win32_locked_dir(Path("missing"), Path("missing")) is None


def test_open_win32_confined_read_fd(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed validation closes the Win32 handle before the error reaches the caller."""
    fake_kernel = FakeKernel32()
    monkeypatch.setattr(shared, "_win32_kernel32", lambda: fake_kernel)
    monkeypatch.setattr(shared, "_open_win32_handle", lambda *_args: 42)

    def reject_handle(*_args: object, **_kwargs: object) -> None:
        raise ValueError("reparse point")

    monkeypatch.setattr(shared, "_validate_win32_handle", reject_handle)

    with pytest.raises(ValueError, match="reparse point"):
        shared.open_win32_confined_read_fd(Path("target"), Path("parent"))

    assert fake_kernel.closed_handles == [42]


def test_verify_fd_identity_case_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The descriptor identity check refuses a linked directory entry."""
    monkeypatch.setattr(shared.os, "fstat", lambda _fd: regular_stat())
    monkeypatch.setattr(shared.os, "lstat", lambda _path: link_stat())

    with pytest.raises(ValueError, match="cannot be a symbolic link"):
        shared.verify_fd_identity_and_parent(1, Path("target"), Path("parent"))


def test_verify_fd_identity_rejects_a(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The descriptor identity check refuses a replaced file."""
    monkeypatch.setattr(shared.os, "fstat", lambda _fd: regular_stat(inode=1))
    monkeypatch.setattr(shared.os, "lstat", lambda _path: regular_stat(inode=2))

    with pytest.raises(ValueError, match="changed during open"):
        shared.verify_fd_identity_and_parent(1, Path("target"), Path("parent"))


def test_verify_fd_identity_rejects_an(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The descriptor identity check refuses a target outside the expected parent."""
    target_dir = tmp_path / "target"
    expected_dir = tmp_path / "expected"
    target_dir.mkdir()
    expected_dir.mkdir()
    target = target_dir / "file.json"
    target.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(shared.os, "fstat", lambda _fd: regular_stat())
    monkeypatch.setattr(shared.os, "lstat", lambda _path: regular_stat())

    with pytest.raises(ValueError, match="outside the mount root"):
        shared.verify_fd_identity_and_parent(1, target, expected_dir)


def test_read_bounded_fd_rejects_a(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file that grows past the limit fails after the read."""
    path = tmp_path / "data.json"
    path.write_bytes(b"too long")
    descriptor = os.open(path, os.O_RDONLY)
    monkeypatch.setattr(shared.os, "fstat", lambda _fd: regular_stat(size=0))

    with pytest.raises(ValueError, match="exceeds maximum allowed size"):
        shared.read_bounded_fd_utf8(descriptor, max_bytes=1)


def test_ensure_confined_root_refuses_a(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The confined root check refuses a linked root at the first check."""
    monkeypatch.setattr(shared, "is_symlink_or_reparse_point", lambda _path: True)

    with pytest.raises(ValueError, match="cannot be a symbolic link"):
        shared._ensure_confined_root(Path("root"), create=False)


def test_posix_dir_step_reports_a(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The POSIX directory step returns no descriptor for a missing child."""
    monkeypatch.setattr(shared.os, "O_DIRECTORY", 0, raising=False)
    monkeypatch.setattr(shared.os, "O_NOFOLLOW", 0, raising=False)
    monkeypatch.setattr(
        shared.os, "open", lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError())
    )

    assert shared._open_posix_dir_step(1, "missing", create=False) is None


@pytest.mark.parametrize("error_number", [errno.ELOOP, errno.ENOTDIR])
def test_posix_dir_step_translates_link(monkeypatch: pytest.MonkeyPatch, error_number: int) -> None:
    """The POSIX directory step translates link and type errors."""
    monkeypatch.setattr(shared.os, "O_DIRECTORY", 0, raising=False)
    monkeypatch.setattr(shared.os, "O_NOFOLLOW", 0, raising=False)

    def fail_open(*_args: object, **_kwargs: object) -> int:
        raise OSError(error_number, "unsafe component")

    monkeypatch.setattr(shared.os, "open", fail_open)

    with pytest.raises(ValueError, match="symbolic link"):
        shared._open_posix_dir_step(1, "child", create=False)


def test_posix_confined_walk_refuses_a(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The POSIX confined walk refuses a linked allowed root."""
    monkeypatch.setattr(shared, "is_symlink_or_reparse_point", lambda _path: True)

    with pytest.raises(ValueError, match="symbolic link"):
        shared._open_posix_confined_dir(Path("root/child"), Path("root"), create=False)


def test_open_confined_context_case_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The confined context reports a missing POSIX target."""
    monkeypatch.setattr(shared, "supports_dir_fd_nofollow", lambda: True)
    monkeypatch.setattr(
        shared,
        "_open_posix_confined_dir",
        lambda *_args, **_kwargs: None,
    )

    with shared.open_confined_directory_context(
        Path("root/child"), Path("root"), create=False
    ) as opened:
        assert opened is None


def test_open_confined_context_reports_a(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The confined context reports a missing fallback target."""
    monkeypatch.setattr(shared, "supports_dir_fd_nofollow", lambda: False)

    def missing_target(*_args: object, **_kwargs: object) -> Path:
        raise FileNotFoundError

    monkeypatch.setattr(shared, "validate_confined_ancestors", missing_target)

    with shared.open_confined_directory_context(
        Path("root/child"), Path("root"), create=False
    ) as opened:
        assert opened is None


def test_open_confined_context_closes_a(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The confined context closes its locked Win32 directory handle."""
    closed: list[int] = []
    monkeypatch.setattr(shared, "supports_dir_fd_nofollow", lambda: False)
    monkeypatch.setattr(shared.sys, "platform", "win32")
    monkeypatch.setattr(
        shared,
        "validate_confined_ancestors",
        lambda *_args, **_kwargs: tmp_path,
    )
    monkeypatch.setattr(shared, "open_win32_locked_dir", lambda *_args: 42)
    monkeypatch.setattr(shared, "close_win32_handle", closed.append)

    with shared.open_confined_directory_context(
        tmp_path / "child", tmp_path, create=False
    ) as opened:
        assert opened == (tmp_path, None)

    assert closed == [42]


def test_confined_json_read_reports_a(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A confined descriptor read reports a missing entry."""
    monkeypatch.setattr(shared.os, "O_NOFOLLOW", 0, raising=False)
    monkeypatch.setattr(
        shared.os,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )

    assert shared.read_confined_dir_json_text(Path("parent"), "missing.json", dir_fd=1) is None


def test_confined_json_read_handles_a(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A confined Win32 read reports a missing file."""
    monkeypatch.setattr(shared.sys, "platform", "win32")
    monkeypatch.setattr(shared, "open_win32_confined_read_fd", lambda *_args: None)

    assert shared.read_confined_dir_json_text(Path("parent"), "missing.json", dir_fd=None) is None


def test_confined_json_read_case_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A confined POSIX read reports a missing file."""
    monkeypatch.setattr(shared.sys, "platform", "linux")
    monkeypatch.setattr(shared, "reject_symlink_target", lambda *_args: None)
    monkeypatch.setattr(
        shared.os,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileNotFoundError()),
    )

    assert shared.read_confined_dir_json_text(Path("parent"), "missing.json", dir_fd=None) is None


def test_temp_fd_writer_reports_a(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The descriptor writer reports an exhausted set of temporary names."""
    monkeypatch.setattr(shared.secrets, "token_hex", lambda _size: "fixed")
    monkeypatch.setattr(
        shared.os,
        "open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(FileExistsError()),
    )

    with pytest.raises(OSError, match="Unable to allocate unique temporary"):
        shared._write_temp_json_via_fd(1, "data.json", "text")


def test_temp_fd_writer_cleans_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The descriptor writer removes its temporary file after a flush failure."""
    descriptor, _name = tempfile.mkstemp(text=True)
    removed: list[str] = []
    monkeypatch.setattr(shared.os, "open", lambda *_args, **_kwargs: descriptor)
    monkeypatch.setattr(
        shared.os,
        "fsync",
        lambda _fd: (_ for _ in ()).throw(OSError("flush failed")),
    )
    monkeypatch.setattr(shared.os, "unlink", lambda name, **_kwargs: removed.append(name))

    with pytest.raises(OSError, match="flush failed"):
        shared._write_temp_json_via_fd(1, "data.json", "text")

    assert len(removed) == 1
