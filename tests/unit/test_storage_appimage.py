"""Behavioral tests for the AppImage storage folder (D83a2a, D280 to D282)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from infiniteprep import storage_paths
from infiniteprep.storage_paths import (
    APPIMAGE_DATA_DIR_NAME,
    FALLBACK_LIST_NAME,
    PORTABLE_DATA_DIR_NAME,
    PORTABLE_MARKER_NAME,
    AppImageLaunch,
    FallbackAnswer,
    StorageMode,
    appimage_launch_from_environment,
    resolve_locations,
)

APPIMAGE_NAME = "InfinitePrep-0.39.0-x86_64.AppImage"
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
def downloads(tmp_path: Path) -> Path:
    """Return a folder holding the .AppImage file, like ~/Downloads."""
    folder = tmp_path / "Downloads"
    folder.mkdir()
    (folder / APPIMAGE_NAME).write_bytes(b"")
    return folder


@pytest.fixture
def appdir(tmp_path: Path) -> Path:
    """Return the mounted AppDir that holds the bundled executable."""
    folder = tmp_path / "mount" / ".mount_InfiniXYZ"
    (folder / "usr" / "bin").mkdir(parents=True)
    (folder / "usr" / "bin" / EXE_NAME).write_bytes(b"")
    return folder


def _executable(appdir: Path) -> Path:
    """Return the bundled executable inside the AppDir."""
    return appdir / "usr" / "bin" / EXE_NAME


def _launch(downloads: Path, appdir: Path) -> AppImageLaunch:
    """Return the launch details the AppImage runtime would set."""
    return AppImageLaunch(image=downloads / APPIMAGE_NAME, appdir=appdir)


def _fallback_list(user_dirs: Path) -> list[str]:
    """Return the remembered folders, or an empty list when the file is absent."""
    path = user_dirs / "config" / FALLBACK_LIST_NAME
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def _block_data_folder(downloads: Path) -> None:
    """Make the data folder impossible to create by putting a file in its place."""
    (downloads / APPIMAGE_DATA_DIR_NAME).write_bytes(b"not a folder")


def _never_asked() -> RecordingConfirm:
    """Return a prompt that would quit, so a test fails loudly if it is shown."""
    return RecordingConfirm(FallbackAnswer(use_per_user=False, remember=False))


def test_environment_needs_both_values() -> None:
    """The launch details come from APPIMAGE and APPDIR, and both must be set."""
    full = {"APPIMAGE": "/apps/a.AppImage", "APPDIR": "/run/mount_a"}

    assert appimage_launch_from_environment(full) == AppImageLaunch(
        image=Path("/apps/a.AppImage"), appdir=Path("/run/mount_a")
    )
    assert appimage_launch_from_environment({"APPIMAGE": "/apps/a.AppImage"}) is None
    assert appimage_launch_from_environment({"APPDIR": "/run/mount_a"}) is None
    assert appimage_launch_from_environment({"APPIMAGE": "", "APPDIR": "/run/mount_a"}) is None


def test_appimage_keeps_database_and_settings_beside_the_file(
    user_dirs: Path, downloads: Path, appdir: Path
) -> None:
    """The folder beside the .AppImage holds the database and settings only (D281, D282)."""
    confirm = _never_asked()

    locations = resolve_locations(
        _executable(appdir),
        frozen=True,
        confirm_fallback=confirm,
        appimage=_launch(downloads, appdir),
    )

    data = downloads.resolve() / APPIMAGE_DATA_DIR_NAME
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


def test_source_run_ignores_the_appimage(user_dirs: Path, downloads: Path, appdir: Path) -> None:
    """A run from source stays per-user even with AppImage values set."""
    locations = resolve_locations(
        _executable(appdir),
        frozen=False,
        confirm_fallback=_never_asked(),
        appimage=_launch(downloads, appdir),
    )

    assert locations is not None
    assert locations.mode is StorageMode.PER_USER
    assert not (downloads / APPIMAGE_DATA_DIR_NAME).exists()


@pytest.mark.parametrize(
    "launch_factory",
    [
        pytest.param(
            lambda downloads, appdir: AppImageLaunch(image=Path(APPIMAGE_NAME), appdir=appdir),
            id="relative-image",
        ),
        pytest.param(
            lambda downloads, appdir: AppImageLaunch(
                image=downloads / "missing.AppImage", appdir=appdir
            ),
            id="missing-image",
        ),
        pytest.param(
            lambda downloads, appdir: AppImageLaunch(image=downloads, appdir=appdir),
            id="image-is-a-folder",
        ),
        pytest.param(
            lambda downloads, appdir: AppImageLaunch(
                image=downloads / APPIMAGE_NAME, appdir=Path("mount")
            ),
            id="relative-appdir",
        ),
    ],
)
def test_invalid_appimage_values_are_ignored(
    user_dirs: Path, downloads: Path, appdir: Path, launch_factory
) -> None:
    """A relative or missing path never picks a data folder."""
    locations = resolve_locations(
        _executable(appdir),
        frozen=True,
        confirm_fallback=_never_asked(),
        appimage=launch_factory(downloads, appdir),
    )

    assert locations is not None
    assert locations.mode is StorageMode.PER_USER
    assert not (downloads / APPIMAGE_DATA_DIR_NAME).exists()


def test_inherited_appimage_values_are_ignored(
    user_dirs: Path, downloads: Path, appdir: Path, tmp_path: Path
) -> None:
    """A tar.gz copy started from inside another AppImage inherits APPIMAGE and APPDIR.

    Its executable lies outside that APPDIR, so it keeps its own storage.
    """
    tarball = tmp_path / "InfinitePrep-tarball"
    tarball.mkdir()
    (tarball / EXE_NAME).write_bytes(b"")

    locations = resolve_locations(
        tarball / EXE_NAME,
        frozen=True,
        confirm_fallback=_never_asked(),
        appimage=_launch(downloads, appdir),
    )

    assert locations is not None
    assert locations.mode is StorageMode.PER_USER
    assert not (downloads / APPIMAGE_DATA_DIR_NAME).exists()


def test_tarball_marker_still_works_with_inherited_values(
    user_dirs: Path, downloads: Path, appdir: Path, tmp_path: Path
) -> None:
    """The tar.gz marker keeps working when ignored AppImage values are present (D279)."""
    tarball = tmp_path / "InfinitePrep-tarball"
    tarball.mkdir()
    (tarball / EXE_NAME).write_bytes(b"")
    (tarball / PORTABLE_MARKER_NAME).write_bytes(b"")

    locations = resolve_locations(
        tarball / EXE_NAME,
        frozen=True,
        confirm_fallback=_never_asked(),
        appimage=_launch(downloads, appdir),
    )

    assert locations is not None
    assert locations.mode is StorageMode.PORTABLE
    assert locations.data_dir == tarball.resolve() / PORTABLE_DATA_DIR_NAME


def test_appimage_wins_over_a_marker(user_dirs: Path, downloads: Path, appdir: Path) -> None:
    """A marker inside the AppDir does not move the data into the read-only mount."""
    (_executable(appdir).parent / PORTABLE_MARKER_NAME).write_bytes(b"")

    locations = resolve_locations(
        _executable(appdir),
        frozen=True,
        confirm_fallback=_never_asked(),
        appimage=_launch(downloads, appdir),
    )

    assert locations is not None
    assert locations.data_dir == downloads.resolve() / APPIMAGE_DATA_DIR_NAME
    assert not (_executable(appdir).parent / PORTABLE_DATA_DIR_NAME).exists()


def test_unwritable_folder_with_remember_saves_the_choice(
    user_dirs: Path, downloads: Path, appdir: Path
) -> None:
    """An unwritable folder gets the D258 prompt, keyed by the AppImage's folder (D280)."""
    _block_data_folder(downloads)
    confirm = RecordingConfirm(FallbackAnswer(use_per_user=True, remember=True))

    locations = resolve_locations(
        _executable(appdir),
        frozen=True,
        confirm_fallback=confirm,
        appimage=_launch(downloads, appdir),
    )

    assert locations is not None
    assert locations.mode is StorageMode.PER_USER_FALLBACK
    assert locations.data_dir == user_dirs / "data"
    assert confirm.asked == [downloads.resolve()]
    assert _fallback_list(user_dirs) == [str(downloads.resolve())]


