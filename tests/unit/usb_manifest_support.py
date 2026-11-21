"""Shared USB export manifest and journal entry builders for the USB recovery tests."""

from __future__ import annotations

from infiniteprep.services.usb.manifest import (
    MANIFEST_STATE_COMPLETED,
    ExportScope,
    ManifestReaderResult,
    ManifestTagEntry,
    ManifestTargetCounts,
    ManifestTrackOmission,
    ManifestTrackRef,
    ManifestTrackTags,
    UsbExportManifest,
    compute_my_tags_digest,
)
from infiniteprep.services.usb.recovery import UsbJournalEntry
from tests.unit.usb_preflight_support import build_drive


def build_sample_vocabulary() -> list[ManifestTagEntry]:
    """Return a sample My Tags vocabulary."""
    return [
        ManifestTagEntry(category="Energy", name="Peak", parent="Club"),
        ManifestTagEntry(category="Mood", name="Dark", parent=""),
        ManifestTagEntry(category="Situation", name="Warmup", parent="Set"),
    ]


def build_sample_assignments() -> list[ManifestTrackTags]:
    """Return sample My Tags assignments per track relative path."""
    return [
        ManifestTrackTags(relative_path="crate/b.flac", tags=("Warmup", "Dark")),
        ManifestTrackTags(relative_path="crate/a.aiff", tags=("Peak", "Dark")),
    ]


def build_manifest(
    *,
    export_id: str = "exp-001",
    state: str = MANIFEST_STATE_COMPLETED,
    completed_at_utc: str | None = "2026-11-15T12:05:00Z",
    scope: ExportScope = ExportScope.MULTIPLE_PLAYLISTS,
    playlist_names: tuple[str, ...] = ("Warmup", "Peak Hour"),
) -> UsbExportManifest:
    """Build a populated USB export manifest fixture."""
    vocabulary = build_sample_vocabulary()
    assignments = build_sample_assignments()
    return UsbExportManifest(
        export_id=export_id,
        state=state,
        started_at_utc="2026-11-15T12:00:00Z",
        completed_at_utc=completed_at_utc,
        app_version="0.63.0",
        adapter_versions={"rekordbox": "6.8.0", "serato": "3.1.2"},
        analysis_versions={"bpm": "1.2", "key": "2.0", "waveform": "1.0"},
        readers=(
            ManifestReaderResult(name="id3", version="2.4", result="ok"),
            ManifestReaderResult(name="flac", version="1.4", result="ok"),
        ),
        scope=scope,
        playlist_names=playlist_names,
        track_count=2,
        target_counts={
            "rekordbox": ManifestTargetCounts(
                exported=1, exported_with_omissions=1, skipped=0, failed=0
            ),
            "serato": ManifestTargetCounts(
                exported=2, exported_with_omissions=0, skipped=0, failed=0
            ),
        },
        omissions=(
            ManifestTrackOmission(
                track=ManifestTrackRef(
                    relative_path="crate/b.flac",
                    title="Night Drive",
                    artist="DJ Example",
                ),
                field_name="composer",
                source_value="Composed By Name",
                reason="unsupported_in_target_schema",
            ),
        ),
        my_tags_sha256=compute_my_tags_digest(vocabulary, assignments),
    )


def build_journal_entry(mount_point: str, export_id: str = "exp-001") -> UsbJournalEntry:
    """Build a computer-side journal entry aligned with the default test stick."""
    return UsbJournalEntry(
        export_id=export_id,
        started_at_utc="2026-10-01T12:00:00Z",
        volume_uuid="1A2B-3C4D",
        drive_identity=build_drive(),
        mount_point=mount_point,
        scope=ExportScope.SINGLE_PLAYLIST,
        playlist_names=("Club Set",),
        track_paths=("crate/a.aiff", "crate/b.flac"),
        backup_path="backups/exp-001",
    )
