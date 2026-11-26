"""Behavioral tests for the final shared USB helper branches."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import infiniteprep.services.usb._shared as shared
from infiniteprep.services.usb.models import SpaceInfo


def test_default_command_runner_keeps_arguments() -> None:
    """The fixed command runner passes one argument without shell interpretation."""
    completed = shared.default_command_runner(
        [sys.executable, "-c", "import sys; print(sys.argv[1])", "a;b"]
    )

    assert completed.returncode == 0
    assert completed.stdout.strip() == "a;b"
    assert completed.stderr == ""


def test_clean_str_removes_escapes_controls() -> None:
    """The metadata cleaner removes escape sequences and controls from text."""
    assert shared.clean_str(None) is None
    assert shared.clean_str("\x1b[31mTrack\x00") == "Track"
    assert shared.clean_str("\x00\x07") is None
    assert shared.clean_str("  Name  ") == "Name"


def test_clean_str_limits_the_result() -> None:
    """The metadata cleaner truncates text at its configured limit."""
    assert shared.clean_str("abcdef", max_length=3) == "abc"


def test_clean_int_accepts_values_and() -> None:
    """The integer cleaner rejects invalid, infinite, and negative values."""
    assert shared.clean_int(None, 5) == 5
    assert shared.clean_int("7") == 7
    assert shared.clean_int(-3) == 0
    assert shared.clean_int("bad", 9) == 9
    assert shared.clean_int(float("inf"), 4) == 4


def test_deduplicate_space_entries_keeps_the() -> None:
    """Duplicate mount points keep the first space record in input order."""
    first = SpaceInfo(total_bytes=10, used_bytes=2, available_bytes=8)
    second = SpaceInfo(total_bytes=20, used_bytes=3, available_bytes=17)

    result = shared.deduplicate_space_entries(
        [("/media/a", first), ("/media/a", second), ("/media/b", second)]
    )

    assert result == (("/media/a", first), ("/media/b", second))


@pytest.mark.parametrize(
    "value",
    [None, 3, "", "/media/../etc", "/media/stick\x00"],
)
def test_sanitize_mount_point_rejects_unsafe(value: object) -> None:
    """The mount sanitizer rejects every path that does not name a safe absolute path."""
    assert shared.sanitize_mount_point(value) is None


def test_sanitize_mount_point_rejects_an() -> None:
    """The mount sanitizer rejects a path beyond its length limit."""
    value = "/" + "x" * shared.MAX_MOUNT_PATH_LENGTH

    assert shared.sanitize_mount_point(value) is None


def test_reject_symlink_target_refuses_a(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The path-based link check refuses a linked target."""
    monkeypatch.setattr(shared, "is_symlink_or_reparse_point", lambda _path: True)

    with pytest.raises(ValueError, match="Refusing symlink target"):
        shared.reject_symlink_target(tmp_path, "manifest.json")


def test_win32_error_without_a_code() -> None:
    """A Win32 error without a code names no path."""
    error = shared.win32_error(0)

    assert error.filename is None
    assert shared.WIN32_NO_ERROR_MESSAGE in str(error)


def test_is_symlink_or_case_3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The link detector accepts a path that reports itself as a symbolic link."""
    monkeypatch.setattr(Path, "is_symlink", lambda _path: True)

    assert shared.is_symlink_or_reparse_point(Path("linked")) is True


def test_is_symlink_or_reparse_point(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The link detector accepts a path that reports itself as a junction."""
    monkeypatch.setattr(Path, "is_symlink", lambda _path: False)
    monkeypatch.setattr(Path, "is_junction", lambda _path: True, raising=False)

    assert shared.is_symlink_or_reparse_point(Path("junction")) is True


def test_is_symlink_or_case_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The link detector accepts a path whose stat record carries a reparse tag."""
    monkeypatch.setattr(Path, "is_symlink", lambda _path: False)
    monkeypatch.setattr(Path, "is_junction", lambda _path: False, raising=False)
    monkeypatch.setattr(
        os,
        "lstat",
        lambda _path: SimpleNamespace(st_reparse_tag=1),
    )

    assert shared.is_symlink_or_reparse_point(Path("reparse")) is True


def test_ensure_confined_root_refuses_a(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The confined root check refuses a path that becomes linked after the first check."""
    calls = 0

    def linked_after_first_check(_path: Path) -> bool:
        nonlocal calls
        calls += 1
        return calls > 1

    monkeypatch.setattr(shared, "is_symlink_or_reparse_point", linked_after_first_check)

    with pytest.raises(ValueError, match="cannot be a symbolic link"):
        shared._ensure_confined_root(tmp_path, create=False)


def test_ensure_confined_root_refuses_missing(tmp_path: Path) -> None:
    """The confined root must exist and name a directory."""
    missing = tmp_path / "missing"
    regular_file = tmp_path / "file.txt"
    regular_file.write_text("data", encoding="utf-8")

    with pytest.raises(FileNotFoundError):
        shared._ensure_confined_root(missing, create=False)
    with pytest.raises(ValueError, match="not a directory"):
        shared._ensure_confined_root(regular_file, create=False)


def test_validate_confined_ancestors_refuses_missing(tmp_path: Path) -> None:
    """A missing component stops a non-creating confined path walk."""
    root = tmp_path / "root"
    root.mkdir()

    with pytest.raises(FileNotFoundError):
        shared.validate_confined_ancestors(root / "missing", root, create=False)


def test_validate_confined_ancestors_refuses_linked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A linked component stops a confined path walk."""
    root = tmp_path / "root"
    root.mkdir()
    target = root / "child"
    monkeypatch.setattr(
        shared,
        "is_symlink_or_reparse_point",
        lambda path: path != root,
    )

    with pytest.raises(ValueError, match="Path component"):
        shared.validate_confined_ancestors(target, root, create=True)


def test_write_json_atomic_requires_an(tmp_path: Path) -> None:
    """A non-creating atomic write refuses a missing parent directory."""
    with pytest.raises(FileNotFoundError):
        shared.write_json_atomic(
            tmp_path / "missing",
            "data.json",
            {"value": 1},
            create_parent=False,
        )


def test_close_win32_handle_does_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Win32 handle closer does no platform work outside Windows."""
    calls: list[int] = []
    monkeypatch.setattr(shared.sys, "platform", "linux")
    monkeypatch.setattr(
        shared, "_win32_kernel32", lambda: SimpleNamespace(CloseHandle=calls.append)
    )

    shared.close_win32_handle(42)

    assert calls == []


def test_default_command_runner_does_not(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The default runner fixes the shell and capture options at its boundary."""
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(command: list[str], **kwargs: Any) -> Any:
        calls.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(shared.subprocess, "run", run)

    shared.default_command_runner(["tool", "value"])

    assert calls[0][1]["shell"] is False
    assert calls[0][1]["capture_output"] is True
    assert calls[0][1]["check"] is False
