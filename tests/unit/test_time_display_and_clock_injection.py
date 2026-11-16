"""Tests for local time display, MusicBrainz year extraction, and injected clocks.

The journal dialogs show stored UTC instants as local time. A malformed provider date
yields no year. Each journal writer takes its timestamp from an injected clock.
"""

from __future__ import annotations

from datetime import timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from infiniteprep.adapters.catalog.musicbrainz import extract_release_year
from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.adapters.transcoder.safe_transcoder import SafeTranscoderAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from infiniteprep.ui.journal_dialog import JournalDialog
from infiniteprep.ui.time_format import format_local_timestamp
from tests.fixtures.audio_factory import create_wav_fixture

STORED_TIME = "2026-11-16T12:00:00.000000+00:00"
SERVICE_CLOCK_TIME = "2026-11-16T12:00:00.000000+00:00"
ADAPTER_CLOCK_TIME = "2026-11-16T12:00:05.000000+00:00"
TRANSCODER_CLOCK_TIME = "2026-11-16T12:00:09.000000+00:00"
FIVE_HOURS_BEHIND_UTC = timezone(timedelta(hours=-5))
HALF_HOUR_ZONE = timezone(timedelta(hours=5, minutes=30))
TIMESTAMP_COLUMN = 0


def test_stored_utc_time_displays_in_the_given_zone() -> None:
    """A stored UTC instant reads as local date and time with no offset or fraction."""
    assert format_local_timestamp(STORED_TIME, FIVE_HOURS_BEHIND_UTC) == "2026-11-16 07:00:00"
    assert format_local_timestamp(STORED_TIME, HALF_HOUR_ZONE) == "2026-11-16 17:30:00"


def test_local_display_crosses_the_date_line_of_the_zone() -> None:
    """An instant early in the UTC day falls on the previous local date west of UTC."""
    early_utc = "2026-03-01T02:00:00+00:00"

    assert format_local_timestamp(early_utc, FIVE_HOURS_BEHIND_UTC) == "2026-02-28 21:00:00"


@pytest.mark.parametrize("unreadable", ["", "not a time", "2026-11-16T12:00:00"])
def test_text_that_names_no_instant_displays_unchanged(unreadable: str) -> None:
    """Damaged or offset-less text stays visible as stored."""
    assert format_local_timestamp(unreadable, FIVE_HOURS_BEHIND_UTC) == unreadable


@pytest.mark.parametrize(
    ("raw_date", "expected_year"),
    [
        ("1999", "1999"),
        ("1999-07", "1999"),
        ("1999-07-04", "1999"),
        (" 2004-01-01 ", "2004"),
        ("", None),
        ("199", None),
        ("19990704", None),
        ("July 1999", None),
        ("abcd-07-04", None),
        (None, None),
        (1999, None),
    ],
)
def test_release_year_needs_four_digits_then_end_or_hyphen(
    raw_date: object, expected_year: str | None
) -> None:
    """Only a date that starts with a four-digit year yields a year."""
    assert extract_release_year(raw_date) == expected_year


def _import_one_track(repo: Repository, tag_adapter: MetadataTagAdapter, folder: Path) -> Any:
    """Import one WAV fixture and return its stored asset."""
    source = create_wav_fixture(
        folder / "crate" / "track.wav", duration_sec=1.0, tags={"artist": "Old", "title": "T"}
    )
    return ImportService(repo, tag_adapter).import_paths([source]).imported_files[0]


def test_write_takes_journal_times_from_the_injected_clocks(tmp_path: Path) -> None:
    """The stored journal time comes from the service clock. The result uses the adapter clock."""
    repo = Repository(DatabaseManager(":memory:"))
    tag_adapter = MetadataTagAdapter(now_iso=lambda: ADAPTER_CLOCK_TIME)
    asset = _import_one_track(repo, tag_adapter, tmp_path)
    ReviewService(repo).set_manual_field_value(asset.id, FieldName.ARTIST.value, "Sasha")
    write_service = WriteService(
        repo, tag_adapter, backup_dir=tmp_path / "backups", now_iso=lambda: SERVICE_CLOCK_TIME
    )

    record = write_service.apply_single_plan(write_service.build_batch_preview([asset.id])[0])

    stored = repo.get_change_record(record.operation_id)
    assert stored is not None
    assert stored.timestamp == SERVICE_CLOCK_TIME
    assert record.timestamp == ADAPTER_CLOCK_TIME


def test_transcoder_journal_takes_its_time_from_the_injected_clock(tmp_path: Path) -> None:
    """The in-place transcode journal record carries the injected clock value."""
    repo = Repository(DatabaseManager(":memory:"))
    tag_adapter = MetadataTagAdapter()
    asset = _import_one_track(repo, tag_adapter, tmp_path)
    transcoder = SafeTranscoderAdapter(
        repo, tag_adapter, backup_dir=tmp_path / "backups", now_iso=lambda: TRANSCODER_CLOCK_TIME
    )
    source = Path(asset.current_path)

    record = transcoder._backup_and_journal_in_place(asset, source, source)

    stored = repo.get_change_record(record.operation_id)
    assert stored is not None
    assert stored.timestamp == TRANSCODER_CLOCK_TIME


def test_journal_dialog_shows_local_time_not_stored_text(qapp: Any, tmp_path: Path) -> None:
    """The journal table cell holds the local form of the stored instant."""
    repo = Repository(DatabaseManager(":memory:"))
    tag_adapter = MetadataTagAdapter()
    asset = _import_one_track(repo, tag_adapter, tmp_path)
    ReviewService(repo).set_manual_field_value(asset.id, FieldName.ARTIST.value, "Sasha")
    write_service = WriteService(
        repo, tag_adapter, backup_dir=tmp_path / "backups", now_iso=lambda: STORED_TIME
    )
    write_service.apply_single_plan(write_service.build_batch_preview([asset.id])[0])
    dialog = JournalDialog(repo, write_service)

    dialog.refresh_journal()

    shown = dialog.table.item(0, TIMESTAMP_COLUMN).text()
    dialog.close()
    assert shown == format_local_timestamp(STORED_TIME)
    assert shown != STORED_TIME
