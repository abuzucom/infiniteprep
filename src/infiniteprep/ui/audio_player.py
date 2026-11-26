"""Needle-drop audio preview widget with waveform overview and cue-jump buttons."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Final

import numpy as np
import soundfile as sf
from PySide6.QtCore import QSignalBlocker, Qt, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QMouseEvent,
    QPainter,
    QPaintEvent,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QBoxLayout,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStylePainter,
    QVBoxLayout,
    QWidget,
)

from infiniteprep.config.settings import (
    DEFAULT_WAVEFORM_ZOOM_SECONDS,
    SUPPORTED_WAVEFORM_ZOOM_SECONDS,
    SettingsManager,
)
from infiniteprep.ui.accessible_names import announce, name_combo_box
from infiniteprep.ui.time_format import split_time_seconds

logger = logging.getLogger(__name__)

DEFAULT_ENVELOPE_BINS: Final[int] = 120
MAX_PREVIEW_FILE_BYTES: Final[int] = 524_288_000  # 500 MiB
MAX_PREVIEW_DURATION_SEC: Final[float] = 1800.0  # 30 minutes
MAX_ANALYSIS_CHANNELS: Final[int] = 8
MAX_ANALYSIS_SAMPLE_RATE_HZ: Final[int] = 384_000
MAX_DECODED_SAMPLES: Final[int] = 200_000_000
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
WAVEFORM_FULL_HEIGHT_SCALE: Final[float] = 2.0
WAVEFORM_CENTER_DIVISOR: Final[float] = 2.0
ZOOM_PLAYHEAD_CENTER_RATIO: Final[float] = 0.5
# The bands overlap, so each stays translucent. At this alpha every band still reaches
# 3:1 against the waveform background.
SPECTRUM_OVERLAY_ALPHA: Final[int] = 192
WAVEFORM_BACKGROUND_COLOR: Final[str] = "#181c24"
PLAYED_BAR_COLOR: Final[str] = "#3b82f6"
# Gray 500 reaches 3:1 against the background. Gray 600 reached only 2.3:1.
UNPLAYED_BAR_COLOR: Final[str] = "#6b7280"
PLAYHEAD_COLOR: Final[str] = "#f59e0b"
# Okabe-Ito colors stay distinct with red-green color blindness. Red and green did not.
SPECTRUM_BAND_COLORS: Final[dict[str, str]] = {
    "Low": "#56b4e9",
    "Mid": "#e69f00",
    "High": "#f0e442",
}
LEGEND_SWATCH_STYLE: Final[str] = "background-color: {color}; min-width: 12px; max-width: 12px;"
SPECTRUM_DURATION_EPSILON_SEC: Final[float] = 1e-9
PLAYHEAD_WIDTH_PX: Final[int] = 2
ZOOM_FALLBACK_COLOR: Final[str] = "#6b7280"
ZOOM_OVERVIEW_COLOR: Final[str] = "#4b82e8"
ZOOM_SECONDS: Final[tuple[int, ...]] = SUPPORTED_WAVEFORM_ZOOM_SECONDS
OVERVIEW_MODE: Final[str] = "Overview"
ZOOM_MODE: Final[str] = "Zoom"
LAYOUT_MARGIN_PX: Final[int] = 4
DECK_ROW_SPACING: Final[int] = 2
DECK_WAVEFORM_MIN_HEIGHT: Final[int] = 16
DECK_EMPTY_TEXT: Final[str] = "Pick a track and press Play, or drag a track here."
MS_PER_SECOND: Final[float] = 1000.0
SECONDS_PER_MINUTE: Final[float] = 60.0
SLIDER_SINGLE_STEP_MS: Final[int] = 1000
SLIDER_PAGE_STEP_MS: Final[int] = 10_000
POSITION_SLIDER_NAME: Final[str] = "Playback position"
# The drag payload carries the file ID of a library row. A path would break when
# the file moves, and the ID is what the player and the panel both address.
FILE_ID_MIME_TYPE: Final[str] = "application/x-infiniteprep-file-id"
DROP_HIGHLIGHT_COLOR: Final[str] = "#f59e0b"
# The border sits one pixel inside the widget edge and stays two pixels wide.
DROP_HIGHLIGHT_INSET_PX: Final[int] = 1
DROP_HIGHLIGHT_BORDER_PX: Final[int] = 2
LOADED_TRACK_LABEL_PREFIX: Final[str] = "Loaded: "
LOADED_TRACK_NONE_TEXT: Final[str] = "No track loaded"
BACKEND_UNAVAILABLE_TEXT: Final[str] = (
    "Audio playback isn't available. Check your audio device, then restart InfinitePrep."
)
UNKNOWN_PLAYBACK_ERROR: Final[str] = "unknown error"


def format_playback_failure(detail: str) -> str:
    """Return the status line for a track that won't play, with the player's reason."""
    return f"This track won't play ({detail}). Check that it plays in another app, then try again."


def format_analysis_failure(message: str) -> str:
    """Return the status line for a waveform that couldn't be drawn."""
    return f"Couldn't draw the waveform: {message}"


