"""Behavioral coverage for the application entrypoint and its external seams."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QMessageBox

from infiniteprep import main as main_module
from infiniteprep.config.settings import AppSettings
from infiniteprep.data_cleanup import CleanupChoice, CleanupReport
from infiniteprep.domain.ports import FileWritePlan
from infiniteprep.services.job_orchestrator import BatchKind
from infiniteprep.services.usb.recovery import UsbExpiryScanResult
from infiniteprep.ui.theme import NATIVE_THEME, ThemeApplication


class FakeApplication:
    def __init__(self, exit_code: int = 0) -> None:
        self.exit_code = exit_code
        self.quit_on_last_window_closed = True
        self.quit_calls = 0

    def setQuitOnLastWindowClosed(self, enabled: bool) -> None:
        self.quit_on_last_window_closed = enabled

    def quit(self) -> None:
        self.quit_calls += 1

    def exec(self) -> int:
        return self.exit_code


class FakeWindow:
    def __init__(self, *, busy: bool = False, close_result: bool = True) -> None:
        self.batch_queue = SimpleNamespace(is_busy=lambda: busy)
        self.close_result = close_result
        self.reload_calls = 0

    def close(self) -> bool:
        return self.close_result

    def reload_settings(self) -> None:
        self.reload_calls += 1


class FakeRepository:
    def __init__(self) -> None:
        self.db = MagicMock()

    def mark_analysis_results_stale(self, analyzer_name: str, current_version: str) -> int:
        """Record the request that flags the results of an older analyzer version."""
        return 0


@pytest.mark.parametrize(
    ("busy", "cleanup_enabled", "dialog_result", "reload_count"),
    [(True, False, QDialog.DialogCode.Accepted, 1), (False, True, QDialog.DialogCode.Rejected, 0)],
)
def test_settings_dialog_reflects_busy_state(
    monkeypatch: pytest.MonkeyPatch,
    busy: bool,
    cleanup_enabled: bool,
    dialog_result: QDialog.DialogCode,
    reload_count: int,
) -> None:
    """The dialog disables cleanup during a batch and accepted settings reload the window."""
    records: list[SimpleNamespace] = []

    class FakeSettingsDialog:
        def __init__(self, manager: object, credentials: object, **kwargs: object) -> None:
            records.append(SimpleNamespace(manager=manager, credentials=credentials, **kwargs))
            self.cleanup_choice = None

        def exec(self) -> None:
            return None

        def result(self) -> QDialog.DialogCode:
            return dialog_result

    monkeypatch.setattr(main_module, "SettingsDialog", FakeSettingsDialog)
    monkeypatch.setattr(main_module, "SettingsManager", object)
    monkeypatch.setattr(main_module, "CredentialStore", object)
    window = FakeWindow(busy=busy)

    main_module.open_settings_dialog(window, FakeRepository())

    assert records[0].cleanup_enabled is cleanup_enabled
    assert records[0].parent is window
    assert window.reload_calls == reload_count


def test_settings_dialog_runs_the_selected(monkeypatch: pytest.MonkeyPatch) -> None:
    """A cleanup choice closes storage, removes selected data, reports it, and quits."""
    choice = CleanupChoice(delete_data=True, delete_keys=False)
    cleanup_calls: list[tuple[CleanupChoice, object]] = []

    dialog = MagicMock()
    dialog.cleanup_choice = choice
    dialog.result.return_value = QDialog.DialogCode.Rejected

    def run_fake_cleanup(active_choice: CleanupChoice, locations: object) -> CleanupReport:
        cleanup_calls.append((active_choice, locations))
        return CleanupReport()

    app = FakeApplication()
    locations = object()
    monkeypatch.setattr(main_module, "SettingsDialog", MagicMock(return_value=dialog))
    monkeypatch.setattr(main_module, "SettingsManager", object)
    monkeypatch.setattr(main_module, "CredentialStore", object)
    monkeypatch.setattr(main_module, "QApplication", SimpleNamespace(instance=lambda: app))
    monkeypatch.setattr(main_module, "run_cleanup", run_fake_cleanup)
    monkeypatch.setattr(main_module, "build_cleanup_result_box", lambda _report: MagicMock())
    monkeypatch.setattr(main_module.storage_paths, "current", lambda: locations)
    window = FakeWindow()
    repo = FakeRepository()

    main_module.open_settings_dialog(window, repo)

    assert cleanup_calls == [(choice, locations)]
    repo.db.close.assert_called_once_with()
    assert app.quit_calls == 1


def test_cleanup_is_blocked_when_the(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A refused close preserves the database and reports that cleanup is blocked."""
    app = FakeApplication()
    blocked_box = MagicMock()
    repo = FakeRepository()
    window = FakeWindow(close_result=False)
    monkeypatch.setattr(main_module, "QApplication", SimpleNamespace(instance=lambda: app))
    monkeypatch.setattr(main_module, "build_cleanup_blocked_box", lambda: blocked_box)

    main_module.delete_data_and_quit(window, repo, CleanupChoice(True, False))

    repo.db.close.assert_not_called()
    blocked_box.exec.assert_called_once_with()
    assert app.quit_on_last_window_closed is True
    assert app.quit_calls == 0


