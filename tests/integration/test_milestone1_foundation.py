"""Integration tests for SQLite database schema, Repository CRUD, idempotency, locks, and import."""

from __future__ import annotations

from pathlib import Path

from infiniteprep.adapters.metadata_io.mutagen_adapter import compute_sha256
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import (
    ApprovalOrigin,
    AudioFormat,
    EntityType,
    FieldName,
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
    ReleaseDetails,
    SourceGroup,
)
from infiniteprep.services.import_service import ImportService
from tests.fixtures.audio_factory import (
    create_aiff_fixture,
    create_flac_fixture,
    create_mp3_fixture,
    create_ogg_fixture,
    create_wav_fixture,
)


def test_repository_crud_idempotency_and_lock_enforcement(tmp_path: Path) -> None:
    """Verify SQLite persistence, natural-key idempotency, and locked field protection."""
    db_file = tmp_path / "test_infiniteprep.sqlite3"
    db = DatabaseManager(db_file)
    repo = Repository(db)

    group = SourceGroup(
        id="grp-1",
        user_label="Tribal America 12-inch",
        grouping_basis=GroupingBasis.FOLDER_HYPOTHESIS,
        original_folder=str(tmp_path),
        source_type=SourceType.VINYL,
    )
    repo.upsert_source_group(group)
    assert repo.get_source_group("grp-1") == group
    assert repo.get_source_group_by_folder(str(tmp_path)) == group
    assert len(repo.list_source_groups()) == 1

    wav_path = create_wav_fixture(
        tmp_path / "A1_track.wav",
        tags={"artist": "Deep Dish", "title": "The Dream"},
        include_traktor_frame=True,
    )
    importer = ImportService(repo)
    report = importer.import_paths([wav_path], source_type=SourceType.VINYL)
    assert len(report.imported_files) == 1
    file_asset = report.imported_files[0]
    assert file_asset.format == AudioFormat.WAV
    assert file_asset.is_write_supported is True

    # CandidateMatch idempotency on (file_id, provider, provider_entity_id, entity_type)
    cand = CandidateMatch(
        id="cand-1",
        file_id=file_asset.id,
        provider="discogs",
        provider_entity_id="rel-1001",
        entity_type=EntityType.RELEASE,
        artist="Deep Dish Presents Prana",
        title="The Dream",
        mix_name="Sharam's Deep Dish Dreamscape Mix",
        remixer="Sharam",
        duration=480.0,
        release_details=ReleaseDetails(
            release_title="The Dream",
            label="Tribal America",
            catalog_number="TRIUK014",
            vinyl_position="A1",
            year="1994",
        ),
        match_score=0.94,
    )
    repo.upsert_candidate_match(cand)
    repo.upsert_candidate_match(cand)
    matches = repo.list_candidate_matches(file_asset.id, EntityType.RELEASE)
    assert len(matches) == 1
    assert matches[0].release_details.catalog_number == "TRIUK014"

    # FieldProposal idempotency on (file_id, field_name, proposed_value, provider, entity_id)
    prop = FieldProposal(
        id="prop-1",
        file_id=file_asset.id,
        field_name=FieldName.MIX_NAME.value,
        proposed_value="Sharam's Deep Dish Dreamscape Mix",
        provider="discogs",
        provider_entity_id="rel-1001",
        provider_confidence=0.95,
        application_match_score=0.92,
    )
    repo.upsert_field_proposal(prop)
    repo.upsert_field_proposal(prop)
    proposals = repo.list_field_proposals(file_asset.id, FieldName.MIX_NAME.value)
    assert len(proposals) == 1
    assert proposals[0].provider_confidence == 0.95
    assert proposals[0].application_match_score == 0.92

    repo.update_proposal_status(proposals[0].id, ProposalReviewStatus.ACCEPTED)
    updated_props = repo.list_field_proposals(file_asset.id)
    assert updated_props[0].review_status == ProposalReviewStatus.ACCEPTED

    _verify_analysis_and_lock_persistence(repo, file_asset.id, wav_path, tmp_path)
    db.close()