def compute_cue_jump_positions(duration_sec: float) -> dict[str, float]:
    """Compute target needle-drop cue positions in seconds for a given track duration."""
    safe_duration = max(0.0, float(duration_sec))
    intro_position = (
        min(INTRO_MAX_SEC, safe_duration * INTRO_RATIO_FALLBACK)
        if safe_duration < SHORT_TRACK_THRESHOLD_SEC
        else INTRO_MAX_SEC
    )
    outro_position = (
        max(0.0, safe_duration - OUTRO_OFFSET_SEC)
        if safe_duration >= LONG_TRACK_OUTRO_THRESHOLD_SEC
        else safe_duration * OUTRO_RATIO_FALLBACK
    )
    return {
        "Intro": round(intro_position, CUE_ROUND_DIGITS),
        "25%": round(safe_duration * CUE_RATIO_25, CUE_ROUND_DIGITS),
        "50% Drop": round(safe_duration * CUE_RATIO_50, CUE_ROUND_DIGITS),
        "75%": round(safe_duration * CUE_RATIO_75, CUE_ROUND_DIGITS),
        "Outro": round(outro_position, CUE_ROUND_DIGITS),
    }


def format_clock(seconds: float) -> str:
    """Format a player time as M:SS.mmm or H:MM:SS.mmm."""
    total_milliseconds = int(max(0.0, float(seconds)) * MS_PER_SECOND)
    total_seconds, milliseconds = divmod(total_milliseconds, int(MS_PER_SECOND))
    hours, minutes, seconds_part = split_time_seconds(total_seconds)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds_part:02d}.{milliseconds:03d}"
    return f"{minutes}:{seconds_part:02d}.{milliseconds:03d}"


def _stream_block_peaks(
    file_path: Path, max_frames: int, cancel_event: threading.Event | None = None
) -> list[float]:
    """Read audio in fixed-size blocks up to max_frames and collect per-block peak values.

    A set cancel event stops the read between blocks. The caller discards the partial result.
    """
    block_peaks: list[float] = []
    frames_seen = 0
    for block in sf.blocks(
        str(file_path), blocksize=STREAM_BLOCK_FRAMES, dtype="float32", always_2d=True
    ):
        if block.size == 0 or frames_seen >= max_frames:
            break
        if cancel_event is not None and cancel_event.is_set():
            break
        mono = np.abs(np.mean(block, axis=1))
        block_peaks.append(float(np.max(mono)))
        frames_seen += int(block.shape[0])
    return block_peaks


def bounded_frame_count(
    sample_rate: int,
    channels: int,
    declared_frames: int,
    max_duration_sec: float = MAX_PREVIEW_DURATION_SEC,
) -> int:
    """Validate decoded audio metadata and return a bounded frame count."""
    if channels <= 0 or channels > MAX_ANALYSIS_CHANNELS:
        raise ValueError(f"Channel count must be between 1 and {MAX_ANALYSIS_CHANNELS}.")
    if sample_rate <= 0 or sample_rate > MAX_ANALYSIS_SAMPLE_RATE_HZ:
        raise ValueError(f"Sample rate must be between 1 and {MAX_ANALYSIS_SAMPLE_RATE_HZ} Hz.")
    duration_cap = int(sample_rate * max_duration_sec)
    sample_cap = MAX_DECODED_SAMPLES // channels
    return min(max(0, declared_frames), duration_cap, sample_cap)


def extract_waveform_envelope(
    file_path: Path,
    bins: int = DEFAULT_ENVELOPE_BINS,
    cancel_event: threading.Event | None = None,
) -> list[float]:
    """Extract normalized peak amplitude envelope bins using bounded streaming blocks.

    The waveform worker calls this off the UI thread for the fallback overview. A set
    cancel event ends the read early. The caller then discards the returned bins.
    """
    # Only a regular file reaches the decoder. A FIFO or device could block the read.
    if bins <= 0 or not file_path.is_file():
        return [FALLBACK_ENVELOPE_EMPTY] * max(1, bins)
    try:
        size_bytes = file_path.stat().st_size
        if size_bytes > MAX_PREVIEW_FILE_BYTES:
            logger.warning("Audio preview file %s exceeds size cap (%d B).", file_path, size_bytes)
            return [FALLBACK_ENVELOPE_ERROR] * bins
        info = sf.info(str(file_path))
        max_frames = max(
            1,
            bounded_frame_count(
                int(info.samplerate),
                int(info.channels),
                int(info.frames),
                MAX_PREVIEW_DURATION_SEC,
            ),
        )
        raw_peaks = _stream_block_peaks(file_path, max_frames, cancel_event)
    except (OSError, ValueError, RuntimeError) as exc:
        logger.warning("Waveform extraction failed for %s: %s", file_path, exc)
        return [FALLBACK_ENVELOPE_ERROR] * bins
    if not raw_peaks:
        return [FALLBACK_ENVELOPE_EMPTY] * bins
    resampled_peaks = np.interp(
        np.linspace(0.0, max(0, len(raw_peaks) - 1), bins),
        np.arange(len(raw_peaks), dtype=np.float64),
        np.array(raw_peaks, dtype=np.float64),
    )
    max_peak = float(np.max(resampled_peaks)) if resampled_peaks.size > 0 else 0.0
    if max_peak <= 0.0:
        return [FALLBACK_ENVELOPE_SILENT] * bins
    return [round(float(peak) / max_peak, ENVELOPE_ROUND_DIGITS) for peak in resampled_peaks]


