"""Behavioral tests for the macOS app bundle storage folder (D83a3a, D289 to D292)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from infiniteprep import storage_paths
from infiniteprep.storage_paths import (
    FALLBACK_LIST_NAME,
    MACOS_DATA_DIR_NAME,
    PORTABLE_DATA_DIR_NAME,
    PORTABLE_MARKER_NAME,
    FallbackAnswer,
    StorageMode,
    resolve_locations,
)

APP_NAME = "InfinitePrep.app"
EXE_NAME = "InfinitePrep"


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
def applications(tmp_path: Path) -> Path:
    """Return a folder holding InfinitePrep.app, like /Applications or a USB drive."""
    folder = tmp_path / "Applications"
    macos_dir = folder / APP_NAME / "Contents" / "MacOS"
    macos_dir.mkdir(parents=True)
    (macos_dir / EXE_NAME).write_bytes(b"")
    return folder


def _executable(folder: Path) -> Path:
    """Return the executable inside the app bundle in folder."""
    return folder / APP_NAME / "Contents" / "MacOS" / EXE_NAME


def _add_marker(folder: Path) -> None:
    """Put portable.mode next to the app bundle."""
    (folder / PORTABLE_MARKER_NAME).write_bytes(b"")


def _block_data_folder(folder: Path) -> None:
    """Make the data folder impossible to create by putting a file in its place."""
    (folder / MACOS_DATA_DIR_NAME).write_bytes(b"not a folder")


def _fallback_list(user_dirs: Path) -> list[str]:
    """Return the remembered folders, or an empty list when the file is absent."""
    path = user_dirs / "config" / FALLBACK_LIST_NAME
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _never_asked() -> RecordingConfirm:
    """Return a prompt that would quit, so a test fails loudly if it is shown."""
    return RecordingConfirm(FallbackAnswer(use_per_user=False, remember=False))


def test_marker_next_to_the_app_keeps_data_beside_it(user_dirs: Path, applications: Path) -> None:
    """The database and settings go in InfinitePrep-Data next to the .app (D289, D291)."""
    _add_marker(applications)
    confirm = _never_asked()

    locations = resolve_locations(_executable(applications), frozen=True, confirm_fallback=confirm)

    data = applications.resolve() / MACOS_DATA_DIR_NAME
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


def test_no_marker_stays_per_user(user_dirs: Path, applications: Path) -> None:
    """Without the marker the app uses the per-user folders (D289)."""
    locations = resolve_locations(
        _executable(applications), frozen=True, confirm_fallback=_never_asked()
    )

    assert locations is not None
    assert locations.mode is StorageMode.PER_USER
    assert locations.data_dir == user_dirs / "data"
    assert not (applications / MACOS_DATA_DIR_NAME).exists()


def test_source_run_ignores_the_marker(user_dirs: Path, applications: Path) -> None:
    """A run from source stays per-user even beside a marker."""
    _add_marker(applications)

    locations = resolve_locations(
        _executable(applications), frozen=False, confirm_fallback=_never_asked()
    )

    assert locations is not None
    assert locations.mode is StorageMode.PER_USER
    assert not (applications / MACOS_DATA_DIR_NAME).exists()


def test_marker_inside_the_bundle_is_ignored(user_dirs: Path, applications: Path) -> None:
    """A marker in Contents/MacOS never writes data inside the signed bundle."""
    macos_dir = _executable(applications).parent
    (macos_dir / PORTABLE_MARKER_NAME).write_bytes(b"")

    locations = resolve_locations(
        _executable(applications), frozen=True, confirm_fallback=_never_asked()
    )

    assert locations is not None
    assert locations.mode is StorageMode.PER_USER
    assert not (macos_dir / PORTABLE_DATA_DIR_NAME).exists()


def test_translocated_copy_stays_per_user(user_dirs: Path, tmp_path: Path) -> None:
    """A translocated copy holds only the .app, so it sees no marker (D292)."""
    translocated = tmp_path / "private" / "var" / "folders" / "AppTranslocation" / "ABC" / "d"
    macos_dir = translocated / APP_NAME / "Contents" / "MacOS"
    macos_dir.mkdir(parents=True)
    (macos_dir / EXE_NAME).write_bytes(b"")
    confirm = _never_asked()

    locations = resolve_locations(macos_dir / EXE_NAME, frozen=True, confirm_fallback=confirm)

    assert locations is not None
    assert locations.mode is StorageMode.PER_USER
    assert confirm.asked == []


def test_bundle_lookalike_uses_the_executable_folder(user_dirs: Path, tmp_path: Path) -> None:
    """A MacOS folder without Contents is no bundle, so the plain marker rule applies."""
    folder = tmp_path / "Tools.app" / "MacOS"
    folder.mkdir(parents=True)
    (folder / EXE_NAME).write_bytes(b"")
    (folder / PORTABLE_MARKER_NAME).write_bytes(b"")

    locations = resolve_locations(folder / EXE_NAME, frozen=True, confirm_fallback=_never_asked())

    assert locations is not None
    assert locations.mode is StorageMode.PORTABLE
    assert locations.data_dir == folder.resolve() / PORTABLE_DATA_DIR_NAME


def test_unwritable_folder_with_remember_saves_the_choice(
    user_dirs: Path, applications: Path
) -> None:
    """An unwritable folder gets the D258 prompt, keyed by the folder holding the .app."""
    _add_marker(applications)
    _block_data_folder(applications)
    confirm = RecordingConfirm(FallbackAnswer(use_per_user=True, remember=True))

    locations = resolve_locations(_executable(applications), frozen=True, confirm_fallback=confirm)

    assert locations is not None
    assert locations.mode is StorageMode.PER_USER_FALLBACK
    assert locations.data_dir == user_dirs / "data"
    assert confirm.asked == [applications.resolve()]
    assert _fallback_list(user_dirs) == [str(applications.resolve())]


def test_unwritable_folder_without_remember_asks_again(user_dirs: Path, applications: Path) -> None:
    """Without Remember the fallback holds for this run only."""
    _add_marker(applications)
    _block_data_folder(applications)
    confirm = RecordingConfirm(FallbackAnswer(use_per_user=True, remember=False))

    first = resolve_locations(_executable(applications), frozen=True, confirm_fallback=confirm)
    second = resolve_locations(_executable(applications), frozen=True, confirm_fallback=confirm)

    assert first is not None
    assert second is not None
    assert first.mode is StorageMode.PER_USER_FALLBACK
    assert len(confirm.asked) == 2
    assert _fallback_list(user_dirs) == []


def test_unwritable_folder_quit_returns_none(user_dirs: Path, applications: Path) -> None:
    """Quit ends the launch and remembers nothing."""
    _add_marker(applications)
    _block_data_folder(applications)
    confirm = RecordingConfirm(FallbackAnswer(use_per_user=False, remember=True))

    locations = resolve_locations(_executable(applications), frozen=True, confirm_fallback=confirm)

    assert locations is None
    assert _fallback_list(user_dirs) == []


def test_writable_again_forgets_the_remembered_choice(user_dirs: Path, applications: Path) -> None:
    """Once the folder can be written, the app uses it again (D259)."""
    _add_marker(applications)
    _block_data_folder(applications)
    remembering = RecordingConfirm(FallbackAnswer(use_per_user=True, remember=True))
    resolve_locations(_executable(applications), frozen=True, confirm_fallback=remembering)
    (applications / MACOS_DATA_DIR_NAME).unlink()

    locations = resolve_locations(
        _executable(applications), frozen=True, confirm_fallback=_never_asked()
    )

    assert locations is not None
    assert locations.mode is StorageMode.PORTABLE
    assert _fallback_list(user_dirs) == []
