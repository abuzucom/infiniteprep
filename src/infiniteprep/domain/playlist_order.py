"""Order playlist occurrences by their effective library metadata."""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import TYPE_CHECKING

from infiniteprep.domain.enums import (
    FieldName,
    KeyDisplayNotation,
    format_canonical_key,
    parse_key_string,
)
from infiniteprep.domain.metadata_values import resolve_effective_value, top_proposal_values

if TYPE_CHECKING:
    from infiniteprep.db.repositories import Repository
    from infiniteprep.domain.models import ApprovedField, FileAsset


def order_tracks(repository: Repository, file_ids: list[str], *, bpm_first: bool) -> list[str]:
    """Keep ties and incomplete tracks in their prior relative order.

    The values of every track load in three queries. The order runs inside the playlist
    write transaction, so one query per track held the write lock for the whole playlist.
    """
    unique_ids = list(dict.fromkeys(file_ids))
    assets = repository.files_by_id(unique_ids)
    approved = repository.approved_fields_by_file(unique_ids)
    proposals = repository.field_proposals_by_file(unique_ids)
    values = {
        file_id: _track_values(
            assets.get(file_id),
            approved.get(file_id, {}),
            top_proposal_values(proposals.get(file_id, [])),
        )
        for file_id in unique_ids
    }
    complete = [file_id for file_id in file_ids if values[file_id] is not None]
    incomplete = [file_id for file_id in file_ids if values[file_id] is None]

    def sort_key(file_id: str) -> tuple[float, float]:
        value = values[file_id]
        assert value is not None
        camelot, bpm = value
        return (bpm, camelot) if bpm_first else (camelot, bpm)

    return sorted(complete, key=sort_key) + incomplete


def _track_values(
    asset: FileAsset | None,
    approved: Mapping[str, ApprovedField],
    proposals: Mapping[str, str],
) -> tuple[int, float] | None:
    """Return the Camelot position and BPM of one track, or None when either is missing."""
    if asset is None:
        return None
    tags = asset.original_tags.get("standard_tags", {})
    key = parse_key_string(resolve_effective_value(tags, approved, proposals, FieldName.KEY.value))
    bpm_text = resolve_effective_value(tags, approved, proposals, FieldName.BPM.value)
    try:
        bpm = float(bpm_text)
    except (TypeError, ValueError):
        return None
    if key is None or not math.isfinite(bpm) or bpm <= 0:
        return None
    camelot = format_canonical_key(*key, KeyDisplayNotation.CAMELOT)
    return int(camelot[:-1]) * 2 + int(camelot[-1] == "B"), bpm
