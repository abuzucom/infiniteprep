"""Behavioral tests for storage locations and portable mode (D83a1a)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from infiniteprep import storage_paths
from infiniteprep.storage_paths import (
    FALLBACK_LIST_NAME,
    PORTABLE_DATA_DIR_NAME,
    PORTABLE_MARKER_NAME,
    FallbackAnswer,
    StorageLocations,
    StorageMode,
    StorageRegistry,
    resolve_locations,
)

EXE_NAME = "InfinitePrep.exe"


class RecordingConfirm:
    """Return a fixed answer and record each folder it was asked about."""

    def __init__(self, answer: FallbackAnswer) -> None:
        self.answer = answer
        self.asked: list[Path] = []

    def __call__(self, folder: Path) -> FallbackAnswer:
        self.asked.append(folder)
        return self.answer


@pytest.fixture
def user_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the per-user folders at a temp tree and return its root."""
    root = tmp_path / "user"
    monkeypatch.setattr(storage_paths.platformdirs, "user_data_dir", lambda *_: str(root / "data"))
    monkeypatch.setattr(
        storage_paths.platformdirs, "user_config_dir", lambda *_: str(root / "config")
    )
    monkeypatch.setattr(
        storage_paths.platformdirs, "user_cache_dir", lambda *_: str(root / "cache")
    )
    return root


@pytest.fixture
def app_folder(tmp_path: Path) -> Path:
    """Return an unpacked app folder holding the executable and the portable marker."""
    folder = tmp_path / "InfinitePrep"
    folder.mkdir()
    (folder / EXE_NAME).write_bytes(b"")
    (folder / PORTABLE_MARKER_NAME).write_bytes(b"")
    return folder


def _block_data_folder(app_folder: Path) -> None:
    """Make the Data folder impossible to create by putting a file in its place."""
    (app_folder / PORTABLE_DATA_DIR_NAME).write_bytes(b"not a folder")


def _fallback_list(user_dirs: Path) -> list[str]:
    """Return the remembered folders, or an empty list when the file is absent."""
    path = user_dirs / "config" / FALLBACK_LIST_NAME
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def test_source_run_ignores_the_marker(user_dirs: Path, app_folder: Path) -> None:
    """A run from source stays per-user even beside a marker (D261)."""
    confirm = RecordingConfirm(FallbackAnswer(use_per_user=True, remember=False))

    locations = resolve_locations(app_folder / EXE_NAME, frozen=False, confirm_fallback=confirm)

    assert locations is not None
    assert locations.mode is StorageMode.PER_USER
    assert locations.data_dir == user_dirs / "data"
    assert not (app_folder / PORTABLE_DATA_DIR_NAME).exists()


def test_frozen_build_without_marker_is_per_user(user_dirs: Path, app_folder: Path) -> None:
    """A packaged build with no marker keeps today's per-user folders."""
    (app_folder / PORTABLE_MARKER_NAME).unlink()
    confirm = RecordingConfirm(FallbackAnswer(use_per_user=True, remember=False))

    locations = resolve_locations(app_folder / EXE_NAME, frozen=True, confirm_fallback=confirm)

    assert locations is not None
    assert locations.mode is StorageMode.PER_USER
    assert locations.config_dir == user_dirs / "config"
    assert locations.backup_dir == user_dirs / "data" / "backups"
    assert locations.bin_dir == user_dirs / "data" / "bin"


def test_portable_keeps_database_and_settings_in_data(user_dirs: Path, app_folder: Path) -> None:
    """Portable mode puts the database and settings in Data, the rest per-user (D260)."""
    confirm = RecordingConfirm(FallbackAnswer(use_per_user=True, remember=False))

    locations = resolve_locations(app_folder / EXE_NAME, frozen=True, confirm_fallback=confirm)

    data = app_folder.resolve() / PORTABLE_DATA_DIR_NAME
    assert locations is not None
    assert locations.mode is StorageMode.PORTABLE
    assert locations.data_dir == data
    assert locations.config_dir == data
    assert locations.cache_dir == user_dirs / "cache"
    assert locations.backup_dir == user_dirs / "data" / "backups"
    assert locations.bin_dir == user_dirs / "data" / "bin"
    assert data.is_dir()
    assert list(data.iterdir()) == []
    assert confirm.asked == []


