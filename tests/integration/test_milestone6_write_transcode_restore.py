"""Integration tests for Milestone 6: WriteService, SafeTranscoder, Playlist, and Dialogs."""

from __future__ import annotations

from pathlib import Path

import pytest
import soundfile as sf
from mutagen.flac import FLAC
from mutagen.id3 import ID3
from mutagen.wave import WAVE

from infiniteprep.adapters.metadata_io.mutagen_adapter import MutagenAdapter, compute_sha256
from infiniteprep.adapters.transcoder.safe_transcoder import (
    SafeTranscoderAdapter,
    TranscodeMode,
    TranscodePreset,
)
from infiniteprep.config.credentials import CredentialStore
from infiniteprep.config.settings import SettingsManager
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import (
    FieldName,
    OperationStatus,
    ReviewStatus,
    SourceType,
)
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.playlist_exporter import PlaylistExporter
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from infiniteprep.ui.journal_dialog import JournalDialog
from infiniteprep.ui.settings_dialog import SettingsDialog
from infiniteprep.ui.write_plan_dialog import WritePlanDialog
from tests.fixtures.audio_factory import (
    create_aiff_fixture,
    create_flac_fixture,
    create_mp3_fixture,
    create_wav_fixture,
)


def test_write_service_applies_tags_renames_preserves_traktor_and_restores(
    tmp_path: Path,
) -> None:
    """Verify WriteService writes MP3/FLAC/WAV/AIFF, preserves PRIV:TRAKTOR4, and restores."""
    db = DatabaseManager(":memory:")
    repo = Repository(db)
    tag_adapter = MutagenAdapter()
    importer = ImportService(repo, tag_adapter)
    review_svc = ReviewService(repo)
    backup_dir = tmp_path / "backups"
    write_svc = WriteService(repo, tag_adapter, backup_dir=backup_dir)

    src_dir = tmp_path / "music"
    mp3_p = create_mp3_fixture(
        src_dir / "01_mp3.mp3",
        tags={"artist": "Old Artist", "title": "Old Title"},
        include_traktor_frame=True,
    )
    flac_p = create_flac_fixture(
        src_dir / "02_flac.flac",
        tags={"artist": "Old Artist", "title": "Old FLAC"},
        include_traktor_comment=True,
    )
    wav_p = create_wav_fixture(
        src_dir / "03_wav.wav",
        tags={"artist": "Old Artist", "title": "Old WAV"},
        include_traktor_frame=True,
    )
    aiff_p = create_aiff_fixture(
        src_dir / "04_aiff.aiff",
        tags={"artist": "Old Artist", "title": "Old AIFF"},
        include_traktor_frame=True,
    )

    orig_wav_hash = compute_sha256(wav_p)
    report = importer.import_paths([mp3_p, flac_p, wav_p, aiff_p], source_type=SourceType.VINYL)
    assert len(report.imported_files) == 4

    for idx, asset in enumerate(report.imported_files, start=1):
        review_svc.set_manual_field_value(asset.id, FieldName.ARTIST.value, "Deep Dish")
        review_svc.set_manual_field_value(asset.id, FieldName.TITLE.value, f"Track {idx}")
        review_svc.set_manual_field_value(asset.id, FieldName.MIX_NAME.value, "Dreamscape Mix")
        review_svc.set_manual_field_value(asset.id, FieldName.LABEL.value, "Tribal America")
        review_svc.set_manual_field_value(asset.id, FieldName.CATALOG_NUMBER.value, "TRIUK014")

    file_ids = [f.id for f in report.imported_files]
    plans = write_svc.build_batch_preview(
        file_ids,
        rename_template="{artist} - {title} ({mix_name})",
        enable_rename=True,
    )
    assert len(plans) == 4
    assert all(p.is_safe_to_apply for p in plans)

    records = write_svc.apply_batch(plans)
    assert len(records) == 4
    assert all(r.operation_status == OperationStatus.COMPLETED for r in records)

    # Verify FLAC has both LABEL and ORGANIZATION and preserved TRAKTOR4
    flac_record = records[1]
    flac_after = FLAC(flac_record.new_path)
    assert flac_after["LABEL"] == ["Tribal America"]
    assert flac_after["ORGANIZATION"] == ["Tribal America"]
    assert "TRAKTOR4" in flac_after

    # Verify WAV has both ID3v2.3 PRIV:TRAKTOR4 and RIFF LIST-INFO chunk
    wav_record = records[2]
    wav_after = WAVE(wav_record.new_path)
    assert isinstance(wav_after.tags, ID3)
    assert any(k.startswith("PRIV:TRAKTOR4") for k in wav_after.tags)
    raw_wav_bytes = Path(wav_record.new_path).read_bytes()
    assert b"LIST" in raw_wav_bytes
    assert b"INFOINAM" in raw_wav_bytes

    # Verify Restore from backup restores exact bytes and original path
    restored = write_svc.restore_operation(wav_record.operation_id)
    assert restored.operation_status == OperationStatus.RESTORED
    assert wav_p.exists()
    assert compute_sha256(wav_p) == orig_wav_hash
    assert repo.get_file_by_id(wav_record.file_id).review_status == ReviewStatus.APPROVED
    db.close()


