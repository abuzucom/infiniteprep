"""Adapter contracts (ports) separating external integrations from core logic."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from infiniteprep.domain.enums import AudioFormat, EntityType
from infiniteprep.domain.models import (
    AnalysisResult,
    ApprovedField,
    CandidateMatch,
    ChangeRecord,
    FileAsset,
)


@dataclass
class TechnicalAndTagMetadata:
    """Normalized result from reading an audio file's technical properties and existing tags."""

    format: AudioFormat
    duration: float | None
    sample_rate: int | None
    channels: int | None
    bitrate: int | None
    standard_tags: dict[str, str] = field(default_factory=dict)
    raw_custom_tags: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


@dataclass
class FingerprintMatchItem:
    """A single acoustic fingerprint match from a provider."""

    fingerprint_id: str
    confidence: float
    recording_ids: list[str] = field(default_factory=list)
    artist: str | None = None
    title: str | None = None
    mix_name: str | None = None
    duration: float | None = None
    release_group_ids: list[str] = field(default_factory=list)
    raw_evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class FingerprintResult:
    """Result returned by FingerprintProvider.identify()."""

    provider: str
    raw_fingerprint: str | None
    duration: float | None
    matches: list[FingerprintMatchItem] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class MetadataSearchQuery:
    """Query criteria for MetadataProvider.search()."""

    artist: str | None = None
    title: str | None = None
    mix_name: str | None = None
    remixer: str | None = None
    release_title: str | None = None
    label: str | None = None
    catalog_number: str | None = None
    duration: float | None = None
    recording_id: str | None = None
    release_id: str | None = None


@dataclass
class FileWritePlan:
    """Preview and execution plan for modifying a single FileAsset."""

    file_id: str
    source_path: Path
    destination_path: Path
    rename_enabled: bool
    expected_content_hash: str
    existing_tags: dict[str, Any]
    proposed_tags_to_write: dict[str, str]
    preserved_custom_tags: dict[str, Any]
    backup_destination_path: Path
    collision_warnings: list[str] = field(default_factory=list)
    validation_errors: list[str] = field(default_factory=list)

    @property
    def is_safe_to_apply(self) -> bool:
        return len(self.collision_warnings) == 0 and len(self.validation_errors) == 0


class FingerprintProvider(Protocol):
    """Contract for audio fingerprinting providers (e.g., Chromaprint/AcoustID, ACRCloud)."""

    provider_name: str
    is_cloud_opt_in: bool

    def is_available(self) -> tuple[bool, str | None]:
        """Return (True, None) if ready, or (False, reason) if unavailable/unconfigured."""
        ...

    def identify(self, audio_reference: Path, file_id: str | None = None) -> FingerprintResult:
        """Compute fingerprint and query recognition backend."""
        ...


class MetadataProvider(Protocol):
    """Contract for catalog metadata providers (e.g., MusicBrainz, Discogs, Beatport)."""

    provider_name: str

    def is_available(self) -> tuple[bool, str | None]:
        """Return (True, None) if ready, or (False, reason) if unavailable/unconfigured."""
        ...

    def search(self, query: MetadataSearchQuery, file_id: str = "") -> list[CandidateMatch]:
        """Search recording/release candidates matching the query."""
        ...

    def fetch(
        self, entity_id: str, entity_type: EntityType = EntityType.RELEASE, file_id: str = ""
    ) -> list[CandidateMatch]:
        """Fetch specific recording or release entity by provider ID."""
        ...


class AudioAnalyzer(Protocol):
    """Contract for local BPM and musical key analyzers."""

    analyzer_name: str
    analyzer_version: str

    def is_available(self) -> tuple[bool, str | None]: ...

    def analyze(self, audio_reference: Path, file_id: str = "") -> AnalysisResult:
        """Analyze local audio for BPM and canonical musical key."""
        ...


class TagWriter(Protocol):
    """Contract for metadata inspection, write preview, and safe tag application."""

    supported_write_formats: frozenset[AudioFormat]

    def read_metadata(self, file_path: Path) -> TechnicalAndTagMetadata:
        """Read technical audio properties and all existing tags without modifying the file."""
        ...

    def preview(
        self,
        file_asset: FileAsset,
        approved_fields: dict[str, ApprovedField],
        backup_dir: Path,
        rename_template: str | None = None,
        enable_rename: bool = False,
    ) -> FileWritePlan:
        """Build a preflighted FileWritePlan without modifying any file on disk."""
        ...

    def apply(self, write_plan: FileWritePlan, operation_id: str) -> ChangeRecord:
        """Apply approved tags (and optional rename) to the file on disk."""
        ...
