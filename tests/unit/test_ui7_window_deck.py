"""Exercise the player deck and pane sizing in a real main window."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtCore import QDir, QPoint, QRect
from PySide6.QtWidgets import QApplication
from qt_material.resources import generate

from infiniteprep.config.settings import TEXT_SIZE_COMPACT, TEXT_SIZE_LARGE, TEXT_SIZE_STANDARD
from infiniteprep.services.job_orchestrator import BatchKind
from infiniteprep.ui import detail_panel as detail_panel_module
from infiniteprep.ui import main_window as main_window_module
from infiniteprep.ui.main_window import MainWindow
from infiniteprep.ui.theme import DEFAULT_DARK_THEME, DEFAULT_LIGHT_THEME, apply_application_theme
from tests.fixtures.batch_jobs import GatedJobs
from tests.fixtures.memory_settings import MemorySettingsManager
from tests.unit.test_ui2_main_window_shell import build_window


@pytest.fixture
def window(qtbot: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> MainWindow:
    """Keep window settings in memory while using the real library and player widgets."""
    manager = MemorySettingsManager()
    monkeypatch.setattr(main_window_module, "SettingsManager", lambda: manager)
    monkeypatch.setattr(detail_panel_module, "SettingsManager", lambda: manager)
    main_window, _repository = build_window(qtbot, tmp_path, ("track.wav",))
    main_window.show()
    return main_window


@pytest.fixture
def theme_assets(
    qapp: QApplication, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[None]:
    """Apply the real theme with generated icons confined to the test directory."""
    monkeypatch.setattr(generate, "RESOURCES_PATH", str(tmp_path / "theme-assets"))
    paths = {prefix: QDir.searchPaths(prefix) for prefix in ("icon", "qt_material")}
    stylesheet, palette, style = qapp.styleSheet(), qapp.palette(), qapp.style().name()
    try:
        yield
    finally:
        for prefix, directories in paths.items():
            QDir.setSearchPaths(prefix, directories)
        qapp.setStyle(style)
        qapp.setPalette(palette)
        qapp.setStyleSheet(stylesheet)


@pytest.mark.parametrize(
    ("width", "height", "deck_height"),
    [(960, 600, 144), (1707, 960, 154), (1920, 1080, 173), (2560, 1440, 230), (3840, 2160, 288)],
)
def test_deck_stays_above_body(
    qtbot: Any, window: MainWindow, width: int, height: int, deck_height: int
) -> None:
    """The same player sits above the library at each target logical size."""
    window.resize(width, height)
    deck = window.detail_panel.audio_player
    qtbot.waitUntil(
        lambda: deck.height() == deck_height and deck.geometry().bottom() < window.body_splitter.y()
    )

    assert deck.parentWidget() is window.centralWidget()
    assert deck.width() == window.workflow_bar.width() == window.body_splitter.width()
    assert deck.y() > window.workflow_bar.geometry().bottom()
    assert window.workflow_bar.height() == window.workflow_bar.sizeHint().height()
    assert window.width() == width
    assert window.height() == height
    content = window.centralWidget().layout().contentsRect()
    assert window.body_splitter.geometry().bottom() == content.bottom()

    assert deck.deck_hint.isVisible()
    deck.play_btn.click()
    assert deck.current_file_path is None
    window.file_table.selectRow(0)
    deck.play_btn.click()
    assert deck.current_file_path is not None
    assert deck.height() == deck_height
    assert not deck.deck_hint.isVisible()


def test_large_panes_use_extra_width(qtbot: Any, window: MainWindow) -> None:
    """The large band gives both panes more width and leaves the remainder to the library."""
    window.resize(1679, 960)
    qtbot.waitUntil(lambda: abs(window.body_splitter.sizes()[0] - 200) <= 4)
    assert abs(window.detail_splitter.sizes()[1] - 480) <= 4

    window.resize(1680, 960)
    qtbot.waitUntil(lambda: window.body_splitter.sizes()[0] >= 235)
    assert window.detail_splitter.sizes()[1] >= 515

    window.resize(2560, 1440)
    qtbot.waitUntil(lambda: window.body_splitter.sizes()[0] >= 295)
    assert window.detail_splitter.sizes()[1] >= 775
    assert window.detail_splitter.sizes()[0] > 1400


def test_compact_deck_fits_controls(qtbot: Any, window: MainWindow) -> None:
    """At the minimum, waveform settings share a row with transport and cues sit below it."""
    window.resize(960, 600)
    deck = window.detail_panel.audio_player
    qtbot.waitUntil(lambda: deck.play_btn.y() > deck.position_slider.geometry().bottom())
    assert deck.deck_views.geometry().center().y() == deck.play_btn.geometry().center().y()
    cue = deck.cue_buttons["Intro"]
    assert cue.y() > deck.play_btn.geometry().bottom()
    controls = [deck.play_btn, deck.stop_btn, deck.view_combo, deck.zoom_combo]
    for control in [*controls, *deck.cue_buttons.values()]:
        bounds = QRect(control.mapTo(deck, QPoint()), control.size())
        assert deck.rect().contains(bounds)


@pytest.mark.parametrize(
    ("theme", "text_size", "deck_height"),
    [
        (DEFAULT_DARK_THEME, TEXT_SIZE_COMPACT, 131),
        (DEFAULT_DARK_THEME, TEXT_SIZE_STANDARD, 144),
        (DEFAULT_LIGHT_THEME, TEXT_SIZE_LARGE, 157),
    ],
)
def test_styled_deck_fits_minimum(
    qtbot: Any,
    qapp: QApplication,
    window: MainWindow,
    theme_assets: None,
    *,
    theme: str,
    text_size: str,
    deck_height: int,
) -> None:
    """The project theme and text preset fit the full deck at the minimum logical size."""
    manager = main_window_module.SettingsManager()
    manager.settings.text_size = text_size
    assert apply_application_theme(qapp, theme, text_size).applied
    window.reload_settings()
    window.resize(960, 600)
    deck = window.detail_panel.audio_player
    deck.view_combo.setCurrentText("Zoom")
    qtbot.waitUntil(lambda: deck.height() == deck_height)
    qtbot.waitUntil(lambda: deck.cue_buttons["Outro"].geometry().bottom() < deck.height())
    assert window.size().width() == 960
    assert window.size().height() == 600
    assert deck.layout().minimumSize().height() <= deck.height()
    assert deck.deck_transport.minimumSize().width() <= deck.width()
    assert window.file_table.horizontalScrollBar().maximum() == 0


@pytest.mark.parametrize(
    ("width", "height"), [(960, 600), (1920, 1080), (2560, 1440), (3840, 2160)]
)
def test_queue_keeps_deck_pinned(qtbot: Any, window: MainWindow, width: int, height: int) -> None:
    """Opening the queue replaces the strip and takes room from the library body."""
    window.resize(width, height)
    deck = window.detail_panel.audio_player
    qtbot.waitUntil(lambda: deck.geometry().bottom() < window.body_splitter.y())
    unchanged = deck.geometry()
    jobs = GatedJobs()
    try:
        window.queue_batch(BatchKind.ANALYZE, "Analyze tracks", jobs.make("analyze"))
        qtbot.waitUntil(window.batch_strip.isVisible)
        strip = window.batch_strip
        qtbot.waitUntil(lambda: strip.y() > window.body_splitter.geometry().bottom())
        assert strip.height() == strip.sizeHint().height()
        collapsed_body_height = window.body_splitter.height()
        strip.queue_button.click()
        qtbot.waitUntil(lambda: window.batch_dock.isVisible() and not strip.isVisible())
        qtbot.waitUntil(lambda: window.body_splitter.height() < collapsed_body_height)
        assert deck.geometry() == unchanged
        assert window.batch_dock.batch_list.height() <= (
            window.batch_dock.batch_list.sizeHintForRow(0)
            + 2 * window.batch_dock.batch_list.frameWidth()
        )
        for index in range(5):
            name = f"Queued batch {index}"
            window.queue_batch(BatchKind.ANALYZE, name, jobs.make(name))
        batch_list = window.batch_dock.batch_list
        cap = 2 if height == 600 else 5
        qtbot.waitUntil(lambda: batch_list.verticalScrollBar().maximum() > 0)
        assert batch_list.count() == 6
        assert batch_list.height() == (
            sum(batch_list.sizeHintForRow(row) for row in range(cap)) + 2 * batch_list.frameWidth()
        )
        assert deck.geometry() == unchanged
        window.batch_dock.close()
        qtbot.waitUntil(strip.isVisible)
    finally:
        jobs.gate.set()
        qtbot.waitUntil(lambda: not window.batch_queue.is_busy())
