"""Unit and Qt UI tests for Milestone 2: Mock Providers, ReviewService, and PySide6 UI."""

from __future__ import annotations

from pathlib import Path

from infiniteprep.adapters.analyzers.mock_analyzer import MockAudioAnalyzerAdapter
from infiniteprep.adapters.catalog.mock_catalog import MockCatalogProviderAdapter
from infiniteprep.adapters.fingerprint.mock_fp import MockFingerprintAdapter
from infiniteprep.adapters.llm.mock_llm import MockReasoningAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import (
    ApprovalOrigin,
    EntityType,
    FieldName,
    KeyDisplayNotation,
    ProposalReviewStatus,
    ReviewStatus,
    SourceType,
)
from infiniteprep.domain.models import CandidateMatch, FieldProposal, ReleaseDetails
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui.audio_player import AudioPreviewWidget, compute_cue_jump_positions
from infiniteprep.ui.main_window import MainWindow
from tests.fixtures.audio_factory import create_wav_fixture


def test_mock_providers_produce_deterministic_candidates_and_analysis(tmp_path: Path) -> None:
    """Verify mock fingerprint, catalog, analyzer, and LLM adapters work offline."""
    wav_path = create_wav_fixture(
        tmp_path / "01_deep_dish_the_dream.wav",
        tags={"artist": "Deep Dish", "title": "The Dream"},
    )
    fp_adapter = MockFingerprintAdapter()
    fp_result = fp_adapter.fingerprint_and_lookup(wav_path)
    assert fp_result.fingerprint.startswith("MOCK_FP_")
    assert len(fp_result.candidates) >= 1
    assert fp_result.candidates[0].provider == "acoustid_mock"

    cat_adapter = MockCatalogProviderAdapter()
    cat_matches = cat_adapter.search_candidates(
        file_id="file-1",
        artist="Deep Dish",
        title="The Dream",
        release_title=None,
        catalog_number="TRIUK014",
        duration_sec=480.0,
    )
    assert len(cat_matches) >= 1
    assert cat_matches[0].release_details.catalog_number == "TRIUK014"

    analyzer = MockAudioAnalyzerAdapter()
    ana_res = analyzer.analyze("file-1", wav_path)
    assert ana_res.bpm == 124.0
    assert ana_res.key_tonic is not None
    assert ana_res.key_mode is not None

    llm_adapter = MockReasoningAdapter()
    rec = llm_adapter.arbitrate({"allowed_candidates": [{"id": "cand-1", "value": "Club Mix"}]})
    assert rec["selected_candidate_id"] == "cand-1"


def test_review_service_accept_edit_reject_lock_and_bulk_approve(tmp_path: Path) -> None:
    """Verify ReviewService enforces locks, manual edits, and non-conflicting bulk approval."""
    db = DatabaseManager(":memory:")
    repo = Repository(db)
    importer = ImportService(repo)
    review_svc = ReviewService(repo)

    wav1 = create_wav_fixture(tmp_path / "track1.wav", tags={"artist": "Artist A"})
    wav2 = create_wav_fixture(tmp_path / "track2.wav", tags={"artist": "Artist B"})
    report = importer.import_paths([wav1, wav2], source_type=SourceType.VINYL)
    file1, file2 = report.imported_files[0], report.imported_files[1]

    # File 1 has a single non-conflicting high-score proposal for ARTIST and conflicting for TITLE
    p_artist = FieldProposal(
        id="p-art-1",
        file_id=file1.id,
        field_name=FieldName.ARTIST.value,
        proposed_value="Danny Tenaglia",
        provider="discogs",
        provider_entity_id="rel-100",
        provider_confidence=0.95,
        application_match_score=0.94,
    )
    p_title_a = FieldProposal(
        id="p-tit-a",
        file_id=file1.id,
        field_name=FieldName.TITLE.value,
        proposed_value="Elements (The Chant)",
        provider="discogs",
        provider_entity_id="rel-100",
        provider_confidence=0.90,
        application_match_score=0.88,
    )
    p_title_b = FieldProposal(
        id="p-tit-b",
        file_id=file1.id,
        field_name=FieldName.TITLE.value,
        proposed_value="Elements (Original Vocal)",
        provider="musicbrainz",
        provider_entity_id="mb-200",
        provider_confidence=0.89,
        application_match_score=0.87,
    )
    repo.upsert_field_proposal(p_artist)
    repo.upsert_field_proposal(p_title_a)
    repo.upsert_field_proposal(p_title_b)

    # File 2 has a non-conflicting LABEL proposal
    p_label_2 = FieldProposal(
        id="p-lbl-2",
        file_id=file2.id,
        field_name=FieldName.LABEL.value,
        proposed_value="Twisted America",
        provider="discogs",
        provider_entity_id="rel-100",
        provider_confidence=0.96,
        application_match_score=0.93,
    )
    repo.upsert_field_proposal(p_label_2)

    # Bulk approve non-conflicting proposals across both files
    approved_count = review_svc.bulk_approve_non_conflicting([file1.id, file2.id], min_score=0.85)
    assert approved_count == 2

    art_app = repo.get_approved_field(file1.id, FieldName.ARTIST.value)
    assert art_app is not None
    assert art_app.approved_value == "Danny Tenaglia"
    assert art_app.approval_origin == ApprovalOrigin.PROPOSAL_ACCEPTED

    # Conflicting TITLE on file1 was NOT bulk-approved
    assert repo.get_approved_field(file1.id, FieldName.TITLE.value) is None

    # Manual edit with lock=True on TITLE
    review_svc.set_manual_field_value(
        file1.id, FieldName.TITLE.value, "Elements (Danny's Custom Dub)", lock=True
    )
    tit_app = repo.get_approved_field(file1.id, FieldName.TITLE.value)
    assert tit_app is not None
    assert tit_app.locked is True
    assert tit_app.approval_origin == ApprovalOrigin.MANUAL_EDIT

    # Accepting a proposal on a locked field must be rejected/no-op unless unlocked first
    accepted = review_svc.accept_proposal("p-tit-a")
    assert accepted is False
    tit_after = repo.get_approved_field(file1.id, FieldName.TITLE.value)
    assert tit_after is not None
    assert tit_after.approved_value == "Elements (Danny's Custom Dub)"

    # Unlock field and accept proposal p-tit-a
    review_svc.set_field_lock(file1.id, FieldName.TITLE.value, locked=False)
    assert review_svc.accept_proposal("p-tit-a") is True
    tit_unlocked = repo.get_approved_field(file1.id, FieldName.TITLE.value)
    assert tit_unlocked is not None
    assert tit_unlocked.approved_value == "Elements (The Chant)"

    # Reject proposal p-tit-b
    review_svc.reject_proposal("p-tit-b")
    props = {p.id: p for p in repo.list_field_proposals(file1.id, FieldName.TITLE.value)}
    assert props["p-tit-b"].review_status == ProposalReviewStatus.REJECTED

    _verify_group_release_propagation(repo, review_svc, file1.id, file2.id, file1.source_group_id)
    db.close()