def test_analyze_runner_body_passes_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The queued Analyze body reconciles each file and reads the stop policy at start."""
    calls: list[dict[str, Any]] = []

    def process_files(file_ids: list[str], **kwargs: Any) -> str:
        calls.append({"file_ids": file_ids, **kwargs})
        return "analyze-result"

    def reconcile_file(_file_id: str) -> None:
        return None

    orchestrator = SimpleNamespace(process_files_continuing=process_files)
    reconciler = SimpleNamespace(reconcile_file=reconcile_file)
    settings_manager = SimpleNamespace(load=lambda: AppSettings(stop_batch_on_first_failure=True))
    monkeypatch.setattr(main_module, "SettingsManager", lambda: settings_manager)
    runner = main_module.build_analyze_runner(orchestrator, reconciler)
    cancel = MagicMock(return_value=False)
    progress = object()

    result = runner(["file-1"], cancel, progress)

    assert result == "analyze-result"
    assert calls[0]["file_ids"] == ["file-1"]
    assert calls[0]["post_process"] == reconcile_file
    assert calls[0]["should_cancel"] is cancel
    assert calls[0]["stop_on_failure"] is True
    assert calls[0]["progress"] is progress


def test_write_runner_body_passes_plans(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The queued write body applies every previewed plan with the current stop policy."""
    calls: list[dict[str, Any]] = []

    def apply_batch(plans: list[FileWritePlan], **kwargs: Any) -> str:
        calls.append({"plans": plans, **kwargs})
        return "write-result"

    queued: list[tuple[BatchKind, str, Any]] = []

    def queue_batch(kind: BatchKind, label: str, runner: Any) -> None:
        queued.append((kind, label, runner))

    write_service = SimpleNamespace(apply_batch_continuing=apply_batch)
    window = SimpleNamespace(queue_batch=queue_batch)
    settings_manager = SimpleNamespace(load=lambda: AppSettings(stop_batch_on_first_failure=False))
    monkeypatch.setattr(main_module, "SettingsManager", lambda: settings_manager)
    plans: list[FileWritePlan] = []

    main_module.queue_safe_writes(window, write_service, plans)
    assert len(queued) == 1
    kind, label, runner = queued[0]
    cancel = MagicMock(return_value=False)
    progress = object()

    result = runner(cancel, progress)

    assert kind is BatchKind.WRITE
    assert label == "Apply writes to 0 file(s)"
    assert result == "write-result"
    assert calls[0]["plans"] is plans
    assert calls[0]["should_cancel"] is cancel
    assert calls[0]["stop_on_failure"] is False
    assert calls[0]["progress"] is progress


