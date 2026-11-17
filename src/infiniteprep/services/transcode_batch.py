"""Batch XDJ-RX3 transcode runner that isolates per-file failures."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

import soundfile as sf

from infiniteprep.adapters.transcoder.safe_transcoder import (
    SafeTranscoderAdapter,
    TranscodeMode,
    TranscodePreset,
    TranscodeResult,
)

logger = logging.getLogger(__name__)

# soundfile raises SoundFileRuntimeError, the base of LibsndfileError, on decode and
# encode failures. Other RuntimeError types surface as programming errors. A decode that
# runs out of memory fails that one file, and the batch continues.
EXPECTED_TRANSCODE_ERRORS: Final[tuple[type[Exception], ...]] = (
    OSError,
    ValueError,
    sf.SoundFileRuntimeError,
    MemoryError,
)


CancelCheck = Callable[[], bool]
ProgressCallback = Callable[[int, int, str], None]


@dataclass
class TranscodeBatchSummary:
    """Completed transcode results and per-file failure messages keyed by file_id."""

    results: list[TranscodeResult] = field(default_factory=list)
    failures: dict[str, str] = field(default_factory=dict)
    # True when a cancel request ended the batch before its last file.
    stopped_early: bool = False


def resolve_transcode_options(
    mode_value: str, preset_value: str
) -> tuple[TranscodeMode, TranscodePreset]:
    """Parse stored settings strings into enums, falling back to the non-destructive defaults."""
    try:
        mode = TranscodeMode(mode_value)
    except ValueError:
        logger.warning("Unknown transcode mode %r; using sibling_export.", mode_value)
        mode = TranscodeMode.SIBLING_EXPORT
    try:
        preset = TranscodePreset(preset_value)
    except ValueError:
        logger.warning("Unknown transcode preset %r; using XDJ_LOSSLESS_AUTO.", preset_value)
        preset = TranscodePreset.XDJ_LOSSLESS_AUTO
    return mode, preset


def transcode_files(
    transcoder: SafeTranscoderAdapter,
    file_ids: list[str],
    *,
    preset: TranscodePreset,
    mode: TranscodeMode,
    should_cancel: CancelCheck | None = None,
    progress: ProgressCallback | None = None,
) -> TranscodeBatchSummary:
    """Transcode each file in order and record a failure without stopping the batch.

    A true should_cancel stops before the next file and keeps the finished files.
    progress receives (file number, total, path) before each file.
    """
    summary = TranscodeBatchSummary()
    total = len(file_ids)
    for index, file_id in enumerate(file_ids):
        if should_cancel is not None and should_cancel():
            summary.stopped_early = True
            return summary
        if progress is not None:
            asset = transcoder.repo.get_file_by_id(file_id)
            progress(index + 1, total, asset.current_path if asset is not None else file_id)
        try:
            summary.results.append(transcoder.transcode_file(file_id, preset=preset, mode=mode))
        except EXPECTED_TRANSCODE_ERRORS as exc:
            logger.warning("Transcode failed for file %s: %s", file_id, exc.__class__.__name__)
            summary.failures[file_id] = f"{exc.__class__.__name__}: {exc}"
    return summary
