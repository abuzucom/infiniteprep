"""Generated audio fixtures exercise fake-lossless detection and UI preflight wiring."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

import numpy as np
import soundfile as sf
from PySide6.QtWidgets import QApplication
from scipy.signal import butter, sosfilt

from infiniteprep.adapters.transcoder.safe_transcoder import TranscodeMode, TranscodePreset
from infiniteprep.config.settings import AppSettings, SettingsManager
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import AudioFormat
from infiniteprep.domain.models import AnalysisResult, FileAsset
from infiniteprep.services.fake_lossless import (
    MIN_ANALYSIS_SAMPLE_RATE_HZ,
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
from infiniteprep.services.review_service import ReviewService
from infiniteprep.services.waveform_analysis import (
    WaveformAnalysisCancelled,
    analyze_waveform,
)
from infiniteprep.ui.action_handlers import (
    build_transcode_confirmation_text,
    collect_transcode_preflight_warnings,
)
from infiniteprep.ui.audio_player import (
    DEFAULT_ENVELOPE_BINS,
    FALLBACK_ENVELOPE_ERROR,
    MAX_ANALYSIS_CHANNELS,
    MAX_ANALYSIS_SAMPLE_RATE_HZ,
    MAX_DECODED_SAMPLES,
    bounded_frame_count,
    extract_waveform_envelope,
)
from infiniteprep.ui.detail_panel import DetailPanel

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

    def test_ring_buffer_advances_past_max_stored_frames(self) -> None:
        """Full-band energy after 512 frames updates distinct ring slots."""
        with tempfile.TemporaryDirectory() as tmp:
            intro = _broadband_signal(SAMPLE_RATE_44K, seconds=25.0)
            sos = butter(8, 16000.0, btype="lowpass", fs=SAMPLE_RATE_44K, output="sos")
            band_limited_intro = sosfilt(sos, intro).astype(np.float32)
            full_band_tail = _broadband_signal(SAMPLE_RATE_44K, seconds=4.0)
            combined = np.concatenate((band_limited_intro, full_band_tail))
            source = _write_flac(Path(tmp) / "late_full_band.flac", combined, SAMPLE_RATE_44K)

            result = detect_fake_lossless(source)

            self.assertFalse(result.is_suspect)
            self.assertEqual(result.traces, ())
            self.assertIsNotNone(result.cutoff_hz)
            assert result.cutoff_hz is not None
            self.assertGreaterEqual(result.cutoff_hz, 19500.0)

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

    def test_min_analysis_sample_rate_boundary_is_analyzed(self) -> None:
        """A 22050 Hz lossless track at the minimum rate boundary is analyzed."""
        with tempfile.TemporaryDirectory() as tmp:
            samples = _broadband_signal(MIN_ANALYSIS_SAMPLE_RATE_HZ)
            sos = butter(
                8,
                7000.0,
                btype="lowpass",
                fs=MIN_ANALYSIS_SAMPLE_RATE_HZ,
                output="sos",
            )
            filtered = sosfilt(sos, samples).astype(np.float32)
            source = _write_flac(
                Path(tmp) / "boundary_22050.flac",
                filtered,
                MIN_ANALYSIS_SAMPLE_RATE_HZ,
            )

            result = detect_fake_lossless(source)

            self.assertTrue(result.is_suspect)
            self.assertIn(TRACE_SPECTRAL_CUTOFF, result.traces)
            self.assertEqual(result.sample_rate, MIN_ANALYSIS_SAMPLE_RATE_HZ)

    def test_oversized_channels_and_sample_rate_rejected(self) -> None:
        """Excessive channels or sample rates are rejected before full decoding."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            too_many_channels = MAX_ANALYSIS_CHANNELS + 1
            samples = np.zeros((1024, too_many_channels), dtype=np.float32)
            bad_wav = tmp_path / "too_many_channels.wav"
            sf.write(bad_wav, samples, SAMPLE_RATE_44K, format="WAV")

            with self.assertRaises(ValueError):
                detect_fake_lossless(bad_wav)
            with self.assertRaises(ValueError):
                analyze_waveform(bad_wav, cache_directory=tmp_path / "cache")
            envelope = extract_waveform_envelope(bad_wav, bins=DEFAULT_ENVELOPE_BINS)
            self.assertEqual(envelope, [FALLBACK_ENVELOPE_ERROR] * DEFAULT_ENVELOPE_BINS)

        with self.assertRaises(ValueError):
            bounded_frame_count(MAX_ANALYSIS_SAMPLE_RATE_HZ + 1, 2, 1000)
        with self.assertRaises(ValueError):
            bounded_frame_count(SAMPLE_RATE_44K, 0, 1000)
        capped = bounded_frame_count(MAX_ANALYSIS_SAMPLE_RATE_HZ, MAX_ANALYSIS_CHANNELS, 10**9)
        self.assertLessEqual(capped * MAX_ANALYSIS_CHANNELS, MAX_DECODED_SAMPLES)

    def test_transcode_preflight_surfaces_fake_lossless_warning_without_blocking(self) -> None:
        """Fake-lossless analysis warnings appear in transcode preflight confirmation text."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db = DatabaseManager(tmp_path / "test.db")
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
                    content_hash="hash-1",
                    duration=120.0,
                    original_tags={},
                    import_batch_id="batch-1",
                    source_group_id=None,
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

    def test_detail_panel_persists_fake_lossless_and_ignores_stale_generation(self) -> None:
        """DetailPanel persists current fake-lossless results and ignores stale generations."""
        _app = QApplication.instance() or QApplication([])
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            db = DatabaseManager(tmp_path / "test.db")
            panel: DetailPanel | None = None
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
                    content_hash="hash-1",
                    duration=120.0,
                    original_tags={},
                    import_batch_id="batch-1",
                    source_group_id=None,
                )
                repo.upsert_file_asset(asset)

                panel = DetailPanel(
                    repo,
                    ReviewService(repo),
                    enable_audio_backend=False,
                    settings_manager=SettingsManager(tmp_path / "settings.json"),
                )
                panel.current_file_id = "file-1"
                panel._analysis_generation = 2
                panel._latest_generation_by_file["file-1"] = 2

                suspect = FakeLosslessResult(
                    is_suspect=True,
                    cutoff_hz=16000.0,
                    sample_rate=44100,
                    nyquist_hz=22050.0,
                    traces=(TRACE_SPECTRAL_CUTOFF,),
                    confidence=0.82,
                    method=FakeLosslessMethod.SIGNAL_ANALYSIS.value,
                )
                panel._on_fake_lossless_ready("file-1", 2, suspect)

                stored = repo.list_analysis_results("file-1")
                self.assertEqual(len(stored), 1)
                self.assertEqual(stored[0].analyzer_name, "fake_lossless")
                self.assertEqual(stored[0].configuration["cutoff_hz"], 16000.0)
                self.assertEqual(stored[0].configuration["traces"], [TRACE_SPECTRAL_CUTOFF])
                self.assertEqual(len(stored[0].warnings), 1)
                self.assertIn("Suspected fake lossless", panel.warnings_label.text())

                stale_clean = FakeLosslessResult(
                    is_suspect=False,
                    cutoff_hz=21000.0,
                    sample_rate=44100,
                    nyquist_hz=22050.0,
                    traces=(),
                    confidence=0.0,
                    method=FakeLosslessMethod.SIGNAL_ANALYSIS.value,
                )
                panel._on_fake_lossless_ready("file-1", 1, stale_clean)

                after_stale = repo.list_analysis_results("file-1")
                self.assertEqual(len(after_stale), 1)
                self.assertEqual(len(after_stale[0].warnings), 1)
                self.assertIn("Suspected fake lossless", panel.warnings_label.text())
            finally:
                if panel is not None:
                    panel.close()
                db.close()
