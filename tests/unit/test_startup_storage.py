"""Behavioral tests for opening storage at startup (D83a1a)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from infiniteprep import storage_paths
from infiniteprep.startup_storage import (
    DATABASE_FILE_NAME,
    QUIT_EXIT_CODE,
    STORAGE_ERROR_EXIT_CODE,
    OpenedStorage,
    open_storage,
)
from infiniteprep.storage_paths import (
    PORTABLE_DATA_DIR_NAME,
    PORTABLE_MARKER_NAME,
    FallbackAnswer,
    StorageMode,
    StorageRegistry,
)
from infiniteprep.ui.storage_prompt import STORAGE_ERROR_TITLE, build_storage_error_box

EXE_NAME = "InfinitePrep.exe"
PER_USER_DIR_FUNCTIONS = ("user_data_dir", "user_config_dir", "user_cache_dir")


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Isolate the per-user folders and the registry, and record shown errors."""
    for name in PER_USER_DIR_FUNCTIONS:
        monkeypatch.setattr(storage_paths.platformdirs, name, lambda *_, n=name: str(tmp_path / n))
    registry = StorageRegistry()
    monkeypatch.setattr(storage_paths, "STORAGE_REGISTRY", registry)
    app_folder = tmp_path / "InfinitePrep"
    app_folder.mkdir()
    (app_folder / EXE_NAME).write_bytes(b"")
    return {"root": tmp_path, "app": app_folder, "registry": registry, "errors": []}


def _open(env: dict[str, Any], *, frozen: bool, answer: FallbackAnswer) -> OpenedStorage | int:
    """Run open_storage against the test app folder."""
    return open_storage(
        env["app"] / EXE_NAME,
        frozen=frozen,
        confirm_fallback=lambda _folder: answer,
        show_error=env["errors"].append,
    )


def _blocked_portable_folder(env: dict[str, Any]) -> None:
    """Turn on portable mode and block the Data folder with a file."""
    (env["app"] / PORTABLE_MARKER_NAME).write_bytes(b"")
    (env["app"] / PORTABLE_DATA_DIR_NAME).write_bytes(b"not a folder")


def test_opens_the_database_and_sets_the_locations(env: dict[str, Any]) -> None:
    """A normal start opens the database in data_dir and sets the locations once."""
    opened = _open(env, frozen=False, answer=FallbackAnswer(use_per_user=False, remember=False))

    assert isinstance(opened, OpenedStorage)
    assert opened.locations.mode is StorageMode.PER_USER
    assert (opened.locations.data_dir / DATABASE_FILE_NAME).is_file()
    assert env["registry"].current() == opened.locations
    assert env["errors"] == []
    opened.repository.db.close()


def test_quit_returns_the_quit_code_without_an_error(env: dict[str, Any]) -> None:
    """Quit in the read-only prompt ends the start quietly and sets nothing."""
    _blocked_portable_folder(env)

    result = _open(env, frozen=True, answer=FallbackAnswer(use_per_user=False, remember=False))

    assert result == QUIT_EXIT_CODE
    assert env["errors"] == []
    env["registry"].configure(storage_paths.per_user_locations())


def test_unwritable_profile_on_remember_shows_an_error(env: dict[str, Any]) -> None:
    """A per-user config folder that cannot hold the remembered choice stops with a message."""
    _blocked_portable_folder(env)
    (env["root"] / "user_config_dir").write_bytes(b"not a folder")

    result = _open(env, frozen=True, answer=FallbackAnswer(use_per_user=True, remember=True))

    assert result == STORAGE_ERROR_EXIT_CODE
    [message] = env["errors"]
    assert message.startswith("InfinitePrep cannot open its library folder.")
    assert "\n" not in message


def test_unwritable_data_folder_shows_an_error_and_sets_nothing(env: dict[str, Any]) -> None:
    """A per-user data folder that cannot be created stops before the database opens."""
    (env["root"] / "user_data_dir").write_bytes(b"not a folder")

    result = _open(env, frozen=False, answer=FallbackAnswer(use_per_user=False, remember=False))

    assert result == STORAGE_ERROR_EXIT_CODE
    assert len(env["errors"]) == 1
    # The registry stayed unset, so it still accepts its one configure call.
    env["registry"].configure(storage_paths.per_user_locations())


def test_error_box_shows_plain_text(qtbot: Any) -> None:
    """The startup error box renders its message literally."""
    message = "InfinitePrep cannot open its library folder. <b>C:\\Music</b>"
    box = build_storage_error_box(message)
    qtbot.addWidget(box)

    assert box.windowTitle() == STORAGE_ERROR_TITLE
    assert box.text() == message
    assert box.textFormat().name == "PlainText"