def test_write_service_blocks_external_modification_and_filename_collision(
    tmp_path: Path,
) -> None:
    """Verify WriteService preflight blocks external file tampering and rename collisions."""
    db = DatabaseManager(":memory:")
    repo = Repository(db)
    tag_adapter = MutagenAdapter()
    importer = ImportService(repo, tag_adapter)
    review_svc = ReviewService(repo)
    write_svc = WriteService(repo, tag_adapter, backup_dir=tmp_path / "backups")

    wav1 = create_wav_fixture(tmp_path / "track1.wav", tags={"artist": "A", "title": "T"})
    wav2 = create_wav_fixture(tmp_path / "Sasha - Xpander.wav")
    asset = importer.import_paths([wav1]).imported_files[0]
    review_svc.set_manual_field_value(asset.id, FieldName.ARTIST.value, "Sasha")
    review_svc.set_manual_field_value(asset.id, FieldName.TITLE.value, "Xpander")

    # Collision check against existing wav2 ("Sasha - Xpander.wav")
    collision_plans = write_svc.build_batch_preview(
        [asset.id], rename_template="{artist} - {title}", enable_rename=True
    )
    assert collision_plans[0].is_safe_to_apply is False
    assert len(collision_plans[0].collision_warnings) == 1
    assert wav2.exists()

    # External modification check
    wav1.write_bytes(wav1.read_bytes() + b"\x00\x00")
    tampered_plans = write_svc.build_batch_preview([asset.id], enable_rename=False)
    assert tampered_plans[0].is_safe_to_apply is False
    with pytest.raises(ValueError, match="preflight"):
        write_svc.apply_batch(tampered_plans)
    db.close()


def test_safe_transcoder_and_m3u8_playlist_exporter(tmp_path: Path) -> None:
    """Verify SafeTranscoderAdapter converts 96kHz/32f to 48kHz/24-bit and exports M3U8."""
    db = DatabaseManager(":memory:")
    repo = Repository(db)
    tag_adapter = MutagenAdapter()
    importer = ImportService(repo, tag_adapter)
    review_svc = ReviewService(repo)

    highres_wav = create_wav_fixture(
        tmp_path / "96k_32float.wav",
        duration_sec=2.0,
        sample_rate=96000,
        subtype="FLOAT",
        tags={"artist": "Sasha", "title": "Xpander"},
        include_traktor_frame=True,
    )
    asset = importer.import_paths([highres_wav]).imported_files[0]
    review_svc.set_manual_field_value(asset.id, FieldName.ARTIST.value, "Sasha")
    review_svc.set_manual_field_value(asset.id, FieldName.TITLE.value, "Xpander")
    review_svc.set_manual_field_value(asset.id, FieldName.BPM.value, "128.0")

    transcoder = SafeTranscoderAdapter(
        repository=repo,
        tag_adapter=tag_adapter,
        backup_dir=tmp_path / "backups",
    )
    out_dir = tmp_path / "rx3_export"
    result = transcoder.transcode_file(
        asset.id,
        preset=TranscodePreset.XDJ_RX3_LOSSLESS_AUTO,
        mode=TranscodeMode.SIBLING_EXPORT,
        export_dir=out_dir,
    )
    assert result.output_path.exists()
    info = sf.info(str(result.output_path))
    assert info.samplerate == 48000
    assert info.subtype == "PCM_24"
    # Verify original 96kHz file was untouched in SIBLING_EXPORT mode
    assert sf.info(str(highres_wav)).samplerate == 96000

    # Export M3U8 playlist
    exporter = PlaylistExporter(repo)
    m3u8_path = tmp_path / "crate.m3u8"
    exporter.export_m3u8([asset.id], m3u8_path)
    content = m3u8_path.read_text(encoding="utf-8")
    assert "#EXTM3U" in content
    assert "#EXTINF:" in content
    assert "Sasha - Xpander" in content
    db.close()


def test_milestone6_ui_dialogs_smoke(qtbot, tmp_path: Path) -> None:
    """Verify WritePlanDialog, JournalDialog, and SettingsDialog instantiate cleanly in Qt."""
    db = DatabaseManager(":memory:")
    repo = Repository(db)
    tag_adapter = MutagenAdapter()
    write_svc = WriteService(repo, tag_adapter, backup_dir=tmp_path / "backups")
    settings_mgr = SettingsManager(tmp_path / "settings.json")
    creds = CredentialStore(use_memory_backend=True)

    wp_dlg = WritePlanDialog(write_service=write_svc, file_ids=[])
    qtbot.addWidget(wp_dlg)
    assert wp_dlg.table.rowCount() == 0

    j_dlg = JournalDialog(repository=repo, write_service=write_svc)
    qtbot.addWidget(j_dlg)
    assert j_dlg.table.rowCount() == 0

    s_dlg = SettingsDialog(settings_manager=settings_mgr, credential_store=creds)
    qtbot.addWidget(s_dlg)
    s_dlg.save_settings()
    assert (tmp_path / "settings.json").exists()
    db.close()
