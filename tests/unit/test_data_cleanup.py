"""Behavioral tests for the Settings data cleanup (D83a3b, D293 to D295)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import KeyringError, PasswordDeleteError

from infiniteprep.config.credentials import ENV_VAR_MAP, SERVICE_NAME
from infiniteprep.data_cleanup import (
    NOT_A_FOLDER_REASON,
    REFUSED_REASON,
    CleanupChoice,
    data_folders,
    is_app_folder,
    run_cleanup,
)
from infiniteprep.storage_paths import StorageLocations, StorageMode

DATA_ONLY = CleanupChoice(delete_data=True, delete_keys=False)
KEYS_ONLY = CleanupChoice(delete_data=False, delete_keys=True)


class MemoryKeyring(KeyringBackend):
    """A keyring backend that keeps passwords in a dictionary."""

    priority = 1

    def __init__(self) -> None:
        super().__init__()
        self.store: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        """Return the stored password or None."""
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        """Store a password."""
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        """Delete a password, raising PasswordDeleteError when it is absent."""
        if (service, username) not in self.store:
            raise PasswordDeleteError("not found")
        del self.store[(service, username)]


class FailingKeyring(MemoryKeyring):
    """A keyring backend whose deletes always fail."""

    def delete_password(self, service: str, username: str) -> None:
        """Fail every delete."""
        raise KeyringError("backend unavailable")


@pytest.fixture
def restore_keyring() -> Iterator[None]:
    """Put back the original keyring backend after the test."""
    original = keyring.get_keyring()
    yield
    keyring.set_keyring(original)


def _per_user(root: Path) -> StorageLocations:
    """Return per-user locations under root, shaped like the real platformdirs tree."""
    data = root / "share" / "InfinitePrep"
    return StorageLocations(
        data_dir=data,
        config_dir=root / "config" / "InfinitePrep",
        cache_dir=root / "cache" / "InfinitePrep",
        backup_dir=data / "backups",
        bin_dir=data / "bin",
        mode=StorageMode.PER_USER,
    )


def _portable(root: Path, user: StorageLocations) -> StorageLocations:
    """Return portable locations: Data beside a marker, the rest per-user."""
    app_folder = root / "InfinitePrep-0.45.0"
    app_folder.mkdir(parents=True)
    (app_folder / "portable.mode").write_bytes(b"")
    data = app_folder / "Data"
    return StorageLocations(
        data_dir=data,
        config_dir=data,
        cache_dir=user.cache_dir,
        backup_dir=user.backup_dir,
        bin_dir=user.bin_dir,
        mode=StorageMode.PORTABLE,
    )


def _fill(folders: list[Path]) -> None:
    """Create each folder with one file inside."""
    for folder in folders:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "item.bin").write_bytes(b"data")


def test_per_user_folders_drop_nested_ones(tmp_path: Path) -> None:
    """Backups and bin sit inside the data folder, so only the outer folders are listed."""
    locations = _per_user(tmp_path)

    folders = data_folders(locations)

    assert sorted(folders) == sorted(
        [locations.data_dir, locations.config_dir, locations.cache_dir]
    )


def test_portable_folders_include_the_per_user_rest(tmp_path: Path) -> None:
    """Portable mode lists Data once, plus the per-user backups, bin, and cache."""
    user = _per_user(tmp_path / "user")
    locations = _portable(tmp_path / "usb", user)

    folders = data_folders(locations)

    assert folders.count(locations.data_dir) == 1
    assert set(folders) == {locations.data_dir, user.backup_dir, user.bin_dir, user.cache_dir}


def test_data_choice_removes_every_folder_and_keeps_music(tmp_path: Path) -> None:
    """The data box removes each listed folder, and a music folder nearby stays."""
    locations = _per_user(tmp_path)
    folders = data_folders(locations)
    _fill([*folders, locations.backup_dir, locations.bin_dir])
    music = tmp_path / "Music" / "track.flac"
    music.parent.mkdir()
    music.write_bytes(b"audio")

    report = run_cleanup(DATA_ONLY, locations)

    assert report.succeeded
    assert sorted(report.removed) == sorted(folders)
    assert not any(folder.exists() for folder in folders)
    assert music.read_bytes() == b"audio"


def test_portable_data_folder_is_removed(tmp_path: Path) -> None:
    """A Data folder with its marker beside it counts as InfinitePrep's own."""
    user = _per_user(tmp_path / "user")
    locations = _portable(tmp_path / "usb", user)
    _fill([locations.data_dir])

    report = run_cleanup(DATA_ONLY, locations)

    assert report.succeeded
    assert not locations.data_dir.exists()
    assert (locations.data_dir.parent / "portable.mode").is_file()