def test_unwritable_folder_with_remember_saves_the_choice(
    user_dirs: Path, app_folder: Path
) -> None:
    """Use my user profile with Remember falls back and records the folder (D258)."""
    _block_data_folder(app_folder)
    confirm = RecordingConfirm(FallbackAnswer(use_per_user=True, remember=True))

    locations = resolve_locations(app_folder / EXE_NAME, frozen=True, confirm_fallback=confirm)

    assert locations is not None
    assert locations.mode is StorageMode.PER_USER_FALLBACK
    assert locations.data_dir == user_dirs / "data"
    assert confirm.asked == [app_folder.resolve()]
    assert _fallback_list(user_dirs) == [str(app_folder.resolve())]


def test_unwritable_folder_without_remember_asks_again(user_dirs: Path, app_folder: Path) -> None:
    """Without Remember the fallback holds for this run only, and the next run asks again."""
    _block_data_folder(app_folder)
    confirm = RecordingConfirm(FallbackAnswer(use_per_user=True, remember=False))

    first = resolve_locations(app_folder / EXE_NAME, frozen=True, confirm_fallback=confirm)
    second = resolve_locations(app_folder / EXE_NAME, frozen=True, confirm_fallback=confirm)

    assert first is not None
    assert second is not None
    assert first.mode is StorageMode.PER_USER_FALLBACK
    assert len(confirm.asked) == 2
    assert _fallback_list(user_dirs) == []


def test_quit_returns_none_and_remembers_nothing(user_dirs: Path, app_folder: Path) -> None:
    """Quit ends the launch before any storage is chosen."""
    _block_data_folder(app_folder)
    confirm = RecordingConfirm(FallbackAnswer(use_per_user=False, remember=True))

    locations = resolve_locations(app_folder / EXE_NAME, frozen=True, confirm_fallback=confirm)

    assert locations is None
    assert _fallback_list(user_dirs) == []


def test_remembered_folder_skips_the_prompt(user_dirs: Path, app_folder: Path) -> None:
    """A remembered read-only folder falls back without asking."""
    _block_data_folder(app_folder)
    remembering = RecordingConfirm(FallbackAnswer(use_per_user=True, remember=True))
    resolve_locations(app_folder / EXE_NAME, frozen=True, confirm_fallback=remembering)
    silent = RecordingConfirm(FallbackAnswer(use_per_user=False, remember=False))

    locations = resolve_locations(app_folder / EXE_NAME, frozen=True, confirm_fallback=silent)

    assert locations is not None
    assert locations.mode is StorageMode.PER_USER_FALLBACK
    assert silent.asked == []


def test_writable_again_forgets_the_remembered_choice(user_dirs: Path, app_folder: Path) -> None:
    """Once Data can be written, portable mode resumes and the entry is removed (D259)."""
    _block_data_folder(app_folder)
    remembering = RecordingConfirm(FallbackAnswer(use_per_user=True, remember=True))
    resolve_locations(app_folder / EXE_NAME, frozen=True, confirm_fallback=remembering)
    (app_folder / PORTABLE_DATA_DIR_NAME).unlink()

    locations = resolve_locations(app_folder / EXE_NAME, frozen=True, confirm_fallback=remembering)

    assert locations is not None
    assert locations.mode is StorageMode.PORTABLE
    assert _fallback_list(user_dirs) == []
    assert not (user_dirs / "config" / FALLBACK_LIST_NAME).exists()


def test_registry_configures_once(user_dirs: Path, tmp_path: Path) -> None:
    """An unset registry gives per-user folders, and a second configure raises."""
    registry = StorageRegistry()
    assert registry.current().data_dir == user_dirs / "data"
    chosen = StorageLocations(
        data_dir=tmp_path / "d",
        config_dir=tmp_path / "c",
        cache_dir=tmp_path / "k",
        backup_dir=tmp_path / "b",
        bin_dir=tmp_path / "t",
        mode=StorageMode.PORTABLE,
    )

    registry.configure(chosen)

    assert registry.current() == chosen
    with pytest.raises(RuntimeError):
        registry.configure(chosen)
