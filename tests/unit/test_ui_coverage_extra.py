"""Behavioral tests for uncovered storage, technical detail, and theme paths."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from PySide6.QtWidgets import QComboBox

from infiniteprep.domain.enums import AudioFormat, OperationStatus
from infiniteprep.domain.models import ChangeRecord, FileAsset
from infiniteprep.ui import theme as theme_module
from infiniteprep.ui.storage_prompt import (
    QUIT_LABEL,
    RESTORE_LABEL,
    USE_PER_USER_LABEL,
    build_fallback_prompt,
    build_restore_prompt,
    confirm_library_restore,
    confirm_storage_fallback,
    show_storage_error,
)
from infiniteprep.ui.technical_details import (
    BACKUP_LABEL,
    CONTENT_HASH_LABEL,
    DECODER_LABEL,
    NO_BACKUP_TEXT,
    tag_frame_names,
    technical_detail_rows,
)
from infiniteprep.ui.theme import (
    DEFAULT_DARK_THEME,
    DEFAULT_LIGHT_THEME,
    NATIVE_THEME,
    SYSTEM_THEME,
    apply_application_theme,
    fill_theme_picker,
    get_applicable_themes,
    resolve_theme,
    select_theme,
    switch_application_theme,
    theme_label,
)


def make_asset(audio_format: AudioFormat, error_message: str | None = None) -> FileAsset:
    """Return a file asset for the technical detail rows."""
    return FileAsset(
        id="track-id",
        current_path="track.wav",
        original_path="track.wav",
        file_size=10,
        content_hash="abc123",
        format=audio_format,
        duration=1.0,
        original_tags={},
        import_batch_id="batch-id",
        source_group_id=None,
        error_message=error_message,
    )


def make_change_record(backup_reference: str) -> ChangeRecord:
    """Return a change record with the given backup reference."""
    return ChangeRecord(
        operation_id="operation-id",
        file_id="track-id",
        previous_path="old.wav",
        new_path="track.wav",
        previous_tags={},
        written_tags={},
        backup_reference=backup_reference,
        operation_status=OperationStatus.COMPLETED,
        timestamp="2026-11-26T00:00:00Z",
    )


def test_storage_error_box_runs_to(monkeypatch: pytest.MonkeyPatch) -> None:
    """The storage error helper waits for the message box to close."""
    box = MagicMock()
    monkeypatch.setattr(
        "infiniteprep.ui.storage_prompt.build_storage_error_box",
        lambda _message: box,
    )

    show_storage_error("The folder is unavailable.")

    box.exec.assert_called_once_with()


def test_restore_confirmation_returns_the_button(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The restore helper runs the prompt and reports its answer."""
    prompt = SimpleNamespace(box=MagicMock(), confirmed=lambda: True)
    monkeypatch.setattr(
        "infiniteprep.ui.storage_prompt.build_restore_prompt",
        lambda _message: prompt,
    )

    assert confirm_library_restore("The library is newer.") is True
    prompt.box.exec.assert_called_once_with()


def test_fallback_confirmation_returns_the_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The fallback helper runs the prompt and returns its answer."""
    answer = SimpleNamespace(use_per_user=True, remember=True)
    prompt = SimpleNamespace(box=MagicMock(), answer=lambda: answer)
    monkeypatch.setattr(
        "infiniteprep.ui.storage_prompt.build_fallback_prompt",
        lambda _folder: prompt,
    )

    assert confirm_storage_fallback(Path("stick")).use_per_user is True
    prompt.box.exec.assert_called_once_with()


def test_restore_prompt_uses_plain_text(qtbot: Any) -> None:
    """The restore prompt names Restore and keeps Quit as the default."""
    prompt = build_restore_prompt("The library is newer.")
    qtbot.addWidget(prompt.box)

    buttons = {button.text(): button for button in prompt.box.buttons()}
    assert RESTORE_LABEL in buttons
    assert prompt.box.defaultButton().text() != RESTORE_LABEL


def test_fallback_prompt_uses_plain_text(qtbot: Any) -> None:
    """The fallback prompt names both choices and keeps Quit as the default."""
    prompt = build_fallback_prompt(Path("stick"))
    qtbot.addWidget(prompt.box)

    buttons = {button.text(): button for button in prompt.box.buttons()}
    assert USE_PER_USER_LABEL in buttons
    assert QUIT_LABEL in buttons
    assert prompt.box.defaultButton().text() == QUIT_LABEL


def test_tag_frame_names_cover_vorbiss() -> None:
    """Vorbis formats list comment fields and unknown formats list none."""
    vorbis = tag_frame_names(AudioFormat.FLAC, lambda field: field.upper())
    id3 = tag_frame_names(AudioFormat.MP3, lambda field: field.upper())

    assert vorbis
    assert id3
    assert tag_frame_names(AudioFormat.UNSUPPORTED, str) == []


def test_technical_rows_include_backup_and() -> None:
    """The rows show the latest backup and a decoder error when present."""
    asset = make_asset(AudioFormat.MP3, "The decoder failed.")
    rows = technical_detail_rows(
        asset,
        [make_change_record("backup-one.zip")],
        lambda field: field,
    )
    labels = dict(rows)

    assert labels[CONTENT_HASH_LABEL] == "abc123"
    assert labels[BACKUP_LABEL] == "backup-one.zip"
    assert labels[DECODER_LABEL] == "The decoder failed."


def test_technical_rows_name_the_missing() -> None:
    """A track with no backup names that fact in the backup row."""
    rows = technical_detail_rows(make_asset(AudioFormat.FLAC), [], lambda field: field)
    labels = dict(rows)

    assert labels[BACKUP_LABEL] == NO_BACKUP_TEXT
    assert DECODER_LABEL not in labels


def test_theme_label_keeps_an_unknown() -> None:
    """An unknown theme identifier stays visible in the picker."""
    assert theme_label("custom-theme") == "custom-theme"


def test_theme_picker_uses_labels_and(qtbot: Any) -> None:
    """The picker shows readable labels and keeps the theme identifiers."""
    combo = QComboBox()
    qtbot.addWidget(combo)
    fill_theme_picker(combo, [DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME])

    assert combo.itemText(0) == "Dark"
    assert combo.itemData(0) == DEFAULT_DARK_THEME


def test_select_theme_maps_legacy_values(
    qtbot: Any,
) -> None:
    """A legacy theme selects its project theme and an unknown theme fails."""
    combo = QComboBox()
    qtbot.addWidget(combo)
    fill_theme_picker(combo, [DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME])

    assert select_theme(combo, "dark_teal.xml") is True
    assert combo.currentData() == DEFAULT_DARK_THEME
    assert select_theme(combo, "missing-theme") is False


def test_qt_material_package_case_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A package spec without an origin has no theme directory."""
    monkeypatch.setattr(importlib.util, "find_spec", lambda _name: SimpleNamespace(origin=None))

    assert theme_module._qt_material_package_dir() is None


