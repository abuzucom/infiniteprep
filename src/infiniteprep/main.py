"""Application entrypoint for InfinitePrep desktop GUI and CLI evaluation harness."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Final

from PySide6.QtWidgets import QApplication

from infiniteprep import storage_paths
from infiniteprep.adapters.analyzers.local_dsp import LocalDspAnalyzer
from infiniteprep.adapters.catalog.mock_catalog import MockCatalogProviderAdapter
from infiniteprep.adapters.fingerprint.mock_fp import MockFingerprintAdapter
from infiniteprep.adapters.transcoder.safe_transcoder import SafeTranscoderAdapter
from infiniteprep.config.credentials import CredentialStore, forget_all_credentials
from infiniteprep.config.settings import SettingsManager
from infiniteprep.data_cleanup import CleanupChoice, run_cleanup
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName, SourceType
from infiniteprep.domain.ports import FileWritePlan
from infiniteprep.hashing import compute_sha256
from infiniteprep.qt_logging import silence_ffmpeg_logs
from infiniteprep.services.import_service import ImportService, discover_files_bounded
from infiniteprep.services.job_orchestrator import BatchKind, JobOrchestrator
from infiniteprep.services.playlist_exporter import PlaylistExporter
from infiniteprep.services.reconciliation import ReconciliationService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from infiniteprep.smoke_check import run_smoke_check
from infiniteprep.startup_storage import open_storage
from infiniteprep.storage_paths import appimage_launch_from_environment
from infiniteprep.ui.action_handlers import (
    export_playlist_with_feedback,
    run_transcode_with_confirmation,
)
from infiniteprep.ui.batch_queue import CancelCheck, ProgressReport
from infiniteprep.ui.cleanup_dialog import build_cleanup_blocked_box, build_cleanup_result_box
from infiniteprep.ui.journal_dialog import JournalDialog
from infiniteprep.ui.main_window import AnalyzeRunner, MainWindow
from infiniteprep.ui.recovery_dialog import RecoveryDialog
from infiniteprep.ui.settings_dialog import SettingsDialog
from infiniteprep.ui.storage_prompt import confirm_storage_fallback, show_storage_error
from infiniteprep.ui.theme import apply_application_theme
from infiniteprep.ui.write_plan_dialog import WritePlanDialog

SMOKE_CHECK_FLAG: Final[str] = "--smoke-check"
# The flag plus one output path.
SMOKE_CHECK_ARG_COUNT: Final[int] = 2
SMOKE_CHECK_USAGE_ERROR: Final[int] = 2
# A hidden flag for the Windows uninstaller's saved-keys box (D266).
FORGET_CREDENTIALS_FLAG: Final[str] = "--forget-credentials"


def run_evaluation_sample(input_dir: Path) -> dict[str, Any]:
    """Run non-destructive pilot evaluation on input_dir and return metrics dictionary."""
    if not input_dir.exists() or not input_dir.is_dir():
        raise ValueError(f"Input directory does not exist: {input_dir}")
    hashes_before = {
        p: compute_sha256(p) for p in discover_files_bounded([input_dir]) if p.is_file()
    }
    db = DatabaseManager(":memory:")
    repo = Repository(db)
    imp_report = ImportService(repo).import_paths([input_dir], source_type=SourceType.VINYL)
    file_ids = [f.id for f in imp_report.imported_files]

    JobOrchestrator(
        repository=repo,
        fingerprint_provider=MockFingerprintAdapter(),
        catalog_providers=[MockCatalogProviderAdapter()],
        audio_analyzer=LocalDspAnalyzer(),
    ).process_files(file_ids)
    reconciler = ReconciliationService(repo)
    for fid in file_ids:
        reconciler.reconcile_file(fid)

    metrics = {
        "input_dir": str(input_dir),
        "total_imported_files": len(file_ids),
        "damaged_files": len(imp_report.damaged_files),
        "unsupported_files": len(imp_report.unsupported_files),
        "identified_files": sum(1 for fid in file_ids if repo.list_candidate_matches(fid)),
        "analyzed_files": sum(1 for fid in file_ids if repo.list_analysis_results(fid)),
        "zero_unintended_file_mutations": all(
            compute_sha256(p) == h for p, h in hashes_before.items()
        ),
        "FieldName_BPM": FieldName.BPM.value,
    }
    db.close()
    return metrics


def open_settings_dialog(window: MainWindow, repo: Repository) -> None:
    """Show Settings, then run the cleanup when the person confirmed one there."""
    dialog = SettingsDialog(
        SettingsManager(),
        CredentialStore(),
        parent=window,
        # Deleting the data under a running or waiting batch would pull files from it.
        cleanup_enabled=not window.batch_queue.is_busy(),
    )
    dialog.exec()
    if dialog.cleanup_choice is not None:
        delete_data_and_quit(window, repo, dialog.cleanup_choice)


def delete_data_and_quit(window: MainWindow, repo: Repository, choice: CleanupChoice) -> None:
    """Close the window and the database, delete the chosen items, report, and quit (D295).

    The window closes first, so an import prompt can still stop the cleanup. The
    database closes before the delete, since Windows cannot remove an open file. Qt
    must not quit on the last window yet, or the result message would close with it.
    """
    app = QApplication.instance()
    if app is not None:
        app.setQuitOnLastWindowClosed(False)
    if not window.close():
        if app is not None:
            app.setQuitOnLastWindowClosed(True)
        build_cleanup_blocked_box().exec()
        return
    # From here the window and database are gone, so the app must quit even when
    # an unexpected error interrupts the cleanup or its message.
    try:
        repo.db.close()
        report = run_cleanup(choice, storage_paths.current())
        build_cleanup_result_box(report).exec()
    finally:
        if app is not None:
            app.quit()


def stop_on_first_failure() -> bool:
    """Read the saved setting that ends an Analyze or write batch at its first failed file."""
    return SettingsManager().load().stop_batch_on_first_failure


def build_analyze_runner(
    orchestrator: JobOrchestrator, reconciler: ReconciliationService
) -> AnalyzeRunner:
    """Return the body of a queued Identify & Analyze batch.

    Each file is processed and then reconciled into field proposals. The setting is
    read when the batch starts, so a change applies to batches that start later.
    """

    def run_analyze_batch(
        file_ids: list[str], should_cancel: CancelCheck, progress: ProgressReport
    ) -> object:
        return orchestrator.process_files_continuing(
            file_ids,
            post_process=reconciler.reconcile_file,
            should_cancel=should_cancel,
            stop_on_failure=stop_on_first_failure(),
            progress=progress,
        )

    return run_analyze_batch


def queue_safe_writes(
    window: MainWindow, write_svc: WriteService, plans: list[FileWritePlan]
) -> None:
    """Add the previewed safe plans to the batch queue as one write batch."""

    def run_write_batch(should_cancel: CancelCheck, progress: ProgressReport) -> object:
        return write_svc.apply_batch_continuing(
            plans,
            should_cancel=should_cancel,
            stop_on_failure=stop_on_first_failure(),
            progress=progress,
        )

    window.queue_batch(BatchKind.WRITE, f"Apply writes to {len(plans)} file(s)", run_write_batch)


def launch_desktop_app() -> int:
    """Launch the PySide6 desktop GUI application."""
    # Qt reads the logging rules when QApplication starts.
    silence_ffmpeg_logs()
    qt_app = QApplication.instance() or QApplication(sys.argv)
    # The storage prompts need Qt, and the database needs the chosen folder.
    opened = open_storage(
        Path(sys.executable),
        frozen=bool(getattr(sys, "frozen", False)),
        confirm_fallback=confirm_storage_fallback,
        show_error=show_storage_error,
        appimage=appimage_launch_from_environment(os.environ),
    )
    if isinstance(opened, int):
        return opened
    repo = opened.repository
    write_svc = WriteService(repo, backup_dir=opened.locations.backup_dir)
    orchestrator = JobOrchestrator(repository=repo, audio_analyzer=LocalDspAnalyzer())
    reconciler = ReconciliationService(repo)
    transcoder = SafeTranscoderAdapter(repo, backup_dir=opened.locations.backup_dir)
    exporter = PlaylistExporter(repo)
    saved_theme = SettingsManager().load().theme
    apply_application_theme(qt_app, saved_theme)

    window = MainWindow(
        repository=repo,
        import_service=ImportService(repo),
        review_service=ReviewService(repo),
        analyze_runner=build_analyze_runner(orchestrator, reconciler),
        on_open_write_dialog=lambda fids: WritePlanDialog(
            write_svc,
            fids,
            parent=window,
            submit_plans=lambda plans: queue_safe_writes(window, write_svc, plans),
        ).exec(),
        on_open_journal_dialog=lambda: JournalDialog(repo, write_svc, parent=window).exec(),
        on_open_settings_dialog=lambda: open_settings_dialog(window, repo),
        on_transcode_batch=lambda fids: run_transcode_with_confirmation(
            window, repo, transcoder, fids, SettingsManager()
        ),
        on_export_playlist=lambda fids, dest: export_playlist_with_feedback(
            window,
            exporter,
            fids,
            dest,
            relative_paths=SettingsManager().load().playlist_relative_paths,
        ),
        enable_audio_backend=True,
    )
    window.show()
    if write_svc.list_interrupted_operations():
        RecoveryDialog(write_svc, parent=window).exec()
    return int(qt_app.exec())


def main(argv: list[str] | None = None) -> int:
    """CLI and desktop entrypoint for infiniteprep."""
    args = sys.argv[1:] if argv is None else argv
    if args == [FORGET_CREDENTIALS_FLAG]:
        return forget_all_credentials()
    if args and args[0] == SMOKE_CHECK_FLAG:
        # A hidden flag for CI. The windowed Windows build has no stdout, so the
        # result goes to a file.
        if len(args) != SMOKE_CHECK_ARG_COUNT:
            print("Usage: --smoke-check <output-file>. Pass one output path.", file=sys.stderr)
            return SMOKE_CHECK_USAGE_ERROR
        output_path = Path(args[1])
        if output_path.exists():
            print("The smoke-check output path exists. Pass a new file path.", file=sys.stderr)
            return SMOKE_CHECK_USAGE_ERROR
        return run_smoke_check(output_path)
    if args and args[0] == "evaluate-sample":
        parser = argparse.ArgumentParser(prog="infiniteprep evaluate-sample")
        parser.add_argument("--input-dir", required=True, help="Directory of pilot audio files.")
        parser.add_argument("--output-json", required=False, help="Optional JSON report path.")
        parsed = parser.parse_args(args[1:])
        metrics = run_evaluation_sample(Path(parsed.input_dir))
        formatted = json.dumps(metrics, indent=2, ensure_ascii=True)
        if parsed.output_json:
            out_path = Path(parsed.output_json)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(formatted, encoding="utf-8")
        return 0
    return launch_desktop_app()


if __name__ == "__main__":
    raise SystemExit(main())
