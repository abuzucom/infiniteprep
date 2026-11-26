"""Focused coverage for shared USB path, JSON, confinement, and Win32 helpers."""

from __future__ import annotations

import ctypes
import errno
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

import infiniteprep.services.usb._shared as shared

POSIX_DIR_FD_SUPPORT = (
    hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
    and os.open in os.supports_dir_fd
    and os.replace in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
    and os.lstat in os.supports_dir_fd
)


class FakeKernel32:
    """Small Win32 API double that records handle closes."""

    def __init__(
        self,
        *,
        attributes: int = shared._WIN32_ATTRIBUTE_DIRECTORY,
        final_path: str = "",
        info_result: int = 1,
        path_length: int | None = None,
        disposition_result: int = 1,
    ) -> None:
        self.attributes = attributes
        self.final_path = final_path
        self.info_result = info_result
        self.path_length = path_length
        self.disposition_result = disposition_result
        self.closed_handles: list[int] = []
        self.disposition_calls = 0

    def GetFileInformationByHandle(self, handle: int, info_pointer: Any) -> int:
        """Populate a real FILE_INFO structure and report the configured result."""
        info = ctypes.cast(info_pointer, ctypes.POINTER(shared._Win32HandleFileInfo)).contents
        info.dwFileAttributes = self.attributes
        return self.info_result

    def GetFinalPathNameByHandleW(
        self, handle: int, path_buffer: Any, buffer_length: int, flags: int
    ) -> int:
        """Return a configured final path and length."""
        path_buffer.value = self.final_path
        if self.path_length is not None:
            return self.path_length
        return len(self.final_path)

    def SetFileInformationByHandle(
        self, handle: int, file_class: int, information: Any, size: int
    ) -> int:
        """Record a file disposition request."""
        self.disposition_calls += 1
        return self.disposition_result

    def CloseHandle(self, handle: int) -> int:
        """Record a closed handle."""
        self.closed_handles.append(int(handle.value))
        return 1


def test_load_json_text_reports_deep_nesting_as_malformed() -> None:
    """JSON nesting beyond the parser recursion limit becomes a ValueError."""
    with pytest.raises(ValueError, match="nesting is too deep"):
        shared.load_json_text("[" * 200_000 + "]" * 200_000, "payload")


def test_load_json_text_accepts_valid_json_objects() -> None:
    """The JSON helper returns parsed objects unchanged."""
    assert shared.load_json_text('{"name": "value"}', "payload") == {"name": "value"}


def test_timestamp_helpers_require_offsets_and_normalize_to_utc() -> None:
    """Aware timestamps normalize to UTC and naive timestamps fail."""
    parsed = shared.parse_utc_datetime("2026-11-26T08:00:00-04:00")

    assert parsed.isoformat() == "2026-11-26T12:00:00+00:00"
    assert shared.normalize_iso_utc("2026-11-26T08:00:00-04:00") == "2026-11-26T12:00:00Z"
    with pytest.raises(ValueError, match="timezone offset"):
        shared.parse_utc_datetime("2026-11-26T08:00:00")


def test_timestamp_helper_rejects_an_offset_overflow() -> None:
    """A valid offset that crosses the datetime range becomes a ValueError."""
    with pytest.raises(ValueError, match="outside the supported range"):
        shared.parse_utc_datetime("0001-01-01T00:00:00+01:00")


@pytest.mark.parametrize(
    "path",
    ["relative/usb", "D:/USB/../Windows", "", "/media/usb\x00", "/media/usb/.."],
)
def test_unsafe_absolute_paths_are_rejected(path: str) -> None:
    """Relative paths, traversal, control characters, and empty text are rejected."""
    assert not shared.is_safe_absolute_path(path)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("\\\\?\\C:\\USB", "C:\\USB"),
        ("\\\\?\\UNC\\server\\share", "\\\\server\\share"),
        ("C:\\USB", "C:\\USB"),
    ],
)
def test_win32_final_path_prefixes_are_normalized(raw: str, expected: str) -> None:
    """Extended-length Win32 prefixes are removed before canonical comparison."""
    assert shared._normalize_win32_final_path(raw) == expected


