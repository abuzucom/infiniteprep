"""Unit tests for Milestone 4: LocalDspAnalyzer, Rip Diagnostics, and EssentiaCliAnalyzer."""

from __future__ import annotations

import json
from pathlib import Path

import soundfile as sf

from infiniteprep.adapters.analyzers.essentia_ext import EssentiaCliAnalyzer
from infiniteprep.adapters.analyzers.local_dsp import LocalDspAnalyzer
from infiniteprep.domain.enums import KeyMode, PitchClass
from tests.fixtures.audio_factory import synthesize_rhythmic_chord_pcm


def test_local_dsp_analyzer_estimates_bpm_and_canonical_key(tmp_path: Path) -> None:
    """Verify LocalDspAnalyzer computes accurate BPM and canonical key on synthetic audio."""
    # Synthesize 8 seconds of 125.0 BPM A minor (A3=220, C4=261.63, E4=329.63)
    pcm = synthesize_rhythmic_chord_pcm(
        duration_sec=8.0,
        sample_rate=44100,
        bpm=125.0,
        frequencies_hz=(220.0, 261.63, 329.63),
    )
    wav_path = tmp_path / "aminor_125bpm.wav"
    sf.write(str(wav_path), pcm, 44100, subtype="PCM_16", format="WAV")

    analyzer = LocalDspAnalyzer()
    assert analyzer.is_available()[0] is True
    result = analyzer.analyze(wav_path, file_id="file-dsp-1")

    assert result.bpm is not None
    assert abs(result.bpm - 125.0) <= 3.5
    assert result.key_tonic == PitchClass.A
    assert result.key_mode == KeyMode.MINOR
    assert result.analysis_strength is not None
    assert result.analysis_strength > 0.2
    assert "waveform_envelope" in result.configuration
    assert len(result.configuration["waveform_envelope"]) == 64


def test_local_dsp_analyzer_detects_clipping_dc_offset_and_silence(tmp_path: Path) -> None:
    """Verify LocalDspAnalyzer emits warnings for clipping, DC offset, and leading silence."""
    pcm = synthesize_rhythmic_chord_pcm(
        duration_sec=6.0,
        sample_rate=44100,
        bpm=128.0,
        dc_offset=0.05,
        clip_signal=True,
        leading_silence_sec=2.5,
    )
    wav_path = tmp_path / "bad_vinyl_rip.wav"
    sf.write(str(wav_path), pcm, 44100, subtype="FLOAT", format="WAV")

    analyzer = LocalDspAnalyzer()
    result = analyzer.analyze(wav_path, file_id="file-rip-1")

    joined_warnings = " | ".join(result.warnings).lower()
    assert "clipping" in joined_warnings
    assert "dc offset" in joined_warnings
    assert "silence" in joined_warnings


def test_essentia_cli_analyzer_subprocess_adapter(tmp_path: Path) -> None:
    """Verify EssentiaCliAnalyzer parses JSON output from an external CLI binary (shell=False)."""
    wav_path = tmp_path / "track.wav"
    pcm = synthesize_rhythmic_chord_pcm(duration_sec=2.0, sample_rate=44100)
    sf.write(str(wav_path), pcm, 44100, subtype="PCM_16", format="WAV")

    fake_cli_bin = tmp_path / "essentia_streaming_extractor_music.exe"
    fake_cli_bin.write_bytes(b"FAKE_BINARY")

    def fake_runner(cmd: list[str]) -> None:
        out_json_path = Path(cmd[2])
        payload = {
            "rhythm": {"bpm": 126.4},
            "tonal": {"key_temperley": {"key": "F#", "scale": "minor", "strength": 0.87}},
        }
        out_json_path.write_text(json.dumps(payload), encoding="utf-8")

    ext_analyzer = EssentiaCliAnalyzer(cli_path=fake_cli_bin, runner=fake_runner)
    assert ext_analyzer.is_available()[0] is True
    res = ext_analyzer.analyze(wav_path, file_id="file-ess-1")
    assert res.bpm == 126.4
    assert res.key_tonic == PitchClass.F_SHARP
    assert res.key_mode == KeyMode.MINOR
    assert res.analysis_strength == 0.87
