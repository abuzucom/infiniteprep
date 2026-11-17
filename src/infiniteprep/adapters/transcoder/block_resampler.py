"""Block-wise polyphase resampling that never holds a whole audio file in memory.

Each block reads its own input frames plus enough context on both sides to cover the
resampling filter. The output of the context is discarded, so the joined blocks equal the
output of one whole-file resample_poly call.
"""

from __future__ import annotations

import math
from collections.abc import Iterator
from typing import Final

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

# About 6 seconds at 44.1 kHz. A block of 8 float64 channels then holds about 16 MiB.
TRANSCODE_BLOCK_FRAMES: Final[int] = 262_144
# resample_poly builds its default Kaiser window with this many taps per rate unit on each
# side of the center. The value tracks the scipy implementation.
FILTER_HALF_LENGTH_PER_RATE: Final[int] = 10
# One spare input frame on each side covers the rounding of the filter span.
FILTER_CONTEXT_SPARE_FRAMES: Final[int] = 1


def resample_ratio(source_rate: int, target_rate: int) -> tuple[int, int]:
    """Return the reduced (up, down) factors that turn source_rate into target_rate."""
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("Sample rates must be positive; check the source file header.")
    divisor = math.gcd(source_rate, target_rate)
    return target_rate // divisor, source_rate // divisor


def filter_context_frames(up: int, down: int) -> int:
    """Return the input frames each block reads on each side, as a multiple of down."""
    half_length = FILTER_HALF_LENGTH_PER_RATE * max(up, down)
    needed = -(-half_length // up) + FILTER_CONTEXT_SPARE_FRAMES
    return down * -(-needed // down)


def output_frame_count(input_frames: int, up: int, down: int) -> int:
    """Return the frame count resample_poly produces for input_frames input frames."""
    return -(-input_frames * up // down)


def count_source_frames(source: sf.SoundFile, block_frames: int = TRANSCODE_BLOCK_FRAMES) -> int:
    """Return the frame count of a stream that declares none, then rewind it.

    The count reads the stream once in blocks and keeps no samples.
    """
    total = 0
    for block in source.blocks(blocksize=block_frames, dtype="float32", always_2d=True):
        total += int(block.shape[0])
    source.seek(0)
    return total


def _read_exact(source: sf.SoundFile, start: int, frames: int) -> np.ndarray:
    """Read frames frames from start, raising ValueError when the stream ends early."""
    source.seek(start)
    chunk = source.read(frames, dtype="float64", always_2d=True)
    if chunk.shape[0] != frames:
        raise ValueError(
            "Audio stream ended before its declared length; re-encode the source file."
        )
    return chunk


def iter_resampled_blocks(
    source: sf.SoundFile,
    target_rate: int,
    block_frames: int = TRANSCODE_BLOCK_FRAMES,
) -> Iterator[np.ndarray]:
    """Yield the resampled audio of source in order, one bounded block at a time."""
    total = int(source.frames) if source.frames > 0 else count_source_frames(source)
    up, down = resample_ratio(int(source.samplerate), target_rate)
    if up == down:
        yield from _iter_plain_blocks(source, total, block_frames)
        return
    context = filter_context_frames(up, down)
    step = down * -(-block_frames // down)
    total_output = output_frame_count(total, up, down)
    for start in range(0, total, step):
        low = max(0, start - context)
        high = min(total, start + step + context)
        resampled = resample_poly(_read_exact(source, low, high - low), up, down, axis=0)
        # low and start are multiples of down, so both offsets are exact integers.
        first = (start - low) * up // down
        global_first = start * up // down
        global_end = min(total_output, (start + step) * up // down)
        yield resampled[first : first + global_end - global_first]


def _iter_plain_blocks(source: sf.SoundFile, total: int, block_frames: int) -> Iterator[np.ndarray]:
    """Yield the source frames unchanged when the rates already match."""
    for start in range(0, total, block_frames):
        yield _read_exact(source, start, min(block_frames, total - start))