def test_write_json_atomic_creates_parent_and_replaces_target(tmp_path: Path) -> None:
    """The path writer creates UTF-8 JSON and atomically replaces an existing target."""
    parent = tmp_path / "nested" / "metadata"
    target = parent / "data.json"
    target.parent.mkdir(parents=True)
    target.write_text("old", encoding="utf-8")

    result = shared.write_json_atomic(parent, "data.json", {"name": "cafe"})

    assert result == target
    assert target.read_text(encoding="utf-8") == ('{\n  "name": "cafe"\n}\n')
    assert list(parent.glob(".data.json.*.tmp")) == []


def test_write_json_atomic_cleans_a_temporary_file_after_replace_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed replacement leaves the old target and removes the temporary file."""
    parent = tmp_path / "metadata"
    parent.mkdir()
    target = parent / "data.json"
    target.write_text("old", encoding="utf-8")

    def fail_replace(source: Path, destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(shared.os, "replace", fail_replace)
    with pytest.raises(OSError, match="replace failed"):
        shared.write_json_atomic(parent, "data.json", {"name": "new"})

    assert target.read_text(encoding="utf-8") == "old"
    assert list(parent.glob(".data.json.*.tmp")) == []


@pytest.mark.skipif(not POSIX_DIR_FD_SUPPORT, reason="This platform lacks POSIX dir_fd support.")
def test_temporary_file_collision_limit_preserves_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repeatedly occupied temporary name fails without deleting the occupant."""
    parent = tmp_path / "metadata"
    parent.mkdir()
    occupied = parent / ".data.json.deadbeef.tmp"
    occupied.write_text("occupied", encoding="utf-8")
    monkeypatch.setattr(shared.secrets, "token_hex", lambda size: "deadbeef")
    descriptor = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(OSError, match="Unable to allocate unique temporary JSON file"):
            shared._write_temp_json_via_fd(descriptor, "data.json", "new")
    finally:
        os.close(descriptor)

    assert occupied.read_text(encoding="utf-8") == "occupied"


@pytest.mark.skipif(not POSIX_DIR_FD_SUPPORT, reason="This platform lacks POSIX dir_fd support.")
def test_write_json_atomic_uses_a_confined_directory_descriptor(tmp_path: Path) -> None:
    """The dir_fd writer places the final file inside the opened directory."""
    flags = os.O_RDONLY | os.O_DIRECTORY
    descriptor = os.open(tmp_path, flags)
    try:
        result = shared.write_json_atomic(
            tmp_path,
            "data.json",
            {"name": "confined"},
            dir_fd=descriptor,
            create_parent=False,
        )
    finally:
        os.close(descriptor)

    assert result == tmp_path / "data.json"
    assert json.loads(result.read_text(encoding="utf-8")) == {"name": "confined"}


def test_read_bounded_fd_utf8_rejects_an_oversized_regular_file(tmp_path: Path) -> None:
    """A regular file larger than the limit fails and closes its descriptor."""
    path = tmp_path / "large.json"
    path.write_text("12345", encoding="utf-8")
    descriptor = os.open(path, os.O_RDONLY)

    with pytest.raises(ValueError, match="exceeds maximum allowed size"):
        shared.read_bounded_fd_utf8(descriptor, max_bytes=4)

    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_read_bounded_fd_utf8_rejects_invalid_utf8_and_closes(tmp_path: Path) -> None:
    """Invalid UTF-8 fails during decoding after the descriptor closes."""
    path = tmp_path / "invalid.json"
    path.write_bytes(b"\xff")
    descriptor = os.open(path, os.O_RDONLY)

    with pytest.raises(UnicodeDecodeError):
        shared.read_bounded_fd_utf8(descriptor)

    with pytest.raises(OSError):
        os.fstat(descriptor)


def test_read_bounded_json_file_reads_a_regular_file(tmp_path: Path) -> None:
    """The bounded JSON reader returns regular UTF-8 file content."""
    path = tmp_path / "data.json"
    path.write_text('{"ok": true}', encoding="utf-8")

    assert shared.read_bounded_json_file(path, max_bytes=64) == '{"ok": true}'


