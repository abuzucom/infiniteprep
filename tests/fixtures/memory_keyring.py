"""In-memory keyring backends for credential and cleanup tests (D266, D293 to D295)."""

from __future__ import annotations

from keyring.backend import KeyringBackend
from keyring.errors import KeyringError, PasswordDeleteError


class MemoryKeyring(KeyringBackend):
    """A keyring backend that keeps passwords in a dictionary."""

    priority = 1

    def __init__(self) -> None:
        """Start with an empty password store."""
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
