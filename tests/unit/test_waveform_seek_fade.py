"""Exercise waveform seeks with a real Qt Multimedia player."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from pytestqt.qtbot import QtBot

from infiniteprep.config.settings import SettingsManager
from infiniteprep.ui.audio_player import MS_PER_SECOND, ZOOM_MODE, AudioPreviewWidget
from tests.fixtures.audio_factory import create_wav_fixture

TRACK_SECONDS = 30.0
WINDOW_WIDTH = 640
WINDOW_HEIGHT = 180
OUTPUT_VOLUME = 0.6
CLICK_FRACTION = 0.75
DRAG_FRACTIONS = (0.55, 0.65, 0.75)
RELEASE_FRACTION = 0.85
OSCILLATION_CHECK_MS = 220


@pytest.fixture
def playing_preview(
    qtbot: QtBot, tmp_path: Path
) -> tuple[AudioPreviewWidget, QMediaPlayer, QAudioOutput]:
    """Load a seekable source with muted audio and an observable gain setting."""
    widget = AudioPreviewWidget(
        enable_multimedia_backend=True, settings_manager=SettingsManager(tmp_path / "settings.json")
    )
    qtbot.addWidget(widget)
    widget.resize(WINDOW_WIDTH, WINDOW_HEIGHT)
    widget.show()
    player, output = widget.player, widget.audio_output
    assert player is not None and output is not None
    output.setMuted(True)
    output.setVolume(OUTPUT_VOLUME)
    widget.load_file(create_wav_fixture(tmp_path / "track.wav", duration_sec=TRACK_SECONDS))
    qtbot.waitUntil(player.isSeekable)
    widget.start_playback()
    qtbot.waitUntil(lambda: widget.is_playing)
    yield widget, player, output
    widget.stop_playback()


def locate_waveform_point(widget: AudioPreviewWidget, fraction: float) -> QPoint:
    """Return a point in the waveform's coordinate space."""
    canvas = widget.waveform_bar
    return QPoint(int(canvas.width() * fraction), canvas.height() // 2)


def test_waveform_click_seeks_at_silence(qtbot: QtBot, playing_preview: tuple) -> None:
    """The track jumps once at zero gain and returns to its previous volume."""
    widget, player, output = playing_preview
    canvas = widget.waveform_bar
    point = locate_waveform_point(widget, CLICK_FRACTION)
    target_sec = point.x() / canvas.width() * widget.duration_sec
    target_ms = int(target_sec * MS_PER_SECOND)
    gains_at_seek = []
    player.positionChanged.connect(
        lambda position: gains_at_seek.append(output.volume()) if position == target_ms else None
    )

    qtbot.mouseClick(canvas, Qt.MouseButton.LeftButton, pos=point)

    assert widget.current_position_sec == pytest.approx(target_sec)
    assert player.position() < target_ms
    qtbot.waitUntil(lambda: gains_at_seek == [0.0])
    qtbot.waitUntil(lambda: output.volume() == pytest.approx(OUTPUT_VOLUME))
    assert output.isMuted()


def test_zoom_drag_coalesces_latest_position(qtbot: QtBot, playing_preview: tuple) -> None:
    """A burst of drag events lands at the release point in the original zoom window."""
    widget, player, output = playing_preview
    widget.seek_to_seconds(TRACK_SECONDS / 2)
    widget.view_combo.setCurrentText(ZOOM_MODE)
    canvas = widget.waveform_bar
    start, end = canvas.zoom_window()
    release = locate_waveform_point(widget, RELEASE_FRACTION)
    target_sec = start + release.x() / canvas.width() * (end - start)
    target_ms = int(target_sec * MS_PER_SECOND)
    positions = []
    player.positionChanged.connect(positions.append)

    qtbot.mousePress(canvas, Qt.MouseButton.LeftButton, pos=locate_waveform_point(widget, 0.5))
    for fraction in DRAG_FRACTIONS:
        qtbot.mouseMove(canvas, locate_waveform_point(widget, fraction))
    qtbot.mouseRelease(canvas, Qt.MouseButton.LeftButton, pos=release)

    assert canvas.playhead_ratio == 0.5
    assert widget.current_position_sec == pytest.approx(target_sec)
    assert positions == []
    qtbot.waitUntil(lambda: target_ms in positions)
    qtbot.waitUntil(lambda: output.volume() == pytest.approx(OUTPUT_VOLUME))
    assert positions.count(target_ms) == 1


def test_cue_cancels_pending_waveform_seek(qtbot: QtBot, playing_preview: tuple) -> None:
    """A cue jump takes priority over a pending waveform request."""
    widget, player, output = playing_preview
    point = locate_waveform_point(widget, CLICK_FRACTION)
    positions = []
    player.positionChanged.connect(positions.append)
    qtbot.mouseClick(widget.waveform_bar, Qt.MouseButton.LeftButton, pos=point)

    cue = widget.jump_to_cue("25%")

    assert player.position() == int(cue * MS_PER_SECOND)
    assert output.volume() == pytest.approx(OUTPUT_VOLUME)
    qtbot.wait(OSCILLATION_CHECK_MS)
    assert all(position < TRACK_SECONDS * CLICK_FRACTION * MS_PER_SECOND for position in positions)


def test_stop_restores_gain_during_fade(qtbot: QtBot, playing_preview: tuple) -> None:
    """Stopping during a seek fade restores the original output level and resets playback."""
    widget, player, output = playing_preview
    point = locate_waveform_point(widget, CLICK_FRACTION)
    qtbot.mouseClick(widget.waveform_bar, Qt.MouseButton.LeftButton, pos=point)
    qtbot.waitUntil(lambda: output.volume() < OUTPUT_VOLUME / 2)

    widget.stop_playback()

    assert output.volume() == pytest.approx(OUTPUT_VOLUME)
    qtbot.wait(OSCILLATION_CHECK_MS)
    assert player.position() == 0
    assert output.volume() == pytest.approx(OUTPUT_VOLUME)
