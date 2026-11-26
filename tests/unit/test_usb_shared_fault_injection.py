"""Fault-injection coverage for shared USB confinement and write helpers."""

from __future__ import annotations

import contextlib
import ctypes
import os
import stat
from pathlib import Path
from typing import Any

import pytest

import infiniteprep.services.usb._shared as shared


class FakeStat:
    """Minimal stat result for identity checks."""

    def __init__(self, mode: int = stat.S_IFREG, reparse_tag: int = 0) -> None:
        self.st_mode = mode
        self.st_reparse_tag = reparse_tag
        self.st_size = 0


class FakeKernel32:
    """Win32 API double for handle validation and disposition faults."""

    def __init__(
        self,
        *,
        attributes: int = shared._WIN32_ATTRIBUTE_DIRECTORY,
        final_path: str = "",
        info_result: int = 1,
        path_length: int | None = None,
        disposition_result: int = 1,
        create_result: int | None = 42,
    ) -> None:
        self.attributes = attributes
        self.final_path = final_path
        self.info_result = info_result
        self.path_length = path_length
        self.disposition_result = disposition_result
        self.create_result = create_result
        self.closed_handles: list[int] = []

    def CreateFileW(self, *args: object) -> int | None:
        """Return the configured handle value."""
        return self.create_result

    def GetFileInformationByHandle(self, handle: object, info_pointer: Any) -> int:
        """Populate file attributes and report the configured result."""
        info = ctypes.cast(info_pointer, ctypes.POINTER(shared._Win32HandleFileInfo)).contents
        info.dwFileAttributes = self.attributes
        return self.info_result

    def GetFinalPathNameByHandleW(
        self, handle: object, path_buffer: Any, buffer_length: int, flags: int
    ) -> int:
        """Return the configured final path length."""
        path_buffer.value = self.final_path
        if self.path_length is not None:
            return self.path_length
        return len(self.final_path)

    def SetFileInformationByHandle(
        self, handle: object, file_class: int, information: object, size: int
    ) -> int:
        """Return the configured disposition result."""
        return self.disposition_result

    def CloseHandle(self, handle: object) -> int:
        """Record a closed handle."""
        self.closed_handles.append(int(handle.value))
        return 1


class FakeWriter:
    """Text writer double for temporary-file failure paths."""

    def __init__(self, descriptor: int, *, fail: bool = False) -> None:
        self.descriptor = descriptor
        self.fail = fail

    def __enter__(self) -> FakeWriter:
        """Enter the writer context."""
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        """Close a descriptor supplied by the real temporary-file helper."""
        with contextlib.suppress(OSError):
            os.close(self.descriptor)

    def write(self, text: str) -> int:
        """Write text or raise the configured failure."""
        if self.fail:
            raise OSError("write failed")
        return len(text)

    def flush(self) -> None:
        """Provide a flush seam."""

    def fileno(self) -> int:
        """Return the descriptor for the fsync seam."""
        return self.descriptor


@pytest.mark.parametrize(
    ("attributes", "expect_dir", "message"),
    [
        (shared._WIN32_ATTRIBUTE_REPARSE_POINT, True, "symbolic link"),
        (0, True, "unexpected filesystem type"),
    ],
)
def test_win32_handle_validation_rejects_unsafe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attributes: int,
    expect_dir: bool,
    message: str,
) -> None:
    """Reparse points and mismatched filesystem types fail validation."""
    fake_kernel = FakeKernel32(attributes=attributes)
    monkeypatch.setattr(shared, "_win32_kernel32", lambda: fake_kernel)
    with pytest.raises(ValueError, match=message):
        shared._validate_win32_handle(7, tmp_path, expect_dir=expect_dir, check_parent=False)


