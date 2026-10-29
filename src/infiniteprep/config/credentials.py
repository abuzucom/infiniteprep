"""OS keyring and environment variable credential storage (Section 14)."""

from __future__ import annotations

import os
from typing import Final

import keyring

SERVICE_NAME: Final[str] = "InfinitePrep"
ENV_VAR_MAP: Final[dict[str, str]] = {
    "acoustid_api_key": "INFINITEPREP_ACOUSTID_API_KEY",
    "discogs_token": "INFINITEPREP_DISCOGS_TOKEN",
    "acrcloud_access_key": "INFINITEPREP_ACRCLOUD_ACCESS_KEY",
    "acrcloud_access_secret": "INFINITEPREP_ACRCLOUD_ACCESS_SECRET",
    "llm_api_key": "INFINITEPREP_LLM_API_KEY",
}


class CredentialStore:
    """Stores and retrieves API tokens via OS keyring or in-memory test store."""

    def __init__(self, use_memory_backend: bool = False) -> None:
        """Initialize CredentialStore with OS keyring or isolated memory dictionary."""
        self.use_memory_backend = use_memory_backend
        self._memory: dict[str, str] = {}

    def get_secret(self, key_name: str) -> str | None:
        """Retrieve a secret by key_name from environment variable, memory, or OS keyring."""
        env_name = ENV_VAR_MAP.get(key_name)
        if env_name and os.environ.get(env_name):
            return os.environ[env_name]
        if self.use_memory_backend:
            return self._memory.get(key_name)
        try:
            return keyring.get_password(SERVICE_NAME, key_name)
        except Exception:
            return self._memory.get(key_name)

    def set_secret(self, key_name: str, secret_value: str) -> None:
        """Store a secret in memory or the OS keyring."""
        if self.use_memory_backend:
            self._memory[key_name] = secret_value
            return
        try:
            keyring.set_password(SERVICE_NAME, key_name, secret_value)
        except Exception:
            self._memory[key_name] = secret_value

    def delete_secret(self, key_name: str) -> None:
        """Remove a stored secret from memory or OS keyring."""
        self._memory.pop(key_name, None)
        if self.use_memory_backend:
            return
        try:
            keyring.delete_password(SERVICE_NAME, key_name)
        except Exception:
            return