def _verify_analysis_and_lock_persistence(
    repo: Repository, file_id: str, wav_path: Path, tmp_path: Path
) -> None:
    """Verify AnalysisResult, locked ApprovedField protection, and ChangeRecord persistence."""
    analysis = AnalysisResult(
        id="ana-1",
        file_id=file_id,
        analyzer_name="local_dsp",
        analyzer_version="0.1.0",
        configuration={"sr": 44100},
        bpm=124.0,
        key_tonic=PitchClass.F_SHARP,
        key_mode=KeyMode.MINOR,
        analysis_strength=0.88,
        warnings=[],
    )
    repo.upsert_analysis_result(analysis)
    repo.upsert_analysis_result(analysis)
    analyses = repo.list_analysis_results(file_id)
    assert len(analyses) == 1
    assert analyses[0].key_tonic == PitchClass.F_SHARP

    # ApprovedField & Lock survival (Rule 6)
    approved = ApprovedField(
        file_id=file_id,
        field_name=FieldName.MIX_NAME.value,
        approved_value="Sharam's Deep Dish Dreamscape Mix",
        approval_origin=ApprovalOrigin.PROPOSAL_ACCEPTED,
        approved_at="2026-10-28T00:00:00Z",
        locked=True,
        provider_attribution={"provider": "discogs", "entity_id": "rel-1001"},
    )
    repo.upsert_approved_field(approved)

    attempted_overwrite = ApprovedField(
        file_id=file_id,
        field_name=FieldName.MIX_NAME.value,
        approved_value="Radio Edit",
        approval_origin=ApprovalOrigin.PROPOSAL_ACCEPTED,
        approved_at="2026-10-28T01:00:00Z",
        locked=False,
    )
    repo.upsert_approved_field(attempted_overwrite, respect_existing_lock=True)
    current_approved = repo.get_approved_field(file_id, FieldName.MIX_NAME.value)
    assert current_approved is not None
    assert current_approved.approved_value == "Sharam's Deep Dish Dreamscape Mix"
    assert current_approved.locked is True
    assert repo.remove_approved_field(file_id, FieldName.MIX_NAME.value) is False

    # ChangeRecord persistence
    record = ChangeRecord(
        operation_id="op-1",
        file_id=file_id,
        previous_path=str(wav_path),
        new_path=str(wav_path),
        previous_tags={"artist": "Deep Dish"},
        written_tags={"artist": "Deep Dish Presents Prana"},
        backup_reference=str(tmp_path / "backup.wav"),
        operation_status=OperationStatus.COMPLETED,
        timestamp="2026-10-28T00:00:00Z",
    )
    repo.upsert_change_record(record)
    assert repo.get_change_record("op-1") is not None
    assert len(repo.list_change_records(file_id)) == 1


def test_import_service_is_non_destructive_and_detects_xdj_rx3_warnings(tmp_path: Path) -> None:
    """Verify import leaves files 100% unmodified and flags XDJ-RX3 / duplicate / damaged files."""
    db = DatabaseManager(":memory:")
    repo = Repository(db)
    importer = ImportService(repo)

    release_dir = tmp_path / "Release_12_Inch"
    mp3_file = create_mp3_fixture(
        release_dir / "01_track.mp3",
        tags={"artist": "Danny Tenaglia", "title": "Elements", "custom_tag": "KeepMe"},
        include_traktor_frame=True,
    )
    flac_96k_file = create_flac_fixture(
        release_dir / "02_highres_96k.flac",
        sample_rate=96000,
        subtype="PCM_24",
        tags={"artist": "Danny Tenaglia", "title": "Elements (Dub)"},
        include_traktor_comment=True,
    )
    wav_32f_file = create_wav_fixture(
        release_dir / "03_vinyl_rip_32float.wav",
        sample_rate=44100,
        subtype="FLOAT",
    )
    aiff_file = create_aiff_fixture(
        release_dir / "04_club_mix.aiff",
        tags={"artist": "Danny Tenaglia", "title": "Elements (Club)"},
        include_traktor_frame=True,
    )
    ogg_file = create_ogg_fixture(release_dir / "05_traktor_only.ogg")
    unsupported_txt = release_dir / "rip_notes.txt"
    unsupported_txt.write_text("Vinyl rip log", encoding="utf-8")
    damaged_mp3 = release_dir / "06_corrupt.mp3"
    damaged_mp3.write_bytes(b"NOT_AN_MP3_HEADER")

    hashes_before = {
        p: compute_sha256(p)
        for p in (mp3_file, flac_96k_file, wav_32f_file, aiff_file, ogg_file, damaged_mp3)
    }

    report = importer.import_paths([release_dir], source_type=SourceType.VINYL)

    # Verify zero file modification during import
    for p, expected_hash in hashes_before.items():
        assert compute_sha256(p) == expected_hash

    assert len(report.imported_files) == 5
    assert str(unsupported_txt.resolve()) in report.unsupported_files
    assert len(report.damaged_files) == 1

    # Re-importing same folder detects previously imported paths without duplicating rows
    second_report = importer.import_paths([release_dir])
    assert len(second_report.previously_imported_paths) == 6
    assert len(second_report.imported_files) == 0

    # Verify XDJ-RX3 hardware compatibility warnings on 96kHz FLAC, 32-bit float WAV, and OGG
    flac_asset = repo.get_file_by_path(str(flac_96k_file.resolve()))
    assert flac_asset is not None
    flac_warnings = flac_asset.original_tags.get("technical", {}).get("xdj_rx3_warnings", [])
    assert any("96000" in w or "48000" in w for w in flac_warnings)

    wav_asset = repo.get_file_by_path(str(wav_32f_file.resolve()))
    assert wav_asset is not None
    wav_warnings = wav_asset.original_tags.get("technical", {}).get("xdj_rx3_warnings", [])
    assert any("32-bit" in w or "bit depth" in w.lower() for w in wav_warnings)

    ogg_asset = repo.get_file_by_path(str(ogg_file.resolve()))
    assert ogg_asset is not None
    ogg_warnings = ogg_asset.original_tags.get("technical", {}).get("xdj_rx3_warnings", [])
    assert any("ogg" in w.lower() for w in ogg_warnings)

    aiff_asset = repo.get_file_by_path(str(aiff_file.resolve()))
    assert aiff_asset is not None
    assert aiff_asset.is_write_supported is True
    assert aiff_asset.task_status == TaskStatus.IDLE
    assert aiff_asset.review_status == ReviewStatus.UNPROCESSED
    db.close()
