"""Confirmation and result dialogs for the batch transcode action."""

from __future__ import annotations

from pathlib import Path
from typing import Final

from PySide6.QtWidgets import QMessageBox, QWidget

from infiniteprep.adapters.transcoder.safe_transcoder import (
    SafeTranscoderAdapter,
    TranscodeMode,
    TranscodePreset,
)
from infiniteprep.config.settings import SettingsManager
from infiniteprep.db.repositories import Repository
from infiniteprep.services.transcode_batch import (
    TranscodeBatchSummary,
    resolve_transcode_options,
    transcode_files,
)

TRANSCODE_DIALOG_TITLE: Final[str] = "Transcode to XDJ"
MAX_LISTED_FAILURES: Final[int] = 10
SIBLING_EXPORT_NOTE: Final[str] = (
    "Each file gets a new '<name>_XDJ' copy next to the original. The originals stay unchanged."
)
IN_PLACE_WARNING: Final[str] = (
    "WARNING: in-place mode replaces each original file. Each replacement first takes a "
    "SHA-256 verified backup and records a Change Journal entry."
)


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
