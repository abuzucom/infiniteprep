"""Test that two instances migrating one library at once apply each step once (D349)."""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

from infiniteprep.db.migrations import MIGRATIONS, Migration
from infiniteprep.db.migrations.runner import migrate_database

LIBRARY_NAME = "infiniteprep.sqlite3"
INSTANCE_COUNT = 2
# The step holds its transaction long enough for the other instance to read the version.
STEP_HOLD_SECONDS = 0.3
LOCK_TIMEOUT_SECONDS = 30.0
JOIN_TIMEOUT_SECONDS = 60.0


def _count_runs(conn: sqlite3.Connection) -> None:
    """Test migration 2: record one run, then hold the transaction open."""
    conn.execute("INSERT INTO step_runs (ran) VALUES (?)", (1,))
    time.sleep(STEP_HOLD_SECONDS)


def _prepare_library(db_path: Path) -> None:
    """Leave a version-1 library with an empty run log, in WAL mode."""
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("PRAGMA journal_mode = WAL")
        MIGRATIONS[0].apply(conn)
        conn.execute("CREATE TABLE step_runs (ran INTEGER NOT NULL)")
        conn.execute("PRAGMA user_version = 1")


def _migrate_in_thread(
    db_path: Path, start: threading.Barrier, results: list[int], errors: list[BaseException]
) -> None:
    """Open a separate connection and migrate once the other instance is ready too."""
    steps = (MIGRATIONS[0], Migration(2, _count_runs))
    conn = sqlite3.connect(db_path, timeout=LOCK_TIMEOUT_SECONDS)
    try:
        start.wait()
        results.append(migrate_database(conn, db_path, migrations=steps))
    except Exception as error:  # surfaces in the main thread through the errors list
        errors.append(error)
    finally:
        conn.close()


def test_concurrent_instances_apply_a_step_once(tmp_path: Path) -> None:
    """Both instances reach version 2. The step itself runs one time."""
    db_path = tmp_path / LIBRARY_NAME
    _prepare_library(db_path)
    start = threading.Barrier(INSTANCE_COUNT)
    results: list[int] = []
    errors: list[BaseException] = []
    threads = [
        threading.Thread(target=_migrate_in_thread, args=(db_path, start, results, errors))
        for _ in range(INSTANCE_COUNT)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(JOIN_TIMEOUT_SECONDS)

    assert errors == []
    assert results == [2, 2]
    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM step_runs").fetchone()[0] == 1
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert list(tmp_path.glob("*.tmp")) == []