def test_unwritable_folder_quit_returns_none(
    user_dirs: Path, downloads: Path, appdir: Path
) -> None:
    """Quit ends the launch and remembers nothing."""
    _block_data_folder(downloads)
    confirm = RecordingConfirm(FallbackAnswer(use_per_user=False, remember=True))

    locations = resolve_locations(
        _executable(appdir),
        frozen=True,
        confirm_fallback=confirm,
        appimage=_launch(downloads, appdir),
    )

    assert locations is None
    assert _fallback_list(user_dirs) == []


def test_unwritable_folder_without_remember_asks_again(
    user_dirs: Path, downloads: Path, appdir: Path
) -> None:
    """Without Remember the fallback holds for this run only."""
    _block_data_folder(downloads)
    confirm = RecordingConfirm(FallbackAnswer(use_per_user=True, remember=False))
    launch = _launch(downloads, appdir)

    first = resolve_locations(
        _executable(appdir), frozen=True, confirm_fallback=confirm, appimage=launch
    )
    second = resolve_locations(
        _executable(appdir), frozen=True, confirm_fallback=confirm, appimage=launch
    )

    assert first is not None
    assert second is not None
    assert first.mode is StorageMode.PER_USER_FALLBACK
    assert len(confirm.asked) == 2
    assert _fallback_list(user_dirs) == []


def test_writable_again_forgets_the_remembered_choice(
    user_dirs: Path, downloads: Path, appdir: Path
) -> None:
    """Once the folder can be written, the AppImage uses it again (D259)."""
    _block_data_folder(downloads)
    launch = _launch(downloads, appdir)
    remembering = RecordingConfirm(FallbackAnswer(use_per_user=True, remember=True))
    resolve_locations(
        _executable(appdir), frozen=True, confirm_fallback=remembering, appimage=launch
    )
    (downloads / APPIMAGE_DATA_DIR_NAME).unlink()

    locations = resolve_locations(
        _executable(appdir), frozen=True, confirm_fallback=_never_asked(), appimage=launch
    )

    assert locations is not None
    assert locations.mode is StorageMode.PORTABLE
    assert _fallback_list(user_dirs) == []
