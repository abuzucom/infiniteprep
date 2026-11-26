"""An in-place transcode keeps the library record in step with the file on disk."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.adapters.transcoder.safe_transcoder import (
    SafeTranscoderAdapter,
    TranscodeMode,
    TranscodePreset,
)
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import AudioFormat, FieldName
from infiniteprep.hashing import compute_sha256
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.review_service import ReviewService
from tests.fixtures.audio_factory import create_flac_fixture, create_wav_fixture

HIGH_SAMPLE_RATE: int = 96000


class InPlaceTranscodeAssetRefreshTests(unittest.TestCase):
    """The FileAsset row follows the replaced file."""

    def setUp(self) -> None:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.root = Path(temp_dir.name).resolve()
        database = DatabaseManager(":memory:")
        self.addCleanup(database.close)
        self.repo = Repository(database)
        self.adapter = MetadataTagAdapter()
        self.transcoder = SafeTranscoderAdapter(self.repo, backup_dir=self.root / "backups")

    def import_file(self, path: Path) -> str:
        """Import one file and return its asset id."""
        report = ImportService(self.repo, self.adapter).import_paths([path])
        return report.imported_files[0].id

    def test_same_path_transcode_refreshes_asset(self) -> None:
        """An in-place transcode to the same path stores the new hash and size."""
        source = create_wav_fixture(
            self.root / "crate" / "96k.wav",
            duration_sec=1.0,
            sample_rate=HIGH_SAMPLE_RATE,
            subtype="FLOAT",
        )
        file_id = self.import_file(source)

        self.transcoder.transcode_file(
            file_id,
            preset=TranscodePreset.XDJ_LOSSLESS_AUTO,
            mode=TranscodeMode.IN_PLACE_BACKED_UP,
        )

        asset = self.repo.get_file_by_id(file_id)
        assert asset is not None
        self.assertEqual(asset.content_hash, compute_sha256(source))
        self.assertEqual(asset.file_size, source.stat().st_size)
        self.assertEqual(Path(asset.current_path), source)

    def test_extension_change_moves_record(self) -> None:
        """A FLAC to WAV transcode points the record at the new WAV and its hash."""
        source = create_flac_fixture(self.root / "crate" / "track.flac", duration_sec=1.0)
        file_id = self.import_file(source)

        result = self.transcoder.transcode_file(
            file_id,
            preset=TranscodePreset.FORCE_WAV_44K_16B,
            mode=TranscodeMode.IN_PLACE_BACKED_UP,
        )

        asset = self.repo.get_file_by_id(file_id)
        assert asset is not None
        self.assertFalse(source.exists())
        self.assertEqual(Path(asset.current_path), result.output_path)
        self.assertTrue(Path(asset.current_path).exists())
        self.assertEqual(asset.format, AudioFormat.WAV)
        self.assertEqual(asset.content_hash, compute_sha256(result.output_path))

    def test_blank_manual_value_keeps_tag(self) -> None:
        """A blank manual artist leaves the original artist tag on the transcoded file."""
        source = create_wav_fixture(
            self.root / "crate" / "tagged.wav",
            duration_sec=1.0,
            sample_rate=HIGH_SAMPLE_RATE,
            subtype="FLOAT",
            tags={"artist": "Deep Dish", "title": "The Dream"},
        )
        file_id = self.import_file(source)
        ReviewService(self.repo).set_manual_field_value(file_id, FieldName.ARTIST.value, "")

        result = self.transcoder.transcode_file(
            file_id,
            preset=TranscodePreset.XDJ_LOSSLESS_AUTO,
            mode=TranscodeMode.SIBLING_EXPORT,
        )

        written = self.adapter.read_metadata(result.output_path)
        self.assertEqual(written.standard_tags.get(FieldName.ARTIST.value), "Deep Dish")


if __name__ == "__main__":
    unittest.main()
