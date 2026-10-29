"""Unit tests for domain enums, canonical key notation conversions, and domain models."""

from __future__ import annotations

from infiniteprep.domain.enums import (
    READ_SUPPORTED_FORMATS,
    WRITE_SUPPORTED_FORMATS,
    AudioFormat,
    EntityType,
    GroupingBasis,
    KeyDisplayNotation,
    KeyMode,
    PitchClass,
    ReviewStatus,
    SourceType,
    TaskStatus,
    format_canonical_key,
    parse_key_string,
)
from infiniteprep.domain.models import FileAsset, ReleaseDetails


def test_write_and_read_supported_formats_include_aiff_and_wav() -> None:
    """Verify MP3, FLAC, WAV, and AIFF are write-supported while OGG/M4A are read-only."""
    assert AudioFormat.MP3 in WRITE_SUPPORTED_FORMATS
    assert AudioFormat.FLAC in WRITE_SUPPORTED_FORMATS
    assert AudioFormat.WAV in WRITE_SUPPORTED_FORMATS
    assert AudioFormat.AIFF in WRITE_SUPPORTED_FORMATS
    assert AudioFormat.OGG not in WRITE_SUPPORTED_FORMATS
    assert AudioFormat.OGG in READ_SUPPORTED_FORMATS
    assert AudioFormat.UNSUPPORTED not in READ_SUPPORTED_FORMATS


def test_canonical_key_formatting_across_all_three_notations() -> None:
    """Verify Standard, Camelot, and Open Key display notations for canonical keys."""
    assert (
        format_canonical_key(PitchClass.F_SHARP, KeyMode.MINOR, KeyDisplayNotation.STANDARD)
        == "F#m"
    )
    assert (
        format_canonical_key(PitchClass.F_SHARP, KeyMode.MINOR, KeyDisplayNotation.CAMELOT) == "11A"
    )
    assert (
        format_canonical_key(PitchClass.F_SHARP, KeyMode.MINOR, KeyDisplayNotation.OPEN_KEY) == "4m"
    )

    assert format_canonical_key(PitchClass.A, KeyMode.MAJOR, KeyDisplayNotation.STANDARD) == "A"
    assert format_canonical_key(PitchClass.A, KeyMode.MAJOR, KeyDisplayNotation.CAMELOT) == "11B"
    assert format_canonical_key(PitchClass.A, KeyMode.MAJOR, KeyDisplayNotation.OPEN_KEY) == "4d"

    assert format_canonical_key(None, None, KeyDisplayNotation.STANDARD) == "unknown"


def test_parse_key_string_handles_camelot_open_key_and_enharmonic_strings() -> None:
    """Verify parse_key_string normalizes diverse DJ key notations into canonical tuples."""
    assert parse_key_string("11A") == (PitchClass.F_SHARP, KeyMode.MINOR)
    assert parse_key_string("4m") == (PitchClass.F_SHARP, KeyMode.MINOR)
    assert parse_key_string("F#m") == (PitchClass.F_SHARP, KeyMode.MINOR)
    assert parse_key_string("Gbm") == (PitchClass.F_SHARP, KeyMode.MINOR)
    assert parse_key_string("F# minor") == (PitchClass.F_SHARP, KeyMode.MINOR)
    assert parse_key_string("Db major") == (PitchClass.C_SHARP, KeyMode.MAJOR)
    assert parse_key_string("3B") == (PitchClass.C_SHARP, KeyMode.MAJOR)
    assert parse_key_string("8A") == (PitchClass.A, KeyMode.MINOR)
    assert parse_key_string("") is None
    assert parse_key_string(None) is None
    assert parse_key_string("not-a-key") is None


def test_release_details_roundtrip_and_file_asset_write_property() -> None:
    """Verify ReleaseDetails dict serialization and FileAsset.is_write_supported."""
    details = ReleaseDetails(
        release_title="The Dream",
        label="Tribal America",
        catalog_number="TRIUK014",
        vinyl_position="A1",
        year="1994",
    )
    restored = ReleaseDetails.from_dict(details.to_dict())
    assert restored == details
    assert ReleaseDetails.from_dict(None).release_title is None

    aiff_asset = FileAsset(
        id="f1",
        current_path="/music/track.aiff",
        original_path="/music/track.aiff",
        file_size=1024,
        content_hash="a" * 64,
        format=AudioFormat.AIFF,
        duration=360.0,
        original_tags={},
        import_batch_id="b1",
        source_group_id=None,
    )
    ogg_asset = FileAsset(
        id="f2",
        current_path="/music/track.ogg",
        original_path="/music/track.ogg",
        file_size=1024,
        content_hash="b" * 64,
        format=AudioFormat.OGG,
        duration=360.0,
        original_tags={},
        import_batch_id="b1",
        source_group_id=None,
    )
    assert aiff_asset.is_write_supported is True
    assert ogg_asset.is_write_supported is False
    assert aiff_asset.task_status == TaskStatus.IDLE
    assert aiff_asset.review_status == ReviewStatus.UNPROCESSED
    assert EntityType.RECORDING == "recording"
    assert GroupingBasis.FOLDER_HYPOTHESIS == "folder_hypothesis"
    assert SourceType.VINYL == "vinyl"