def test_win32_handle_validation_rejects_outside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A final path outside the expected directory fails validation."""
    fake_kernel = FakeKernel32(final_path=str(tmp_path / "outside"))
    monkeypatch.setattr(shared, "_win32_kernel32", lambda: fake_kernel)
    with pytest.raises(ValueError, match="outside the mount root"):
        shared._validate_win32_handle(7, tmp_path, expect_dir=True, check_parent=False)


def test_win32_open_raises_unexpected_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unexpected Win32 open error raises with its Win32 code."""
    fake_kernel = FakeKernel32(create_result=ctypes.c_void_p(-1).value)
    monkeypatch.setattr(shared, "_win32_kernel32", lambda: fake_kernel)
    ctypes.set_last_error(5)
    with pytest.raises(OSError) as error:
        shared._open_win32_handle(Path("missing"), 1, 1, 0)
    assert error.value.winerror == 5


def test_win32_unlink_reports_a_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed disposition raises and still closes the handle."""
    target = tmp_path / "data.json"
    target.write_text("{}", encoding="utf-8")
    fake_kernel = FakeKernel32(
        attributes=0,
        final_path=str(target.resolve(strict=True)),
        disposition_result=0,
    )
    monkeypatch.setattr(shared, "_win32_kernel32", lambda: fake_kernel)
    monkeypatch.setattr(shared, "_open_win32_handle", lambda path, access, share, flags: 42)
    with pytest.raises(OSError):
        shared.unlink_win32_confined_file(target, tmp_path)
    assert fake_kernel.closed_handles == [42]


def test_verify_fd_identity_rejects_non(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A directory descriptor cannot identify a regular manifest target."""
    monkeypatch.setattr(shared.os, "fstat", lambda descriptor: FakeStat(stat.S_IFDIR))
    with pytest.raises(ValueError, match="regular file"):
        shared.verify_fd_identity_and_parent(3, tmp_path, tmp_path.parent)


def test_read_bounded_fd_rejects_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A directory descriptor cannot supply bounded JSON text."""
    monkeypatch.setattr(shared.os, "fstat", lambda descriptor: FakeStat(stat.S_IFDIR))
    monkeypatch.setattr(shared.os, "close", lambda descriptor: None)
    with pytest.raises(ValueError, match="regular file"):
        shared.read_bounded_fd_utf8(3)


def test_read_bounded_json_file_rejects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """JSON reads reject links and close descriptors when identity checks fail."""
    target = tmp_path / "data.json"
    target.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(shared, "is_symlink_or_reparse_point", lambda path: True)
    with pytest.raises(ValueError, match="Refusing symlink JSON file"):
        shared.read_bounded_json_file(target)

    monkeypatch.setattr(shared, "is_symlink_or_reparse_point", lambda path: False)
    monkeypatch.setattr(
        shared,
        "verify_fd_identity_and_parent",
        lambda descriptor, path, parent: (_ for _ in ()).throw(ValueError("changed")),
    )
    with pytest.raises(ValueError, match="changed"):
        shared.read_bounded_json_file(target)


def test_confined_root_rejects_missing_and(tmp_path: Path) -> None:
    """A confined root must exist and name a directory."""
    missing = tmp_path / "missing"
    with pytest.raises(FileNotFoundError):
        shared.validate_confined_ancestors(missing, missing, create=False)
    file_path = tmp_path / "file.txt"
    file_path.write_text("data", encoding="utf-8")
    with pytest.raises(ValueError, match="not a directory"):
        shared.validate_confined_ancestors(file_path, file_path, create=False)


def test_posix_confined_dir_walk_closes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The confined POSIX walk closes every descriptor it replaces."""
    root = tmp_path / "root"
    root.mkdir()
    opened: list[int] = []
    closed: list[int] = []

    def fake_open(path: object, flags: int, dir_fd: int | None = None) -> int:
        descriptor = 10 if Path(str(path)).name == "root" else 11
        opened.append(descriptor)
        return descriptor

    monkeypatch.setattr(shared.os, "O_DIRECTORY", 0x10000, raising=False)
    monkeypatch.setattr(shared.os, "O_NOFOLLOW", 0x20000, raising=False)
    monkeypatch.setattr(shared, "is_symlink_or_reparse_point", lambda path: False)
    monkeypatch.setattr(shared.os, "open", fake_open)
    monkeypatch.setattr(shared.os, "close", closed.append)
    result = shared._open_posix_confined_dir(root / "child", root, create=False)

    assert result is not None
    assert result[1] == 11
    assert closed == [10]


