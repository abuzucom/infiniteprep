"""Shared row values for library searches and live playlist membership."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Final

from infiniteprep.domain.enums import (
    FieldName,
    KeyDisplayNotation,
    ReviewStatus,
    format_canonical_key,
    parse_key_string,
)
from infiniteprep.domain.library_query import LibraryQuery, canonical_key
from infiniteprep.domain.metadata_values import resolve_effective_value, top_proposal_values
from infiniteprep.domain.models import AnalysisResult, ApprovedField, FileAsset
from infiniteprep.ui.table_sort import LibraryRow, parse_bpm_value
from infiniteprep.ui.time_format import split_time_seconds
from infiniteprep.ui.write_preview_model import format_count

if TYPE_CHECKING:
    from infiniteprep.db.repositories import Repository

MAX_YEAR_DIGITS: Final = 4

# The plain label of each review status. Each label is unique, so the status filter
# maps a label back to one stored value.
STATUS_LABELS: Final[dict[ReviewStatus, str]] = {
    ReviewStatus.UNPROCESSED: "Not analyzed",
    ReviewStatus.PROCESSING: "Analyzing",
    ReviewStatus.PROPOSED: "Has suggestions",
    ReviewStatus.READY_FOR_REVIEW: "Needs review",
    ReviewStatus.CONFLICTING_CANDIDATES: "Conflicting matches",
    ReviewStatus.PARTIALLY_IDENTIFIED: "Partly matched",
    ReviewStatus.PARTIALLY_APPROVED: "Partly approved",
    ReviewStatus.NO_MATCH: "No match",
    ReviewStatus.APPROVED: "Approved",
    ReviewStatus.READY_TO_WRITE: "Ready to save",
    ReviewStatus.APPLIED: "Applied",
    ReviewStatus.WRITTEN: "Saved",
    ReviewStatus.ERROR: "Failed",
}


def format_status_label(status: ReviewStatus) -> str:
    """Return the plain label of one review status, such as "Needs review"."""
    return STATUS_LABELS[status]


def format_status_cell(value: str) -> str:
    """Return the label a stored status shows, such as "Ready to save".

    An unknown stored value shows as stored, so no row loses its status.
    """
    try:
        status = ReviewStatus(value)
    except ValueError:
        return value
    return format_status_label(status)


WARNING_LINE_SEPARATOR: Final[str] = "\n"
# The warning that marks a file whose analysis no longer states the current values.
STALE_ANALYSIS_WARNING: Final[str] = (
    "The analysis of this track is out of date. Analyze the track again."
)


def format_warning_summary(warnings: list[str]) -> str:
    """Return the Issues cell text: a count such as "2 issues", or nothing for none."""
    if not warnings:
        return ""
    return format_count(len(warnings), "issue", "issues")


@dataclass(frozen=True)
class LibrarySnapshot:
    """The per-file values one table refresh reads, loaded in three queries.

    A refresh read approved fields, proposals, and analysis results per file, twice for
    the analysis. A 5,000-file library ran about 20,000 queries on the GUI thread.
    """

    approved: dict[str, dict[str, ApprovedField]]
    top_proposals: dict[str, dict[str, str]]
    analysis_warnings: dict[str, list[str]]


def load_library_snapshot(repo: Repository) -> LibrarySnapshot:
    """Read the approved fields, top proposals, and analysis warnings of every file."""
    proposals = repo.field_proposals_by_file()
    analyses = repo.analysis_results_by_file()
    return LibrarySnapshot(
        approved=repo.approved_fields_by_file(),
        top_proposals={
            file_id: top_proposal_values(file_proposals)
            for file_id, file_proposals in proposals.items()
        },
        analysis_warnings={
            file_id: analysis_warning_lines(results) for file_id, results in analyses.items()
        },
    )


def analysis_warning_lines(results: list[AnalysisResult]) -> list[str]:
    """Return the warning lines of the stored analysis results of one file.

    A result that an older analyzer version wrote no longer states the current values.
    The file reads as needing a new analysis only while no current result exists.
    """
    lines = [warning for result in results if not result.stale for warning in result.warnings]
    if results and all(result.stale for result in results):
        lines.append(STALE_ANALYSIS_WARNING)
    return lines


def format_duration(seconds: float) -> str:
    """Format a track duration as M:SS or H:MM:SS, truncating fractions."""
    whole_seconds = int(max(0.0, float(seconds)))
    hours, minutes, seconds_part = split_time_seconds(whole_seconds)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds_part:02d}"
    return f"{minutes}:{seconds_part:02d}"


class LibraryRows:
    def __init__(self, notation: KeyDisplayNotation = KeyDisplayNotation.CAMELOT) -> None:
        self.current_key_notation = notation

    @staticmethod
    def _asset_warnings(asset: FileAsset, snapshot: LibrarySnapshot) -> list[str]:
        """Collect technical and analysis warnings for one library asset."""
        warnings: list[str] = list(asset.original_tags.get("technical", {}).get("warnings", []))
        warnings.extend(snapshot.analysis_warnings.get(asset.id, []))
        return warnings

    def _build_library_row(self, asset: FileAsset, snapshot: LibrarySnapshot) -> LibraryRow:
        """Build the table row for one asset with the values the sort needs."""
        standard_tags = asset.original_tags.get("standard_tags", {})
        approved = snapshot.approved.get(asset.id, {})
        top_by_field = snapshot.top_proposals.get(asset.id, {})
        raw_key = self._effective_val(standard_tags, approved, top_by_field, FieldName.KEY.value)
        raw_bpm = self._effective_val(standard_tags, approved, top_by_field, FieldName.BPM.value)
        warnings = self._asset_warnings(asset, snapshot)
        cells = self._build_table_row_cells(asset, standard_tags, approved, top_by_field, warnings)
        return LibraryRow(
            file_id=asset.id,
            cells=tuple(cells),
            path=asset.current_path,
            duration_seconds=asset.duration,
            bpm=parse_bpm_value(raw_bpm),
            key=parse_key_string(raw_key),
            review_status=str(asset.review_status),
            warning_details=WARNING_LINE_SEPARATOR.join(warnings),
        )

    def _build_table_row_cells(
        self,
        asset: FileAsset,
        standard_tags: dict[str, str],
        approved: dict,
        top_by_field: dict[str, str],
        warnings: list[str],
    ) -> list[str]:
        """Compute string cells for a single FileAsset row in the batch review table."""
        label_text = self._effective_val(
            standard_tags, approved, top_by_field, FieldName.LABEL.value
        )
        catalog_number_text = self._effective_val(
            standard_tags, approved, top_by_field, FieldName.CATALOG_NUMBER.value
        )
        raw_key = self._effective_val(standard_tags, approved, top_by_field, FieldName.KEY.value)

        return [
            Path(asset.current_path).name,
            str(asset.format).upper(),
            format_duration(asset.duration) if asset.duration is not None else "-",
            self._summary_pair(standard_tags, approved, top_by_field, FieldName.ARTIST.value),
            self._summary_pair(standard_tags, approved, top_by_field, FieldName.TITLE.value),
            self._effective_val(standard_tags, approved, top_by_field, FieldName.MIX_NAME.value),
            f"{label_text} [{catalog_number_text}]" if catalog_number_text else label_text,
            self._effective_val(standard_tags, approved, top_by_field, FieldName.BPM.value),
            self._format_key_cell(raw_key),
            format_status_cell(str(asset.review_status)),
            format_warning_summary(warnings),
        ]

    def _summary_pair(
        self,
        standard_tags: dict[str, str],
        approved: dict,
        top_by_field: dict[str, str],
        field_name: str,
    ) -> str:
        """Format 'Current -> Approved/Proposed' summary string for table cells."""
        current_value = standard_tags.get(field_name, "-")
        target = (
            approved[field_name].approved_value
            if field_name in approved
            else top_by_field.get(field_name, "")
        )
        if target and target != current_value:
            return f"{current_value} -> {target}"
        return target or current_value

    def _effective_val(
        self,
        standard_tags: dict[str, str],
        approved: dict,
        top_by_field: dict[str, str],
        field_name: str,
    ) -> str:
        """Return approved value, top proposal, or existing standard tag."""
        return resolve_effective_value(standard_tags, approved, top_by_field, field_name)

    def _format_key_cell(self, raw_key: str) -> str:
        """Format musical key according to active KeyDisplayNotation."""
        parsed = parse_key_string(raw_key)
        if parsed is None:
            return raw_key
        return format_canonical_key(parsed[0], parsed[1], self.current_key_notation)

    def search_record(self, asset: FileAsset, row: LibraryRow, snapshot: LibrarySnapshot) -> dict:
        tags = asset.original_tags.get("standard_tags", {})
        approved = snapshot.approved.get(asset.id, {})
        proposals = snapshot.top_proposals.get(asset.id, {})
        fields = ("artist", "title", "mix_name", "label", "catalog_number", "genre", "year", "key")
        record = {
            field: resolve_effective_value(tags, approved, proposals, field) for field in fields
        }
        record["mix"] = record.pop("mix_name")
        record["key"] = canonical_key(record["key"])
        year = str(record["year"])
        valid_year = year.isascii() and year.isdigit() and len(year) <= MAX_YEAR_DIGITS
        record["year"] = int(year) if valid_year else None
        record.update(
            id=asset.id,
            text=" ".join(row.cells),
            format=str(asset.format),
            status=str(asset.review_status),
            status_label=format_status_cell(str(asset.review_status)),
            warnings=row.warning_details,
            duration=row.duration_seconds,
            bpm=row.bpm,
        )
        return record


def search_playlist(
    repo: Repository, query: LibraryQuery, notation: KeyDisplayNotation
) -> list[str]:
    """Evaluate a saved query against current library metadata."""
    snapshot = load_library_snapshot(repo)
    builder = LibraryRows(notation)
    records = [
        builder.search_record(asset, builder._build_library_row(asset, snapshot), snapshot)
        for asset in repo.list_files()
    ]
    return query.select_ids(repo.db.connect(), records)
