"""Behavioral tests for shared USB atomic write failures."""

from __future__ import annotations

from pathlib import Path

import pytest

import infiniteprep.services.usb._shared as shared


def test_temp_path_writer_cleans_up_after_a_flush_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The path writer removes its temporary file after a flush failure."""
    monkeypatch.setattr(
        shared.os,
        "fsync",
        lambda _fd: (_ for _ in ()).throw(OSError("flush failed")),
    )

    with pytest.raises(OSError, match="flush failed"):
        shared._write_temp_json_via_path(tmp_path, "data.json", "text")

    assert list(tmp_path.glob(".data.json.*.tmp")) == []


def test_atomic_dir_fd_writer_cleans_up_after_replace_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The descriptor writer removes its temporary file after a replace failure."""
    removed: list[str] = []
    monkeypatch.setattr(shared, "_write_temp_json_via_fd", lambda *_args: "temporary")
    monkeypatch.setattr(
        shared.os,
        "replace",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("replace failed")),
    )
    monkeypatch.setattr(shared.os, "unlink", lambda name, **_kwargs: removed.append(name))

    with pytest.raises(OSError, match="replace failed"):
        shared._write_json_atomic_dir_fd(Path("parent"), "data.json", "text", 1, False)

    assert removed == ["temporary"]


def test_atomic_dir_fd_writer_checks_the_target_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The descriptor writer checks the target before and after its temporary write."""
    checks = 0

    def reject_target(*_args: object, **_kwargs: object) -> None:
        nonlocal checks
        checks += 1
        if checks == 2:
            raise ValueError("linked target")

    monkeypatch.setattr(shared, "reject_symlink_target", reject_target)
    monkeypatch.setattr(shared, "_write_temp_json_via_fd", lambda *_args: "temporary")
    monkeypatch.setattr(shared.os, "unlink", lambda *_args, **_kwargs: None)

    with pytest.raises(ValueError, match="linked target"):
        shared._write_json_atomic_dir_fd(Path("parent"), "data.json", "text", 1, True)

    assert checks == 2


def test_atomic_writer_routes_a_descriptor_to_the_dir_fd_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The atomic writer routes a descriptor to its confined writer."""
    monkeypatch.setattr(
        shared,
        "_write_json_atomic_dir_fd",
        lambda *_args: Path("parent/data.json"),
    )

    result = shared.write_json_atomic(
        Path("parent"),
        "data.json",
        {"value": 1},
        dir_fd=1,
    )

    assert result == Path("parent/data.json")
