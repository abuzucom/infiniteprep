"""The overview extractor decodes regular files only."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from infiniteprep.ui.audio_player import (
    DEFAULT_ENVELOPE_BINS,
    FALLBACK_ENVELOPE_EMPTY,
    extract_waveform_envelope,
)


def test_directory_source_never_reaches_the_decoder(tmp_path: Path, monkeypatch: Any) -> None:
    """A non-regular source returns the empty overview without opening the path."""

    def fail_if_decoded(*_args: Any, **_kwargs: Any) -> None:
        pytest.fail("a non-regular source must not reach the audio decoder")

    monkeypatch.setattr("infiniteprep.ui.audio_player.sf.info", fail_if_decoded)
    monkeypatch.setattr("infiniteprep.ui.audio_player.sf.blocks", fail_if_decoded)

    envelope = extract_waveform_envelope(tmp_path)

    assert envelope == [FALLBACK_ENVELOPE_EMPTY] * DEFAULT_ENVELOPE_BINS
