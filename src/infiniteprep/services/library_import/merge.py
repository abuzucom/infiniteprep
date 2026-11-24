"""The field-level merge of an imported library into the app (D333).

A value conflicts when the same field carries two different values, the
imported one against the stored one. A value with no conflict becomes an
approved field. A conflicting value becomes a proposal in pending review. The
merge writes nothing. The plan holds the result until one commit.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from infiniteprep.domain.enums import ApprovalOrigin, FieldName, ProposalReviewStatus
from infiniteprep.domain.models import ApprovedField, FieldProposal
from infiniteprep.services.library_import.models import (
    HOT_CUE_AUTO_LOAD_FIELD,
    STORED_FIELD_NAMES,
    ImportedPendingValue,
    ImportedTrack,
    ImportedValue,
    SourceLibrary,
)

# The provider name the merge records for a value read from a source library.
LIBRARY_PROVIDER: str = "dj_library"


@dataclass(frozen=True)
class FieldConflict:
    """One field where the imported value differs from the stored value."""

    field_name: str
    stored_value: str
    imported_value: str


@dataclass
class MergedTrack:
    """The merge outcome of one imported track."""

    track: ImportedTrack
    approved_fields: list[ApprovedField] = field(default_factory=list)
    proposals: list[FieldProposal] = field(default_factory=list)
    conflicts: list[FieldConflict] = field(default_factory=list)
    pending_fields: list[str] = field(default_factory=list)
    pending_values: list[ImportedPendingValue] = field(default_factory=list)


@dataclass(frozen=True)
class MergeContext:
    """The values the app already holds, beside the library being merged.

    stored_values maps a track source path to the field values the app holds
    for that track. file_ids maps the same key to the file identifier.
    """

    library: SourceLibrary
    stored_values: dict[str, dict[str, str]] = field(default_factory=dict)
    file_ids: dict[str, str] = field(default_factory=dict)


@dataclass
class MergeOutcome:
    """The merge outcome of one imported library."""

    library: SourceLibrary
    tracks: list[MergedTrack] = field(default_factory=list)
    observed_stored: dict[tuple[str, str], str | None] = field(default_factory=dict)

    @property
    def approved_fields(self) -> list[ApprovedField]:
        """Return every approved field the merge produced."""
        return [item for track in self.tracks for item in track.approved_fields]

    @property
    def proposals(self) -> list[FieldProposal]:
        """Return every proposal the merge produced."""
        return [item for track in self.tracks for item in track.proposals]

    @property
    def conflicts(self) -> list[FieldConflict]:
        """Return every conflict the merge found."""
        return [item for track in self.tracks for item in track.conflicts]

    @property
    def pending_values(self) -> list[ImportedPendingValue]:
        """Return every value that waits for its storage."""
        return [item for track in self.tracks for item in track.pending_values]


def merge_library(context: MergeContext) -> MergeOutcome:
    """Merge every track of the library against the values already stored."""
    outcome = MergeOutcome(library=context.library)
    for track in context.library.tracks:
        outcome.tracks.append(_merge_track(track, context))
    outcome.observed_stored = _observed_stored_values(context)
    return outcome


def _observed_stored_values(context: MergeContext) -> dict[tuple[str, str], str | None]:
    """Return the stored value the merge saw for every field it compared.

    The commit reads these values back, so a field that changed after the
    merge turns into a conflict instead of an overwrite.
    """
    observed: dict[tuple[str, str], str | None] = {}
    for track in context.library.tracks:
        key = track.source_path
        stored = context.stored_values.get(key, {})
        file_id = context.file_ids.get(key, "")
        if not file_id:
            continue
        for value in track.values:
            observed[(file_id, value.field_name)] = stored.get(value.field_name)
    return observed


def _merge_track(track: ImportedTrack, context: MergeContext) -> MergedTrack:
    """Merge one track against the values the app holds for it."""
    merged = MergedTrack(track=track)
    key = track.source_path
    stored = context.stored_values.get(key, {})
    file_id = context.file_ids.get(key, "")
    for value in track.values:
        _apply_value(merged, value, stored, file_id, context.library)
    for pending in track.pending:
        merged.pending_fields.append(pending.field_name)
        merged.pending_values.append(pending)
    return merged


def _apply_value(
    merged: MergedTrack,
    value: ImportedValue,
    stored: dict[str, str],
    file_id: str,
    library: SourceLibrary,
) -> None:
    """Store one imported value as an approved field or as a proposal.

    A field with no storage yet stays pending. The app writes no field name
    it cannot store. The pending value keeps the value and the track, so the
    caller can show it until the import is committed.
    """
    if value.field_name not in STORED_FIELD_NAMES or not file_id:
        merged.pending_fields.append(value.field_name)
        merged.pending_values.append(
            ImportedPendingValue(
                field_name=value.field_name,
                payload={"value": value.value, "source_path": merged.track.source_path},
            )
        )
        return
    stored_value = stored.get(value.field_name)
    if stored_value is None:
        merged.approved_fields.append(_approved_field(file_id, value.field_name, value.value))
        return
    if stored_value == value.value:
        return
    merged.conflicts.append(FieldConflict(value.field_name, stored_value, value.value))
    merged.proposals.append(_proposal(file_id, value.field_name, value.value, library))


def _approved_field(file_id: str, field_name: str, value: str) -> ApprovedField:
    """Return the approved field for one imported value."""
    return ApprovedField(
        file_id=file_id,
        field_name=field_name,
        approved_value=value,
        approval_origin=ApprovalOrigin.PROPOSAL_ACCEPTED,
        approved_at="",
        provider_attribution={"provider": LIBRARY_PROVIDER},
    )


def _proposal(file_id: str, field_name: str, value: str, library: SourceLibrary) -> FieldProposal:
    """Return the proposal for one conflicting imported value."""
    digest = f"{file_id}:{field_name}:{LIBRARY_PROVIDER}:{library.source_path}:{value}"
    return FieldProposal(
        id=str(uuid.uuid5(uuid.NAMESPACE_OID, digest)),
        file_id=file_id,
        field_name=field_name,
        proposed_value=value,
        provider=LIBRARY_PROVIDER,
        provider_entity_id=library.source_path,
        evidence={"source_format": library.source_format},
        review_status=ProposalReviewStatus.PENDING,
    )


def build_proposal(
    file_id: str, field_name: str, value: str, library: SourceLibrary
) -> FieldProposal:
    """Return the proposal that records one conflicting imported value."""
    return _proposal(file_id, field_name, value, library)


def hot_cue_auto_load_field() -> str:
    """Return the field name that holds the Hot Cue Auto Load setting."""
    return HOT_CUE_AUTO_LOAD_FIELD


def title_field() -> str:
    """Return the field name that holds the track title."""
    return FieldName.TITLE.value
