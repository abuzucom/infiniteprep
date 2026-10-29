"""Application entrypoint for InfinitePrep desktop GUI and CLI evaluation harness."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import platformdirs
from PySide6.QtWidgets import QApplication

from infiniteprep.adapters.analyzers.local_dsp import LocalDspAnalyzer
from infiniteprep.adapters.catalog.mock_catalog import MockCatalogProviderAdapter
from infiniteprep.adapters.fingerprint.mock_fp import MockFingerprintAdapter
from infiniteprep.adapters.metadata_io.mutagen_adapter import compute_sha256
from infiniteprep.adapters.transcoder.safe_transcoder import SafeTranscoderAdapter
from infiniteprep.config.credentials import CredentialStore
from infiniteprep.config.settings import SettingsManager
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName, SourceType
from infiniteprep.services.import_service import ImportService, discover_files_bounded
from infiniteprep.services.job_orchestrator import JobOrchestrator
from infiniteprep.services.playlist_exporter import PlaylistExporter
from infiniteprep.services.reconciliation import ReconciliationService
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.write_service import WriteService
from infiniteprep.ui.action_handlers import (
    export_playlist_with_feedback,
    run_transcode_with_confirmation,
)
from infiniteprep.ui.journal_dialog import JournalDialog
from infiniteprep.ui.main_window import MainWindow
from infiniteprep.ui.settings_dialog import SettingsDialog
from infiniteprep.ui.write_plan_dialog import WritePlanDialog


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


def launch_desktop_app() -> int:
    """Launch the PySide6 desktop GUI application."""
    app_dir = Path(platformdirs.user_data_dir("InfinitePrep", "InfinitePrep"))
    app_dir.mkdir(parents=True, exist_ok=True)
    repo = Repository(DatabaseManager(app_dir / "infiniteprep.sqlite3"))
    write_svc = WriteService(repo, backup_dir=app_dir / "backups")
    orchestrator = JobOrchestrator(repository=repo, audio_analyzer=LocalDspAnalyzer())
    reconciler = ReconciliationService(repo)
    transcoder = SafeTranscoderAdapter(repo, backup_dir=app_dir / "backups")
    exporter = PlaylistExporter(repo)
    qt_app = QApplication.instance() or QApplication(sys.argv)

    def handle_analyze_batch(file_ids: list[str]) -> None:
        orchestrator.process_files(file_ids)
        for fid in file_ids:
            reconciler.reconcile_file(fid)

    window = MainWindow(
        repository=repo,
        import_service=ImportService(repo),
        review_service=ReviewService(repo),
        on_analyze_batch=handle_analyze_batch,
        on_open_write_dialog=lambda fids: WritePlanDialog(write_svc, fids, parent=window).exec(),
        on_open_journal_dialog=lambda: JournalDialog(repo, write_svc, parent=window).exec(),
        on_open_settings_dialog=lambda: SettingsDialog(
            SettingsManager(), CredentialStore(), parent=window
        ).exec(),
        on_transcode_batch=lambda fids: run_transcode_with_confirmation(
            window, repo, transcoder, fids, SettingsManager()
        ),
        on_export_playlist=lambda fids, dest: export_playlist_with_feedback(
            window, exporter, fids, dest
        ),
    )
    window.show()
    return int(qt_app.exec())


def main(argv: list[str] | None = None) -> int:
    """CLI and desktop entrypoint for infiniteprep."""
    args = sys.argv[1:] if argv is None else argv
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
