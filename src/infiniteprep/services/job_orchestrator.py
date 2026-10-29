"""Durable SQLite-backed job orchestrator for fingerprinting, catalog lookup, and analysis."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from infiniteprep.adapters.analyzers.mock_analyzer import MockAudioAnalyzerAdapter
from infiniteprep.adapters.catalog.mock_catalog import MockCatalogProviderAdapter
from infiniteprep.adapters.fingerprint.mock_fp import MockFingerprintAdapter
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import EntityType, ReviewStatus, TaskStatus
from infiniteprep.domain.models import CandidateMatch, FileAsset, ReleaseDetails
from infiniteprep.domain.ports import (
    AudioAnalyzer,
    FingerprintProvider,
    MetadataProvider,
    MetadataSearchQuery,
)


@dataclass
class BatchJobSummary:
    """Summary of a batch identification and analysis run."""

    completed_files: int = 0
    failed_files: int = 0
    errors: dict[str, str] = field(default_factory=dict)


class JobOrchestrator:
    """Coordinates identification, catalog enrichment, and local audio analysis jobs."""

    def __init__(
        self,
        repository: Repository,
        fingerprint_provider: FingerprintProvider | None = None,
        catalog_providers: list[MetadataProvider] | None = None,
        audio_analyzer: AudioAnalyzer | None = None,
    ) -> None:
        """Initialize JobOrchestrator with Repository and configured providers."""
        self.repo = repository
        self.fp_provider: FingerprintProvider = fingerprint_provider or MockFingerprintAdapter()
        self.catalog_providers: list[MetadataProvider] = catalog_providers or [
            MockCatalogProviderAdapter()
        ]
        self.audio_analyzer: AudioAnalyzer = audio_analyzer or MockAudioAnalyzerAdapter()

    def process_files(self, file_ids: list[str]) -> BatchJobSummary:
        """Execute identification, catalog search, and audio analysis for each file_id."""
        summary = BatchJobSummary()
        for file_id in file_ids:
            asset = self.repo.get_file_by_id(file_id)
            if asset is None:
                continue
            try:
                self._process_single_asset(asset)
                summary.completed_files += 1
            except Exception as exc:
                msg = f"Processing failed ({exc}); retry or check provider settings."
                self.repo.update_file_status(
                    file_id,
                    task_status=TaskStatus.FAILED,
                    review_status=ReviewStatus.ERROR,
                    error_message=msg,
                )
                summary.failed_files += 1
                summary.errors[file_id] = msg
        return summary

    def _process_single_asset(self, asset: FileAsset) -> None:
        """Run fingerprint, catalog lookup, and audio analysis steps for one FileAsset."""
        self.repo.update_file_status(asset.id, task_status=TaskStatus.PROCESSING)
        audio_path = Path(asset.current_path)

        # 1. Fingerprint job
        self.repo.enqueue_job(str(uuid.uuid4()), asset.id, "fingerprint")
        self._run_fingerprint_step(asset, audio_path)
        self.repo.update_job_state(asset.id, "fingerprint", "completed")

        # 2. Catalog lookup job
        self.repo.enqueue_job(str(uuid.uuid4()), asset.id, "catalog_lookup")
        self._run_catalog_step(asset)
        self.repo.update_job_state(asset.id, "catalog_lookup", "completed")

        # 3. Local audio analysis job
        self.repo.enqueue_job(str(uuid.uuid4()), asset.id, "audio_analysis")
        ana_result = self.audio_analyzer.analyze(audio_path, asset.id)
        ana_result.file_id = asset.id
        self.repo.upsert_analysis_result(ana_result)
        self.repo.update_job_state(asset.id, "audio_analysis", "completed")

        self.repo.update_file_status(
            asset.id,
            task_status=TaskStatus.COMPLETED,
            review_status=ReviewStatus.PROPOSED,
        )

    def _run_fingerprint_step(self, asset: FileAsset, audio_path: Path) -> None:
        """Run fingerprint provider if available and persist recording CandidateMatches."""
        ready, _ = self.fp_provider.is_available()
        if not ready:
            return
        fp_res = self.fp_provider.identify(audio_path, file_id=asset.id)
        for idx, item in enumerate(fp_res.matches):
            rec_id = item.recording_ids[0] if item.recording_ids else f"fp-{idx}"
            cand = CandidateMatch(
                id=f"cand-{asset.id}-{rec_id}",
                file_id=asset.id,
                provider=fp_res.provider,
                provider_entity_id=rec_id,
                entity_type=EntityType.RECORDING,
                artist=item.artist,
                title=item.title,
                mix_name=item.mix_name,
                duration=item.duration,
                release_details=ReleaseDetails(),
                match_evidence=item.raw_evidence,
                match_score=item.confidence,
            )
            self.repo.upsert_candidate_match(cand)

    def _run_catalog_step(self, asset: FileAsset) -> None:
        """Query all available catalog providers using existing tags and persist matches."""
        std = asset.original_tags.get("standard_tags", {})
        query = MetadataSearchQuery(
            artist=std.get("artist"),
            title=std.get("title"),
            mix_name=std.get("mix_name"),
            release_title=std.get("release_title"),
            label=std.get("label"),
            catalog_number=std.get("catalog_number"),
            duration=asset.duration,
        )
        for provider in self.catalog_providers:
            ready, _ = provider.is_available()
            if not ready:
                continue
            for cand in provider.search(query, file_id=asset.id):
                cand.file_id = asset.id
                self.repo.upsert_candidate_match(cand)
