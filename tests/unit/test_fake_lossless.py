"""Generated audio fixtures exercise fake-lossless detection and UI preflight wiring."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf
from scipy.signal import butter, sosfilt

from infiniteprep.adapters.transcoder.safe_transcoder import TranscodeMode, TranscodePreset
from infiniteprep.config.settings import AppSettings, SettingsManager
from infiniteprep.db.connection import Database
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import AudioFormat
from infiniteprep.domain.models import AnalysisResult, FileAsset
from infiniteprep.services.fake_lossless import (
    TRACE_AAC_SBR_GAP,
    TRACE_MP3_LOWPASS_SHELF,
    TRACE_SPECTRAL_CUTOFF,
    FakeLosslessMethod,
    FakeLosslessResult,
    SignalAnalysisDetector,
    available_methods,
    detect_fake_lossless,
    get_detector,
)
from infiniteprep.services.waveform_analysis import WaveformAnalysisCancelled
from infiniteprep.ui.action_handlers import (
    build_transcode_confirmation_text,
    collect_transcode_preflight_warnings,
)

SAMPLE_RATE_44K = 44100
SAMPLE_RATE_48K = 48000
FIXTURE_DURATION_SEC = 2.0


def _broadband_signal(sample_rate: int, seconds: float = FIXTURE_DURATION_SEC) -> np.ndarray:
    """Generate deterministic broadband audio spanning 100 Hz up to Nyquist."""
    rng = np.random.default_rng(42)
    num_samples = round(sample_rate * seconds)
    noise = rng.standard_normal(num_samples).astype(np.float32)
    peak = float(np.max(np.abs(noise)))
    return 0.4 * (noise / peak)


def _write_flac(path: Path, samples: np.ndarray, sample_rate: int) -> Path:
    """Write samples to a lossless FLAC fixture."""
    sf.write(path, samples, sample_rate, format="FLAC")
    return path


class FakeLosslessDetectionTests(unittest.TestCase):
    """Exercise fake-lossless detection, method interface, and preflight notices."""

    def test_full_band_lossless_passes(self) -> None:
        """A full-band 44.1 kHz FLAC fixture is not flagged as fake lossless."""
        with tempfile.TemporaryDirectory() as tmp:
            samples = _broadband_signal(SAMPLE_RATE_44K)
            source = _write_flac(Path(tmp) / "full_band.flac", samples, SAMPLE_RATE_44K)

            result = detect_fake_lossless(source)

            self.assertFalse(result.is_suspect)
            self.assertEqual(result.traces, ())
            self.assertEqual(result.confidence, 0.0)
            self.assertIsNotNone(result.cutoff_hz)
            assert result.cutoff_hz is not None
            self.assertGreaterEqual(result.cutoff_hz, 19500.0)
            self.assertIsNone(result.format_warning())

    def test_band_limited_fixture_flagged(self) -> None:
        """A 16 kHz band-limited FLAC fixture is flagged with spectral_cutoff."""
        with tempfile.TemporaryDirectory() as tmp:
            samples = _broadband_signal(SAMPLE_RATE_44K)
            sos = butter(8, 16000.0, btype="lowpass", fs=SAMPLE_RATE_44K, output="sos")
            filtered = sosfilt(sos, samples).astype(np.float32)
            source = _write_flac(Path(tmp) / "band_limited.flac", filtered, SAMPLE_RATE_44K)

            result = detect_fake_lossless(source)

            self.assertTrue(result.is_suspect)
            self.assertIn(TRACE_SPECTRAL_CUTOFF, result.traces)
            self.assertIsNotNone(result.cutoff_hz)
            assert result.cutoff_hz is not None
            self.assertLess(result.cutoff_hz, 17500.0)
            self.assertGreaterEqual(result.confidence, 0.5)
            warning = result.format_warning()
            self.assertIsNotNone(warning)
            assert warning is not None
            self.assertIn("Suspected fake lossless", warning)

    def test_mp3_shelf_fixture_flagged(self) -> None:
        """A sharp 18 kHz MP3-style lowpass shelf is flagged with mp3_lowpass_shelf."""
        with tempfile.TemporaryDirectory() as tmp:
            samples = _broadband_signal(SAMPLE_RATE_44K)
            sos = butter(16, 18000.0, btype="lowpass", fs=SAMPLE_RATE_44K, output="sos")
            shelved = sosfilt(sos, samples).astype(np.float32)
            source = _write_flac(Path(tmp) / "mp3_shelf.flac", shelved, SAMPLE_RATE_44K)

            result = detect_fake_lossless(source)

            self.assertTrue(result.is_suspect)
            self.assertIn(TRACE_MP3_LOWPASS_SHELF, result.traces)
            self.assertGreaterEqual(result.confidence, 0.65)

    def test_aac_sbr_gap_fixture_flagged(self) -> None:
        """A spectral notch at 11-12.5 kHz with upper SBR energy triggers aac_sbr_gap."""
        with tempfile.TemporaryDirectory() as tmp:
            samples = _broadband_signal(SAMPLE_RATE_48K)
            sos_stop = butter(
                10, [10800.0, 12800.0], btype="bandstop", fs=SAMPLE_RATE_48K, output="sos"
            )
            gapped = sosfilt(sos_stop, samples).astype(np.float32)
            source = _write_flac(Path(tmp) / "sbr_gap.flac", gapped, SAMPLE_RATE_48K)

            result = detect_fake_lossless(source)

            self.assertTrue(result.is_suspect)
            self.assertIn(TRACE_AAC_SBR_GAP, result.traces)
            self.assertGreaterEqual(result.confidence, 0.65)

    def test_non_lossless_format_is_not_flagged(self) -> None:
        """Lossy OGG files are not flagged as fake lossless."""
        with tempfile.TemporaryDirectory() as tmp:
            samples = _broadband_signal(SAMPLE_RATE_44K)
            source = Path(tmp) / "lossy.ogg"
            sf.write(source, samples, SAMPLE_RATE_44K, format="OGG")

            result = detect_fake_lossless(source)

            self.assertFalse(result.is_suspect)
            self.assertEqual(result.traces, ())
            self.assertEqual(result.confidence, 0.0)

    def test_method_interface_and_settings_roundtrip(self) -> None:
        """Detector registry and AppSettings expose selectable fake-lossless methods."""
        methods = available_methods()
        self.assertIn(FakeLosslessMethod.SIGNAL_ANALYSIS.value, methods)
        detector = get_detector(FakeLosslessMethod.SIGNAL_ANALYSIS.value)
        self.assertIsInstance(detector, SignalAnalysisDetector)

        with tempfile.TemporaryDirectory() as tmp:
            settings_path = Path(tmp) / "settings.json"
            manager = SettingsManager(settings_path)
            loaded = manager.load()
            self.assertEqual(loaded.fake_lossless_method, FakeLosslessMethod.SIGNAL_ANALYSIS.value)

            manager.save(AppSettings(fake_lossless_method=FakeLosslessMethod.SIGNAL_ANALYSIS.value))
            reloaded = manager.load()
            self.assertEqual(
                reloaded.fake_lossless_method, FakeLosslessMethod.SIGNAL_ANALYSIS.value
            )

    def test_cancelled_detection_exits_early(self) -> None:
        """A preset cancel event raises WaveformAnalysisCancelled before file access."""
        cancel_event = threading.Event()
        cancel_event.set()

        with (
            tempfile.TemporaryDirectory() as tmp,
            self.assertRaises(WaveformAnalysisCancelled),
        ):
            detect_fake_lossless(Path(tmp) / "missing.flac", cancel_event=cancel_event)

    def test_transcode_preflight_surfaces_fake_lossless_warning_without_blocking(self) -> None:
        """Fake-lossless analysis warnings appear in transcode preflight confirmation text."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db = Database(tmp_path / "test.db")
            db.initialize_schema()
            try:
                repo = Repository(db)
                track = tmp_path / "suspect.flac"
                track.write_bytes(b"placeholder")
                asset = FileAsset(
                    id="file-1",
                    original_path=str(track),
                    current_path=str(track),
                    format=AudioFormat.FLAC,
                    file_size=11,
                    file_hash="hash-1",
                    duration=120.0,
                    sample_rate=44100,
                )
                repo.upsert_file_asset(asset)

                result = FakeLosslessResult(
                    is_suspect=True,
                    cutoff_hz=16000.0,
                    sample_rate=44100,
                    nyquist_hz=22050.0,
                    traces=(TRACE_SPECTRAL_CUTOFF, TRACE_MP3_LOWPASS_SHELF),
                    confidence=0.88,
                    method=FakeLosslessMethod.SIGNAL_ANALYSIS.value,
                )
                warning = result.format_warning()
                self.assertIsNotNone(warning)
                assert warning is not None
                repo.upsert_analysis_result(
                    AnalysisResult(
                        id="fake-lossless-file-1",
                        file_id="file-1",
                        analyzer_name="fake_lossless",
                        analyzer_version="1",
                        configuration={"method": result.method},
                        bpm=None,
                        key_tonic=None,
                        key_mode=None,
                        analysis_strength=result.confidence,
                        warnings=[warning],
                    )
                )

                notices = collect_transcode_preflight_warnings(repo, ["file-1"])
                self.assertEqual(len(notices), 1)
                self.assertIn("suspect.flac", notices[0])
                self.assertIn("16.0 kHz", notices[0])

                prompt = build_transcode_confirmation_text(
                    1,
                    TranscodeMode.SIBLING_EXPORT,
                    TranscodePreset.XDJ_LOSSLESS_AUTO,
                    preflight_warnings=notices,
                )
                self.assertIn("Preflight notices (non-blocking):", prompt)
                self.assertIn("suspect.flac", prompt)
            finally:
                db.close()
