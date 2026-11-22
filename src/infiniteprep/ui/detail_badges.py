"""Badge values for the detail panel header and the changed-field row order."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from infiniteprep.domain.enums import (
    FieldName,
    KeyDisplayNotation,
    format_canonical_key,
    parse_key_string,
)
from infiniteprep.domain.metadata_values import resolve_effective_value, top_proposal_values
from infiniteprep.domain.models import ApprovedField, FieldProposal, FileAsset
from infiniteprep.ui.library_columns import format_status_cell, key_cell_style
from infiniteprep.ui.tokens import KeyCellColors


@dataclass(frozen=True)
class TrackBadges:
    """The header badges of one track: status, format, BPM, and key.

    An empty text means the badge has no value and stays hidden.
    """

    status: str
    status_text: str
    format_text: str
    bpm_text: str
    key_text: str
    key_colors: KeyCellColors | None


def build_track_badges(
    asset: FileAsset,
    approved: Mapping[str, ApprovedField],
    proposals: Iterable[FieldProposal],
    notation: KeyDisplayNotation,
) -> TrackBadges:
    """Return the header badges for one track.

    BPM and key show the value the library row shows: the approved value, then the
    top proposal, then the file's own tag.
    """
    tags = asset.original_tags.get("standard_tags", {})
    top = top_proposal_values(proposals)
    bpm = resolve_effective_value(tags, approved, top, FieldName.BPM.value)
    raw_key = resolve_effective_value(tags, approved, top, FieldName.KEY.value)
    parsed_key = parse_key_string(raw_key)
    key_text = raw_key
    if parsed_key is not None:
        key_text = format_canonical_key(parsed_key[0], parsed_key[1], notation)
    status = str(asset.review_status)
    return TrackBadges(
        status=status,
        status_text=format_status_cell(status),
        format_text=str(asset.format).upper(),
        bpm_text=bpm,
        key_text=key_text,
        key_colors=key_cell_style(parsed_key),
    )


def is_field_changed(
    existing: str, approved: ApprovedField | None, proposals: Iterable[FieldProposal]
) -> bool:
    """Return True when a field has a proposal or an approved value unlike its tag."""
    if approved is not None and approved.approved_value != existing:
        return True
    return bool(top_proposal_values(proposals))


def order_changed_first(changed: Mapping[FieldName, bool]) -> list[FieldName]:
    """Return the fields with changes first, each group in its original order."""
    fields = list(changed)
    return [field for field in fields if changed[field]] + [
        field for field in fields if not changed[field]
    ]
