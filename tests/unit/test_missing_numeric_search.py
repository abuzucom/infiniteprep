"""Verify numeric search over absent, null, zero, and populated metadata."""

import pytest

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import AudioFormat
from infiniteprep.domain.library_query import parse_library_query
from infiniteprep.domain.models import FileAsset

UPPER_BOUND = 10
FIELDS = ("bpm", "year", "duration")


def build_records() -> list[dict]:
    """Keep absent JSON properties distinct from explicit null properties."""
    records = [{"id": "absent"}]
    values = {"null": None, "zero": 0, "upper": UPPER_BOUND, "outside": UPPER_BOUND + 1}
    for name, value in values.items():
        records.append({"id": name, **dict.fromkeys(FIELDS, value)})
    return records


def store_track(repo: Repository, record: dict) -> None:
    name = record["id"]
    path = f"/music/{name}.mp3"
    tags = {field: str(record[field]) for field in ("bpm", "year") if record.get(field) is not None}
    repo.upsert_file_asset(
        FileAsset(
            id=name,
            current_path=path,
            original_path=path,
            file_size=1,
            content_hash=name,
            format=AudioFormat.MP3,
            duration=record.get("duration"),
            original_tags={"standard_tags": tags},
            import_batch_id="batch",
            source_group_id=None,
        )
    )


@pytest.mark.parametrize("field", FIELDS)
@pytest.mark.parametrize(
    ("comparison", "expected"),
    [("=0", {"zero"}), ("<=0", {"zero"}), (f":0-{UPPER_BOUND}", {"zero", "upper"})],
)
def test_numeric_search_excludes_missing(field: str, comparison: str, expected: set[str]) -> None:
    source = field + comparison
    records = build_records()
    with DatabaseManager(":memory:") as db:
        query = parse_library_query(source)
        assert set(query.select_ids(db.connect(), records)) == expected

        repo = Repository(db)
        for record in records:
            store_track(repo, record)
        saved = repo.collections.save_search(source, source)
        assert set(repo.collections.playlist_tracks(saved.id)) == expected
