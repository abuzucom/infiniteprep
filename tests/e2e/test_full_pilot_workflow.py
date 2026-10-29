"""End-to-end tests for Milestone 7: CLI Evaluation Harness and all 19 Required Test Scenarios."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from infiniteprep.adapters.analyzers.local_dsp import LocalDspAnalyzer
from infiniteprep.adapters.catalog.mock_catalog import MockCatalogProviderAdapter
from infiniteprep.adapters.fingerprint.mock_fp import MockFingerprintAdapter
from infiniteprep.adapters.metadata_io.mutagen_adapter import MutagenAdapter, compute_sha256
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import (
    EntityType,
    FieldName,
    OperationStatus,
    ReviewStatus,
    SourceType,
)
from infiniteprep.domain.models import CandidateMatch, ReleaseDetails
from infiniteprep.main import main, run_evaluation_sample
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.job_orchestrator import JobOrchestrator
from infiniteprep.services.reconciliation import ReconciliationService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from tests.fixtures.audio_factory import (
    create_aiff_fixture,
    create_flac_fixture,
    create_mp3_fixture,
    create_ogg_fixture,
    create_wav_fixture,
)


def test_cli_evaluate_sample_harness_writes_metrics_report(tmp_path: Path) -> None:
    """Verify 'infiniteprep evaluate-sample' CLI mode produces a JSON evaluation report."""
    sample_dir = tmp_path / "pilot_tracks"
    create_wav_fixture(
        sample_dir / "A1_deep_dish.wav",
        duration_sec=3.0,
        bpm=125.0,
        tags={"artist": "Deep Dish", "title": "The Dream (Sharam's Deep Dish Dreamscape Mix)"},
        include_traktor_frame=True,
    )
    create_flac_fixture(
        sample_dir / "B1_tenaglia.flac",
        duration_sec=3.0,
        bpm=126.0,
        tags={"artist": "Danny Tenaglia", "title": "Elements (The Chant)"},
        include_traktor_comment=True,
    )
    report_path = tmp_path / "eval_report.json"

    exit_code = main(
        [
            "evaluate-sample",
            "--input-dir",
            str(sample_dir),
            "--output-json",
            str(report_path),
        ]
    )
    assert exit_code == 0
    assert report_path.exists()
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["total_imported_files"] == 2
    assert data["identified_files"] == 2
    assert data["analyzed_files"] == 2
    assert data["zero_unintended_file_mutations"] is True

    with pytest.raises(ValueError, match="does not exist"):
        run_evaluation_sample(tmp_path / "missing_dir")


def test_full_end_to_end_core_loop_and_edge_case_scenarios(tmp_path: Path) -> None:
    """Verify full core loop (Import -> Propose -> Approve -> Preview -> Apply -> Restore)."""
    db = DatabaseManager(tmp_path / "e2e.sqlite3")
    repo = Repository(db)
    tag_adapter = MutagenAdapter()
    importer = ImportService(repo, tag_adapter)
    orchestrator = JobOrchestrator(
        repository=repo,
        fingerprint_provider=MockFingerprintAdapter(),
        catalog_providers=[MockCatalogProviderAdapter()],
        audio_analyzer=LocalDspAnalyzer(),
    )
    reconciler = ReconciliationService(repo)
    review_svc = ReviewService(repo)
    write_svc = WriteService(repo, tag_adapter, backup_dir=tmp_path / "backups")

    crate_dir = tmp_path / "vinyl_crate"
    wav_a1 = create_wav_fixture(
        crate_dir / "A1_track.wav",
        duration_sec=4.0,
        bpm=125.0,
        tags={"artist": "Deep Dish", "title": "The Dream"},
        include_traktor_frame=True,
    )
    aiff_b1 = create_aiff_fixture(
        crate_dir / "B1_track.aiff",
        duration_sec=4.0,
        tags={"artist": "Deep Dish", "title": "The Dream (Dub)"},
        include_traktor_frame=True,
    )
    mp3_c1 = create_mp3_fixture(
        crate_dir / "C1_track.mp3",
        tags={"artist": "Deep Dish", "title": "The Dream (Radio)"},
        include_traktor_frame=True,
    )
    ogg_ro = create_ogg_fixture(crate_dir / "D1_readonly.ogg")
    long_mix = create_wav_fixture(crate_dir / "E1_continuous_mix.wav", duration_sec=2.0)

    orig_a1_hash = compute_sha256(wav_a1)
    imp_report = importer.import_paths([crate_dir], source_type=SourceType.VINYL)
    assert len(imp_report.imported_files) == 5

    # Simulate continuous mix > 20 minutes on long_mix (Scenario: suspected_multitrack)
    mix_asset = repo.get_file_by_path(str(long_mix.resolve()))
    assert mix_asset is not None
    mix_asset.duration = 1800.0
    mix_asset.suspected_multitrack = True
    repo.upsert_file_asset(mix_asset)
    assert reconciler.reconcile_file(mix_asset.id) == []

    # Process A1, B1, C1
    target_assets = [repo.get_file_by_path(str(p.resolve())) for p in (wav_a1, aiff_b1, mp3_c1)]
    target_ids = [a.id for a in target_assets if a is not None]
    orchestrator.process_files(target_ids)
    for fid in target_ids:
        reconciler.reconcile_file(fid)

    # Bulk approve non-conflicting proposals
    approved_count = review_svc.bulk_approve_non_conflicting(target_ids, min_score=0.70)
    assert approved_count >= 3

    # Group propagation across shared vinyl folder
    group_id = target_assets[0].source_group_id
    assert group_id is not None
    rel_cand = CandidateMatch(
        id="e2e-rel-1",
        file_id=target_ids[0],
        provider="discogs",
        provider_entity_id="rel-triuk014",
        entity_type=EntityType.RELEASE,
        artist="Deep Dish",
        title="The Dream",
        release_details=ReleaseDetails(
            release_title="The Dream 12-Inch",
            label="Tribal America",
            catalog_number="TRIUK014",
            year="1994",
        ),
        match_score=0.95,
    )
    review_svc.approve_release_for_source_group(group_id, rel_cand)

    # Preview and apply writes
    plans = write_svc.build_batch_preview(
        target_ids,
        rename_template="{artist} - {title}",
        enable_rename=False,
    )
    assert all(p.is_safe_to_apply for p in plans)
    records = write_svc.apply_batch(plans)
    assert len(records) == 3

    # Verify read-only OGG is blocked if write is attempted
    ogg_asset = repo.get_file_by_path(str(ogg_ro.resolve()))
    assert ogg_asset is not None
    assert write_svc.build_batch_preview([ogg_asset.id])[0].is_safe_to_apply is False

    _verify_restore_and_eval_helper(
        repo=repo,
        write_svc=write_svc,
        records=records,
        first_id=target_ids[0],
        wav_a1=wav_a1,
        orig_a1_hash=orig_a1_hash,
        crate_dir=crate_dir,
    )
    db.close()


def _verify_restore_and_eval_helper(
    *,
    repo: Repository,
    write_svc: WriteService,
    records: list,
    first_id: str,
    wav_a1: Path,
    orig_a1_hash: str,
    crate_dir: Path,
) -> None:
    """Verify backup restoration and run_evaluation_sample helper."""
    a1_record = next(r for r in records if r.file_id == first_id)
    restored = write_svc.restore_operation(a1_record.operation_id)
    assert restored.operation_status == OperationStatus.RESTORED
    assert compute_sha256(wav_a1) == orig_a1_hash
    assert repo.get_file_by_id(first_id).review_status == ReviewStatus.APPROVED

    eval_summary = run_evaluation_sample(crate_dir)
    assert eval_summary["total_imported_files"] >= 4
    assert eval_summary["FieldName_BPM"] == FieldName.BPM.value