def test_missing_folders_are_no_error(tmp_path: Path) -> None:
    """A folder that never existed is skipped without a failure."""
    report = run_cleanup(DATA_ONLY, _per_user(tmp_path))

    assert report.succeeded
    assert report.removed == []


def test_foreign_folder_is_refused(tmp_path: Path) -> None:
    """A folder outside InfinitePrep's own names stays, and the report names it."""
    foreign = tmp_path / "Documents"
    _fill([foreign])
    locations = StorageLocations(
        data_dir=foreign,
        config_dir=foreign,
        cache_dir=foreign,
        backup_dir=foreign,
        bin_dir=foreign,
        mode=StorageMode.PORTABLE,
    )

    report = run_cleanup(DATA_ONLY, locations)

    assert not is_app_folder(foreign)
    assert report.failed == [(foreign, REFUSED_REASON)]
    assert (foreign / "item.bin").exists()
    assert not report.succeeded


def test_one_failure_does_not_stop_the_rest(tmp_path: Path) -> None:
    """A file where a folder should be is reported, and the other folders still go."""
    locations = _per_user(tmp_path)
    _fill([locations.data_dir, locations.config_dir])
    locations.cache_dir.parent.mkdir(parents=True)
    locations.cache_dir.write_bytes(b"not a folder")

    report = run_cleanup(DATA_ONLY, locations)

    assert report.failed == [(locations.cache_dir, NOT_A_FOLDER_REASON)]
    assert sorted(report.removed) == sorted([locations.data_dir, locations.config_dir])
    assert locations.cache_dir.is_file()
    assert "Could not remove" in report.describe()


@pytest.mark.usefixtures("restore_keyring")
def test_keys_choice_removes_keys_and_keeps_data(tmp_path: Path) -> None:
    """The keys box removes the saved keys only. The data folders stay."""
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    for key_name in ENV_VAR_MAP:
        backend.set_password(SERVICE_NAME, key_name, "placeholder-value")
    locations = _per_user(tmp_path)
    _fill([locations.data_dir])

    report = run_cleanup(KEYS_ONLY, locations)

    assert report.succeeded
    assert report.keys_removed
    assert backend.store == {}
    assert (locations.data_dir / "item.bin").exists()


@pytest.mark.usefixtures("restore_keyring")
def test_keyring_failure_is_reported(tmp_path: Path) -> None:
    """A keyring that rejects deletes leaves the report unsuccessful."""
    keyring.set_keyring(FailingKeyring())

    report = run_cleanup(KEYS_ONLY, _per_user(tmp_path))

    assert not report.keys_removed
    assert not report.succeeded
    assert "Could not remove the saved API keys" in report.describe()


def test_nothing_chosen_removes_nothing(tmp_path: Path) -> None:
    """With both boxes unchecked the cleanup touches nothing."""
    locations = _per_user(tmp_path)
    _fill([locations.data_dir])

    report = run_cleanup(CleanupChoice(delete_data=False, delete_keys=False), locations)

    assert report.removed == []
    assert (locations.data_dir / "item.bin").exists()
