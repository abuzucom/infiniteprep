"""Confirmation and result dialogs for the batch transcode and playlist export actions."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox, QWidget

from infiniteprep.adapters.transcoder.safe_transcoder import (
    SafeTranscoderAdapter,
    TranscodeMode,
    TranscodePreset,
)
from infiniteprep.config.settings import SettingsManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.playlist_exporter import UNSAFE_PATH_CHARACTERS, PlaylistExporter
from infiniteprep.services.transcode_batch import (
    TranscodeBatchSummary,
    resolve_transcode_options,
    transcode_files,
)

logger = logging.getLogger(__name__)

TRANSCODE_DIALOG_TITLE: Final[str] = "Transcode to XDJ"
EXPORT_DIALOG_TITLE: Final[str] = "Export Playlist (.m3u8)"
MAX_LISTED_FAILURES: Final[int] = 10
SIBLING_EXPORT_NOTE: Final[str] = (
    "Each file gets a new '<name>_XDJ' copy next to the original. The originals stay unchanged."
)
IN_PLACE_WARNING: Final[str] = (
    "WARNING: in-place mode replaces each original file. Each replacement first takes a "
    "SHA-256 verified backup and records a Change Journal entry."
)


def show_plain_message(
    parent: QWidget | None, icon: QMessageBox.Icon, title: str, text: str
) -> None:
    """Show a modal message that renders file names as literal text, never as rich text."""
    box = QMessageBox(icon, title, text, QMessageBox.StandardButton.Ok, parent)
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.exec()


def ask_plain_question(parent: QWidget | None, title: str, text: str) -> bool:
    """Ask a Yes/No question in plain text, defaulting to No, and return True on Yes."""
    box = QMessageBox(
        QMessageBox.Icon.Question,
        title,
        text,
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        parent,
    )
    box.setTextFormat(Qt.TextFormat.PlainText)
    box.setDefaultButton(QMessageBox.StandardButton.No)
    return box.exec() == QMessageBox.StandardButton.Yes


def build_transcode_confirmation_text(
    file_count: int, mode: TranscodeMode, preset: TranscodePreset
) -> str:
    """Return the confirmation prompt naming the file count, mode, and preset."""
    note = IN_PLACE_WARNING if mode == TranscodeMode.IN_PLACE_BACKED_UP else SIBLING_EXPORT_NOTE
    return (
        f"Transcode {file_count} visible file(s) with preset {preset.value} "
        f"in mode {mode.value}?\n\n{note}\n\nChange the mode and preset in Settings & Providers."
    )


def build_transcode_summary_text(repo: Repository, summary: TranscodeBatchSummary) -> str:
    """Return a result message listing successes and up to MAX_LISTED_FAILURES failures."""
    total = len(summary.results) + len(summary.failures)
    lines = [f"Transcoded {len(summary.results)} of {total} file(s)."]
    lines.extend(f"Output: {result.output_path.name}" for result in summary.results)
    for file_id, message in list(summary.failures.items())[:MAX_LISTED_FAILURES]:
        asset = repo.get_file_by_id(file_id)
        name = Path(asset.current_path).name if asset is not None else file_id
        lines.append(f"Failed: {name} ({message})")
    hidden_failures = len(summary.failures) - MAX_LISTED_FAILURES
    if hidden_failures > 0:
        lines.append(f"{hidden_failures} more failure(s) appear in the application log.")
    return "\n".join(lines)


def run_transcode_with_confirmation(
    parent: QWidget | None,
    repo: Repository,
    transcoder: SafeTranscoderAdapter,
    file_ids: list[str],
    settings_manager: SettingsManager,
) -> TranscodeBatchSummary | None:
    """Confirm the batch with the user, transcode on Yes, and show the result summary."""
    if not file_ids:
        return None
    settings = settings_manager.load()
    mode, preset = resolve_transcode_options(settings.transcode_mode, settings.transcode_preset)
    answer = QMessageBox.question(
        parent,
        TRANSCODE_DIALOG_TITLE,
        build_transcode_confirmation_text(len(file_ids), mode, preset),
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    if answer != QMessageBox.StandardButton.Yes:
        return None
    summary = transcode_files(transcoder, file_ids, preset=preset, mode=mode)
    message = build_transcode_summary_text(repo, summary)
    if summary.failures:
        QMessageBox.warning(parent, TRANSCODE_DIALOG_TITLE, message)
    else:
        QMessageBox.information(parent, TRANSCODE_DIALOG_TITLE, message)
    return summary


def export_playlist_with_feedback(
    parent: QWidget | None,
    exporter: PlaylistExporter,
    file_ids: list[str],
    destination: Path,
    *,
    relative_paths: bool = False,
) -> Path | None:
    """Write the playlist and report the destination, or report the failure and return None.

    Only OSError gets a dialog. It covers the expected failures of a user-chosen destination,
    such as a missing permission, a full disk, a directory, or a refused symlink. Any other
    exception is a defect and propagates to the application error handler.
    """
    try:
        report = exporter.export_m3u8_report(file_ids, destination, relative_paths=relative_paths)
    except OSError as exc:
        logger.warning("Playlist export to %s failed: %s", destination, exc.__class__.__name__)
        show_plain_message(
            parent,
            QMessageBox.Icon.Warning,
            EXPORT_DIALOG_TITLE,
            f"Playlist export failed ({exc}). Choose a writable location and retry.",
        )
        return None
    message = f"Exported {report.written_count} track(s) to {report.path}."
    if report.skipped_file_ids:
        message += "\n\n" + build_skipped_tracks_text(exporter.repo, report.skipped_file_ids)
    show_plain_message(parent, QMessageBox.Icon.Information, EXPORT_DIALOG_TITLE, message)
    return report.path


def build_skipped_tracks_text(repo: Repository, skipped_file_ids: list[str]) -> str:
    """Name each track left out because its file path holds a control character."""
    lines = [
        f"Skipped {len(skipped_file_ids)} track(s) with a line break or other control "
        "character in the file name:"
    ]
    for file_id in skipped_file_ids[:MAX_LISTED_FAILURES]:
        asset = repo.get_file_by_id(file_id)
        if asset is None:
            lines.append(f"<unknown track {file_id}>")
            continue
        raw_name = Path(asset.current_path).name
        lines.append("".join(" " if char in UNSAFE_PATH_CHARACTERS else char for char in raw_name))
    return "\n".join(lines)