class WaveformOverviewBar(QWidget):
    """Interactive waveform bar displaying amplitude peaks and playhead position."""

    file_id_dropped = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        """Initialize WaveformOverviewBar."""
        super().__init__(parent)
        self.setMinimumHeight(WAVEFORM_MIN_HEIGHT_PX)
        self.envelope: list[float] = [FALLBACK_ENVELOPE_EMPTY] * DEFAULT_ENVELOPE_BINS
        self.fallback_envelope = list(self.envelope)
        self.playhead_ratio: float = 0.0
        self.duration_sec = 0.0
        self.position_sec = 0.0
        self.zoom_seconds = DEFAULT_WAVEFORM_ZOOM_SECONDS
        self.view_mode = OVERVIEW_MODE
        self.spectrum: object | None = None
        self._dragging = False
        self._drag_window = (0.0, 0.0)
        self._drop_highlight = False
        self.on_seek_ratio = None
        self.setMouseTracking(True)
        # Only a library row drag reaches the waveform. A file from the file
        # manager carries no file ID, so the drop stays ignored.
        self.setAcceptDrops(True)

    @property
    def drop_highlight(self) -> bool:
        """Report whether the waveform is marked as a drop target."""
        return self._drop_highlight

    def set_envelope(self, envelope: list[float]) -> None:
        """Update the waveform envelope values and trigger a repaint."""
        # The copy keeps a later caller-side mutation out of the stored overview.
        self.fallback_envelope = list(envelope or [FALLBACK_ENVELOPE_EMPTY] * DEFAULT_ENVELOPE_BINS)
        if self.spectrum is None:
            self.envelope = list(self.fallback_envelope)
        else:
            self.set_spectrum_envelope(
                np.asarray(self.spectrum.overall, dtype=np.float32),
                float(self.spectrum.duration_sec),
            )
        self.update()

    def set_spectrum_envelope(self, peaks: np.ndarray, analyzed_duration: float) -> None:
        """Overlay analyzed peaks and retain fallback coverage beyond the analysis cap."""
        self.envelope = list(self.fallback_envelope)
        if not peaks.size or analyzed_duration <= 0.0 or self.duration_sec <= 0.0:
            self.update()
            return
        covered = min(
            len(self.envelope),
            max(
                1,
                int(np.ceil(len(self.envelope) * min(1.0, analyzed_duration / self.duration_sec))),
            ),
        )
        groups = np.array_split(peaks, covered)
        max_peak = max((float(group.max()) for group in groups if group.size), default=0.0)
        if max_peak:
            self.envelope[:covered] = [
                float(group.max()) / max_peak if group.size else 0.0 for group in groups
            ]
        self.update()

    def set_playhead_ratio(self, ratio: float) -> None:
        """Set normalized playhead position in [0.0, 1.0]."""
        self.playhead_ratio = max(0.0, min(1.0, float(ratio)))
        self.update()

    def set_playback(self, position_sec: float, duration_sec: float) -> None:
        """Update playback time while keeping the zoom viewport centered."""
        self.position_sec = max(0.0, float(position_sec))
        self.duration_sec = max(0.0, float(duration_sec))
        ratio = self.position_sec / self.duration_sec if self.duration_sec else 0.0
        self.set_playhead_ratio(
            ZOOM_PLAYHEAD_CENTER_RATIO if self.view_mode == ZOOM_MODE else ratio
        )

    def zoom_window(self) -> tuple[float, float]:
        """Return the time interval centered around the playhead."""
        width = min(float(self.zoom_seconds), self.duration_sec)
        return (
            self.position_sec - width / WAVEFORM_CENTER_DIVISOR,
            self.position_sec + width / WAVEFORM_CENTER_DIVISOR,
        )

    def _seek_from_x(self, x: float) -> None:
        """Map a canvas coordinate to the overview or zoom time interval."""
        ratio = max(0.0, min(1.0, x / float(max(1, self.width()))))
        if self.view_mode == ZOOM_MODE:
            start, end = self._drag_window if self._dragging else self.zoom_window()
            target = start + ratio * (end - start)
            seek_ratio = target / self.duration_sec if self.duration_sec > 0 else 0.0
        else:
            seek_ratio = ratio
        self.set_playhead_ratio(
            ratio if self.view_mode == OVERVIEW_MODE else ZOOM_PLAYHEAD_CENTER_RATIO
        )
        if callable(self.on_seek_ratio):
            self.on_seek_ratio(seek_ratio)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """Allow clicking on the waveform bar to seek to that ratio."""
        self._dragging = True
        self._drag_window = self.zoom_window()
        self._seek_from_x(event.position().x())

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """Allow drag scrubbing in zoom mode."""
        if self.view_mode == ZOOM_MODE and self._dragging and self.duration_sec > 0:
            self._seek_from_x(event.position().x())

    def mouseReleaseEvent(self, _event: QMouseEvent) -> None:
        """Finish a zoom scrub gesture."""
        self._dragging = False

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        """Accept a library row drag and mark the waveform as a drop target."""
        if not event.mimeData().hasFormat(FILE_ID_MIME_TYPE):
            event.ignore()
            return
        event.acceptProposedAction()
        self._set_drop_highlight(True)

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        """Keep the drop target marked while the drag stays over the waveform."""
        if not event.mimeData().hasFormat(FILE_ID_MIME_TYPE):
            event.ignore()
            return
        event.acceptProposedAction()
        self._set_drop_highlight(True)

    def dragLeaveEvent(self, _event: QDragLeaveEvent) -> None:
        """Clear the drop mark when the drag leaves the waveform."""
        self._set_drop_highlight(False)

    def dropEvent(self, event: QDropEvent) -> None:
        """Report the dropped file ID and clear the drop mark."""
        self._set_drop_highlight(False)
        mime_data = event.mimeData()
        if not mime_data.hasFormat(FILE_ID_MIME_TYPE):
            event.ignore()
            return
        file_id = bytes(mime_data.data(FILE_ID_MIME_TYPE)).decode("utf-8", errors="replace")
        event.acceptProposedAction()
        self.file_id_dropped.emit(file_id)

    def _set_drop_highlight(self, active: bool) -> None:
        """Show or clear the drop mark on the waveform."""
        if self._drop_highlight == active:
            return
        self._drop_highlight = active
        self.update()

    def _paint_drop_highlight(self, painter: QPainter) -> None:
        """Draw the border that marks the waveform as a drop target."""
        painter.setPen(QColor(DROP_HIGHLIGHT_COLOR))
        painter.drawRect(
            self.rect().adjusted(
                DROP_HIGHLIGHT_INSET_PX,
                DROP_HIGHLIGHT_INSET_PX,
                -DROP_HIGHLIGHT_BORDER_PX,
                -DROP_HIGHLIGHT_BORDER_PX,
            )
        )

    def paintEvent(self, event: QPaintEvent) -> None:
        """Render waveform vertical bars and the current playhead indicator."""
        painter = QPainter(self)
        rect = self.rect()
        painter.fillRect(rect, QColor(WAVEFORM_BACKGROUND_COLOR))
        if self.view_mode == ZOOM_MODE:
            # _paint_zoom reads spectrum attributes. Zoom before analysis completes
            # draws the fallback overview segment instead.
            if self.spectrum is None:
                self._paint_zoom_overview(painter, rect.width(), rect.height())
            else:
                self._paint_zoom(painter, rect.width(), rect.height())
            if self._drop_highlight:
                self._paint_drop_highlight(painter)
            painter.end()
            return
        count = max(1, len(self.envelope))
        bar_w = max(1.0, rect.width() / float(count))
        mid_y = rect.height() * ZOOM_PLAYHEAD_CENTER_RATIO
        for index, amp in enumerate(self.envelope):
            half_h = max(
                WAVEFORM_MIN_HALF_BAR_PX, amp * (rect.height() * WAVEFORM_HALF_HEIGHT_SCALE)
            )
            color = (
                QColor(PLAYED_BAR_COLOR)
                if (index / float(count)) <= self.playhead_ratio
                else QColor(UNPLAYED_BAR_COLOR)
            )
            painter.fillRect(
                int(index * bar_w),
                int(mid_y - half_h),
                max(1, int(bar_w - 1)),
                int(half_h * WAVEFORM_FULL_HEIGHT_SCALE),
                color,
            )
        play_x = int(self.playhead_ratio * rect.width())
        painter.fillRect(play_x, 0, PLAYHEAD_WIDTH_PX, rect.height(), QColor(PLAYHEAD_COLOR))
        if self._drop_highlight:
            self._paint_drop_highlight(painter)
        painter.end()

    def _paint_zoom(self, painter: QPainter, width: int, height: int) -> None:
        """Draw low, mid, and high spectrum peaks over the zoom time window."""
        start, end = self.zoom_window()
        count = len(self.spectrum.overall)
        analyzed_duration = float(self.spectrum.duration_sec)
        if count == 0 or analyzed_duration <= 0.0:
            painter.fillRect(
                int(width * ZOOM_PLAYHEAD_CENTER_RATIO),
                0,
                PLAYHEAD_WIDTH_PX,
                height,
                QColor(PLAYHEAD_COLOR),
            )
            return
        colors = tuple(QColor(color) for color in SPECTRUM_BAND_COLORS.values())
        for color in colors:
            color.setAlpha(SPECTRUM_OVERLAY_ALPHA)
        dimensions = (width, height)
        analysis = (start, end, count, analyzed_duration, colors)
        for column in range(width):
            self._paint_zoom_column(painter, column, dimensions, analysis)
        painter.fillRect(
            int(width * ZOOM_PLAYHEAD_CENTER_RATIO),
            0,
            PLAYHEAD_WIDTH_PX,
            height,
            QColor(PLAYHEAD_COLOR),
        )

    def _paint_zoom_column(
        self,
        painter: QPainter,
        column: int,
        dimensions: tuple[int, int],
        analysis: tuple[float, float, int, float, tuple[QColor, ...]],
    ) -> None:
        """Draw one spectrum column, skipping regions outside analyzed audio."""
        width, height = dimensions
        start, end, count, analyzed_duration, colors = analysis
        time_start = start + column * (end - start) / max(width, 1)
        time_end = start + (column + 1) * (end - start) / max(width, 1)
        if self.duration_sec <= 0.0:
            return
        if time_end <= 0.0 or time_start >= analyzed_duration:
            if time_start >= analyzed_duration:
                self._paint_zoom_fallback_column(painter, column, (time_start, time_end), height)
            return
        # The floor keeps a denormal duration from overflowing the sample indices.
        bounded_duration = max(analyzed_duration, SPECTRUM_DURATION_EPSILON_SEC)
        sample_start = max(0, int(time_start * count / bounded_duration))
        sample_end = min(
            count,
            max(sample_start + 1, int(np.ceil(time_end * count / bounded_duration))),
        )
        for name, color in zip(("low", "mid", "high"), colors, strict=True):
            values = getattr(self.spectrum, name)[sample_start:sample_end]
            peak = float(values.max()) if len(values) else 0.0
            half_height = max(WAVEFORM_MIN_HALF_BAR_PX, peak * height * WAVEFORM_HALF_HEIGHT_SCALE)
            top = int((height - half_height * WAVEFORM_FULL_HEIGHT_SCALE) / WAVEFORM_CENTER_DIVISOR)
            painter.fillRect(column, top, 1, int(half_height * WAVEFORM_FULL_HEIGHT_SCALE), color)

    def _paint_zoom_fallback_column(
        self,
        painter: QPainter,
        column: int,
        interval: tuple[float, float],
        height: int,
        color: str = ZOOM_FALLBACK_COLOR,
    ) -> None:
        """Draw overview envelope data for one zoom column.

        The default color marks a zoom interval beyond the analysis cap.
        """
        count = len(self.envelope)
        if self.duration_sec <= 0.0 or count == 0:
            return
        duration = max(self.duration_sec, SPECTRUM_DURATION_EPSILON_SEC)
        first = max(0, int(interval[0] * count / duration))
        last = min(count, max(first + 1, int(np.ceil(interval[1] * count / duration))))
        peak = max(self.envelope[first:last], default=FALLBACK_ENVELOPE_EMPTY)
        half_height = max(WAVEFORM_MIN_HALF_BAR_PX, peak * height * WAVEFORM_HALF_HEIGHT_SCALE)
        top = int((height - half_height * WAVEFORM_FULL_HEIGHT_SCALE) / WAVEFORM_CENTER_DIVISOR)
        painter.fillRect(
            column,
            top,
            1,
            int(half_height * WAVEFORM_FULL_HEIGHT_SCALE),
            QColor(color),
        )

    def _paint_zoom_overview(self, painter: QPainter, width: int, height: int) -> None:
        """Show the selected overview segment until spectrum analysis completes."""
        start, end = self.zoom_window()
        count = len(self.envelope)
        if self.duration_sec <= 0.0 or count == 0:
            painter.fillRect(
                int(width * ZOOM_PLAYHEAD_CENTER_RATIO),
                0,
                PLAYHEAD_WIDTH_PX,
                height,
                QColor(PLAYHEAD_COLOR),
            )
            return
        for column in range(width):
            time_start = start + column * (end - start) / max(width, 1)
            time_end = start + (column + 1) * (end - start) / max(width, 1)
            if time_end <= 0.0 or time_start >= self.duration_sec:
                continue
            self._paint_zoom_fallback_column(
                painter, column, (time_start, time_end), height, ZOOM_OVERVIEW_COLOR
            )
        painter.fillRect(
            int(width * ZOOM_PLAYHEAD_CENTER_RATIO),
            0,
            PLAYHEAD_WIDTH_PX,
            height,
            QColor(PLAYHEAD_COLOR),
        )


