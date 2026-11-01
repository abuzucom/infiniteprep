"""Needle-drop audio preview widget with waveform overview and cue-jump buttons."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

import numpy as np
import soundfile as sf
from PySide6.QtCore import QSignalBlocker, Qt, QUrl
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

DEFAULT_ENVELOPE_BINS: Final[int] = 120
MAX_PREVIEW_FILE_BYTES: Final[int] = 524_288_000  # 500 MiB
MAX_PREVIEW_DURATION_SEC: Final[float] = 1800.0  # 30 minutes
STREAM_BLOCK_FRAMES: Final[int] = 65_536
INTRO_MAX_SEC: Final[float] = 15.0
SHORT_TRACK_THRESHOLD_SEC: Final[float] = 30.0
LONG_TRACK_OUTRO_THRESHOLD_SEC: Final[float] = 60.0
OUTRO_OFFSET_SEC: Final[float] = 30.0
INTRO_RATIO_FALLBACK: Final[float] = 0.10
OUTRO_RATIO_FALLBACK: Final[float] = 0.90
CUE_RATIO_25: Final[float] = 0.25
CUE_RATIO_50: Final[float] = 0.50
CUE_RATIO_75: Final[float] = 0.75
CUE_ROUND_DIGITS: Final[int] = 3
ENVELOPE_ROUND_DIGITS: Final[int] = 4
FALLBACK_ENVELOPE_EMPTY: Final[float] = 0.1
FALLBACK_ENVELOPE_ERROR: Final[float] = 0.25
FALLBACK_ENVELOPE_SILENT: Final[float] = 0.05
WAVEFORM_MIN_HEIGHT_PX: Final[int] = 44
WAVEFORM_HALF_HEIGHT_SCALE: Final[float] = 0.45
WAVEFORM_MIN_HALF_BAR_PX: Final[float] = 2.0
PLAYHEAD_WIDTH_PX: Final[int] = 2
LAYOUT_MARGIN_PX: Final[int] = 4
MS_PER_SECOND: Final[float] = 1000.0
SECONDS_PER_MINUTE: Final[float] = 60.0
SLIDER_SINGLE_STEP_MS: Final[int] = 1000
SLIDER_PAGE_STEP_MS: Final[int] = 10_000
POSITION_SLIDER_NAME: Final[str] = "Playback position"
BACKEND_UNAVAILABLE_TEXT: Final[str] = (
    "Audio playback is unavailable on this system. Check the audio device and the Qt "
    "multimedia backend, then restart the app."
)


def compute_cue_jump_positions(duration_sec: float) -> dict[str, float]:
    """Compute target needle-drop cue positions in seconds for a given track duration."""
    safe_dur = max(0.0, float(duration_sec))
    intro_pos = (
        min(INTRO_MAX_SEC, safe_dur * INTRO_RATIO_FALLBACK)
        if safe_dur < SHORT_TRACK_THRESHOLD_SEC
        else INTRO_MAX_SEC
    )
    outro_pos = (
        max(0.0, safe_dur - OUTRO_OFFSET_SEC)
        if safe_dur >= LONG_TRACK_OUTRO_THRESHOLD_SEC
        else safe_dur * OUTRO_RATIO_FALLBACK
    )
    return {
        "Intro": round(intro_pos, CUE_ROUND_DIGITS),
        "25%": round(safe_dur * CUE_RATIO_25, CUE_ROUND_DIGITS),
        "50% Drop": round(safe_dur * CUE_RATIO_50, CUE_ROUND_DIGITS),
        "75%": round(safe_dur * CUE_RATIO_75, CUE_ROUND_DIGITS),
        "Outro": round(outro_pos, CUE_ROUND_DIGITS),
    }


def format_clock(seconds: float) -> str:
    """Format a time in seconds as MM:SS.d."""
    minutes, rest = divmod(max(0.0, float(seconds)), SECONDS_PER_MINUTE)
    return f"{int(minutes):02d}:{rest:04.1f}"


def _stream_block_peaks(file_path: Path, max_frames: int) -> list[float]:
    """Read audio in fixed-size blocks up to max_frames and collect per-block peak values."""
    block_peaks: list[float] = []
    frames_seen = 0
    for block in sf.blocks(
        str(file_path), blocksize=STREAM_BLOCK_FRAMES, dtype="float32", always_2d=True
    ):
        if block.size == 0 or frames_seen >= max_frames:
            break
        mono = np.abs(np.mean(block, axis=1))
        block_peaks.append(float(np.max(mono)))
        frames_seen += int(block.shape[0])
    return block_peaks


def extract_waveform_envelope(file_path: Path, bins: int = DEFAULT_ENVELOPE_BINS) -> list[float]:
    """Extract normalized peak amplitude envelope bins using bounded streaming blocks."""
    if bins <= 0 or not file_path.exists():
        return [FALLBACK_ENVELOPE_EMPTY] * max(1, bins)
    try:
        size_bytes = file_path.stat().st_size
        if size_bytes > MAX_PREVIEW_FILE_BYTES:
            logger.warning("Audio preview file %s exceeds size cap (%d B).", file_path, size_bytes)
            return [FALLBACK_ENVELOPE_ERROR] * bins
        info = sf.info(str(file_path))
        max_frames = max(1, round(max(1, int(info.samplerate)) * MAX_PREVIEW_DURATION_SEC))
        raw_peaks = _stream_block_peaks(file_path, max_frames)
    except (OSError, ValueError, RuntimeError) as exc:
        logger.warning("Waveform extraction failed for %s: %s", file_path, exc)
        return [FALLBACK_ENVELOPE_ERROR] * bins
    if not raw_peaks:
        return [FALLBACK_ENVELOPE_EMPTY] * bins
    arr = np.interp(
        np.linspace(0.0, max(0, len(raw_peaks) - 1), bins),
        np.arange(len(raw_peaks), dtype=np.float64),
        np.array(raw_peaks, dtype=np.float64),
    )
    max_peak = float(np.max(arr)) if arr.size > 0 else 0.0
    if max_peak <= 0.0:
        return [FALLBACK_ENVELOPE_SILENT] * bins
    return [round(float(p) / max_peak, ENVELOPE_ROUND_DIGITS) for p in arr]


class WaveformOverviewBar(QWidget):
    """Interactive waveform bar displaying amplitude peaks and playhead position."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize WaveformOverviewBar."""
        super().__init__(parent)
        self.setMinimumHeight(WAVEFORM_MIN_HEIGHT_PX)
        self.envelope: list[float] = [FALLBACK_ENVELOPE_EMPTY] * DEFAULT_ENVELOPE_BINS
        self.playhead_ratio: float = 0.0
        self.on_seek_ratio = None

    def set_envelope(self, envelope: list[float]) -> None:
        """Update the waveform envelope values and trigger a repaint."""
        self.envelope = envelope or [FALLBACK_ENVELOPE_EMPTY] * DEFAULT_ENVELOPE_BINS
        self.update()

    def set_playhead_ratio(self, ratio: float) -> None:
        """Set normalized playhead position in [0.0, 1.0]."""
        self.playhead_ratio = max(0.0, min(1.0, float(ratio)))
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Allow clicking on the waveform bar to seek to that ratio."""
        width = max(1, self.width())
        ratio = max(0.0, min(1.0, event.position().x() / float(width)))
        self.set_playhead_ratio(ratio)
        if callable(self.on_seek_ratio):
            self.on_seek_ratio(ratio)

    def paintEvent(self, event: QPaintEvent) -> None:
        """Render waveform vertical bars and the current playhead indicator."""
        painter = QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, QColor("#181c24"))
        count = max(1, len(self.envelope))
        bar_w = max(1.0, rect.width() / float(count))
        mid_y = rect.height() / 2.0
        for idx, amp in enumerate(self.envelope):
            half_h = max(
                WAVEFORM_MIN_HALF_BAR_PX, amp * (rect.height() * WAVEFORM_HALF_HEIGHT_SCALE)
            )
            color = (
                QColor("#3b82f6")
                if (idx / float(count)) <= self.playhead_ratio
                else QColor("#4b5563")
            )
            painter.fillRect(
                int(idx * bar_w),
                int(mid_y - half_h),
                max(1, int(bar_w - 1)),
                int(half_h * 2),
                color,
            )
        play_x = int(self.playhead_ratio * rect.width())
        painter.fillRect(play_x, 0, PLAYHEAD_WIDTH_PX, rect.height(), QColor("#f59e0b"))
        painter.end()


class AudioPreviewWidget(QWidget):
    """Audio preview transport with waveform overview and needle-drop cue buttons."""

    def __init__(
        self, parent: QWidget | None = None, enable_multimedia_backend: bool = False
    ) -> None:
        """Initialize AudioPreviewWidget with waveform overview and cue jump controls."""
        super().__init__(parent)
        self.current_file_path: Path | None = None
        self.duration_sec: float = 0.0
        self.current_position_sec: float = 0.0
        self.cue_positions: dict[str, float] = compute_cue_jump_positions(0.0)
        self.enable_multimedia_backend = enable_multimedia_backend

        self.player: QMediaPlayer | None = None
        self.audio_output: QAudioOutput | None = None
        if self.enable_multimedia_backend:
            self.player = QMediaPlayer(self)
            self.audio_output = QAudioOutput(self)
            self.player.setAudioOutput(self.audio_output)

        self.waveform_bar = WaveformOverviewBar(self)
        self.waveform_bar.on_seek_ratio = self._seek_by_ratio
        self.position_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.position_slider.setRange(0, 0)
        self.position_slider.setSingleStep(SLIDER_SINGLE_STEP_MS)
        self.position_slider.setPageStep(SLIDER_PAGE_STEP_MS)
        self.position_slider.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.position_slider.setAccessibleName(POSITION_SLIDER_NAME)
        self.status_label = QLabel("", self)
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        self.time_label = QLabel("00:00.0 / 00:00.0", self)
        self.play_btn = QPushButton("Play", self)
        self.stop_btn = QPushButton("Stop", self)
        self.cue_buttons: dict[str, QPushButton] = {}

        self._build_layout()
        self._connect_signals()
        if self.player is not None and not self.player.isAvailable():
            self.show_status(BACKEND_UNAVAILABLE_TEXT)

    def _build_layout(self) -> None:
        """Construct the widget layout with waveform bar, transport, and cue buttons."""
        root = QVBoxLayout(self)
        root.setContentsMargins(
            LAYOUT_MARGIN_PX, LAYOUT_MARGIN_PX, LAYOUT_MARGIN_PX, LAYOUT_MARGIN_PX
        )
        root.addWidget(self.waveform_bar)
        root.addWidget(self.position_slider)

        controls = QHBoxLayout()
        controls.addWidget(self.play_btn)
        controls.addWidget(self.stop_btn)
        for label in ("Intro", "25%", "50% Drop", "75%", "Outro"):
            btn = QPushButton(label, self)
            btn.clicked.connect(lambda _checked=False, cue=label: self.jump_to_cue(cue))
            self.cue_buttons[label] = btn
            controls.addWidget(btn)
        controls.addStretch()
        controls.addWidget(self.time_label)
        root.addLayout(controls)
        root.addWidget(self.status_label)

    def _connect_signals(self) -> None:
        """Connect QMediaPlayer, slider, and button signals."""
        self.play_btn.clicked.connect(self.toggle_play_pause)
        self.stop_btn.clicked.connect(self.stop_playback)
        self.position_slider.valueChanged.connect(self._on_slider_value_changed)
        if self.player is not None:
            self.player.positionChanged.connect(self._on_media_position_changed)
            self.player.durationChanged.connect(self._on_media_duration_changed)
            self.player.errorOccurred.connect(self._on_player_error)
            self.player.playbackStateChanged.connect(self._on_playback_state_changed)

    def show_status(self, text: str) -> None:
        """Show a plain-text playback status message, or clear it with an empty string."""
        self.status_label.setText(text)

    def _on_player_error(self, _error: QMediaPlayer.Error, error_string: str) -> None:
        """Report a playback failure and return the transport to its stopped state."""
        detail = error_string or "unknown error"
        self.show_status(
            f"Playback failed: {detail}. Check that the file plays in another app, then retry."
        )
        self.play_btn.setText("Play")

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        """Keep the Play button label in step with the real player state."""
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setText("Pause" if playing else "Play")

    def _on_slider_value_changed(self, value_ms: int) -> None:
        """Seek when the user moves the position slider by mouse or keyboard."""
        self.seek_to_seconds(value_ms / MS_PER_SECOND)

    def _sync_position_slider(self) -> None:
        """Mirror the current position and duration on the slider without re-seeking."""
        blocker = QSignalBlocker(self.position_slider)
        self.position_slider.setMaximum(int(self.duration_sec * MS_PER_SECOND))
        self.position_slider.setValue(int(self.current_position_sec * MS_PER_SECOND))
        blocker.unblock()
        self.position_slider.setAccessibleDescription(format_clock(self.current_position_sec))

    def load_file(self, file_path: Path, fallback_duration_sec: float | None = None) -> None:
        """Load an audio file, compute its waveform envelope, and update cue positions."""
        self.current_file_path = file_path
        self.current_position_sec = 0.0
        probed = self._probe_duration(file_path)
        self.duration_sec = probed if probed > 0.0 else float(fallback_duration_sec or 0.0)
        self.cue_positions = compute_cue_jump_positions(self.duration_sec)
        self.waveform_bar.set_envelope(extract_waveform_envelope(file_path))
        self.waveform_bar.set_playhead_ratio(0.0)
        self.show_status("")
        if self.player is not None:
            self.player.setSource(QUrl.fromLocalFile(str(file_path)))
        self._update_time_label()

    def _probe_duration(self, file_path: Path) -> float:
        """Probe audio duration in seconds via soundfile, logging and returning 0.0 on error."""
        try:
            info = sf.info(str(file_path))
            return float(info.duration)
        except (OSError, ValueError, RuntimeError) as exc:
            logger.warning("Duration probe failed for %s: %s", file_path, exc)
            return 0.0

    def toggle_play_pause(self) -> None:
        """Toggle between playing and paused states."""
        if self.player is None:
            is_playing = self.play_btn.text() == "Pause"
            self.play_btn.setText("Play" if is_playing else "Pause")
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
            self.play_btn.setText("Play")
        else:
            self.player.play()
            self.play_btn.setText("Pause")

    def stop_playback(self) -> None:
        """Stop playback and reset playhead to 0.0."""
        if self.player is not None:
            self.player.stop()
        self.play_btn.setText("Play")
        self.seek_to_seconds(0.0)

    def jump_to_cue(self, cue_label: str) -> float:
        """Jump directly to a named cue point ('Intro', '25%', '50% Drop', '75%', 'Outro')."""
        target_sec = self.cue_positions.get(cue_label, 0.0)
        self.seek_to_seconds(target_sec)
        return self.current_position_sec

    def seek_to_seconds(self, position_sec: float) -> None:
        """Seek playback position to position_sec and update waveform playhead."""
        clamped = max(0.0, min(self.duration_sec, float(position_sec)))
        self.current_position_sec = clamped
        if self.player is not None:
            self.player.setPosition(int(clamped * MS_PER_SECOND))
        ratio = clamped / self.duration_sec if self.duration_sec > 0.0 else 0.0
        self.waveform_bar.set_playhead_ratio(ratio)
        self._update_time_label()

    def _seek_by_ratio(self, ratio: float) -> None:
        """Seek to a normalized ratio in [0.0, 1.0] of total duration."""
        self.seek_to_seconds(ratio * self.duration_sec)

    def _on_media_position_changed(self, position_ms: int) -> None:
        """Handle positionChanged signal from QMediaPlayer."""
        if position_ms > 0:
            self.current_position_sec = position_ms / MS_PER_SECOND
            ratio = (
                self.current_position_sec / self.duration_sec if self.duration_sec > 0.0 else 0.0
            )
            self.waveform_bar.set_playhead_ratio(ratio)
            self._update_time_label()

    def _on_media_duration_changed(self, duration_ms: int) -> None:
        """Handle durationChanged signal from QMediaPlayer."""
        if duration_ms > 0 and self.duration_sec <= 0.0:
            self.duration_sec = duration_ms / MS_PER_SECOND
            self.cue_positions = compute_cue_jump_positions(self.duration_sec)
            self._update_time_label()

    def _update_time_label(self) -> None:
        """Show the current and total time, and mirror the position on the slider."""
        self.time_label.setText(
            f"{format_clock(self.current_position_sec)} / {format_clock(self.duration_sec)}"
        )
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._sync_position_slider()
