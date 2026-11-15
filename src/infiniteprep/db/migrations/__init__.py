"""Numbered schema migrations tracked by SQLite user_version (D349)."""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Final

from infiniteprep.db.migrations import m0001_initial, m0002_collections


@dataclass(frozen=True)
class Migration:
    """One schema step.

    apply runs inside the runner's open transaction. It must not commit, and it must not call
    executescript(), since that commits the transaction. row_count_queries maps each table the
    step creates to a literal COUNT query that backup verification runs.
    """

    version: int
    apply: Callable[[sqlite3.Connection], None]
    row_count_queries: Mapping[str, str] = field(default_factory=dict)


# Append each new step with the next version number. The runner rejects gaps and reordering.
MIGRATIONS: Final[tuple[Migration, ...]] = (
    Migration(m0001_initial.VERSION, m0001_initial.apply, m0001_initial.ROW_COUNT_QUERIES),
    Migration(
        m0002_collections.VERSION, m0002_collections.apply, m0002_collections.ROW_COUNT_QUERIES
    ),
)
LATEST_VERSION: Final[int] = MIGRATIONS[-1].version
ROW_COUNT_QUERIES: Final[Mapping[str, str]] = MappingProxyType(
    {
        table: query
        for migration in MIGRATIONS
        for table, query in migration.row_count_queries.items()
    }
)

__all__ = ["LATEST_VERSION", "MIGRATIONS", "ROW_COUNT_QUERIES", "Migration"]
