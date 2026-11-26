"""Tests for how the repository stores, orders, and compares timestamps.

The journal keeps the time of the first write. Journal and job order follow insertion,
never the wall clock. Cache expiry compares one fixed-width UTC text form.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.migrations import LATEST_VERSION, m0001_initial, m0002_collections
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import AudioFormat, OperationStatus
from infiniteprep.domain.models import ChangeRecord, FileAsset, utc_now_iso

FIRST_WRITE_TIME = "2026-11-16T12:00:00.000000+00:00"
LATER_WRITE_TIME = "2026-11-16T12:05:00.000000+00:00"
LAST_WRITE_TIME = "2026-11-16T12:10:00.000000+00:00"
LEGACY_CREATED_TIME = "2026-11-15T12:00:00+00:00"
CLOCK_WITH_FRACTION = "2026-11-16T12:00:00.000123+00:00"
# A clock that stepped backward stamps a newer record with an older time.
EARLIER_CLOCK_TIME = "2026-11-16T11:00:00.000000+00:00"
FIXED_WIDTH_UTC_LENGTH = len(FIRST_WRITE_TIME)
ONE_HOUR = timedelta(hours=1)
PLUS_FIVE_HOURS = timedelta(hours=5)
STATUSES = tuple(OperationStatus)


@pytest.fixture
def repo() -> Repository:
    """Return a repository on a new in-memory library."""
    return Repository(DatabaseManager(":memory:"))


def _add_file(repo: Repository, file_id: str) -> None:
    """Store the file row that journal and job rows reference."""
    repo.upsert_file_asset(
        FileAsset(
            id=file_id,
            current_path=f"/music/{file_id}.wav",
            original_path=f"/music/{file_id}.wav",
            file_size=1,
            content_hash=f"hash-{file_id}",
            format=next(iter(AudioFormat)),
            duration=None,
            original_tags={},
            import_batch_id="batch",
            source_group_id=None,
        )
    )


def _record(operation_id: str, file_id: str, timestamp: str) -> ChangeRecord:
    """Build a journal record for one operation."""
    return ChangeRecord(
        operation_id=operation_id,
        file_id=file_id,
        previous_path=f"/music/{file_id}.wav",
        new_path=f"/music/{file_id}.wav",
        previous_tags={},
        written_tags={},
        backup_reference="backup",
        operation_status=STATUSES[0],
        timestamp=timestamp,
    )


def test_second_upsert_keeps_first_timestamp(repo: Repository) -> None:
    """A status update changes the status. The stored time of the operation stays."""
    _add_file(repo, "file-a")
    repo.upsert_change_record(_record("op-1", "file-a", FIRST_WRITE_TIME))
    update = _record("op-1", "file-a", LATER_WRITE_TIME)
    update.operation_status = STATUSES[-1]

    repo.upsert_change_record(update)

    stored = repo.get_change_record("op-1")
    assert stored is not None
    assert stored.timestamp == FIRST_WRITE_TIME
    assert stored.operation_status == STATUSES[-1]


def test_upsert_advances_only_updated_at(repo: Repository, monkeypatch: pytest.MonkeyPatch) -> None:
    """updated_at takes the time of each write. The first write sets both columns apart."""
    _add_file(repo, "file-a")
    clock_readings = iter([LATER_WRITE_TIME, LAST_WRITE_TIME])
    monkeypatch.setattr("infiniteprep.db.repositories.utc_now_iso", lambda: next(clock_readings))

    repo.upsert_change_record(_record("op-1", "file-a", FIRST_WRITE_TIME))
    after_insert = repo.get_change_record("op-1")
    repo.upsert_change_record(_record("op-1", "file-a", FIRST_WRITE_TIME))
    after_update = repo.get_change_record("op-1")

    assert after_insert is not None
    assert after_update is not None
    assert after_insert.updated_at == LATER_WRITE_TIME
    assert after_update.updated_at == LAST_WRITE_TIME
    assert after_update.timestamp == FIRST_WRITE_TIME


@pytest.mark.parametrize(
    "supplied",
    ["2026-11-16T12:00:00Z", "2026-11-16T17:00:00+05:00", "2026-11-16T12:00:00.000000+00:00"],
)
def test_journal_timestamp_is_normalized(repo: Repository, supplied: str) -> None:
    """A caller timestamp in any offset form reaches the journal as fixed-width UTC text."""
    _add_file(repo, "file-a")

    repo.upsert_change_record(_record("op-1", "file-a", supplied))

    stored = repo.get_change_record("op-1")
    assert stored is not None
    assert stored.timestamp == FIRST_WRITE_TIME


def test_journal_timestamp_needs_offset(repo: Repository) -> None:
    """A timestamp with no offset names no instant. The repository refuses it."""
    _add_file(repo, "file-a")

    with pytest.raises(ValueError, match="offset"):
        repo.upsert_change_record(_record("op-1", "file-a", "2026-11-16T12:00:00"))

    assert repo.get_change_record("op-1") is None


def test_migration_3_backfills_updated_at(tmp_path: Path) -> None:
    """A version-2 library gains the column. Each existing row starts at its timestamp."""
    library = tmp_path / "library.sqlite3"
    with closing(sqlite3.connect(library)) as conn, conn:
        m0001_initial.apply(conn)
        m0002_collections.apply(conn)
        conn.execute("PRAGMA user_version = 2")
        conn.execute(
            "INSERT INTO file_assets (id, current_path, original_path, file_size, content_hash, "
            "format, import_batch_id, imported_at) "
            "VALUES ('file-a', '/music/a.wav', '/music/a.wav', 1, 'hash-a', ?, 'batch', ?)",
            (next(iter(AudioFormat)).value, FIRST_WRITE_TIME),
        )
        conn.execute(
            "INSERT INTO change_records (operation_id, file_id, previous_path, new_path, "
            "backup_reference, operation_status, timestamp) "
            "VALUES ('op-1', 'file-a', '/music/a.wav', '/music/a.wav', 'backup', ?, ?)",
            (STATUSES[0].value, FIRST_WRITE_TIME),
        )

    with DatabaseManager(library) as db:
        stored = Repository(db).get_change_record("op-1")
        version = db.connect().execute("PRAGMA user_version").fetchone()[0]

    assert version == LATEST_VERSION
    assert stored is not None
    assert stored.timestamp == FIRST_WRITE_TIME
    assert stored.updated_at == FIRST_WRITE_TIME


def test_journal_order_survives_clock_rewind(repo: Repository) -> None:
    """The newest record comes first even when its timestamp reads older."""
    _add_file(repo, "file-a")
    repo.upsert_change_record(_record("op-old", "file-a", FIRST_WRITE_TIME))
    repo.upsert_change_record(_record("op-new", "file-a", EARLIER_CLOCK_TIME))

    assert [item.operation_id for item in repo.list_change_records()] == ["op-new", "op-old"]
    assert [item.operation_id for item in repo.list_change_records("file-a")] == [
        "op-new",
        "op-old",
    ]


def test_jobs_keep_order_on_rewind(repo: Repository, monkeypatch: pytest.MonkeyPatch) -> None:
    """The first queued job stays first when the clock stamps the second one earlier."""
    _add_file(repo, "file-a")
    _add_file(repo, "file-b")
    clock_readings = iter([LATER_WRITE_TIME, EARLIER_CLOCK_TIME])
    monkeypatch.setattr("infiniteprep.db.repositories.utc_now_iso", lambda: next(clock_readings))

    repo.enqueue_job("job-1", "file-a", "analyze")
    repo.enqueue_job("job-2", "file-b", "analyze")

    assert [job["id"] for job in repo.list_pending_jobs()] == ["job-1", "job-2"]


def test_utc_now_iso_fixed_width() -> None:
    """Every reading carries microseconds and the +00:00 offset, so text order equals time order."""
    reading = utc_now_iso()

    assert len(reading) == FIXED_WIDTH_UTC_LENGTH
    assert reading.endswith("+00:00")
    assert datetime.fromisoformat(reading).utcoffset() == timedelta(0)


@pytest.mark.parametrize("suffix_form", ["z", "offset"])
def test_cache_expiry_accepts_any_offset(repo: Repository, suffix_form: str) -> None:
    """A caller expiry with Z or a non-UTC offset still expires at the right instant."""
    now = datetime.now(UTC)

    def as_text(moment: datetime) -> str:
        if suffix_form == "z":
            return moment.isoformat().replace("+00:00", "Z")
        # The same instant, written as local time five hours ahead of UTC.
        return (moment + PLUS_FIVE_HOURS).replace(tzinfo=None).isoformat() + "+05:00"

    repo.set_cached_response("live", "provider", {"v": 1}, expires_at_iso=as_text(now + ONE_HOUR))
    repo.set_cached_response("dead", "provider", {"v": 2}, expires_at_iso=as_text(now - ONE_HOUR))

    assert repo.get_cached_response("live") == {"v": 1}
    assert repo.get_cached_response("dead") is None


@pytest.mark.parametrize(
    ("legacy_expiry", "expected_live"),
    [
        ("2026-11-16T12:00:01+00:00", True),
        # This row expired at fraction zero, before the clock reading in the same second.
        ("2026-11-16T12:00:00+00:00", False),
        ("2026-11-16T11:59:59+00:00", False),
    ],
)
def test_legacy_cache_expires_on_time(
    repo: Repository, monkeypatch: pytest.MonkeyPatch, legacy_expiry: str, expected_live: bool
) -> None:
    """A row an older version stored without microseconds keeps its true expiry."""
    conn = repo.db.connect()
    conn.execute(
        "INSERT INTO provider_cache (cache_key, provider, response_json, created_at, expires_at) "
        "VALUES ('legacy', 'provider', '{\"v\": 1}', ?, ?)",
        (LEGACY_CREATED_TIME, legacy_expiry),
    )
    conn.commit()
    monkeypatch.setattr("infiniteprep.db.repositories.utc_now_iso", lambda: CLOCK_WITH_FRACTION)

    cached = repo.get_cached_response("legacy")

    assert (cached == {"v": 1}) is expected_live


def test_rejected_timestamp_keeps_record(repo: Repository) -> None:
    """Validation runs first. A refused record keeps its empty updated_at."""
    _add_file(repo, "file-a")
    record = _record("op-1", "file-a", "2026-11-16T12:00:00")

    with pytest.raises(ValueError, match="offset"):
        repo.upsert_change_record(record)

    assert record.updated_at == ""
    assert record.timestamp == "2026-11-16T12:00:00"


def test_cache_expiry_rejects_offsetless_time(repo: Repository) -> None:
    """An expiry with no offset names no instant. The repository refuses it."""
    with pytest.raises(ValueError, match="offset"):
        repo.set_cached_response("key", "provider", {}, expires_at_iso="2026-11-16T12:00:00")
