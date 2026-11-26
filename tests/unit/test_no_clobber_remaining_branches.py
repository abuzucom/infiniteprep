"""Focused coverage for remaining no-replace move failure and binding branches."""

from __future__ import annotations

import ctypes
import errno
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from infiniteprep.adapters.metadata_io import no_clobber_move
from infiniteprep.adapters.metadata_io.no_clobber_move import (
    DISCARD_NAME_PREFIX,
    discard_if_same_file,
    file_identity,
    move_to_new_path,
)


class FakeNativeRename:
    """Record calls made through a bound native rename function."""

    def __init__(self, result: int = 0) -> None:
        self.result = result
        self.calls: list[tuple[Any, ...]] = []
        self.argtypes: Any = None
        self.restype: Any = None

    def __call__(self, *args: Any) -> int:
        self.calls.append(args)
        return self.result


@pytest.fixture(autouse=True)
def clear_native_rename_cache() -> None:
    """Keep cached native bindings from leaking between platform simulations."""
    native_function = no_clobber_move.native_no_replace_rename
    native_function.cache_clear()
    yield
    native_function.cache_clear()


def test_discard_keeps_private_file_when(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed restore leaves the private file and reports that discard failed."""
    source = tmp_path / "track.wav"
    source.write_bytes(b"audio")
    identity = file_identity(source)
    original_move = no_clobber_move.move_to_new_path
    calls = 0

    def move_once_then_fail(source_path: Path, destination_path: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            original_move(source_path, destination_path)
            return
        raise OSError("restore failed")

    monkeypatch.setattr(no_clobber_move, "move_to_new_path", move_once_then_fail)

    result = discard_if_same_file(source, (identity[0], identity[1] + 1))

    assert result is False
    assert not source.exists()
    private_files = list(tmp_path.glob(f"{DISCARD_NAME_PREFIX}*"))
    assert len(private_files) == 1
    assert private_files[0].read_bytes() == b"audio"


def test_rename_without_replacing_handles_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing binding refuses the move, while a successful native call permits it."""
    monkeypatch.setattr(no_clobber_move, "native_no_replace_rename", lambda: None)
    assert no_clobber_move._rename_without_replacing(Path("source"), Path("destination")) is False

    native = FakeNativeRename()
    monkeypatch.setattr(no_clobber_move, "native_no_replace_rename", lambda: native)

    result = no_clobber_move._rename_without_replacing(Path("source"), Path("destination"))

    assert result is True
    assert native.calls == [(b"source", b"destination")]


def test_rename_without_replacing_maps_unsupported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unsupported native errors return False, while an existing destination raises."""

    def unsupported(_source: bytes, _destination: bytes) -> int:
        ctypes.set_errno(errno.EINVAL)
        return -1

    monkeypatch.setattr(no_clobber_move, "native_no_replace_rename", lambda: unsupported)
    assert no_clobber_move._rename_without_replacing(Path("source"), Path("destination")) is False

    def existing(_source: bytes, _destination: bytes) -> int:
        ctypes.set_errno(errno.EEXIST)
        return -1

    monkeypatch.setattr(no_clobber_move, "native_no_replace_rename", lambda: existing)
    with pytest.raises(FileExistsError):
        no_clobber_move._rename_without_replacing(Path("source"), Path("destination"))


def test_posix_move_refuses_an_unsupported(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A POSIX system without a no-replace binding keeps the source file."""
    source = tmp_path / "track.wav"
    source.write_bytes(b"audio")
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(no_clobber_move, "native_no_replace_rename", lambda: None)

    with pytest.raises(OSError, match="no move that refuses an existing file"):
        move_to_new_path(source, tmp_path / "renamed.wav")

    assert source.read_bytes() == b"audio"
    assert not (tmp_path / "renamed.wav").exists()


def test_posix_move_uses_a_successful(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful POSIX native rename permits the no-replace move."""
    calls: list[tuple[bytes, bytes]] = []

    def rename(source: bytes, destination: bytes) -> int:
        calls.append((source, destination))
        return 0

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(no_clobber_move, "native_no_replace_rename", lambda: rename)

    no_clobber_move._move_without_replacing(Path("source"), Path("destination"))

    assert calls == [(b"source", b"destination")]


def test_move_to_new_path_preserves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The public move reports an existing destination with recovery guidance."""
    source = tmp_path / "track.wav"
    source.write_bytes(b"audio")
    destination = tmp_path / "renamed.wav"
    destination.write_bytes(b"existing")

    def reject(_source: Path, _destination: Path) -> None:
        raise FileExistsError("native failure")

    monkeypatch.setattr(no_clobber_move, "_move_without_replacing", reject)

    with pytest.raises(FileExistsError, match="Refusing to overwrite existing file"):
        move_to_new_path(source, destination)

    assert source.read_bytes() == b"audio"
    assert destination.read_bytes() == b"existing"


def test_native_binding_handles_an_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A C library that cannot load leaves the platform without a binding."""

    def fail_cdll(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("library unavailable")

    monkeypatch.setattr(no_clobber_move.ctypes, "CDLL", fail_cdll)

    assert no_clobber_move.native_no_replace_rename() is None


def test_native_binding_selects_linux_renameat2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Linux platform binds renameat2 with the no-replace flag."""
    native = FakeNativeRename()
    libc = type("Libc", (), {"renameat2": native})()
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(no_clobber_move.ctypes, "CDLL", lambda *_args, **_kwargs: libc)

    rename = no_clobber_move.native_no_replace_rename()

    assert rename is not None
    assert rename(b"source", b"destination") == 0
    assert native.calls == [(-100, b"source", -100, b"destination", 1)]


def test_native_binding_selects_macos_renamex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The macOS platform binds renamex_np with the exclusive flag."""
    native = FakeNativeRename()
    libc = type("Libc", (), {"renamex_np": native})()
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(no_clobber_move.ctypes, "CDLL", lambda *_args, **_kwargs: libc)

    rename = no_clobber_move.native_no_replace_rename()

    assert rename is not None
    assert rename(b"source", b"destination") == 0
    assert native.calls == [(b"source", b"destination", 4)]


def test_native_binding_returns_none_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A platform without a known no-replace binding receives no native function."""
    monkeypatch.setattr(sys, "platform", "freebsd")
    monkeypatch.setattr(no_clobber_move.ctypes, "CDLL", lambda *_args, **_kwargs: object())

    assert no_clobber_move.native_no_replace_rename() is None


def test_platform_bindings_report_missing_native() -> None:
    """Linux and macOS libraries without their native symbols receive no binding."""
    linux_libc = type("Libc", (), {})()
    macos_libc = type("Libc", (), {})()

    assert no_clobber_move._linux_renameat2(cast(Any, linux_libc)) is None
    assert no_clobber_move._macos_renamex_np(cast(Any, macos_libc)) is None
