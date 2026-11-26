"""Tests that file names and warning text reach the UI as plain text, never as rich text."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.adapters.transcoder.safe_transcoder import SafeTranscoderAdapter
from infiniteprep.config.settings import SettingsManager
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.ui.action_handlers import run_transcode_with_confirmation
from infiniteprep.ui.detail_panel import DetailPanel
from tests.fixtures.audio_factory import create_wav_fixture

# The name holds no slash. A closing tag would split the name into a folder and a file.
MARKUP_FILE_NAME = "<img src=x><b>bold.wav"
STATIC_DIALOG_KINDS = ("question", "warning", "information")
STATIC_DIALOG_FAILURE = "A static QMessageBox call renders untrusted text with AutoText."


@pytest.mark.usefixtures("qapp")
class PlainTextTestCase(unittest.TestCase):
    """Provide the pytest-qt QApplication, a temporary folder, and an in-memory library."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.folder = Path(folder.name)
        self.repository = Repository(DatabaseManager(":memory:"))
        self.settings_manager = SettingsManager(self.folder / "settings.json")

    def import_track(self, name: str) -> tuple[Path, str]:
        """Create and import one WAV file, then return its path and file ID."""
        track = create_wav_fixture(self.folder / "crate" / name, duration_sec=1.0)
        importer = ImportService(self.repository, MetadataTagAdapter())
        report = importer.import_paths([track])
        return track, report.imported_files[0].id

    def build_panel(self) -> DetailPanel:
        """Return a detail panel over the test library."""
        panel = DetailPanel(
            self.repository,
            ReviewService(self.repository),
            settings_manager=self.settings_manager,
        )
        self.addCleanup(panel.close)
        return panel


class DetailPanelLabelTests(PlainTextTestCase):
    """The header and warnings labels show imported text literally."""

    def test_labels_use_plain_text_format(self) -> None:
        """Both detail panel labels use the plain text format from the start."""
        panel = self.build_panel()

        self.assertEqual(panel.header_label.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(panel.warnings_label.textFormat(), Qt.TextFormat.PlainText)

    @unittest.skipIf(os.name == "nt", "Windows file names cannot hold angle brackets.")
    def test_header_keeps_filename_markup_literal(self) -> None:
        """A file name with markup shows in the header as typed."""
        _, file_id = self.import_track(MARKUP_FILE_NAME)
        panel = self.build_panel()

        panel.load_file_asset(file_id)

        self.assertEqual(panel.header_label.textFormat(), Qt.TextFormat.PlainText)
        self.assertIn(MARKUP_FILE_NAME, panel.header_label.text())


class TranscodeDialogTests(PlainTextTestCase):
    """The transcode confirmation and summary show file names literally."""

    def setUp(self) -> None:
        super().setUp()
        self.shown: list[tuple[Qt.TextFormat, str]] = []

        def record_instead_of_showing(box: QMessageBox) -> QMessageBox.StandardButton:
            self.shown.append((box.textFormat(), box.text()))
            return QMessageBox.StandardButton.Yes

        self._replace_dialog("exec", record_instead_of_showing)
        # The static helpers offer no text format control. Each one fails the test.
        for kind in STATIC_DIALOG_KINDS:
            self._replace_dialog(kind, mock.Mock(side_effect=AssertionError(STATIC_DIALOG_FAILURE)))

    def _replace_dialog(self, name: str, replacement: object) -> None:
        """Swap one QMessageBox attribute for the duration of the test."""
        patcher = mock.patch.object(QMessageBox, name, replacement)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_confirm_failure_text_is_plain(self) -> None:
        """The confirmation and the failure summary both use plain text and name the file."""
        removed, file_id = self.import_track("gone.wav")
        removed.unlink()
        transcoder = SafeTranscoderAdapter(self.repository, backup_dir=self.folder / "backups")

        summary = run_transcode_with_confirmation(
            None, self.repository, transcoder, [file_id], self.settings_manager
        )

        self.assertIsNotNone(summary)
        formats = [text_format for text_format, _ in self.shown]
        self.assertEqual(formats, [Qt.TextFormat.PlainText, Qt.TextFormat.PlainText])
        self.assertIn("Failed: gone.wav", self.shown[1][1])


if __name__ == "__main__":
    unittest.main()
