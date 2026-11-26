"""Focused coverage for worker cancellation, theme loading, and launch gaps."""

from __future__ import annotations

import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from PySide6.QtCore import Qt

from infiniteprep import main as main_module
from infiniteprep.config.settings import AppSettings
from infiniteprep.services.usb.recovery import UsbExpiryScanResult
from infiniteprep.ui import theme as theme_module
from infiniteprep.ui import waveform_worker as worker_module
from infiniteprep.ui.theme import (
    DEFAULT_DARK_THEME,
    DEFAULT_LIGHT_THEME,
    NATIVE_THEME,
    SYSTEM_THEME,
    ThemeApplication,
)
from infiniteprep.ui.waveform_worker import WaveformAnalysisPool, _AnalysisTask
from tests.fixtures.call_recorder import CallRecorder


def _make_task(cancel_event: threading.Event) -> _AnalysisTask:
    """Return an analysis task with a controllable cancellation event."""
    return _AnalysisTask("track", 1, Path("track.wav"), cancel_event)


def test_cancelled_analysis_suppresses_failure_and(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancellation during analysis suppresses failure and later detection."""
    cancel_event = threading.Event()

    def cancel_analysis(_path: Path, *, cancel_event: threading.Event) -> str:
        cancel_event.set()
        raise RuntimeError("private details")

    def unexpected_detection(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("Cancellation must stop fake-lossless detection.")

    monkeypatch.setattr(
        worker_module,
        "extract_waveform_envelope",
        lambda _path, *, cancel_event: "overview",
    )
    monkeypatch.setattr(worker_module, "analyze_waveform", cancel_analysis)
    monkeypatch.setattr(worker_module, "detect_fake_lossless", unexpected_detection)
    task = _make_task(cancel_event)
    failures = CallRecorder()
    fake_failures = CallRecorder()
    task.signals.failed.connect(failures)
    task.signals.fake_lossless_failed.connect(fake_failures)

    task.run()

    assert failures == []
    assert fake_failures == []


def test_cancelled_analysis_suppresses_completion_and(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancellation after analysis suppresses completion and later detection."""
    cancel_event = threading.Event()

    def cancel_analysis(_path: Path, *, cancel_event: threading.Event) -> str:
        cancel_event.set()
        return "waveform"

    def unexpected_detection(*_args: object, **_kwargs: object) -> str:
        raise AssertionError("Cancellation must stop fake-lossless detection.")

    monkeypatch.setattr(
        worker_module,
        "extract_waveform_envelope",
        lambda _path, *, cancel_event: "overview",
    )
    monkeypatch.setattr(worker_module, "analyze_waveform", cancel_analysis)
    monkeypatch.setattr(worker_module, "detect_fake_lossless", unexpected_detection)
    task = _make_task(cancel_event)
    completions = CallRecorder()
    fake_results = CallRecorder()
    task.signals.completed.connect(completions)
    task.signals.fake_lossless_ready.connect(fake_results)

    task.run()

    assert completions == []
    assert fake_results == []


def test_cancelled_detection_failure_suppresses_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancellation during fake-lossless decoding suppresses its failure signal."""
    cancel_event = threading.Event()

    def cancel_detection(_path: Path, *, method: str | None, cancel_event: threading.Event) -> str:
        cancel_event.set()
        raise OSError("private path")

    monkeypatch.setattr(
        worker_module,
        "extract_waveform_envelope",
        lambda _path, *, cancel_event: "overview",
    )
    monkeypatch.setattr(
        worker_module,
        "analyze_waveform",
        lambda _path, *, cancel_event: "waveform",
    )
    monkeypatch.setattr(worker_module, "detect_fake_lossless", cancel_detection)
    task = _make_task(cancel_event)
    failures = CallRecorder()
    task.signals.fake_lossless_failed.connect(failures)

    task.run()

    assert failures == []


def test_cancelled_detection_result_suppresses_ready(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancellation during fake-lossless decoding suppresses its result."""
    cancel_event = threading.Event()

    def cancel_detection(_path: Path, *, method: str | None, cancel_event: threading.Event) -> str:
        cancel_event.set()
        return "fake-result"

    monkeypatch.setattr(
        worker_module,
        "extract_waveform_envelope",
        lambda _path, *, cancel_event: "overview",
    )
    monkeypatch.setattr(
        worker_module,
        "analyze_waveform",
        lambda _path, *, cancel_event: "waveform",
    )
    monkeypatch.setattr(worker_module, "detect_fake_lossless", cancel_detection)
    task = _make_task(cancel_event)
    results = CallRecorder()
    task.signals.fake_lossless_ready.connect(results)

    task.run()

    assert results == []


def test_pool_forwards_current_signals_and() -> None:
    """Every stale signal type stops at the pool boundary."""
    pool = WaveformAnalysisPool()
    events = CallRecorder()
    for signal_name in (
        "overview_ready",
        "completed",
        "fake_lossless_ready",
        "fake_lossless_failed",
        "failed",
    ):
        getattr(pool, signal_name).connect(events)
    pool._active_requests.add(("current", 1))

    pool._forward_overview_ready("current", 1, "overview")
    pool._forward_completed("current", 1, "waveform")
    pool._forward_fake_lossless_ready("current", 1, "fake-result")
    pool._forward_fake_lossless_failed("current", 1, "fake failure")
    pool._forward_failed("current", 1, "waveform failure")
    pool._forward_overview_ready("stale", 1, "overview")
    pool._forward_completed("stale", 1, "waveform")
    pool._forward_fake_lossless_ready("stale", 1, "fake-result")
    pool._forward_fake_lossless_failed("stale", 1, "fake failure")
    pool._forward_failed("stale", 1, "waveform failure")

    assert len(events) == 5
    pool.shutdown()


@pytest.mark.parametrize(
    ("scheme", "expected"),
    [(Qt.ColorScheme.Light, DEFAULT_LIGHT_THEME), (Qt.ColorScheme.Dark, DEFAULT_DARK_THEME)],
)
def test_system_theme_uses_the_host(
    monkeypatch: pytest.MonkeyPatch,
    scheme: Qt.ColorScheme,
    expected: str,
) -> None:
    """The system choice follows the host color scheme without an internal stub."""
    app = SimpleNamespace(styleHints=lambda: SimpleNamespace(colorScheme=lambda: scheme))
    monkeypatch.setattr(
        theme_module,
        "QGuiApplication",
        SimpleNamespace(instance=lambda: app),
    )

    assert theme_module.resolve_theme(SYSTEM_THEME) == expected
    assert theme_module.resolve_theme(None) == expected


def test_failed_module_loading_leaves_the(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An import failure during theme loading leaves the native theme in effect."""
    app = MagicMock()

    def fail_import(_name: str) -> None:
        raise ImportError("qt-material is missing")

    monkeypatch.setattr(theme_module.importlib, "import_module", fail_import)

    result = theme_module.apply_application_theme(app, DEFAULT_DARK_THEME)

    assert result.applied is False
    assert result.theme == NATIVE_THEME
    assert "cannot be imported" in result.reason
    app.setStyleSheet.assert_called_once_with("")


def test_cli_falls_back_to_desktop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unknown command reaches desktop launch instead of a command parser."""
    app = MagicMock()
    open_storage = MagicMock(return_value=12)
    monkeypatch.setattr(main_module, "silence_ffmpeg_logs", lambda: None)
    monkeypatch.setattr(
        main_module,
        "QApplication",
        SimpleNamespace(instance=lambda: app),
    )
    monkeypatch.setattr(main_module, "apply_application_icon", lambda _app: None)
    monkeypatch.setattr(main_module, "open_storage", open_storage)
    monkeypatch.setattr(main_module, "appimage_launch_from_environment", lambda _env: None)

    result = main_module.main(["--unknown"])

    assert result == 12
    open_storage.assert_called_once()


def test_desktop_starts_recovery_before_the(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """Desktop launch opens the recovery dialog before it starts the Qt event loop."""
    events: list[str] = []
    app = MagicMock()

    def run_app() -> int:
        events.append("app")
        return 9

    app.exec.side_effect = run_app
    window = MagicMock()
    window.show.side_effect = lambda: events.append("show")
    writer = MagicMock()
    writer.list_interrupted_operations.return_value = [object()]
    recovery_box = MagicMock()
    recovery_box.exec.side_effect = lambda: events.append("recovery")
    settings_manager = MagicMock()
    settings_manager.load.return_value = AppSettings()
    settings_manager.load_tag_output.return_value = object()
    journal_store = MagicMock()
    journal_store.evaluate_expiry.return_value = UsbExpiryScanResult((), (), ())
    opened = SimpleNamespace(
        repository=MagicMock(),
        locations=SimpleNamespace(backup_dir=tmp_path / "backups"),
    )
    recovery_dialog = MagicMock(return_value=recovery_box)

    monkeypatch.setattr(main_module, "silence_ffmpeg_logs", lambda: None)
    monkeypatch.setattr(
        main_module,
        "QApplication",
        SimpleNamespace(instance=lambda: app),
    )
    monkeypatch.setattr(main_module, "apply_application_icon", lambda _app: None)
    monkeypatch.setattr(main_module, "open_storage", MagicMock(return_value=opened))
    monkeypatch.setattr(main_module, "appimage_launch_from_environment", lambda _env: None)
    monkeypatch.setattr(
        main_module,
        "SettingsManager",
        MagicMock(return_value=settings_manager),
    )
    monkeypatch.setattr(main_module, "MetadataTagAdapter", MagicMock())
    monkeypatch.setattr(main_module, "WriteService", MagicMock(return_value=writer))
    monkeypatch.setattr(main_module, "ReconciliationService", MagicMock())
    monkeypatch.setattr(main_module, "SafeTranscoderAdapter", MagicMock())
    monkeypatch.setattr(main_module, "PlaylistExporter", MagicMock())
    monkeypatch.setattr(main_module, "ImportService", MagicMock())
    monkeypatch.setattr(main_module, "ReviewService", MagicMock())
    monkeypatch.setattr(main_module, "MainWindow", MagicMock(return_value=window))
    monkeypatch.setattr(main_module, "RecoveryDialog", recovery_dialog)
    monkeypatch.setattr(
        main_module,
        "UsbRecoveryJournalStore",
        MagicMock(return_value=journal_store),
    )
    monkeypatch.setattr(
        main_module,
        "switch_application_theme",
        lambda _app, theme, _current, _size: ThemeApplication(True, theme),
    )

    result = main_module.launch_desktop_app()

    assert result == 9
    assert events == ["show", "recovery", "app"]
    recovery_dialog.assert_called_once_with(writer, parent=window)
    recovery_box.exec.assert_called_once_with()
