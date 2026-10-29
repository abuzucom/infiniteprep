"""SQLite repository layer for all InfinitePrep domain entities."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

from infiniteprep.db.connection import DatabaseManager
from infiniteprep.domain.enums import (
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
from infiniteprep.domain.models import (
    AnalysisResult,
    ApprovedField,
    CandidateMatch,
    ChangeRecord,
    FieldProposal,
    FileAsset,
    ReleaseDetails,
    SourceGroup,
)


def utc_now_iso() -> str:
    """Return current UTC timestamp in ISO-8601 format."""
    return datetime.now(UTC).isoformat()


class Repository:
    """Data access layer wrapping SQLite queries and domain object mapping."""

    def __init__(self, db: DatabaseManager) -> None:
        """Initialize Repository with a DatabaseManager."""
        self.db = db

    # ------------------------------------------------------------------
    # Source Groups
    # ------------------------------------------------------------------
    def upsert_source_group(self, group: SourceGroup) -> SourceGroup:
        """Insert or update a SourceGroup row."""
        conn = self.db.connect()
        conn.execute(
            """
            INSERT INTO source_groups (
                id, user_label, grouping_basis, original_folder, source_type, notes
            )
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                user_label = excluded.user_label,
                grouping_basis = excluded.grouping_basis,
                original_folder = excluded.original_folder,
                source_type = excluded.source_type,
                notes = excluded.notes
            """,
            (
                group.id,
                group.user_label,
                str(group.grouping_basis),
                group.original_folder,
                str(group.source_type),
                group.notes,
            ),
        )
        conn.commit()
        return group

    def get_source_group_by_folder(self, folder_path: str) -> SourceGroup | None:
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM source_groups WHERE original_folder = ? LIMIT 1",
            (folder_path,),
        ).fetchone()
        if not row:
            return None
        return self._row_to_source_group(row)

    def get_source_group(self, group_id: str) -> SourceGroup | None:
        conn = self.db.connect()
        row = conn.execute("SELECT * FROM source_groups WHERE id = ?", (group_id,)).fetchone()
        return self._row_to_source_group(row) if row else None

    def list_source_groups(self) -> list[SourceGroup]:
        conn = self.db.connect()
        rows = conn.execute("SELECT * FROM source_groups ORDER BY user_label").fetchall()
        return [self._row_to_source_group(r) for r in rows]

    # ------------------------------------------------------------------
    # File Assets
    # ------------------------------------------------------------------
    def upsert_file_asset(self, asset: FileAsset) -> FileAsset:
        if not asset.imported_at:
            asset.imported_at = utc_now_iso()
        conn = self.db.connect()
        conn.execute(
            """
            INSERT INTO file_assets (
                id, current_path, original_path, file_size, content_hash,
                format, duration, original_tags, import_batch_id, source_group_id,
                suspected_multitrack, task_status, review_status, error_message, imported_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                current_path = excluded.current_path,
                file_size = excluded.file_size,
                content_hash = excluded.content_hash,
                format = excluded.format,
                duration = excluded.duration,
                original_tags = excluded.original_tags,
                suspected_multitrack = excluded.suspected_multitrack,
                task_status = excluded.task_status,
                review_status = excluded.review_status,
                error_message = excluded.error_message
            """,
            (
                asset.id,
                asset.current_path,
                asset.original_path,
                asset.file_size,
                asset.content_hash,
                str(asset.format),
                asset.duration,
                json.dumps(asset.original_tags, ensure_ascii=False),
                asset.import_batch_id,
                asset.source_group_id,
                1 if asset.suspected_multitrack else 0,
                str(asset.task_status),
                str(asset.review_status),
                asset.error_message,
                asset.imported_at,
            ),
        )
        conn.commit()
        return asset

    def get_file_by_id(self, file_id: str) -> FileAsset | None:
        """Fetch a FileAsset by its primary key ID."""
        conn = self.db.connect()
        row = conn.execute("SELECT * FROM file_assets WHERE id = ?", (file_id,)).fetchone()
        return self._row_to_file_asset(row) if row else None

    def get_file_asset(self, file_id: str) -> FileAsset | None:
        """Alias for get_file_by_id."""
        return self.get_file_by_id(file_id)

    def get_file_by_path(self, current_path: str) -> FileAsset | None:
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM file_assets WHERE current_path = ?", (current_path,)
        ).fetchone()
        return self._row_to_file_asset(row) if row else None

    def find_files_by_hash(self, content_hash: str) -> list[FileAsset]:
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM file_assets WHERE content_hash = ?", (content_hash,)
        ).fetchall()
        return [self._row_to_file_asset(r) for r in rows]

    def list_files(self, source_group_id: str | None = None) -> list[FileAsset]:
        conn = self.db.connect()
        if source_group_id:
            rows = conn.execute(
                "SELECT * FROM file_assets WHERE source_group_id = ? ORDER BY current_path",
                (source_group_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM file_assets ORDER BY current_path").fetchall()
        return [self._row_to_file_asset(r) for r in rows]

    def update_file_status(
        self,
        file_id: str,
        task_status: TaskStatus | None = None,
        review_status: ReviewStatus | None = None,
        error_message: str | None = None,
    ) -> None:
        conn = self.db.connect()
        asset = self.get_file_by_id(file_id)
        if not asset:
            return
        new_task = str(task_status) if task_status is not None else str(asset.task_status)
        new_review = str(review_status) if review_status is not None else str(asset.review_status)
        new_err = error_message if error_message is not None else asset.error_message
        conn.execute(
            """
            UPDATE file_assets
            SET task_status = ?, review_status = ?, error_message = ?
            WHERE id = ?
            """,
            (new_task, new_review, new_err, file_id),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Candidate Matches
    # ------------------------------------------------------------------
    def upsert_candidate_match(self, match: CandidateMatch) -> CandidateMatch:
        if not match.retrieved_at:
            match.retrieved_at = utc_now_iso()
        conn = self.db.connect()
        conn.execute(
            """
            INSERT INTO candidate_matches (
                id, file_id, provider, provider_entity_id, entity_type,
                artist, title, mix_name, remixer, duration,
                release_details, match_evidence, match_score, retrieved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_id, provider, provider_entity_id, entity_type) DO UPDATE SET
                artist = excluded.artist,
                title = excluded.title,
                mix_name = excluded.mix_name,
                remixer = excluded.remixer,
                duration = excluded.duration,
                release_details = excluded.release_details,
                match_evidence = excluded.match_evidence,
                match_score = excluded.match_score,
                retrieved_at = excluded.retrieved_at
            """,
            (
                match.id,
                match.file_id,
                match.provider,
                match.provider_entity_id,
                str(match.entity_type),
                match.artist,
                match.title,
                match.mix_name,
                match.remixer,
                match.duration,
                json.dumps(match.release_details.to_dict(), ensure_ascii=False),
                json.dumps(match.match_evidence, ensure_ascii=False),
                match.match_score,
                match.retrieved_at,
            ),
        )
        conn.commit()
        return match

    def list_candidate_matches(
        self, file_id: str, entity_type: EntityType | None = None
    ) -> list[CandidateMatch]:
        conn = self.db.connect()
        if entity_type:
            rows = conn.execute(
                """
                SELECT * FROM candidate_matches
                WHERE file_id = ? AND entity_type = ?
                ORDER BY match_score DESC, retrieved_at DESC
                """,
                (file_id, str(entity_type)),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM candidate_matches
                WHERE file_id = ?
                ORDER BY match_score DESC, retrieved_at DESC
                """,
                (file_id,),
            ).fetchall()
        return [self._row_to_candidate_match(r) for r in rows]

    # ------------------------------------------------------------------
    # Field Proposals
    # ------------------------------------------------------------------
    def upsert_field_proposal(self, proposal: FieldProposal) -> FieldProposal:
        if not proposal.retrieved_at:
            proposal.retrieved_at = utc_now_iso()
        conn = self.db.connect()
        conn.execute(
            """
            INSERT INTO field_proposals (
                id, file_id, field_name, proposed_value, provider,
                provider_entity_id, evidence, provider_confidence,
                application_match_score, review_status, retrieved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(
                file_id, field_name, proposed_value, provider, provider_entity_id
            ) DO UPDATE SET
                evidence = excluded.evidence,
                provider_confidence = excluded.provider_confidence,
                application_match_score = excluded.application_match_score,
                retrieved_at = excluded.retrieved_at
            """,
            (
                proposal.id,
                proposal.file_id,
                proposal.field_name,
                proposal.proposed_value,
                proposal.provider,
                proposal.provider_entity_id,
                json.dumps(proposal.evidence, ensure_ascii=False),
                proposal.provider_confidence,
                proposal.application_match_score,
                str(proposal.review_status),
                proposal.retrieved_at,
            ),
        )
        conn.commit()
        return proposal

    def list_field_proposals(
        self, file_id: str, field_name: str | None = None
    ) -> list[FieldProposal]:
        """List field proposals for a file, optionally filtered by field_name."""
        conn = self.db.connect()
        if field_name:
            rows = conn.execute(
                """
                SELECT * FROM field_proposals
                WHERE file_id = ? AND field_name = ?
                ORDER BY
                    COALESCE(application_match_score, provider_confidence, 0) DESC,
                    retrieved_at DESC
                """,
                (file_id, field_name),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM field_proposals
                WHERE file_id = ?
                ORDER BY field_name, COALESCE(application_match_score, provider_confidence, 0) DESC
                """,
                (file_id,),
            ).fetchall()
        return [self._row_to_field_proposal(r) for r in rows]

    def get_field_proposal_by_id(self, proposal_id: str) -> FieldProposal | None:
        """Fetch a single FieldProposal by its ID."""
        conn = self.db.connect()
        row = conn.execute("SELECT * FROM field_proposals WHERE id = ?", (proposal_id,)).fetchone()
        return self._row_to_field_proposal(row) if row else None

    def update_proposal_status(self, proposal_id: str, status: ProposalReviewStatus) -> None:
        conn = self.db.connect()
        conn.execute(
            "UPDATE field_proposals SET review_status = ? WHERE id = ?",
            (str(status), proposal_id),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Analysis Results
    # ------------------------------------------------------------------
    def upsert_analysis_result(self, result: AnalysisResult) -> AnalysisResult:
        if not result.analyzed_at:
            result.analyzed_at = utc_now_iso()
        conn = self.db.connect()
        conn.execute(
            """
            INSERT INTO analysis_results (
                id, file_id, analyzer_name, analyzer_version, configuration,
                bpm, key_tonic, key_mode, analysis_strength,
                beat_positions_optional, warnings, analyzed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_id, analyzer_name, analyzer_version) DO UPDATE SET
                configuration = excluded.configuration,
                bpm = excluded.bpm,
                key_tonic = excluded.key_tonic,
                key_mode = excluded.key_mode,
                analysis_strength = excluded.analysis_strength,
                beat_positions_optional = excluded.beat_positions_optional,
                warnings = excluded.warnings,
                analyzed_at = excluded.analyzed_at
            """,
            (
                result.id,
                result.file_id,
                result.analyzer_name,
                result.analyzer_version,
                json.dumps(result.configuration, ensure_ascii=False),
                result.bpm,
                str(result.key_tonic) if result.key_tonic else None,
                str(result.key_mode) if result.key_mode else None,
                result.analysis_strength,
                json.dumps(result.beat_positions_optional)
                if result.beat_positions_optional is not None
                else None,
                json.dumps(result.warnings, ensure_ascii=False),
                result.analyzed_at,
            ),
        )
        conn.commit()
        return result

    def list_analysis_results(self, file_id: str) -> list[AnalysisResult]:
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM analysis_results WHERE file_id = ? ORDER BY analyzed_at DESC",
            (file_id,),
        ).fetchall()
        return [self._row_to_analysis_result(r) for r in rows]

    # ------------------------------------------------------------------
    # Approved Fields & Locks
    # ------------------------------------------------------------------
    def upsert_approved_field(
        self, approved: ApprovedField, respect_existing_lock: bool = True
    ) -> ApprovedField:
        conn = self.db.connect()
        existing = self.get_approved_field(approved.file_id, approved.field_name)
        if existing and existing.locked and respect_existing_lock:
            return existing

        if not approved.approved_at:
            approved.approved_at = utc_now_iso()

        conn.execute(
            """
            INSERT INTO approved_fields (
                file_id, field_name, approved_value, approval_origin,
                approved_at, locked, provider_attribution
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(file_id, field_name) DO UPDATE SET
                approved_value = excluded.approved_value,
                approval_origin = excluded.approval_origin,
                approved_at = excluded.approved_at,
                locked = excluded.locked,
                provider_attribution = excluded.provider_attribution
            """,
            (
                approved.file_id,
                approved.field_name,
                approved.approved_value,
                str(approved.approval_origin),
                approved.approved_at,
                1 if approved.locked else 0,
                json.dumps(approved.provider_attribution, ensure_ascii=False),
            ),
        )
        conn.commit()
        return approved

    def get_approved_field(self, file_id: str, field_name: str) -> ApprovedField | None:
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM approved_fields WHERE file_id = ? AND field_name = ?",
            (file_id, field_name),
        ).fetchone()
        return self._row_to_approved_field(row) if row else None

    def get_approved_fields_map(self, file_id: str) -> dict[str, ApprovedField]:
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM approved_fields WHERE file_id = ? ORDER BY field_name",
            (file_id,),
        ).fetchall()
        return {r["field_name"]: self._row_to_approved_field(r) for r in rows}

    def set_field_lock(self, file_id: str, field_name: str, locked: bool) -> None:
        conn = self.db.connect()
        conn.execute(
            "UPDATE approved_fields SET locked = ? WHERE file_id = ? AND field_name = ?",
            (1 if locked else 0, file_id, field_name),
        )
        conn.commit()

    def remove_approved_field(
        self, file_id: str, field_name: str, force_unlock: bool = False
    ) -> bool:
        existing = self.get_approved_field(file_id, field_name)
        if not existing:
            return False
        if existing.locked and not force_unlock:
            return False
        conn = self.db.connect()
        conn.execute(
            "DELETE FROM approved_fields WHERE file_id = ? AND field_name = ?",
            (file_id, field_name),
        )
        conn.commit()
        return True

    # ------------------------------------------------------------------
    # Change Journal Records
    # ------------------------------------------------------------------
    def upsert_change_record(self, record: ChangeRecord) -> ChangeRecord:
        if not record.timestamp:
            record.timestamp = utc_now_iso()
        conn = self.db.connect()
        conn.execute(
            """
            INSERT INTO change_records (
                operation_id, batch_operation_id, file_id, previous_path, new_path,
                pre_write_hash, post_write_hash, previous_tags, written_tags,
                backup_reference, operation_status, error_message, timestamp
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(operation_id) DO UPDATE SET
                new_path = excluded.new_path,
                post_write_hash = excluded.post_write_hash,
                written_tags = excluded.written_tags,
                backup_reference = excluded.backup_reference,
                operation_status = excluded.operation_status,
                error_message = excluded.error_message,
                timestamp = excluded.timestamp
            """,
            (
                record.operation_id,
                record.batch_operation_id,
                record.file_id,
                record.previous_path,
                record.new_path,
                record.pre_write_hash,
                record.post_write_hash,
                json.dumps(record.previous_tags, ensure_ascii=False),
                json.dumps(record.written_tags, ensure_ascii=False),
                record.backup_reference,
                str(record.operation_status),
                record.error_message,
                record.timestamp,
            ),
        )
        conn.commit()
        return record

    def get_change_record(self, operation_id: str) -> ChangeRecord | None:
        conn = self.db.connect()
        row = conn.execute(
            "SELECT * FROM change_records WHERE operation_id = ?", (operation_id,)
        ).fetchone()
        return self._row_to_change_record(row) if row else None

    def list_change_records(self, file_id: str | None = None) -> list[ChangeRecord]:
        conn = self.db.connect()
        if file_id:
            rows = conn.execute(
                "SELECT * FROM change_records WHERE file_id = ? ORDER BY timestamp DESC",
                (file_id,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM change_records ORDER BY timestamp DESC").fetchall()
        return [self._row_to_change_record(r) for r in rows]

    # ------------------------------------------------------------------
    # Persistent Jobs & Provider Cache
    # ------------------------------------------------------------------
    def enqueue_job(
        self, job_id: str, file_id: str, job_type: str, payload: dict[str, Any] | None = None
    ) -> None:
        now = utc_now_iso()
        conn = self.db.connect()
        conn.execute(
            """
            INSERT INTO jobs (
                id, file_id, job_type, status, attempts, payload, created_at, updated_at
            )
            VALUES (?, ?, ?, 'queued', 0, ?, ?, ?)
            ON CONFLICT(file_id, job_type) DO UPDATE SET
                status = 'queued',
                updated_at = excluded.updated_at
            """,
            (job_id, file_id, job_type, json.dumps(payload or {}), now, now),
        )
        conn.commit()

    def update_job_state(
        self, file_id: str, job_type: str, status: str, error: str | None = None
    ) -> None:
        now = utc_now_iso()
        conn = self.db.connect()
        conn.execute(
            """
            UPDATE jobs
            SET status = ?,
                attempts = CASE WHEN ? IN ('processing') THEN attempts + 1 ELSE attempts END,
                last_error = ?,
                updated_at = ?
            WHERE file_id = ? AND job_type = ?
            """,
            (status, status, error, now, file_id, job_type),
        )
        conn.commit()

    def list_pending_jobs(self) -> list[dict[str, Any]]:
        conn = self.db.connect()
        rows = conn.execute(
            "SELECT * FROM jobs WHERE status IN ('queued', 'processing') ORDER BY created_at"
        ).fetchall()
        return [dict(r) for r in rows]

    def get_cached_response(self, cache_key: str) -> dict[str, Any] | None:
        now = utc_now_iso()
        conn = self.db.connect()
        row = conn.execute(
            "SELECT response_json FROM provider_cache WHERE cache_key = ? AND expires_at > ?",
            (cache_key, now),
        ).fetchone()
        if not row:
            return None
        return json.loads(row["response_json"])

    def set_cached_response(
        self,
        cache_key: str,
        provider: str,
        response_data: dict[str, Any],
        expires_at_iso: str | None = None,
        ttl_seconds: int = 86400,
    ) -> None:
        """Persist a provider HTTP response in SQLite cache with TTL expiration."""
        now_dt = datetime.now(UTC)
        now = now_dt.isoformat()
        exp = expires_at_iso or (now_dt + timedelta(seconds=ttl_seconds)).isoformat()
        conn = self.db.connect()
        conn.execute(
            """
            INSERT INTO provider_cache (cache_key, provider, response_json, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET
                response_json = excluded.response_json,
                created_at = excluded.created_at,
                expires_at = excluded.expires_at
            """,
            (
                cache_key,
                provider,
                json.dumps(response_data, ensure_ascii=False),
                now,
                exp,
            ),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Row Mappers
    # ------------------------------------------------------------------
    @staticmethod
    def _row_to_source_group(row: Any) -> SourceGroup:
        return SourceGroup(
            id=row["id"],
            user_label=row["user_label"],
            grouping_basis=GroupingBasis(row["grouping_basis"]),
            original_folder=row["original_folder"],
            source_type=SourceType(row["source_type"]),
            notes=row["notes"] or "",
        )

    @staticmethod
    def _row_to_file_asset(row: Any) -> FileAsset:
        return FileAsset(
            id=row["id"],
            current_path=row["current_path"],
            original_path=row["original_path"],
            file_size=int(row["file_size"]),
            content_hash=row["content_hash"],
            format=AudioFormat(row["format"]),
            duration=float(row["duration"]) if row["duration"] is not None else None,
            original_tags=json.loads(row["original_tags"] or "{}"),
            import_batch_id=row["import_batch_id"],
            source_group_id=row["source_group_id"],
            suspected_multitrack=bool(row["suspected_multitrack"]),
            task_status=TaskStatus(row["task_status"]),
            review_status=ReviewStatus(row["review_status"]),
            error_message=row["error_message"],
            imported_at=row["imported_at"],
        )

    @staticmethod
    def _row_to_candidate_match(row: Any) -> CandidateMatch:
        return CandidateMatch(
            id=row["id"],
            file_id=row["file_id"],
            provider=row["provider"],
            provider_entity_id=row["provider_entity_id"],
            entity_type=EntityType(row["entity_type"]),
            artist=row["artist"],
            title=row["title"],
            mix_name=row["mix_name"],
            remixer=row["remixer"],
            duration=float(row["duration"]) if row["duration"] is not None else None,
            release_details=ReleaseDetails.from_dict(json.loads(row["release_details"] or "{}")),
            match_evidence=json.loads(row["match_evidence"] or "{}"),
            match_score=float(row["match_score"]),
            retrieved_at=row["retrieved_at"],
        )

    @staticmethod
    def _row_to_field_proposal(row: Any) -> FieldProposal:
        return FieldProposal(
            id=row["id"],
            file_id=row["file_id"],
            field_name=row["field_name"],
            proposed_value=row["proposed_value"],
            provider=row["provider"],
            provider_entity_id=row["provider_entity_id"],
            evidence=json.loads(row["evidence"] or "{}"),
            provider_confidence=float(row["provider_confidence"])
            if row["provider_confidence"] is not None
            else None,
            application_match_score=float(row["application_match_score"])
            if row["application_match_score"] is not None
            else None,
            review_status=ProposalReviewStatus(row["review_status"]),
            retrieved_at=row["retrieved_at"],
        )

    @staticmethod
    def _row_to_analysis_result(row: Any) -> AnalysisResult:
        return AnalysisResult(
            id=row["id"],
            file_id=row["file_id"],
            analyzer_name=row["analyzer_name"],
            analyzer_version=row["analyzer_version"],
            configuration=json.loads(row["configuration"] or "{}"),
            bpm=float(row["bpm"]) if row["bpm"] is not None else None,
            key_tonic=PitchClass(row["key_tonic"]) if row["key_tonic"] else None,
            key_mode=KeyMode(row["key_mode"]) if row["key_mode"] else None,
            analysis_strength=float(row["analysis_strength"])
            if row["analysis_strength"] is not None
            else None,
            beat_positions_optional=json.loads(row["beat_positions_optional"])
            if row["beat_positions_optional"]
            else None,
            warnings=json.loads(row["warnings"] or "[]"),
            analyzed_at=row["analyzed_at"],
        )

    @staticmethod
    def _row_to_approved_field(row: Any) -> ApprovedField:
        return ApprovedField(
            file_id=row["file_id"],
            field_name=row["field_name"],
            approved_value=row["approved_value"],
            approval_origin=ApprovalOrigin(row["approval_origin"]),
            approved_at=row["approved_at"],
            locked=bool(row["locked"]),
            provider_attribution=json.loads(row["provider_attribution"] or "{}"),
        )

    @staticmethod
    def _row_to_change_record(row: Any) -> ChangeRecord:
        return ChangeRecord(
            operation_id=row["operation_id"],
            batch_operation_id=row["batch_operation_id"],
            file_id=row["file_id"],
            previous_path=row["previous_path"],
            new_path=row["new_path"],
            pre_write_hash=row["pre_write_hash"],
            post_write_hash=row["post_write_hash"],
            previous_tags=json.loads(row["previous_tags"] or "{}"),
            written_tags=json.loads(row["written_tags"] or "{}"),
            backup_reference=row["backup_reference"],
            operation_status=OperationStatus(row["operation_status"]),
            error_message=row["error_message"],
            timestamp=row["timestamp"],
        )
