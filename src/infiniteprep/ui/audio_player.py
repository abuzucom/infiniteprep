"""Needle-drop audio preview widget with waveform overview and cue-jump buttons."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

import numpy as np
import soundfile as sf
from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

logger = logging.getLogger(__name__)

DEFAULT_ENVELOPE_BINS: Final[int] = 120
MAX_PREVIEW_FILE_BYTES: Final[int] = 524_288_000  # 500 MiB
MAX_PREVIEW_DURATION_SEC: Final[float] = 1800.0  # 30 minutes
STREAM_BLOCK_FRAMES: Final[int] = 65_536


def compute_cue_jump_positions(duration_sec: float) -> dict[str, float]:
    """Compute target needle-drop cue positions in seconds for a given track duration."""
    safe_dur = max(0.0, float(duration_sec))
    intro_pos = min(15.0, safe_dur * 0.10) if safe_dur < 30.0 else 15.0
    outro_pos = max(0.0, safe_dur - 30.0) if safe_dur >= 60.0 else safe_dur * 0.90
    return {
        "Intro": round(intro_pos, 3),
        "25%": round(safe_dur * 0.25, 3),
        "50% Drop": round(safe_dur * 0.50, 3),
        "75%": round(safe_dur * 0.75, 3),
        "Outro": round(outro_pos, 3),
    }


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
        return [0.1] * max(1, bins)
    try:
        size_bytes = file_path.stat().st_size
        if size_bytes > MAX_PREVIEW_FILE_BYTES:
            logger.warning("Audio preview file %s exceeds size cap (%d B).", file_path, size_bytes)
            return [0.25] * bins
        info = sf.info(str(file_path))
        max_frames = max(1, round(max(1, int(info.samplerate)) * MAX_PREVIEW_DURATION_SEC))
        raw_peaks = _stream_block_peaks(file_path, max_frames)
    except (OSError, ValueError, RuntimeError) as exc:
        logger.warning("Waveform extraction failed for %s: %s", file_path, exc)
        return [0.25] * bins
    if not raw_peaks:
        return [0.1] * bins
    arr = np.interp(
        np.linspace(0.0, max(0, len(raw_peaks) - 1), bins),
        np.arange(len(raw_peaks), dtype=np.float64),
        np.array(raw_peaks, dtype=np.float64),
    )
    max_peak = float(np.max(arr)) if arr.size > 0 else 0.0
    if max_peak <= 0.0:
        return [0.05] * bins
    return [round(float(p) / max_peak, 4) for p in arr]


class WaveformOverviewBar(QWidget):
    """Interactive waveform bar displaying amplitude peaks and playhead position."""

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize WaveformOverviewBar."""
        super().__init__(parent)
        self.setMinimumHeight(44)
        self.envelope: list[float] = [0.1] * DEFAULT_ENVELOPE_BINS
        self.playhead_ratio: float = 0.0
        self.on_seek_ratio = None

    def set_envelope(self, envelope: list[float]) -> None:
        """Update the waveform envelope values and trigger a repaint."""
        self.envelope = envelope or [0.1] * DEFAULT_ENVELOPE_BINS
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
            half_h = max(2.0, amp * (rect.height() * 0.45))
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
        painter.fillRect(play_x, 0, 2, rect.height(), QColor("#f59e0b"))
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
        self.time_label = QLabel("00:00.0 / 00:00.0", self)
        self.play_btn = QPushButton("Play", self)
        self.stop_btn = QPushButton("Stop", self)
        self.cue_buttons: dict[str, QPushButton] = {}

        self._build_layout()
        self._connect_signals()

    def _build_layout(self) -> None:
        """Construct the widget layout with waveform bar, transport, and cue buttons."""
        root = QVBoxLayout(self)
        root.setContentsMargins(4, 4, 4, 4)
        root.addWidget(self.waveform_bar)

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

    def _connect_signals(self) -> None:
        """Connect QMediaPlayer and button signals."""
        self.play_btn.clicked.connect(self.toggle_play_pause)
        self.stop_btn.clicked.connect(self.stop_playback)
        if self.player is not None:
            self.player.positionChanged.connect(self._on_media_position_changed)
            self.player.durationChanged.connect(self._on_media_duration_changed)

    def load_file(self, file_path: Path, fallback_duration_sec: float | None = None) -> None:
        """Load an audio file, compute its waveform envelope, and update cue positions."""
        self.current_file_path = file_path
        self.current_position_sec = 0.0
        probed = self._probe_duration(file_path)
        self.duration_sec = probed if probed > 0.0 else float(fallback_duration_sec or 0.0)
        self.cue_positions = compute_cue_jump_positions(self.duration_sec)
        self.waveform_bar.set_envelope(extract_waveform_envelope(file_path))
        self.waveform_bar.set_playhead_ratio(0.0)
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
            self.player.setPosition(int(clamped * 1000.0))
        ratio = clamped / self.duration_sec if self.duration_sec > 0.0 else 0.0
        self.waveform_bar.set_playhead_ratio(ratio)
        self._update_time_label()

    def _seek_by_ratio(self, ratio: float) -> None:
        """Seek to a normalized ratio in [0.0, 1.0] of total duration."""
        self.seek_to_seconds(ratio * self.duration_sec)

    def _on_media_position_changed(self, position_ms: int) -> None:
        """Handle positionChanged signal from QMediaPlayer."""
        if position_ms > 0:
            self.current_position_sec = position_ms / 1000.0
            ratio = (
                self.current_position_sec / self.duration_sec if self.duration_sec > 0.0 else 0.0
            )
            self.waveform_bar.set_playhead_ratio(ratio)
            self._update_time_label()

    def _on_media_duration_changed(self, duration_ms: int) -> None:
        """Handle durationChanged signal from QMediaPlayer."""
        if duration_ms > 0 and self.duration_sec <= 0.0:
            self.duration_sec = duration_ms / 1000.0
            self.cue_positions = compute_cue_jump_positions(self.duration_sec)
            self._update_time_label()

    def _update_time_label(self) -> None:
        """Format current and total duration into MM:SS.d display."""
        cur_m, cur_s = divmod(self.current_position_sec, 60.0)
        tot_m, tot_s = divmod(self.duration_sec, 60.0)
        self.time_label.setText(f"{int(cur_m):02d}:{cur_s:04.1f} / {int(tot_m):02d}:{tot_s:04.1f}")
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