class DeckTextLabel(QLabel):
    """Keep full track and status text available when the deck has limited width."""

    def __init__(self, text: str, parent: QWidget) -> None:
        super().__init__(text, parent)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self._elide_text = False

    def use_deck_width(self) -> None:
        """Keep the label within the space left by the deck controls."""
        self._elide_text = True
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setWordWrap(False)
        self.setToolTip(self.text())

    def setText(self, text: str) -> None:
        """Update the accessible text and the full text available on hover."""
        super().setText(text)
        if self._elide_text:
            self.setToolTip(text)

    def paintEvent(self, event: QPaintEvent) -> None:
        """Elide a long label without raising the minimum width of the deck."""
        if not self._elide_text:
            super().paintEvent(event)
            return
        rect = self.contentsRect()
        text = self.fontMetrics().elidedText(self.text(), Qt.TextElideMode.ElideRight, rect.width())
        painter = QStylePainter(self)
        painter.drawItemText(
            rect,
            int(self.alignment()),
            self.palette(),
            self.isEnabled(),
            text,
            self.foregroundRole(),
        )


class AudioPreviewWidget(QWidget):
    """Audio preview transport with waveform overview and needle-drop cue buttons."""

    def __init__(
        self,
        parent: QWidget | None = None,
        enable_multimedia_backend: bool = False,
        *,
        settings_manager: SettingsManager | None = None,
    ) -> None:
        """Initialize AudioPreviewWidget with waveform overview and cue jump controls.

        A default SettingsManager resolves the application settings path, so every
        default instance reads and writes the same file. SettingsManager.update merges
        into the latest file content, and its writes replace the file atomically.
        """
        super().__init__(parent)
        self.current_file_path: Path | None = None
        self.duration_sec: float = 0.0
        self.current_position_sec: float = 0.0
        self.cue_positions: dict[str, float] = compute_cue_jump_positions(0.0)
        self.enable_multimedia_backend = enable_multimedia_backend
        self.settings_manager = settings_manager or SettingsManager()
        # SettingsManager.load returns only supported zoom durations.
        self.waveform_zoom_seconds = self.settings_manager.load().waveform_zoom_seconds
        # The owner of the widget holds the selection and the library rows. It
        # decides what a play request and a dropped track mean for the player.
        self.on_play_selection_requested: Callable[[], bool] | None = None
        self.on_file_dropped: Callable[[str], None] | None = None

        self.player: QMediaPlayer | None = None
        self.audio_output: QAudioOutput | None = None
        if self.enable_multimedia_backend:
            self.player = QMediaPlayer(self)
            self.audio_output = QAudioOutput(self)
            self.player.setAudioOutput(self.audio_output)

        self._in_deck = False
        self._build_controls()
        self._build_layout()
        self._connect_signals()
        if self.player is not None and not self.player.isAvailable():
            self.show_status(BACKEND_UNAVAILABLE_TEXT)

    def _build_controls(self) -> None:
        """Create the waveform, the transport controls, and the status labels."""
        self.waveform_bar = WaveformOverviewBar(self)
        self.waveform_bar.on_seek_ratio = self._seek_by_ratio
        self.waveform_bar.file_id_dropped.connect(self._handle_file_id_dropped)
        self.view_combo = QComboBox(self)
        self.view_combo.addItems((OVERVIEW_MODE, ZOOM_MODE))
        self.zoom_combo = QComboBox(self)
        for seconds in ZOOM_SECONDS:
            self.zoom_combo.addItem(f"{seconds} seconds", seconds)
        self.zoom_combo.setCurrentIndex(self.zoom_combo.findData(self.waveform_zoom_seconds))
        self.position_slider = QSlider(Qt.Orientation.Horizontal, self)
        self.position_slider.setRange(0, 0)
        self.position_slider.setSingleStep(SLIDER_SINGLE_STEP_MS)
        self.position_slider.setPageStep(SLIDER_PAGE_STEP_MS)
        self.position_slider.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.position_slider.setAccessibleName(POSITION_SLIDER_NAME)
        self.status_label = DeckTextLabel("", self)
        self.status_label.setWordWrap(True)
        self.analysis_status_label = DeckTextLabel("", self)
        self.analysis_status_label.setWordWrap(True)
        # The empty label uses the clock format the player shows once a track loads.
        self.time_label = QLabel(f"{format_clock(0.0)} / {format_clock(0.0)}", self)
        # The label names the track the player holds. The detail panel can show a
        # different file, so the label keeps the mismatch visible.
        self.loaded_track_label = DeckTextLabel(LOADED_TRACK_NONE_TEXT, self)
        self.play_btn = QPushButton("Play", self)
        self.stop_btn = QPushButton("Stop", self)
        self.cue_buttons: dict[str, QPushButton] = {}

    def _build_layout(self) -> None:
        """Construct the widget layout with waveform bar, transport, and cue buttons."""
        root = QVBoxLayout(self)
        root.setContentsMargins(
            LAYOUT_MARGIN_PX, LAYOUT_MARGIN_PX, LAYOUT_MARGIN_PX, LAYOUT_MARGIN_PX
        )
        root.addWidget(self.waveform_bar)
        self.deck_views = self._build_view_controls()
        root.addWidget(self.deck_views, alignment=Qt.AlignmentFlag.AlignLeft)
        root.addWidget(self.position_slider)

        controls = QHBoxLayout()
        controls.addWidget(self.play_btn)
        controls.addWidget(self.stop_btn)
        controls.addWidget(self.loaded_track_label)
        self._add_cue_buttons(controls)
        controls.addStretch()
        controls.addWidget(self.time_label)
        root.addLayout(controls)
        root.addWidget(self.status_label)
        root.addWidget(self.analysis_status_label)

    def _build_view_controls(self) -> QWidget:
        """Group the two waveform settings and the zoom legend."""
        self.spectrum_legend = self._build_spectrum_legend()
        name_combo_box(self.view_combo, "Waveform view")
        name_combo_box(self.zoom_combo, "Waveform zoom")
        view_group = QWidget(self)
        view_controls = QHBoxLayout(view_group)
        view_controls.setContentsMargins(0, 0, 0, 0)
        view_controls.addWidget(QLabel("Waveform view", self))
        view_controls.addWidget(self.view_combo)
        view_controls.addWidget(QLabel("Zoom", self))
        view_controls.addWidget(self.zoom_combo)
        view_controls.addWidget(self.spectrum_legend)
        return view_group

    def _add_cue_buttons(self, controls: QHBoxLayout) -> None:
        """Place the cue buttons in playback order."""
        for label in ("Intro", "25%", "50% Drop", "75%", "Outro"):
            cue_button = QPushButton(label, self)
            cue_button.clicked.connect(lambda _checked=False, cue=label: self.jump_to_cue(cue))
            self.cue_buttons[label] = cue_button
            controls.addWidget(cue_button)

    def place_in_deck(self) -> None:
        """Pin the player identity and controls around a waveform that takes spare height."""
        if self._in_deck:
            return
        self._in_deck = True
        root = self.layout()
        while root.count():
            item = root.takeAt(0)
            sublayout = item.layout()
            if sublayout is not None:
                sublayout.deleteLater()
        for label in (self.loaded_track_label, self.status_label, self.analysis_status_label):
            label.use_deck_width()
        self._build_deck_layout(root)

    def _build_deck_layout(self, root: QVBoxLayout) -> None:
        """Lay out the same transport and waveform widgets in the main window deck."""
        root.setContentsMargins(LAYOUT_MARGIN_PX, 0, LAYOUT_MARGIN_PX, 0)
        root.setSpacing(DECK_ROW_SPACING)
        self.deck_heading = QHBoxLayout()
        self.deck_heading.addWidget(self.loaded_track_label, 3)
        self.deck_hint = DeckTextLabel(DECK_EMPTY_TEXT, self)
        self.deck_hint.use_deck_width()
        self.deck_hint.setVisible(self.current_file_path is None)
        self.deck_heading.addWidget(self.deck_hint, 5)
        self.deck_heading.addWidget(self.status_label, 2)
        self.deck_heading.addWidget(self.analysis_status_label, 2)
        self.deck_heading.addWidget(self.time_label)
        self.status_label.setVisible(bool(self.status_label.text()))
        self.analysis_status_label.setVisible(bool(self.analysis_status_label.text()))
        root.addLayout(self.deck_heading)
        self.waveform_bar.setMinimumHeight(DECK_WAVEFORM_MIN_HEIGHT)
        root.addWidget(self.waveform_bar, 1)
        root.addWidget(self.position_slider)

        self.deck_controls = QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.deck_controls.setSpacing(DECK_ROW_SPACING)
        self.deck_transport = QHBoxLayout()
        self.deck_transport.addWidget(self.play_btn)
        self.deck_transport.addWidget(self.stop_btn)
        self.deck_transport.addStretch()
        cues = QHBoxLayout()
        for cue_button in self.cue_buttons.values():
            cues.addWidget(cue_button)
        self.deck_controls.addLayout(self.deck_transport, 1)
        self.deck_controls.addLayout(cues)
        root.addLayout(self.deck_controls)
        self.set_deck_compact(False)

    def set_deck_compact(self, compact: bool) -> None:
        """Move waveform settings beside transport when cues need their own row."""
        self.deck_heading.removeWidget(self.deck_views)
        self.deck_transport.removeWidget(self.deck_views)
        if compact:
            self.deck_controls.setDirection(QBoxLayout.Direction.TopToBottom)
            self.deck_transport.insertWidget(2, self.deck_views)
        else:
            self.deck_controls.setDirection(QBoxLayout.Direction.LeftToRight)
            self.deck_heading.addWidget(self.deck_views)

    def _set_status_text(self, label: QLabel, text: str) -> None:
        """Reserve room for a deck message only while there is text to show."""
        label.setText(text)
        if self._in_deck:
            label.setVisible(bool(text))

    def _build_spectrum_legend(self) -> QWidget:
        """Build the band legend that the zoom view shows.

        Color alone named the bands before. The legend pairs each color with its name.
        """
        legend = QWidget(self)
        layout = QHBoxLayout(legend)
        layout.setContentsMargins(0, 0, 0, 0)
        for band, color in SPECTRUM_BAND_COLORS.items():
            swatch = QLabel(legend)
            swatch.setStyleSheet(LEGEND_SWATCH_STYLE.format(color=color))
            layout.addWidget(swatch)
            layout.addWidget(QLabel(band, legend))
        legend.setAccessibleName("Spectrum bands: low, mid, and high")
        legend.setVisible(self.view_combo.currentText() == ZOOM_MODE)
        return legend

    def _connect_signals(self) -> None:
        """Connect QMediaPlayer, slider, and button signals."""
        self.play_btn.clicked.connect(self._on_play_button_clicked)
        self.stop_btn.clicked.connect(self.stop_playback)
        self.position_slider.valueChanged.connect(self._on_slider_value_changed)
        self.view_combo.currentTextChanged.connect(self._on_view_changed)
        self.zoom_combo.currentIndexChanged.connect(self._on_zoom_changed)
        if self.player is not None:
            self.player.positionChanged.connect(self._on_media_position_changed)
            self.player.durationChanged.connect(self._on_media_duration_changed)
            self.player.errorOccurred.connect(self._on_player_error)
            self.player.playbackStateChanged.connect(self._on_playback_state_changed)

    def show_status(self, text: str) -> None:
        """Show and announce a plain-text playback status, or clear it with an empty string."""
        self._set_status_text(self.status_label, text)
        announce(self.status_label, text)

    def _on_player_error(self, _error: QMediaPlayer.Error, error_string: str) -> None:
        """Report a playback failure and return the transport to its stopped state."""
        self.show_status(format_playback_failure(error_string or UNKNOWN_PLAYBACK_ERROR))
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
        """Load a track without decoding its overview on the UI thread."""
        self.current_file_path = file_path
        self.current_position_sec = 0.0
        self.loaded_track_label.setText(f"{LOADED_TRACK_LABEL_PREFIX}{file_path.name}")
        if self._in_deck:
            self.deck_hint.hide()
        probed = self._probe_duration(file_path)
        self.duration_sec = probed if probed > 0.0 else float(fallback_duration_sec or 0.0)
        self.cue_positions = compute_cue_jump_positions(self.duration_sec)
        self.waveform_bar.spectrum = None
        self.waveform_bar.set_envelope([FALLBACK_ENVELOPE_EMPTY] * DEFAULT_ENVELOPE_BINS)
        self.waveform_bar.set_playback(0.0, self.duration_sec)
        self.show_status("")
        if self.player is not None:
            self.player.setSource(QUrl.fromLocalFile(str(file_path)))
        self._update_time_label()

    def _on_view_changed(self, mode: str) -> None:
        """Switch between the track overview and centered zoom view."""
        self.waveform_bar.view_mode = mode
        self.spectrum_legend.setVisible(mode == ZOOM_MODE)
        self.waveform_bar.zoom_seconds = self.waveform_zoom_seconds
        self.waveform_bar.set_playback(self.current_position_sec, self.duration_sec)

    def _on_zoom_changed(self, index: int) -> None:
        """Persist the selected zoom interval across tracks and restarts.

        The duration comes from item data. Display text stays free to change.
        """
        seconds = self.zoom_combo.itemData(index)
        if type(seconds) is not int or seconds not in ZOOM_SECONDS:
            return
        self.waveform_zoom_seconds = seconds
        self.waveform_bar.zoom_seconds = seconds
        self.settings_manager.update(waveform_zoom_seconds=seconds)
        self.waveform_bar.update()

    def set_spectrum_waveform(self, waveform: object) -> None:
        """Attach analyzed bands and overlay covered overview peaks."""
        self.waveform_bar.spectrum = waveform
        peaks = np.asarray(waveform.overall, dtype=np.float32)
        self.waveform_bar.set_spectrum_envelope(peaks, float(waveform.duration_sec))

    def set_overview_envelope(self, envelope: list[float]) -> None:
        """Show the worker-extracted fallback overview for the loaded track."""
        self.waveform_bar.set_envelope(envelope)

    def show_analysis_pending(self) -> None:
        """Show analysis progress without hiding the current overview."""
        self._set_status_text(self.analysis_status_label, "Analyzing waveform...")

    def show_analysis_failure(self, message: str) -> None:
        """Report and announce analysis failure while retaining the current overview."""
        text = format_analysis_failure(message)
        self._set_status_text(self.analysis_status_label, text)
        announce(self.analysis_status_label, text)

    def show_analysis_complete(self) -> None:
        """Clear analysis progress without changing playback status."""
        self._set_status_text(self.analysis_status_label, "")

    def _probe_duration(self, file_path: Path) -> float:
        """Probe audio duration in seconds via soundfile, logging and returning 0.0 on error."""
        try:
            info = sf.info(str(file_path))
            return float(info.duration)
        except (OSError, ValueError, RuntimeError) as exc:
            logger.warning("Duration probe failed for %s: %s", file_path, exc)
            return 0.0

    @property
    def is_playing(self) -> bool:
        """Report whether the player holds a running track."""
        if self.player is None:
            return self.play_btn.text() == "Pause"
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def start_playback(self) -> None:
        """Start the loaded track and keep the button label in step."""
        if self.player is not None:
            self.player.play()
        self.play_btn.setText("Pause")

    def _handle_file_id_dropped(self, file_id: str) -> None:
        """Pass a dropped library file to the owner of the widget."""
        if self.on_file_dropped is None:
            return
        self.on_file_dropped(file_id)

    def _on_play_button_clicked(self) -> None:
        """Load the selection when the owner of the widget asks for it."""
        if self.on_play_selection_requested is not None and self.on_play_selection_requested():
            return
        self.toggle_play_pause()

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
        self.waveform_bar.set_playback(clamped, self.duration_sec)
        self._update_time_label()

    def _seek_by_ratio(self, ratio: float) -> None:
        """Seek to a normalized ratio in [0.0, 1.0] of total duration."""
        self.seek_to_seconds(ratio * self.duration_sec)

    def _on_media_position_changed(self, position_ms: int) -> None:
        """Handle positionChanged signal from QMediaPlayer."""
        if position_ms > 0:
            self.current_position_sec = position_ms / MS_PER_SECOND
            self.waveform_bar.set_playback(self.current_position_sec, self.duration_sec)
            self._update_time_label()

    def _on_media_duration_changed(self, duration_ms: int) -> None:
        """Handle durationChanged signal from QMediaPlayer."""
        if duration_ms <= 0:
            return
        if self.duration_sec <= 0.0:
            self.duration_sec = duration_ms / MS_PER_SECOND
            self.cue_positions = compute_cue_jump_positions(self.duration_sec)
        self.waveform_bar.set_playback(self.current_position_sec, self.duration_sec)
        self._update_time_label()

    def _update_time_label(self) -> None:
        """Show the current and total time, and mirror the position on the slider."""
        self.time_label.setText(
            f"{format_clock(self.current_position_sec)} / {format_clock(self.duration_sec)}"
        )
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self._sync_position_slider()