def test_qt_material_package_directory_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed package lookup leaves the theme package unavailable."""

    def fail_lookup(_name: str) -> None:
        raise ImportError("package lookup failed")

    monkeypatch.setattr(importlib.util, "find_spec", fail_lookup)

    assert theme_module._qt_material_package_dir() is None


def test_system_theme_detection_case_3(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A build without Qt objects treats the system as dark."""
    monkeypatch.setattr(theme_module, "QGuiApplication", None)
    monkeypatch.setattr(theme_module, "Qt", None)

    assert theme_module._is_system_in_dark_mode() is True


def test_system_theme_detection_handles_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing GUI application treats the system as dark."""
    fake_gui = type("FakeGuiApplication", (), {"instance": staticmethod(lambda: None)})
    monkeypatch.setattr(theme_module, "QGuiApplication", fake_gui)

    assert theme_module._is_system_in_dark_mode() is True


def test_system_theme_detection_case_2(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An application without style hints treats the system as dark."""
    app = SimpleNamespace(styleHints=lambda: None)
    fake_gui = type("FakeGuiApplication", (), {"instance": staticmethod(lambda: app)})
    monkeypatch.setattr(theme_module, "QGuiApplication", fake_gui)

    assert theme_module._is_system_in_dark_mode() is True


def test_load_qt_material_handles_import(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing optional theme package leaves no engine loaded."""

    def fail_import(_name: str) -> None:
        raise ImportError("qt-material is missing")

    monkeypatch.setattr(importlib, "import_module", fail_import)

    assert theme_module._load_qt_material() is None


def test_clear_stylesheet_accepts_missing_application() -> None:
    """The stylesheet helper accepts an absent application."""
    theme_module._clear_stylesheet(None)


def test_applicable_themes_include_only_real(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A theme with no file stays out of the applicable list."""
    themes = tmp_path / "themes"
    themes.mkdir()
    (themes / DEFAULT_DARK_THEME).write_text("<resources/>", encoding="utf-8")
    monkeypatch.setattr(theme_module, "_has_qt_material", lambda: True)
    monkeypatch.setattr(theme_module, "_qt_material_package_dir", lambda: tmp_path)
    monkeypatch.setattr(theme_module, "PROJECT_THEMES_DIR", themes)

    assert get_applicable_themes() == [DEFAULT_DARK_THEME, NATIVE_THEME]


def test_missing_theme_file_has_a(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A project theme without its file reports the missing file."""
    monkeypatch.setattr(theme_module, "_has_qt_material", lambda: True)
    monkeypatch.setattr(theme_module, "PROJECT_THEMES_DIR", tmp_path)

    reason = theme_module._theme_failure_reason(DEFAULT_LIGHT_THEME)

    assert "theme file" in reason


def test_missing_theme_engine_leaves_native(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A theme engine that cannot load leaves the native theme in effect."""
    app = MagicMock()
    monkeypatch.setattr(theme_module, "_theme_failure_reason", lambda _theme: "")

    def fail_import(_name: str) -> None:
        raise ImportError("qt-material is missing")

    monkeypatch.setattr(importlib, "import_module", fail_import)

    result = apply_application_theme(app, DEFAULT_DARK_THEME)

    assert result.applied is False
    assert result.theme == NATIVE_THEME
    assert "cannot be imported" in result.reason


def test_switch_without_current_theme_falls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed theme switch with no current theme leaves the native theme."""
    monkeypatch.setattr(theme_module, "_theme_failure_reason", lambda _theme: "failed")

    result = switch_application_theme(MagicMock(), DEFAULT_DARK_THEME, "")

    assert result.applied is False
    assert result.theme == NATIVE_THEME


def test_system_theme_resolves_to_a(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The system choice resolves to a concrete project theme."""
    monkeypatch.setattr(theme_module, "_is_system_in_dark_mode", lambda: False)

    assert resolve_theme(SYSTEM_THEME) == DEFAULT_LIGHT_THEME
    assert resolve_theme(None) == DEFAULT_LIGHT_THEME
