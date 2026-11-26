"""Behavioral tests for the AppImage storage folder (D83a2a, D280 to D282)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from infiniteprep.storage_paths import (
    APPIMAGE_DATA_DIR_NAME,
    PORTABLE_DATA_DIR_NAME,
    PORTABLE_MARKER_NAME,
    AppImageLaunch,
    FallbackAnswer,
    StorageMode,
    appimage_launch_from_environment,
    resolve_locations,
)
from tests.fixtures.storage_fallback import (
    RecordingConfirm,
    build_never_asked_confirm,
    read_fallback_list,
    redirect_user_dirs,
)

APPIMAGE_NAME = "InfinitePrep-0.39.0-x86_64.AppImage"
EXE_NAME = "InfinitePrep"


@pytest.fixture
def user_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the per-user folders at a temp tree and return its root."""
    return redirect_user_dirs(tmp_path, monkeypatch)


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


def _block_data_folder(downloads: Path) -> None:
    """Make the data folder impossible to create by putting a file in its place."""
    (downloads / APPIMAGE_DATA_DIR_NAME).write_bytes(b"not a folder")


def test_environment_needs_both_values() -> None:
    """The launch details come from APPIMAGE and APPDIR, and both must be set."""
    full = {"APPIMAGE": "/apps/a.AppImage", "APPDIR": "/run/mount_a"}

    assert appimage_launch_from_environment(full) == AppImageLaunch(
        image=Path("/apps/a.AppImage"), appdir=Path("/run/mount_a")
    )
    assert appimage_launch_from_environment({"APPIMAGE": "/apps/a.AppImage"}) is None
    assert appimage_launch_from_environment({"APPDIR": "/run/mount_a"}) is None
    assert appimage_launch_from_environment({"APPIMAGE": "", "APPDIR": "/run/mount_a"}) is None


def test_appimage_keeps_data_beside_file(user_dirs: Path, downloads: Path, appdir: Path) -> None:
    """The folder beside the .AppImage holds the database and settings only (D281, D282)."""
    confirm = build_never_asked_confirm()

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
        confirm_fallback=build_never_asked_confirm(),
        appimage=_launch(downloads, appdir),
    )

    assert locations is not None
    assert locations.mode is StorageMode.PER_USER
    assert not (downloads / APPIMAGE_DATA_DIR_NAME).exists()


def build_relative_image_launch(_downloads: Path, appdir: Path) -> AppImageLaunch:
    """Return a launch whose image path is relative."""
    return AppImageLaunch(image=Path(APPIMAGE_NAME), appdir=appdir)


def build_missing_image_launch(downloads: Path, appdir: Path) -> AppImageLaunch:
    """Return a launch whose image file does not exist."""
    return AppImageLaunch(image=downloads / "missing.AppImage", appdir=appdir)


def build_folder_image_launch(downloads: Path, appdir: Path) -> AppImageLaunch:
    """Return a launch whose image path names a folder."""
    return AppImageLaunch(image=downloads, appdir=appdir)


def build_relative_appdir_launch(downloads: Path, _appdir: Path) -> AppImageLaunch:
    """Return a launch whose AppDir path is relative."""
    return AppImageLaunch(image=downloads / APPIMAGE_NAME, appdir=Path("mount"))


@pytest.mark.parametrize(
    "launch_factory",
    [
        pytest.param(build_relative_image_launch, id="relative-image"),
        pytest.param(build_missing_image_launch, id="missing-image"),
        pytest.param(build_folder_image_launch, id="image-is-a-folder"),
        pytest.param(build_relative_appdir_launch, id="relative-appdir"),
    ],
)
def test_invalid_appimage_values_are_ignored(
    user_dirs: Path,
    downloads: Path,
    appdir: Path,
    launch_factory: Callable[[Path, Path], AppImageLaunch],
) -> None:
    """A relative or missing path never picks a data folder."""
    locations = resolve_locations(
        _executable(appdir),
        frozen=True,
        confirm_fallback=build_never_asked_confirm(),
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
        confirm_fallback=build_never_asked_confirm(),
        appimage=_launch(downloads, appdir),
    )

    assert locations is not None
    assert locations.mode is StorageMode.PER_USER
    assert not (downloads / APPIMAGE_DATA_DIR_NAME).exists()


def test_tarball_marker_works_with_inherited(
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
        confirm_fallback=build_never_asked_confirm(),
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
        confirm_fallback=build_never_asked_confirm(),
        appimage=_launch(downloads, appdir),
    )

    assert locations is not None
    assert locations.data_dir == downloads.resolve() / APPIMAGE_DATA_DIR_NAME
    assert not (_executable(appdir).parent / PORTABLE_DATA_DIR_NAME).exists()


def test_unwritable_remember_saves_choice(user_dirs: Path, downloads: Path, appdir: Path) -> None:
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
    assert read_fallback_list(user_dirs) == [str(downloads.resolve())]


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
    assert read_fallback_list(user_dirs) == []


def test_unwritable_unremembered_folder_asks_again(
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
    assert read_fallback_list(user_dirs) == []


def test_writable_again_forgets_remembered_choice(
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
        _executable(appdir),
        frozen=True,
        confirm_fallback=build_never_asked_confirm(),
        appimage=launch,
    )

    assert locations is not None
    assert locations.mode is StorageMode.PORTABLE
    assert read_fallback_list(user_dirs) == []
