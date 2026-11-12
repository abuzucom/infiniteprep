"""Durable SQLite-backed job orchestrator for fingerprinting, catalog lookup, and analysis."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
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

logger = logging.getLogger(__name__)

EXPECTED_JOB_ERRORS: tuple[type[Exception], ...] = (
    OSError,
    ValueError,
    RuntimeError,
    TimeoutError,
    LookupError,
)


CancelCheck = Callable[[], bool]
ProgressCallback = Callable[[int, int, str], None]


@dataclass
class BatchJobSummary:
    """Summary of a batch identification and analysis run."""

    completed_files: int = 0
    failed_files: int = 0
    errors: dict[str, str] = field(default_factory=dict)
    # True when a cancel request or the stop-on-failure rule ended the batch early.
    stopped_early: bool = False


class BatchKind(StrEnum):
    """The library-changing operations that share the batch queue."""

    ANALYZE = "analyze"
    IMPORT = "import"
    WRITE = "write"


class BatchState(StrEnum):
    """Where a batch stands in the queue."""

    WAITING = "waiting"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


FINISHED_STATES: frozenset[BatchState] = frozenset(
    {BatchState.COMPLETED, BatchState.FAILED, BatchState.CANCELLED}
)


@dataclass
class QueuedBatch:
    """One batch in the queue. payload holds whatever the runner needs to start it."""

    id: str
    kind: BatchKind
    label: str
    payload: object = None
    state: BatchState = BatchState.WAITING
    progress_text: str = ""
    result: object = None
    cancel_requested: bool = False


class BatchQueue:
    """In-memory FIFO of library-changing batches. At most one batch runs at a time.

    The queue holds state only. A caller starts the batch that start_next returns and
    reports its end through finish_running. Waiting batches vanish at exit.
    """

    def __init__(self) -> None:
        """Start with no running, waiting, or finished batch."""
        self._waiting: list[QueuedBatch] = []
        self._running: QueuedBatch | None = None
        self._finished: list[QueuedBatch] = []

    @property
    def running(self) -> QueuedBatch | None:
        """Return the running batch, or None while the queue is idle."""
        return self._running

    def waiting(self) -> list[QueuedBatch]:
        """Return the waiting batches in start order."""
        return list(self._waiting)

    def finished(self) -> list[QueuedBatch]:
        """Return the finished batches, oldest first."""
        return list(self._finished)

    def batches(self) -> list[QueuedBatch]:
        """Return every batch for display: running, then waiting, then finished."""
        head = [self._running] if self._running is not None else []
        return head + self._waiting + self._finished

    def is_busy(self) -> bool:
        """Return True while a batch runs or waits."""
        return self._running is not None or bool(self._waiting)

    def enqueue(self, kind: BatchKind, label: str, payload: object = None) -> QueuedBatch:
        """Add a batch at the end of the waiting line and return it."""
        batch = QueuedBatch(id=str(uuid.uuid4()), kind=kind, label=label, payload=payload)
        self._waiting.append(batch)
        return batch

    def start_next(self) -> QueuedBatch | None:
        """Mark the first waiting batch as running. Return None when one runs or none waits."""
        if self._running is not None or not self._waiting:
            return None
        batch = self._waiting.pop(0)
        batch.state = BatchState.RUNNING
        self._running = batch
        return batch

    def finish_running(self, state: BatchState, result: object = None) -> QueuedBatch:
        """Move the running batch to the finished list with its end state and result."""
        batch = self._running
        if batch is None:
            raise RuntimeError("No batch is running; call start_next before finish_running.")
        if state not in FINISHED_STATES:
            raise ValueError(f"{state} is not an end state; pass completed, failed, or cancelled.")
        batch.state = state
        batch.result = result
        self._running = None
        self._finished.append(batch)
        return batch

    def _waiting_index(self, batch_id: str) -> int:
        """Return the waiting position of batch_id, or -1 when it does not wait."""
        for index, batch in enumerate(self._waiting):
            if batch.id == batch_id:
                return index
        return -1

    def move(self, batch_id: str, offset: int) -> bool:
        """Shift a waiting batch by offset places. Return False when it cannot move there."""
        index = self._waiting_index(batch_id)
        target = index + offset
        if index < 0 or not 0 <= target < len(self._waiting):
            return False
        self._waiting.insert(target, self._waiting.pop(index))
        return True

    def reorder_waiting(self, batch_ids: list[str]) -> bool:
        """Put the waiting batches in the given order. Return False on any mismatch."""
        by_id = {batch.id: batch for batch in self._waiting}
        if len(batch_ids) != len(by_id) or set(batch_ids) != set(by_id):
            return False
        self._waiting = [by_id[batch_id] for batch_id in batch_ids]
        return True

    def cancel_waiting(self, batch_id: str) -> QueuedBatch | None:
        """Remove a waiting batch and record it as cancelled. Return None when absent."""
        index = self._waiting_index(batch_id)
        if index < 0:
            return None
        batch = self._waiting.pop(index)
        batch.state = BatchState.CANCELLED
        self._finished.append(batch)
        return batch

    def cancel_all_waiting(self) -> list[QueuedBatch]:
        """Cancel every waiting batch and return them."""
        return [
            batch
            for batch in (self.cancel_waiting(waiting.id) for waiting in self.waiting())
            if batch is not None
        ]

    def clear_finished(self) -> None:
        """Forget every finished batch."""
        self._finished.clear()


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

    def process_files(
        self, file_ids: list[str], *, raise_on_unexpected: bool = False
    ) -> BatchJobSummary:
        """Execute identification, catalog search, and audio analysis for each file_id."""
        summary = BatchJobSummary()
        for file_id in file_ids:
            asset = self.repo.get_file_by_id(file_id)
            if asset is None:
                continue
            try:
                self._process_single_asset(asset)
                summary.completed_files += 1
            except EXPECTED_JOB_ERRORS as exc:
                logger.error("Job processing failed for file %s: %r", file_id, exc, exc_info=True)
                msg = (
                    f"Processing failed ({exc.__class__.__name__}: {exc}); "
                    "retry or check provider settings."
                )
                self.repo.update_file_status(
                    file_id,
                    task_status=TaskStatus.FAILED,
                    review_status=ReviewStatus.ERROR,
                    error_message=msg,
                )
                summary.failed_files += 1
                summary.errors[file_id] = msg
                if raise_on_unexpected:
                    raise RuntimeError(msg) from exc
        return summary

    def process_files_continuing(
        self,
        file_ids: list[str],
        *,
        post_process: Callable[[str], object] | None = None,
        should_cancel: CancelCheck | None = None,
        stop_on_failure: bool = False,
        progress: ProgressCallback | None = None,
    ) -> BatchJobSummary:
        """Process each file for a queued batch, treating any error as one failed file.

        post_process runs after each processed file, inside the same failure boundary.
        A true should_cancel stops before the next file and keeps the finished files.
        stop_on_failure ends the batch after the first failed file. progress receives
        (file number, total, path) before each file.
        """
        summary = BatchJobSummary()
        total = len(file_ids)
        for index, file_id in enumerate(file_ids):
            if should_cancel is not None and should_cancel():
                summary.stopped_early = True
                return summary
            asset = self.repo.get_file_by_id(file_id)
            if asset is None:
                continue
            if progress is not None:
                progress(index + 1, total, asset.current_path)
            succeeded = self._process_asset_recording_failure(asset, post_process, summary)
            if not succeeded and stop_on_failure:
                summary.stopped_early = True
                return summary
        return summary

    def _process_asset_recording_failure(
        self,
        asset: FileAsset,
        post_process: Callable[[str], object] | None,
        summary: BatchJobSummary,
    ) -> bool:
        """Process one asset. Record any error on the file and in summary. Return success."""
        try:
            self._process_single_asset(asset)
            if post_process is not None:
                post_process(asset.id)
        # A provider or analyzer reading an untrusted file can raise any error type. One
        # bad file must not end a queued batch. The traceback goes to the log.
        except Exception as exc:
            logger.error("Queued job failed for file %s: %r", asset.id, exc, exc_info=True)
            msg = (
                f"Processing failed ({exc.__class__.__name__}: {exc}); "
                "retry or check provider settings."
            )
            self.repo.update_file_status(
                asset.id,
                task_status=TaskStatus.FAILED,
                review_status=ReviewStatus.ERROR,
                error_message=msg,
            )
            summary.failed_files += 1
            summary.errors[asset.id] = msg
            return False
        summary.completed_files += 1
        return True

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
