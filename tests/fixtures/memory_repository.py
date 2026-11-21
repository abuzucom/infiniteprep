"""A repository over a fresh in-memory database for adapter and provider tests."""

from __future__ import annotations

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository

IN_MEMORY_DATABASE = ":memory:"


def build_memory_repository() -> Repository:
    """Return a Repository on a fresh in-memory SQLite database."""
    return Repository(DatabaseManager(IN_MEMORY_DATABASE))
