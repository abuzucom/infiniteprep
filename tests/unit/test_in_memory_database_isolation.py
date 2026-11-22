"""Tests that each in-memory DatabaseManager gets a private database."""

from __future__ import annotations

from infiniteprep.db.connection import DatabaseManager

PROBE_TABLE_SQL = "CREATE TABLE IF NOT EXISTS isolation_probe (value TEXT)"
# Each manager runs every schema migration. Ten closed managers keep the reuse
# check while cutting that cost from the earlier fifty open ones.
REPLACEMENT_MANAGER_COUNT = 10


def count_probe_rows(manager: DatabaseManager) -> int:
    """Return the number of rows in the probe table of one manager's database."""
    conn = manager.connect()
    conn.execute(PROBE_TABLE_SQL)
    return int(conn.execute("SELECT COUNT(*) FROM isolation_probe").fetchone()[0])


def test_two_in_memory_managers_never_share_rows() -> None:
    """Rows written through one manager stay invisible to another, even while both are open."""
    first = DatabaseManager(":memory:")
    first.connect().execute(PROBE_TABLE_SQL)
    first.connect().execute("INSERT INTO isolation_probe VALUES ('kept apart')")
    first.connect().commit()

    second = DatabaseManager(":memory:")

    assert count_probe_rows(first) == 1
    assert count_probe_rows(second) == 0


def test_replacement_manager_starts_empty() -> None:
    """A new manager stays empty after an older one is dropped with its connection open."""
    old = DatabaseManager(":memory:")
    old_conn = old.connect()
    old_conn.execute(PROBE_TABLE_SQL)
    old_conn.execute("INSERT INTO isolation_probe VALUES ('stale')")
    old_conn.commit()
    del old

    for _ in range(REPLACEMENT_MANAGER_COUNT):
        replacement = DatabaseManager(":memory:")
        assert count_probe_rows(replacement) == 0
        # Closing frees the database, so the next manager has a real chance of reuse.
        replacement.close()
    old_conn.close()
