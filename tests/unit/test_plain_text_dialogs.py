"""Tests that playlist export dialogs render file names as plain text."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from infiniteprep.adapters.metadata_io.mutagen_adapter import MutagenAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.playlist_exporter import PlaylistExporter
from infiniteprep.ui.action_handlers import export_playlist_with_feedback, show_plain_message
from tests.fixtures.audio_factory import create_wav_fixture

MARKUP_NAME = "<b>bold</b>.m3u8"


@pytest.fixture
def shown_boxes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[Qt.TextFormat, str]]:
    """Replace QMessageBox.exec so each shown box records its text format and text."""
    shown: list[tuple[Qt.TextFormat, str]] = []

    def record_instead_of_showing(box: QMessageBox) -> int:
        shown.append((box.textFormat(), box.text()))
        return QMessageBox.StandardButton.Ok.value

    monkeypatch.setattr(QMessageBox, "exec", record_instead_of_showing)
    return shown


def test_plain_message_sets_the_plain_text_format(qtbot, shown_boxes: list) -> None:
    """The helper shows its text verbatim with the PlainText format."""
    show_plain_message(None, QMessageBox.Icon.Information, "Title", MARKUP_NAME)

    assert shown_boxes == [(Qt.TextFormat.PlainText, MARKUP_NAME)]


@pytest.mark.skipif(os.name == "nt", reason="Windows file names cannot hold angle brackets.")
def test_export_success_dialog_keeps_markup_in_the_destination_literal(
    qtbot, tmp_path: Path, shown_boxes: list
) -> None:
    """A destination name holding HTML tags reaches the dialog as literal plain text."""
    repo = Repository(DatabaseManager(":memory:"))
    track = create_wav_fixture(tmp_path / "crate" / "track.wav", duration_sec=1.0)
    report = ImportService(repo, MutagenAdapter()).import_paths([track])
    destination = tmp_path / "out" / MARKUP_NAME

    export_playlist_with_feedback(
        None, PlaylistExporter(repo), [a.id for a in report.imported_files], destination
    )

    assert shown_boxes == [(Qt.TextFormat.PlainText, f"Exported 1 track(s) to {destination}.")]