def _verify_group_release_propagation(
    repo: Repository,
    review_svc: ReviewService,
    file1_id: str,
    file2_id: str,
    source_group_id: str | None,
) -> None:
    """Verify release candidate approval propagates release fields to group members."""
    cand = CandidateMatch(
        id="cand-grp-1",
        file_id=file1_id,
        provider="discogs",
        provider_entity_id="rel-999",
        entity_type=EntityType.RELEASE,
        artist="Danny Tenaglia",
        title="Elements",
        release_details=ReleaseDetails(
            release_title="Elements 2x12",
            label="Twisted America",
            catalog_number="TW12-55412",
            year="1997",
        ),
        match_score=0.95,
    )
    repo.upsert_candidate_match(cand)
    assert source_group_id is not None
    propagated = review_svc.approve_release_for_source_group(source_group_id, cand)
    assert propagated >= 2
    f2_cat = repo.get_approved_field(file2_id, FieldName.CATALOG_NUMBER.value)
    assert f2_cat is not None
    assert f2_cat.approved_value == "TW12-55412"


def test_pyside6_main_window_and_needle_drop_audio_preview(qtbot, tmp_path: Path) -> None:
    """Verify MainWindow table filtering, notation switching, and audio cue jump positions."""
    cues = compute_cue_jump_positions(300.0)
    assert cues["Intro"] == 15.0
    assert cues["25%"] == 75.0
    assert cues["50% Drop"] == 150.0
    assert cues["75%"] == 225.0
    assert cues["Outro"] == 270.0

    db = DatabaseManager(":memory:")
    repo = Repository(db)
    importer = ImportService(repo)
    review_svc = ReviewService(repo)

    wav_path = create_wav_fixture(
        tmp_path / "A1_needle_test.wav",
        duration_sec=3.0,
        sample_rate=96000,
        tags={"artist": "Sasha", "title": "Xpander"},
    )
    report = importer.import_paths([wav_path], source_type=SourceType.VINYL)
    file_asset = report.imported_files[0]

    # Add a proposal and analysis result
    repo.upsert_field_proposal(
        FieldProposal(
            id="ui-prop-1",
            file_id=file_asset.id,
            field_name=FieldName.MIX_NAME.value,
            proposed_value="Original 12-Inch Mix",
            provider="discogs",
            provider_entity_id="rel-xp",
            provider_confidence=0.95,
            application_match_score=0.91,
        )
    )

    window = MainWindow(repository=repo, import_service=importer, review_service=review_svc)
    qtbot.addWidget(window)
    window.refresh_file_table()

    assert window.file_table.rowCount() == 1
    window.file_table.selectRow(0)
    assert window.detail_panel.current_file_id == file_asset.id

    # Switch key notation in toolbar
    window.key_notation_combo.setCurrentText(KeyDisplayNotation.OPEN_KEY.value)
    assert window.current_key_notation == KeyDisplayNotation.OPEN_KEY

    # Verify needle-drop preview widget loads waveform envelope and jumps to 50% Drop
    player: AudioPreviewWidget = window.detail_panel.audio_player
    player.load_file(wav_path)
    player.jump_to_cue("50% Drop")
    assert abs(player.current_position_sec - 1.5) < 0.25

    # Approve proposal via DetailPanel
    window.detail_panel.accept_first_proposal_for_field(FieldName.MIX_NAME.value)
    approved = repo.get_approved_field(file_asset.id, FieldName.MIX_NAME.value)
    assert approved is not None
    assert approved.approved_value == "Original 12-Inch Mix"
    assert repo.get_file_asset(file_asset.id).review_status in (
        ReviewStatus.PARTIALLY_APPROVED,
        ReviewStatus.READY_TO_WRITE,
    )
    db.close()
