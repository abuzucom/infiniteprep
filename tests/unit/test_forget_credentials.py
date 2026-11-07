"""Behavioral tests for the hidden --forget-credentials flag (D266)."""

from __future__ import annotations

from collections.abc import Iterator

import keyring
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import KeyringError, PasswordDeleteError

from infiniteprep.config.credentials import ENV_VAR_MAP, FORGET_FAILED_EXIT_CODE, SERVICE_NAME
from infiniteprep.main import FORGET_CREDENTIALS_FLAG, main

OTHER_SERVICE = "SomeOtherApp"


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


@pytest.mark.usefixtures("restore_keyring")
def test_flag_deletes_every_infiniteprep_key() -> None:
    """All InfinitePrep keys go, a missing key is no error, and other apps keep theirs."""
    backend = MemoryKeyring()
    keyring.set_keyring(backend)
    saved = sorted(ENV_VAR_MAP)[:-1]
    for key_name in saved:
        backend.set_password(SERVICE_NAME, key_name, "placeholder-value")
    backend.set_password(OTHER_SERVICE, "acoustid_api_key", "kept")

    assert main([FORGET_CREDENTIALS_FLAG]) == 0
    assert backend.store == {(OTHER_SERVICE, "acoustid_api_key"): "kept"}


@pytest.mark.usefixtures("restore_keyring")
def test_flag_reports_a_keyring_failure() -> None:
    """A keyring that rejects deletes returns the failure exit code."""
    keyring.set_keyring(FailingKeyring())

    assert main([FORGET_CREDENTIALS_FLAG]) == FORGET_FAILED_EXIT_CODE