def test_posix_confined_dir_rejects_outside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confined POSIX walk rejects outside targets and linked roots."""
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(ValueError, match="inside the allowed storage root"):
        shared._open_posix_confined_dir(tmp_path / "outside", root, create=False)

    monkeypatch.setattr(shared, "is_symlink_or_reparse_point", lambda path: True)
    with pytest.raises(ValueError, match="Storage root"):
        shared._open_posix_confined_dir(root, root, create=False)


def test_open_confined_context_handles_missing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The confined context yields None for missing directories and closes handles."""
    monkeypatch.setattr(shared, "supports_dir_fd_nofollow", lambda: True)
    monkeypatch.setattr(shared, "_open_posix_confined_dir", lambda target, root, create: None)
    with shared.open_confined_directory_context(tmp_path, tmp_path, create=False) as opened:
        assert opened is None

    closed: list[int] = []
    monkeypatch.setattr(
        shared,
        "_open_posix_confined_dir",
        lambda target, root, create: (target, 44),
    )
    monkeypatch.setattr(shared.os, "close", closed.append)
    with shared.open_confined_directory_context(tmp_path, tmp_path, create=False) as opened:
        assert opened == (tmp_path, 44)
    assert closed == [44]


def test_temp_json_via_fd_cleans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed descriptor write removes the temporary file."""
    removed: list[str] = []
    monkeypatch.setattr(shared.os, "open", lambda name, flags, mode, dir_fd: 8)
    monkeypatch.setattr(
        shared.os,
        "fdopen",
        lambda descriptor, *args, **kwargs: FakeWriter(descriptor, fail=True),
    )
    monkeypatch.setattr(shared.os, "fsync", lambda descriptor: None)
    monkeypatch.setattr(shared.os, "close", lambda descriptor: None)
    monkeypatch.setattr(shared.os, "unlink", lambda name, dir_fd: removed.append(name))
    with pytest.raises(OSError, match="write failed"):
        shared._write_temp_json_via_fd(3, "data.json", "text")
    assert len(removed) == 1


def test_temp_json_via_path_cleans(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed path write removes its temporary file."""
    monkeypatch.setattr(
        shared.os,
        "fdopen",
        lambda descriptor, *args, **kwargs: FakeWriter(descriptor, fail=True),
    )
    with pytest.raises(OSError, match="write failed"):
        shared._write_temp_json_via_path(tmp_path, "data.json", "text")
    assert list(tmp_path.glob(".data.json.*.tmp")) == []


def test_atomic_write_rejects_linked_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Atomic writes reject a linked parent and a linked target."""
    parent = tmp_path / "parent"
    parent.mkdir()
    monkeypatch.setattr(shared, "is_symlink_or_reparse_point", lambda path: True)
    with pytest.raises(ValueError, match="Metadata folder"):
        shared.write_json_atomic(parent, "data.json", {})

    monkeypatch.setattr(shared, "is_symlink_or_reparse_point", lambda path: False)
    monkeypatch.setattr(
        shared,
        "reject_symlink_target",
        lambda parent_dir, file_name: (_ for _ in ()).throw(ValueError("linked target")),
    )
    with pytest.raises(ValueError, match="linked target"):
        shared.write_json_atomic(parent, "data.json", {}, reject_target_symlink=True)


def test_atomic_write_cleans_up_when(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A parent that becomes linked during the write removes the temporary file."""
    parent = tmp_path / "parent"
    parent.mkdir()
    checks = iter((False, True))
    monkeypatch.setattr(shared, "is_symlink_or_reparse_point", lambda path: next(checks))
    with pytest.raises(ValueError, match="Metadata folder"):
        shared.write_json_atomic(parent, "data.json", {})
    assert list(parent.glob(".data.json.*.tmp")) == []