def test_validate_confined_ancestors_creates_and_rejects_escapes(tmp_path: Path) -> None:
    """Confined ancestor validation creates children and rejects outside targets."""
    root = tmp_path / "root"
    root.mkdir()
    target = root / "one" / "two"

    resolved = shared.validate_confined_ancestors(target, root, create=True)

    assert resolved == target.resolve(strict=True)
    with pytest.raises(ValueError, match="inside the allowed storage root"):
        shared.validate_confined_ancestors(tmp_path / "outside", root, create=True)


@pytest.mark.skipif(not POSIX_DIR_FD_SUPPORT, reason="This platform lacks POSIX dir_fd support.")
def test_posix_confined_directory_walk_uses_nofollow(tmp_path: Path) -> None:
    """The POSIX walker opens every component relative to the allowed root."""
    root = tmp_path / "root"
    root.mkdir()
    target = root / "one" / "two"

    opened = shared._open_posix_confined_dir(target, root, create=True)

    assert opened is not None
    opened_dir, descriptor = opened
    try:
        assert opened_dir == target
        assert os.listdir(descriptor) == []
    finally:
        os.close(descriptor)


@pytest.mark.skipif(not POSIX_DIR_FD_SUPPORT, reason="This platform lacks POSIX dir_fd support.")
def test_posix_confined_directory_walk_reports_a_missing_child(tmp_path: Path) -> None:
    """The POSIX walker returns None when a requested child does not exist."""
    root = tmp_path / "root"
    root.mkdir()

    opened = shared._open_posix_confined_dir(root / "missing", root, create=False)

    assert opened is None


def test_win32_locked_directory_closes_the_handle_after_validation_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A rejected Win32 directory handle closes before the error propagates."""
    fake_kernel = FakeKernel32(attributes=shared._WIN32_ATTRIBUTE_REPARSE_POINT)
    monkeypatch.setattr(shared, "_win32_kernel32", lambda: fake_kernel)
    monkeypatch.setattr(shared, "_open_win32_handle", lambda path, access, share, flags: 42)

    with pytest.raises(ValueError, match="symbolic link"):
        shared.open_win32_locked_dir(tmp_path, tmp_path)

    assert fake_kernel.closed_handles == [42]


def test_win32_unlink_sets_disposition_and_closes_the_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Win32 unlink helper sets disposition information and closes its handle."""
    target = tmp_path / "data.json"
    target.write_text("{}", encoding="utf-8")
    fake_kernel = FakeKernel32(
        attributes=0,
        final_path=str(target.resolve(strict=True)),
    )
    monkeypatch.setattr(shared, "_win32_kernel32", lambda: fake_kernel)
    monkeypatch.setattr(shared, "_open_win32_handle", lambda path, access, share, flags: 42)

    removed = shared.unlink_win32_confined_file(target, tmp_path)

    assert removed is True
    assert fake_kernel.disposition_calls == 1
    assert fake_kernel.closed_handles == [42]


def test_win32_open_treats_missing_files_as_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A missing Win32 file returns None without raising."""
    if sys.platform != "win32":
        pytest.skip("Win32 last-error state is available only on Windows.")

    fake_kernel = FakeKernel32()
    fake_kernel.CreateFileW = lambda *args: ctypes.c_void_p(-1).value
    monkeypatch.setattr(shared, "_win32_kernel32", lambda: fake_kernel)
    ctypes.set_last_error(shared._WIN32_ERROR_FILE_NOT_FOUND)

    result = shared._open_win32_handle(
        tmp_path / "missing.json",
        shared._WIN32_GENERIC_READ,
        shared._WIN32_SHARE_ALL,
        0,
    )

    assert result is None


def test_posix_open_reports_a_symlink_component_as_a_value_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A POSIX open failure with ELOOP becomes a confined-path ValueError."""
    if not POSIX_DIR_FD_SUPPORT:
        pytest.skip("This platform lacks POSIX dir_fd support.")

    def raise_loop(path: str, flags: int, dir_fd: int | None = None) -> int:
        raise OSError(errno.ELOOP, "symbolic link")

    monkeypatch.setattr(shared.os, "open", raise_loop)
    with pytest.raises(ValueError, match="symbolic link"):
        shared._open_posix_confined_dir(tmp_path / "child", tmp_path, create=False)
