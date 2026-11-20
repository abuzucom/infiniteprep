"""Test that two instances migrating one library at once apply each step once (D349)."""

from __future__ import annotations

import sqlite3
import threading
from contextlib import closing
from pathlib import Path

from infiniteprep.db.migrations import MIGRATIONS, Migration
from infiniteprep.db.migrations.runner import migrate_database

LIBRARY_NAME = "infiniteprep.sqlite3"
INSTANCE_COUNT = 2
LOCK_TIMEOUT_SECONDS = 30.0
JOIN_TIMEOUT_SECONDS = 60.0
WRITE_LOCK_STATEMENT = "BEGIN IMMEDIATE"


class OverlapSignal:
    """Counts the instances that asked for the write lock and signals once all did."""

    def __init__(self) -> None:
        """Start with no instance at the write lock."""
        self._lock = threading.Lock()
        self._requests = 0
        self.all_requested = threading.Event()
        self.overlap_seen: list[bool] = []

    def record_statement(self, statement: str) -> None:
        """Count a write lock request. SQLite traces the statement before it waits."""
        if not statement.startswith(WRITE_LOCK_STATEMENT):
            return
        with self._lock:
            self._requests += 1
            if self._requests >= INSTANCE_COUNT:
                self.all_requested.set()

    def count_runs(self, conn: sqlite3.Connection) -> None:
        """Test migration 2: record one run, then hold the lock until every instance waits."""
        conn.execute("INSERT INTO step_runs (ran) VALUES (?)", (1,))
        self.overlap_seen.append(self.all_requested.wait(LOCK_TIMEOUT_SECONDS))


def _prepare_library(db_path: Path) -> None:
    """Leave a version-1 library with an empty run log, in WAL mode."""
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.execute("PRAGMA journal_mode = WAL")
        MIGRATIONS[0].apply(conn)
        conn.execute("CREATE TABLE step_runs (ran INTEGER NOT NULL)")
        conn.execute("PRAGMA user_version = 1")


def _migrate_in_thread(
    db_path: Path,
    start: threading.Barrier,
    signal: OverlapSignal,
    results: list[int],
    errors: list[BaseException],
) -> None:
    """Open a separate connection and migrate once the other instance is ready too."""
    steps = (MIGRATIONS[0], Migration(2, signal.count_runs))
    conn = sqlite3.connect(db_path, timeout=LOCK_TIMEOUT_SECONDS)
    conn.set_trace_callback(signal.record_statement)
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
    signal = OverlapSignal()
    results: list[int] = []
    errors: list[BaseException] = []
    thread_args = (db_path, start, signal, results, errors)
    threads = [
        threading.Thread(target=_migrate_in_thread, args=thread_args) for _ in range(INSTANCE_COUNT)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(JOIN_TIMEOUT_SECONDS)

    assert errors == []
    # The step ran once, and the other instance was already waiting at the lock then.
    assert signal.overlap_seen == [True]
    assert results == [2, 2]
    with closing(sqlite3.connect(db_path)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM step_runs").fetchone()[0] == 1
        assert conn.execute("PRAGMA user_version").fetchone()[0] == 2
    assert list(tmp_path.glob("*.tmp")) == []
