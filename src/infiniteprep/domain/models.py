"""Domain models for InfinitePrep."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from infiniteprep.domain.enums import (
    WRITE_SUPPORTED_FORMATS,
    ApprovalOrigin,
    AudioFormat,
    EntityType,
    GroupingBasis,
    KeyMode,
    OperationStatus,
    PitchClass,
    ProposalReviewStatus,
    ReviewStatus,
    SourceType,
    TaskStatus,
)


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(UTC).isoformat()


@dataclass
class SourceGroup:
    """Represents files believed to share an original disc, release, or transfer session."""

    id: str
    user_label: str
    grouping_basis: GroupingBasis
    original_folder: str
    source_type: SourceType = SourceType.UNKNOWN
    notes: str = ""


@dataclass
class FileAsset:
    """Represents the actual audio file on disk."""

    id: str
    current_path: str
    original_path: str
    file_size: int
    content_hash: str
    format: AudioFormat
    duration: float | None
    original_tags: dict[str, Any]
    import_batch_id: str
    source_group_id: str | None
    suspected_multitrack: bool = False
    task_status: TaskStatus = TaskStatus.IDLE
    review_status: ReviewStatus = ReviewStatus.UNPROCESSED
    error_message: str | None = None
    imported_at: str = ""

    @property
    def is_write_supported(self) -> bool:
        return self.format in WRITE_SUPPORTED_FORMATS


@dataclass
class ReleaseDetails:
    """Structured release attributes attached to a CandidateMatch."""

    release_title: str | None = None
    label: str | None = None
    catalog_number: str | None = None
    track_number: str | None = None
    disc_number: str | None = None
    vinyl_position: str | None = None
    year: str | None = None
    country: str | None = None
    genre: str | None = None
    style: str | None = None
    format_description: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "release_title": self.release_title,
            "label": self.label,
            "catalog_number": self.catalog_number,
            "track_number": self.track_number,
            "disc_number": self.disc_number,
            "vinyl_position": self.vinyl_position,
            "year": self.year,
            "country": self.country,
            "genre": self.genre,
            "style": self.style,
            "format_description": self.format_description,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> ReleaseDetails:
        if not data:
            return cls()
        return cls(
            release_title=data.get("release_title"),
            label=data.get("label"),
            catalog_number=data.get("catalog_number"),
            track_number=data.get("track_number"),
            disc_number=data.get("disc_number"),
            vinyl_position=data.get("vinyl_position"),
            year=data.get("year"),
            country=data.get("country"),
            genre=data.get("genre"),
            style=data.get("style"),
            format_description=data.get("format_description"),
        )


@dataclass
class CandidateMatch:
    """Represents a recording or release candidate from a catalog or fingerprint provider."""

    id: str
    file_id: str
    provider: str
    provider_entity_id: str
    entity_type: EntityType
    artist: str | None
    title: str | None
    mix_name: str | None = None
    remixer: str | None = None
    duration: float | None = None
    release_details: ReleaseDetails = field(default_factory=ReleaseDetails)
    match_evidence: dict[str, Any] = field(default_factory=dict)
    match_score: float = 0.0
    retrieved_at: str = ""


@dataclass
class FieldProposal:
    """Stores an individual field proposal with separate provider confidence and match score."""

    id: str
    file_id: str
    field_name: str
    proposed_value: str
    provider: str
    provider_entity_id: str
    evidence: dict[str, Any] = field(default_factory=dict)
    provider_confidence: float | None = None
    application_match_score: float | None = None
    review_status: ProposalReviewStatus = ProposalReviewStatus.PENDING
    retrieved_at: str = ""


@dataclass
class AnalysisResult:
    """Represents a local BPM and key analysis result."""

    id: str
    file_id: str
    analyzer_name: str
    analyzer_version: str
    configuration: dict[str, Any]
    bpm: float | None
    key_tonic: PitchClass | None
    key_mode: KeyMode | None
    analysis_strength: float | None
    beat_positions_optional: list[float] | None = None
    warnings: list[str] = field(default_factory=list)
    analyzed_at: str = ""


@dataclass
class ApprovedField:
    """Represents a user-approved and optionally locked field value."""

    file_id: str
    field_name: str
    approved_value: str
    approval_origin: ApprovalOrigin
    approved_at: str
    locked: bool = False
    provider_attribution: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChangeRecord:
    """Journal record tracking a tag write and/or rename operation."""

    operation_id: str
    file_id: str
    previous_path: str
    new_path: str
    previous_tags: dict[str, Any]
    written_tags: dict[str, Any]
    backup_reference: str
    operation_status: OperationStatus
    timestamp: str
    batch_operation_id: str | None = None
    pre_write_hash: str | None = None
    post_write_hash: str | None = None
    error_message: str | None = None