def test_theme_fallback_is_saved(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed theme switch stores the theme that remains in effect."""
    settings = AppSettings(theme="requested-theme")

    saved: list[AppSettings] = []
    manager = SimpleNamespace(load=lambda: settings, save=saved.append)
    monkeypatch.setattr(main_module, "SettingsManager", lambda: manager)
    monkeypatch.setattr(
        main_module,
        "switch_application_theme",
        lambda _app, _theme, _current, _size: ThemeApplication(False, NATIVE_THEME),
    )

    main_module.apply_saved_theme(FakeApplication())

    assert settings.theme == NATIVE_THEME
    assert saved == [settings]


def test_usb_notice_wrapper_uses_plain(monkeypatch: pytest.MonkeyPatch) -> None:
    """The USB notice wrapper sets an information icon and preserves message text."""
    box = MagicMock()
    message_box_type = MagicMock(return_value=box)
    message_box_type.Icon = QMessageBox.Icon
    monkeypatch.setattr(main_module, "QMessageBox", message_box_type)

    main_module._show_usb_recovery_notice(None, "USB Notice", "<b>Body</b>")

    box.setWindowTitle.assert_called_once_with("USB Notice")
    box.setIcon.assert_called_once_with(QMessageBox.Icon.Information)
    box.setTextFormat.assert_called_once_with(Qt.TextFormat.PlainText)
    box.setText.assert_called_once_with("<b>Body</b>")
    box.exec.assert_called_once_with()


def test_cli_routes_hidden_flags(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """The credential and smoke flags route to their dedicated runners."""
    credential_calls = 0
    smoke_calls: list[Path] = []

    def forget_credentials() -> int:
        nonlocal credential_calls
        credential_calls += 1
        return 4

    def run_smoke(output_path: Path) -> int:
        smoke_calls.append(output_path)
        return 6

    monkeypatch.setattr(main_module, "forget_all_credentials", forget_credentials)
    monkeypatch.setattr(main_module, "run_smoke_check", run_smoke)
    output_path = tmp_path / "smoke.txt"

    assert main_module.main([main_module.FORGET_CREDENTIALS_FLAG]) == 4
    assert main_module.main([main_module.SMOKE_CHECK_FLAG, str(output_path)]) == 6
    assert credential_calls == 1
    assert smoke_calls == [output_path]


def test_cli_routes_evaluation_and_writes(tmp_path: Path) -> None:
    """The evaluation command runs the sample and writes its optional JSON report."""
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    output_path = tmp_path / "reports" / "result.json"

    result = main_module.main(
        ["evaluate-sample", "--input-dir", str(input_dir), "--output-json", str(output_path)]
    )

    assert result == 0
    metrics = json.loads(output_path.read_text(encoding="utf-8"))
    assert metrics["total_imported_files"] == 0
    assert metrics["zero_unintended_file_mutations"] is True


def test_desktop_launch_wires_its_services(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Desktop launch builds the window from injected services and starts the Qt loop."""
    app = FakeApplication(exit_code=7)
    repo = FakeRepository()
    backup_dir = tmp_path / "backups"
    opened = SimpleNamespace(
        repository=repo,
        locations=SimpleNamespace(backup_dir=backup_dir),
    )
    writer = MagicMock()
    writer.list_interrupted_operations.return_value = []
    window = MagicMock()
    settings_manager = MagicMock()
    settings_manager.load.return_value = AppSettings()
    journal_store = MagicMock()
    journal_store.evaluate_expiry.return_value = UsbExpiryScanResult((), (), ())

    def make_service(name: str) -> Any:
        return MagicMock(return_value=SimpleNamespace(service=name))

    monkeypatch.setattr(main_module, "silence_ffmpeg_logs", lambda: None)
    monkeypatch.setattr(main_module, "QApplication", SimpleNamespace(instance=lambda: app))
    monkeypatch.setattr(main_module, "apply_application_icon", lambda _app: None)
    monkeypatch.setattr(main_module, "open_storage", lambda *_args, **_kwargs: opened)
    monkeypatch.setattr(main_module, "appimage_launch_from_environment", lambda _env: None)
    monkeypatch.setattr(main_module, "SettingsManager", MagicMock(return_value=settings_manager))
    monkeypatch.setattr(main_module, "MetadataTagAdapter", make_service("metadata"))
    monkeypatch.setattr(main_module, "WriteService", MagicMock(return_value=writer))
    monkeypatch.setattr(main_module, "ReconciliationService", make_service("reconciler"))
    monkeypatch.setattr(main_module, "SafeTranscoderAdapter", make_service("transcoder"))
    monkeypatch.setattr(main_module, "PlaylistExporter", make_service("exporter"))
    monkeypatch.setattr(main_module, "ImportService", make_service("importer"))
    monkeypatch.setattr(main_module, "ReviewService", make_service("review"))
    monkeypatch.setattr(main_module, "MainWindow", MagicMock(return_value=window))
    monkeypatch.setattr(
        main_module, "UsbRecoveryJournalStore", MagicMock(return_value=journal_store)
    )
    monkeypatch.setattr(
        main_module,
        "switch_application_theme",
        lambda _app, theme, _current, _size: ThemeApplication(True, theme),
    )

    result = main_module.main([])

    assert result == 7
    window_kwargs = main_module.MainWindow.call_args.kwargs
    assert window_kwargs["repository"] is repo
    assert callable(window_kwargs["analyze_runner"])
    assert window_kwargs["enable_audio_backend"] is True
    window.show.assert_called_once_with()
