"""The WAV fixture factory reuses one rendering but gives every caller its own file."""

from __future__ import annotations

from pathlib import Path

import soundfile as sf

from tests.fixtures.audio_factory import (
    DEFAULT_SAMPLE_RATE,
    create_wav_fixture,
    synthesize_rhythmic_chord_pcm,
)

FIXTURE_SECONDS = 0.5
CHANGED_BYTES = b"changed"


def test_cached_rendering_matches_direct_write(tmp_path: Path) -> None:
    """The cached bytes equal the WAV that soundfile writes straight to a file."""
    direct = tmp_path / "direct.wav"
    pcm = synthesize_rhythmic_chord_pcm(duration_sec=FIXTURE_SECONDS)
    sf.write(str(direct), pcm, DEFAULT_SAMPLE_RATE, subtype="PCM_16", format="WAV")

    fixture = create_wav_fixture(tmp_path / "fixture.wav", duration_sec=FIXTURE_SECONDS)

    assert fixture.read_bytes() == direct.read_bytes()


def test_each_call_writes_own_file(tmp_path: Path) -> None:
    """Two fixtures from one rendering are separate files that change independently."""
    first = create_wav_fixture(tmp_path / "first.wav", duration_sec=FIXTURE_SECONDS)
    second = create_wav_fixture(tmp_path / "second.wav", duration_sec=FIXTURE_SECONDS)
    original = second.read_bytes()

    first.write_bytes(CHANGED_BYTES)

    assert second.read_bytes() == original
    assert first.read_bytes() == CHANGED_BYTES


def test_tags_reach_only_tagged_copy(tmp_path: Path) -> None:
    """A tagged fixture leaves the cached untagged rendering untouched."""
    plain_before = create_wav_fixture(tmp_path / "before.wav", duration_sec=FIXTURE_SECONDS)
    tagged = create_wav_fixture(
        tmp_path / "tagged.wav", duration_sec=FIXTURE_SECONDS, tags={"artist": "Sasha"}
    )
    plain_after = create_wav_fixture(tmp_path / "after.wav", duration_sec=FIXTURE_SECONDS)

    assert tagged.read_bytes() != plain_before.read_bytes()
    assert plain_after.read_bytes() == plain_before.read_bytes()
