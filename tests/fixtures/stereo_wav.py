"""A small stereo WAV writer for the bounded decode tests."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import soundfile as sf

STEREO_WAV_SAMPLE_RATE = 8_000
# Different constant levels per channel make a wrong downmix visible.
CHANNEL_LEVELS = (0.1, 0.5)


def write_stereo_wav(path: Path, frames: int) -> Path:
    """Write a stereo PCM WAV whose two channels hold different constant levels."""
    samples = np.tile(CHANNEL_LEVELS, (frames, 1))
    sf.write(str(path), samples, STEREO_WAV_SAMPLE_RATE, subtype="PCM_16")
    return path
