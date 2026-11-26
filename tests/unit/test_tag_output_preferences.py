"""Exercise persisted tag output preferences through Settings and real tag writes."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.config.credentials import CredentialStore
from infiniteprep.config.settings import SettingsManager
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from infiniteprep.ui.settings_dialog import SettingsDialog
from tests.fixtures.audio_factory import (
    create_aiff_fixture,
    create_flac_fixture,
    create_mp3_fixture,
    create_wav_fixture,
)


def test_settings_dialog_persists_written_formats(qtbot: Any, tmp_path: Path) -> None:
    """Save tag output controls independently of the library display preference."""
    manager = SettingsManager(tmp_path / "settings.json")
    manager.update(key_display_notation="standard")
    dialog = SettingsDialog(manager, CredentialStore(use_memory_backend=True))
    qtbot.addWidget(dialog)
    tab = dialog.general_tab
    tab.tag_key_notation_combo.setCurrentIndex(tab.tag_key_notation_combo.findData("open_key"))
    tab.tag_key_leading_zero_checkbox.setChecked(True)
    tab.tag_bpm_decimals_combo.setCurrentIndex(tab.tag_bpm_decimals_combo.findData(2))
    dialog.save_settings()

    settings = manager.load()
    assert settings.key_display_notation == "standard"
    assert settings.tag_key_notation == "open_key"
    assert settings.tag_key_leading_zero is True
    assert settings.tag_bpm_decimals == 2


@pytest.mark.parametrize(
    ("factory", "suffix"),
    (
        (create_mp3_fixture, "mp3"),
        (create_flac_fixture, "flac"),
        (create_wav_fixture, "wav"),
        (create_aiff_fixture, "aiff"),
    ),
)
@pytest.mark.parametrize(
    ("notation", "leading_zero", "decimals", "approved_bpm", "written_key", "written_bpm"),
    (
        ("camelot", False, 0, "128.5", "3A", "129"),
        ("camelot", True, 0, "128.5", "03A", "129"),
        ("open_key", True, 1, "128.45", "08m", "128.5"),
        ("standard", True, 2, "128.445", "Bbm", "128.45"),
    ),
)
def test_preview_tags_use_saved_formats(
    tmp_path: Path,
    *,
    factory: Callable[..., Path],
    suffix: str,
    notation: str,
    leading_zero: bool,
    decimals: int,
    approved_bpm: str,
    written_key: str,
    written_bpm: str,
) -> None:
    """Use the saved output choices without changing the approved key or BPM."""
    settings = SettingsManager(tmp_path / "settings.json")
    settings.update(
        tag_key_notation=notation, tag_key_leading_zero=leading_zero, tag_bpm_decimals=decimals
    )
    adapter = MetadataTagAdapter(settings.load_tag_output)
    repo = Repository(DatabaseManager(":memory:"))
    source = factory(tmp_path / f"track.{suffix}")
    [asset] = ImportService(repo, adapter).import_paths([source]).imported_files
    review = ReviewService(repo)
    review.set_manual_field_value(asset.id, FieldName.KEY, "Bb minor")
    review.set_manual_field_value(asset.id, FieldName.BPM, approved_bpm)
    service = WriteService(repo, adapter, backup_dir=tmp_path / "backups")
    [plan] = service.build_batch_preview([asset.id])

    assert plan.proposed_tags_to_write[FieldName.KEY] == written_key
    assert plan.proposed_tags_to_write[FieldName.BPM] == written_bpm
    service.apply_single_plan(plan)
    written = adapter.read_metadata(source).standard_tags
    assert written[FieldName.KEY] == written_key
    assert written[FieldName.BPM] == written_bpm
    approved = repo.get_approved_fields_map(asset.id)
    assert approved[FieldName.KEY].approved_value == "Bb minor"
    assert approved[FieldName.BPM].approved_value == approved_bpm
