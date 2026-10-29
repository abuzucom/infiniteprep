"""Safe XDJ-RX3 audio transcoder with Sibling/Export default mode and Backed-up In-Place mode."""

from __future__ import annotations

import math
import shutil
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import numpy as np
import platformdirs
import soundfile as sf
from scipy.signal import resample_poly

from infiniteprep.adapters.metadata_io.mutagen_adapter import MutagenAdapter, compute_sha256
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import AudioFormat, OperationStatus
from infiniteprep.domain.models import ChangeRecord, FileAsset


class TranscodeMode(StrEnum):
    """Output strategy for SafeTranscoderAdapter."""

    SIBLING_EXPORT = "sibling_export"
    IN_PLACE_BACKED_UP = "in_place_backed_up"


class TranscodePreset(StrEnum):
    """Target audio format and resolution presets for XDJ-RX3 compatibility."""

    XDJ_RX3_LOSSLESS_AUTO = "XDJ_RX3_LOSSLESS_AUTO"
    FORCE_WAV_48K_24B = "FORCE_WAV_48K_24B"
    FORCE_WAV_44K_16B = "FORCE_WAV_44K_16B"
    MP3_320_CBR = "MP3_320_CBR"


@dataclass
class TranscodeResult:
    """Summary of a completed safe transcode operation."""

    file_id: str
    source_path: Path
    output_path: Path
    target_sample_rate: int
    target_subtype: str
    change_record_id: str | None = None


class SafeTranscoderAdapter:
    """Converts >48kHz, 32-bit float, or OGG/M4A files into XDJ-RX3 compatible audio."""

    def __init__(
        self,
        repository: Repository,
        tag_adapter: MutagenAdapter | None = None,
        backup_dir: Path | None = None,
    ) -> None:
        """Initialize SafeTranscoderAdapter with Repository, MutagenAdapter, and backup_dir."""
        self.repo = repository
        self.tag_adapter = tag_adapter or MutagenAdapter()
        if backup_dir is None:
            base = Path(platformdirs.user_data_dir("InfinitePrep", "InfinitePrep"))
            backup_dir = base / "backups"
        self.backup_dir = backup_dir

    def transcode_file(
        self,
        file_id: str,
        *,
        preset: TranscodePreset = TranscodePreset.XDJ_RX3_LOSSLESS_AUTO,
        mode: TranscodeMode = TranscodeMode.SIBLING_EXPORT,
        export_dir: Path | None = None,
    ) -> TranscodeResult:
        """Transcode a FileAsset using polyphase resampling and copy approved tags."""
        asset = self.repo.get_file_by_id(file_id)
        if asset is None:
            raise ValueError(f"FileAsset {file_id} not found in database.")
        src_path = Path(asset.current_path)
        if not src_path.exists():
            raise FileNotFoundError(f"Source audio file missing at {src_path}.")

        pcm, src_sr = sf.read(str(src_path), dtype="float64", always_2d=True)
        target_sr, target_subtype, target_ext, sf_fmt = self._resolve_target_params(
            asset, src_sr, preset
        )
        resampled = self._resample_audio(pcm, int(src_sr), target_sr)

        out_path = self._determine_output_path(src_path, target_ext, mode, export_dir)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        record_id = None
        if mode == TranscodeMode.IN_PLACE_BACKED_UP:
            record_id = self._backup_and_journal_in_place(asset, src_path, out_path)

        sf.write(str(out_path), resampled, target_sr, subtype=target_subtype, format=sf_fmt)
        self._apply_effective_tags_to_output(asset, out_path)

        return TranscodeResult(
            file_id=asset.id,
            source_path=src_path,
            output_path=out_path,
            target_sample_rate=target_sr,
            target_subtype=target_subtype,
            change_record_id=record_id,
        )

    def _resolve_target_params(
        self, asset: FileAsset, src_sr: int, preset: TranscodePreset
    ) -> tuple[int, str, str, str]:
        """Determine (target_sample_rate, subtype, extension, sf_format) for preset."""
        if preset == TranscodePreset.FORCE_WAV_44K_16B:
            return 44100, "PCM_16", ".wav", "WAV"
        if preset == TranscodePreset.FORCE_WAV_48K_24B:
            return 48000, "PCM_24", ".wav", "WAV"

        # XDJ_RX3_LOSSLESS_AUTO: cap sample rate at 48kHz (or 44.1kHz for 88.2/176.4kHz multiples)
        target_sr = (44100 if (src_sr % 44100 == 0) else 48000) if src_sr > 48000 else src_sr

        if asset.format == AudioFormat.FLAC:
            return target_sr, "PCM_24", ".flac", "FLAC"
        if asset.format == AudioFormat.AIFF:
            return target_sr, "PCM_24", ".aiff", "AIFF"
        return target_sr, "PCM_24", ".wav", "WAV"

    def _resample_audio(self, pcm: np.ndarray, src_sr: int, target_sr: int) -> np.ndarray:
        """Resample multi-channel PCM using polyphase filtering when sample rates differ."""
        if src_sr == target_sr or src_sr <= 0 or target_sr <= 0:
            return np.clip(pcm, -0.999, 0.999)
        divisor = math.gcd(src_sr, target_sr)
        up = target_sr // divisor
        down = src_sr // divisor
        resampled = resample_poly(pcm, up, down, axis=0)
        return np.clip(resampled, -0.999, 0.999)

    def _determine_output_path(
        self,
        src_path: Path,
        target_ext: str,
        mode: TranscodeMode,
        export_dir: Path | None,
    ) -> Path:
        """Compute destination file path for SIBLING_EXPORT or IN_PLACE_BACKED_UP."""
        if mode == TranscodeMode.IN_PLACE_BACKED_UP:
            return src_path.with_suffix(target_ext)
        dest_folder = export_dir if export_dir is not None else src_path.parent
        return dest_folder / f"{src_path.stem}_XDJ_RX3{target_ext}"

    def _backup_and_journal_in_place(self, asset: FileAsset, src_path: Path, out_path: Path) -> str:
        """Create a SHA-256 verified backup and ChangeRecord before in-place replacement."""
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        backup_path = self.backup_dir / f"{asset.id}_transcode_{src_path.name}"
        pre_hash = compute_sha256(src_path)
        shutil.copy2(src_path, backup_path)
        if compute_sha256(backup_path) != pre_hash:
            raise OSError("Transcode backup SHA-256 verification failed; aborting.")
        op_id = str(uuid.uuid4())
        rec = ChangeRecord(
            operation_id=op_id,
            file_id=asset.id,
            previous_path=str(src_path),
            new_path=str(out_path),
            previous_tags=asset.original_tags.get("standard_tags", {}),
            written_tags={},
            backup_reference=str(backup_path),
            operation_status=OperationStatus.COMPLETED,
            timestamp=datetime.now(UTC).isoformat(),
            pre_write_hash=pre_hash,
        )
        self.repo.upsert_change_record(rec)
        return op_id

    def _apply_effective_tags_to_output(self, asset: FileAsset, out_path: Path) -> None:
        """Write original standard tags merged with user-approved fields onto output file."""
        std = dict(asset.original_tags.get("standard_tags", {}))
        approved = {
            k: v.approved_value for k, v in self.repo.get_approved_fields_map(asset.id).items()
        }
        effective = {**std, **approved}
        if effective:
            self.tag_adapter._write_tags_by_format(out_path, effective)
