"""Tests for USB manifests and recovery journaling (D116-D121, D139-D148, D166, D191-D200)."""

from __future__ import annotations

import ast
import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from infiniteprep.services.usb._shared import MAX_USB_JSON_BYTES
from infiniteprep.services.usb.manifest import (
    COMPLETED_MANIFEST_FILE_NAME,
    IN_PROGRESS_MANIFEST_FILE_NAME,
    MANIFEST_STATE_COMPLETED,
    MANIFEST_STATE_IN_PROGRESS,
    USB_METADATA_DIR_NAME,
    ExportScope,
    ManifestReaderResult,
    ManifestTagEntry,
    ManifestTargetCounts,
    ManifestTrackOmission,
    ManifestTrackRef,
    ManifestTrackTags,
    UsbExportManifest,
    compute_my_tags_digest,
    read_completed_manifest,
    read_in_progress_manifest,
    remove_in_progress_manifest,
    write_completed_manifest,
    write_in_progress_manifest,
)
from infiniteprep.services.usb.recovery import (
    DEFAULT_RECOVERY_EXPIRY_DAYS,
    MAX_CLEANUP_HISTORY_RECORDS,
    ReconnectChoice,
    UsbJournalEntry,
    UsbRecoveryJournalStore,
)
from infiniteprep.services.usb.targets import TargetCandidate
from tests.unit.test_code_quality_invariants import (
    FORBIDDEN_CALL_NAMES,
    FORBIDDEN_COMMENT_MARKERS,
    FORBIDDEN_HASH_ATTRS,
    MAX_FUNCTION_LINES,
    MAX_LOCAL_VARIABLES,
    MAX_NESTING_LEVEL,
    compute_max_nesting,
    count_local_assignments,
    find_inner_loop_breaks,
)
from tests.unit.usb_preflight_support import (
    build_drive,
    build_inspection,
    build_partition,
)

REPO_SRC = Path(__file__).resolve().parents[2] / "src"
# Code points below 128 are ASCII.
ASCII_LIMIT = 128
EXPIRY_BASELINE_UTC = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
OVERDUE_SCAN_OFFSET = timedelta(days=31)
MANUAL_CLEANUP_OFFSET = timedelta(days=32)
BULK_CLEANUP_OFFSET = timedelta(days=33)
BULK_HISTORY_OVERFLOW_COUNT = 10


def _sample_vocabulary() -> list[ManifestTagEntry]:
    """Return a sample My Tags vocabulary."""
    return [
        ManifestTagEntry(category="Energy", name="Peak", parent="Club"),
        ManifestTagEntry(category="Mood", name="Dark", parent=""),
        ManifestTagEntry(category="Situation", name="Warmup", parent="Set"),
    ]


def _sample_assignments() -> list[ManifestTrackTags]:
    """Return sample My Tags assignments per track relative path."""
    return [
        ManifestTrackTags(relative_path="crate/b.flac", tags=("Warmup", "Dark")),
        ManifestTrackTags(relative_path="crate/a.aiff", tags=("Peak", "Dark")),
    ]


def _build_manifest(
    *,
    export_id: str = "exp-001",
    state: str = MANIFEST_STATE_COMPLETED,
    completed_at_utc: str | None = "2026-11-15T12:05:00Z",
    scope: ExportScope = ExportScope.MULTIPLE_PLAYLISTS,
    playlist_names: tuple[str, ...] = ("Warmup", "Peak Hour"),
) -> UsbExportManifest:
    """Build a populated USB export manifest fixture."""
    vocab = _sample_vocabulary()
    assignments = _sample_assignments()
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
        my_tags_sha256=compute_my_tags_digest(vocab, assignments),
    )


def _build_journal_entry(mount_point: str, export_id: str = "exp-001") -> UsbJournalEntry:
    """Build a computer-side journal entry aligned with the default test stick."""
    drive = build_drive()
    return UsbJournalEntry(
        export_id=export_id,
        started_at_utc="2026-10-01T12:00:00Z",
        volume_uuid="1A2B-3C4D",
        drive_identity=drive,
        mount_point=mount_point,
        scope=ExportScope.SINGLE_PLAYLIST,
        playlist_names=("Club Set",),
        track_paths=("crate/a.aiff", "crate/b.flac"),
        backup_path="backups/exp-001",
    )


class MyTagsDigestTests(unittest.TestCase):
    """Canonical SHA-256 digest over My Tags vocabulary and assignments."""

    def test_digest_is_order_independent_and_lowercase_sha256(self) -> None:
        vocab_a = _sample_vocabulary()
        vocab_b = list(reversed(vocab_a))
        assign_a = _sample_assignments()
        assign_b = [
            ManifestTrackTags(relative_path="crate/a.aiff", tags=("Dark", "Peak")),
            ManifestTrackTags(relative_path="crate/b.flac", tags=("Dark", "Warmup")),
        ]

        digest_a = compute_my_tags_digest(vocab_a, assign_a)
        digest_b = compute_my_tags_digest(vocab_b, assign_b)

        self.assertEqual(digest_a, digest_b)
        self.assertEqual(len(digest_a), 64)
        self.assertRegex(digest_a, r"^[0-9a-f]{64}$")

    def test_digest_changes_when_tag_data_or_assignment_changes(self) -> None:
        vocab = _sample_vocabulary()
        assignments = _sample_assignments()
        base = compute_my_tags_digest(vocab, assignments)

        renamed_vocab = [replace(vocab[0], name="Closing"), *vocab[1:]]
        recat_vocab = [replace(vocab[0], category="Vibe"), *vocab[1:]]
        changed_assign = [
            ManifestTrackTags(relative_path="crate/a.aiff", tags=("Peak",)),
            assignments[0],
        ]

        self.assertNotEqual(base, compute_my_tags_digest(renamed_vocab, assignments))
        self.assertNotEqual(base, compute_my_tags_digest(recat_vocab, assignments))
        self.assertNotEqual(base, compute_my_tags_digest(vocab, changed_assign))


class UsbExportManifestTests(unittest.TestCase):
    """Temporary in-progress and latest completed JSON manifests on the USB drive."""

    def test_in_progress_manifest_preserves_existing_completed_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mount_root = Path(tmp_dir)
            prior = _build_manifest(export_id="exp-prior", state=MANIFEST_STATE_COMPLETED)
            current = _build_manifest(
                export_id="exp-current",
                state=MANIFEST_STATE_IN_PROGRESS,
                completed_at_utc=None,
            )

            completed_path = write_completed_manifest(mount_root, prior)
            in_progress_path = write_in_progress_manifest(mount_root, current)

            self.assertEqual(
                completed_path, mount_root / USB_METADATA_DIR_NAME / COMPLETED_MANIFEST_FILE_NAME
            )
            self.assertEqual(
                in_progress_path,
                mount_root / USB_METADATA_DIR_NAME / IN_PROGRESS_MANIFEST_FILE_NAME,
            )
            self.assertEqual(read_completed_manifest(mount_root), prior)
            self.assertEqual(read_in_progress_manifest(mount_root), current)

    def test_completed_manifest_replaces_in_progress_and_round_trips_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mount_root = Path(tmp_dir)
            in_prog = _build_manifest(
                export_id="exp-new",
                state=MANIFEST_STATE_IN_PROGRESS,
                completed_at_utc=None,
            )
            completed = _build_manifest(
                export_id="exp-new",
                state=MANIFEST_STATE_COMPLETED,
                completed_at_utc="2026-11-15T12:10:00Z",
                scope=ExportScope.FULL_LIBRARY,
                playlist_names=(),
            )

            in_progress_path = write_in_progress_manifest(mount_root, in_prog)
            self.assertTrue(in_progress_path.is_file())
            completed_path = write_completed_manifest(mount_root, completed)

            self.assertTrue(completed_path.is_file())
            self.assertFalse(in_progress_path.exists())
            self.assertIsNone(read_in_progress_manifest(mount_root))
            # Equality covers every field the builder set, the omissions included.
            self.assertEqual(read_completed_manifest(mount_root), completed)

    def test_symlinked_metadata_dir_or_manifest_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            mount_root = root / "usb"
            outside_dir = root / "outside"
            mount_root.mkdir()
            outside_dir.mkdir()
            outside_file = outside_dir / "secret.json"
            outside_file.write_text('{"secret": true}\n', encoding="utf-8")

            in_prog = _build_manifest(
                export_id="exp-sym",
                state=MANIFEST_STATE_IN_PROGRESS,
                completed_at_utc=None,
            )

            meta_link = mount_root / USB_METADATA_DIR_NAME
            try:
                meta_link.symlink_to(outside_dir, target_is_directory=True)
            except OSError:
                self.skipTest("this platform cannot create a directory symlink")
            with self.assertRaises(ValueError):
                write_in_progress_manifest(mount_root, in_prog)
            with self.assertRaises(ValueError):
                read_in_progress_manifest(mount_root)
            meta_link.unlink()

            meta_dir = mount_root / USB_METADATA_DIR_NAME
            meta_dir.mkdir()
            file_link = meta_dir / IN_PROGRESS_MANIFEST_FILE_NAME
            file_link.symlink_to(outside_file)
            with self.assertRaises(ValueError):
                read_in_progress_manifest(mount_root)
            with self.assertRaises(ValueError):
                write_in_progress_manifest(mount_root, in_prog)
            with self.assertRaises(ValueError):
                remove_in_progress_manifest(mount_root)
            self.assertTrue(outside_file.exists())

    def test_posix_read_uses_nofollow_and_blocks_swapped_symlink(self) -> None:
        if not hasattr(os, "O_NOFOLLOW"):
            self.skipTest("this platform has no O_NOFOLLOW")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            mount_root = root / "usb"
            mount_root.mkdir()
            outside_file = root / "outside.json"
            outside_file.write_text("{}", encoding="utf-8")
            meta_dir = mount_root / USB_METADATA_DIR_NAME
            meta_dir.mkdir()
            (meta_dir / COMPLETED_MANIFEST_FILE_NAME).symlink_to(outside_file)
            with self.assertRaises(ValueError):
                read_completed_manifest(mount_root)

    def test_malformed_manifest_counts_and_types_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mount_root = Path(tmp_dir) / "usb"
            mount_root.mkdir()
            valid = _build_manifest()
            manifest_path = write_completed_manifest(mount_root, valid)
            base_payload = json.loads(manifest_path.read_text(encoding="utf-8"))

            invalid_patches = (
                {"track_count": -1},
                {"completed_at_utc": False},
                {"completed_at_utc": None, "state": MANIFEST_STATE_COMPLETED},
                {"target_counts": {"rekordbox": {"exported": True}}},
                {"target_counts": {"rekordbox": {"exported": "3"}}},
                {"target_counts": {"rekordbox": {"skipped": -2}}},
            )
            for patch in invalid_patches:
                corrupted = {**base_payload, **patch}
                manifest_path.write_text(json.dumps(corrupted), encoding="utf-8")
                with self.assertRaises(ValueError):
                    read_completed_manifest(mount_root)

    def test_oversized_manifest_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mount_root = Path(tmp_dir) / "usb"
            meta_dir = mount_root / USB_METADATA_DIR_NAME
            meta_dir.mkdir(parents=True)
            manifest_path = meta_dir / COMPLETED_MANIFEST_FILE_NAME
            manifest_path.write_bytes(b"{" + b" " * MAX_USB_JSON_BYTES + b"}")
            with self.assertRaises(ValueError):
                read_completed_manifest(mount_root)

    def test_manifest_normalizes_non_utc_and_dst_timestamps_to_utc(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mount_root = Path(tmp_dir) / "usb"
            mount_root.mkdir()
            manifest_path = write_completed_manifest(mount_root, _build_manifest())
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
            payload["started_at_utc"] = "2026-07-15T12:00:00-04:00"
            payload["completed_at_utc"] = "2026-11-15T14:00:00+02:00"
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")

            parsed = read_completed_manifest(mount_root)
            assert parsed is not None
            self.assertEqual(parsed.started_at_utc, "2026-07-15T16:00:00Z")
            self.assertEqual(parsed.completed_at_utc, "2026-11-15T12:00:00Z")

            payload["started_at_utc"] = "2026-11-15T12:00:00"
            manifest_path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(ValueError):
                read_completed_manifest(mount_root)


class UsbRecoveryMatchingTests(unittest.TestCase):
    """Volume ID and confirmation matching for reconnected USB targets."""

    def test_exact_volume_uuid_identity_and_mount_match_without_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mount_root = Path(tmp_dir) / "usb"
            mount_root.mkdir()
            store = UsbRecoveryJournalStore(Path(tmp_dir) / "usb_recovery_journal.json")
            entry = _build_journal_entry(str(mount_root))
            in_prog = _build_manifest(
                export_id=entry.export_id,
                state=MANIFEST_STATE_IN_PROGRESS,
                completed_at_utc=None,
            )
            store.start_export(entry, mount_root, in_prog)

            inspection = build_inspection(
                drive=entry.drive_identity,
                partitions=(build_partition(uuid="1A2B-3C4D", mount_points=(str(mount_root),)),),
            )
            candidate = TargetCandidate(name="sdb", inspection=inspection, error=None)
            match = store.match_reconnected_target(candidate)

            assert match is not None
            self.assertFalse(match.requires_confirmation)
            self.assertEqual(match.entry.export_id, entry.export_id)

    def test_uuid_match_with_changed_drive_or_mount_requires_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mount_root = Path(tmp_dir) / "usb"
            other_mount = Path(tmp_dir) / "usb2"
            mount_root.mkdir()
            other_mount.mkdir()
            store = UsbRecoveryJournalStore(Path(tmp_dir) / "usb_recovery_journal.json")
            entry = _build_journal_entry(str(mount_root))
            in_prog = _build_manifest(
                export_id=entry.export_id,
                state=MANIFEST_STATE_IN_PROGRESS,
                completed_at_utc=None,
            )
            store.start_export(entry, mount_root, in_prog)

            changed_mount = build_inspection(
                drive=entry.drive_identity,
                partitions=(build_partition(uuid="1A2B-3C4D", mount_points=(str(other_mount),)),),
            )
            changed_drive = build_inspection(
                drive=build_drive(model="Renamed Reader"),
                partitions=(build_partition(uuid="1A2B-3C4D", mount_points=(str(mount_root),)),),
            )

            match_mount = store.match_reconnected_target(
                TargetCandidate(name="sdb", inspection=changed_mount, error=None)
            )
            match_drive = store.match_reconnected_target(
                TargetCandidate(name="sdb", inspection=changed_drive, error=None)
            )

            assert match_mount is not None
            assert match_drive is not None
            self.assertTrue(match_mount.requires_confirmation)
            self.assertTrue(match_drive.requires_confirmation)

    def test_empty_uuid_matches_serial_and_marker_with_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mount_root = Path(tmp_dir) / "usb"
            mount_root.mkdir()
            store = UsbRecoveryJournalStore(Path(tmp_dir) / "usb_recovery_journal.json")
            entry = _build_journal_entry(str(mount_root))
            in_prog = _build_manifest(
                export_id=entry.export_id,
                state=MANIFEST_STATE_IN_PROGRESS,
                completed_at_utc=None,
            )
            store.start_export(entry, mount_root, in_prog)

            candidate_inspection = build_inspection(
                drive=build_drive(serial=entry.drive_identity.serial),
                partitions=(build_partition(uuid="", mount_points=(str(mount_root),)),),
            )
            match = store.match_reconnected_target(
                TargetCandidate(name="sdb", inspection=candidate_inspection, error=None)
            )

            assert match is not None
            self.assertTrue(match.requires_confirmation)
            self.assertEqual(match.entry.export_id, entry.export_id)

    def test_unrelated_target_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            mount_root = Path(tmp_dir) / "usb"
            other_root = Path(tmp_dir) / "other"
            mount_root.mkdir()
            other_root.mkdir()
            store = UsbRecoveryJournalStore(Path(tmp_dir) / "usb_recovery_journal.json")
            entry = _build_journal_entry(str(mount_root))
            in_prog = _build_manifest(
                export_id=entry.export_id,
                state=MANIFEST_STATE_IN_PROGRESS,
                completed_at_utc=None,
            )
            store.start_export(entry, mount_root, in_prog)

            other = build_inspection(
                drive=build_drive(serial="OTHER-SERIAL"),
                partitions=(build_partition(uuid="9999-8888", mount_points=(str(other_root),)),),
            )
            match = store.match_reconnected_target(
                TargetCandidate(name="sdc", inspection=other, error=None)
            )
            self.assertIsNone(match)


class UsbReconnectChoiceTests(unittest.TestCase):
    """Reconnect choices Resume, Restore, and Leave unchanged (D139, D166, D191)."""

    def _setup_store(self, tmp_dir: str) -> tuple[UsbRecoveryJournalStore, Path, UsbJournalEntry]:
        mount_root = Path(tmp_dir) / "usb"
        mount_root.mkdir()
        store = UsbRecoveryJournalStore(Path(tmp_dir) / "usb_recovery_journal.json")
        entry = _build_journal_entry(str(mount_root))
        in_prog = _build_manifest(
            export_id=entry.export_id,
            state=MANIFEST_STATE_IN_PROGRESS,
            completed_at_utc=None,
        )
        store.start_export(entry, mount_root, in_prog)
        return store, mount_root, entry

    def test_unconfirmed_choice_raises_when_confirmation_required(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store, mount_root, entry = self._setup_store(tmp_dir)
            partition = build_partition(uuid=entry.volume_uuid, mount_points=(str(mount_root),))
            inspection = build_inspection(
                drive=build_drive(model="Moved Port"),
                partitions=(partition,),
            )
            match = store.match_reconnected_target(
                TargetCandidate(name="sdb", inspection=inspection, error=None)
            )
            assert match is not None
            self.assertTrue(match.requires_confirmation)

            with self.assertRaises(ValueError):
                store.apply_reconnect_choice(
                    match, ReconnectChoice.RESUME, mount_root, confirmed=False
                )
            self.assertIsNotNone(read_in_progress_manifest(mount_root))

    def test_leave_unchanged_preserves_marker_and_journal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store, mount_root, entry = self._setup_store(tmp_dir)
            partition = build_partition(uuid=entry.volume_uuid, mount_points=(str(mount_root),))
            inspection = build_inspection(
                drive=entry.drive_identity,
                partitions=(partition,),
            )
            match = store.match_reconnected_target(
                TargetCandidate(name="sdb", inspection=inspection, error=None)
            )
            assert match is not None

            updated = store.apply_reconnect_choice(
                match, ReconnectChoice.LEAVE_UNCHANGED, mount_root
            )

            self.assertEqual(updated.last_recovery_action, ReconnectChoice.LEAVE_UNCHANGED.value)
            self.assertIsNotNone(read_in_progress_manifest(mount_root))
            self.assertEqual(len(store.load_entries()), 1)

    def test_resume_runs_callback_removes_marker_and_keeps_journal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store, mount_root, entry = self._setup_store(tmp_dir)
            partition = build_partition(uuid=entry.volume_uuid, mount_points=(str(mount_root),))
            inspection = build_inspection(
                drive=entry.drive_identity,
                partitions=(partition,),
            )
            match = store.match_reconnected_target(
                TargetCandidate(name="sdb", inspection=inspection, error=None)
            )
            assert match is not None
            calls: list[tuple[str, Path]] = []

            updated = store.apply_reconnect_choice(
                match,
                ReconnectChoice.RESUME,
                mount_root,
                resume_callback=lambda item, path: calls.append((item.export_id, path)),
            )

            self.assertEqual(calls, [(entry.export_id, mount_root)])
            self.assertEqual(updated.last_recovery_action, ReconnectChoice.RESUME.value)
            self.assertIsNone(read_in_progress_manifest(mount_root))
            persisted = store.load_entries()
            self.assertEqual(len(persisted), 1)
            self.assertEqual(persisted[0].last_recovery_action, ReconnectChoice.RESUME.value)

    def test_resume_and_restore_require_callback_before_marking_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store, mount_root, entry = self._setup_store(tmp_dir)
            partition = build_partition(uuid=entry.volume_uuid, mount_points=(str(mount_root),))
            inspection = build_inspection(
                drive=entry.drive_identity,
                partitions=(partition,),
            )
            candidate = TargetCandidate(name="sdb", inspection=inspection, error=None)
            match = store.match_reconnected_target(candidate)
            assert match is not None

            for choice in (ReconnectChoice.RESUME, ReconnectChoice.RESTORE):
                with self.assertRaises(ValueError):
                    store.apply_reconnect_choice(match, choice, mount_root)
                self.assertIsNotNone(read_in_progress_manifest(mount_root))
                self.assertEqual(store.load_entries()[0].last_recovery_action, "")
                self.assertIsNotNone(store.match_reconnected_target(candidate))

            def _failing_handler(item: UsbJournalEntry, path: Path) -> None:
                raise OSError("disk disconnected")

            with self.assertRaises(OSError):
                store.apply_reconnect_choice(
                    match,
                    ReconnectChoice.RESUME,
                    mount_root,
                    resume_callback=_failing_handler,
                )
            self.assertIsNotNone(read_in_progress_manifest(mount_root))
            self.assertEqual(store.load_entries()[0].last_recovery_action, "")
            self.assertIsNotNone(store.match_reconnected_target(candidate))

    def test_restore_runs_callback_removes_marker_and_keeps_journal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store, mount_root, entry = self._setup_store(tmp_dir)
            partition = build_partition(uuid=entry.volume_uuid, mount_points=(str(mount_root),))
            inspection = build_inspection(
                drive=entry.drive_identity,
                partitions=(partition,),
            )
            match = store.match_reconnected_target(
                TargetCandidate(name="sdb", inspection=inspection, error=None)
            )
            assert match is not None
            calls: list[tuple[str, Path]] = []

            updated = store.apply_reconnect_choice(
                match,
                ReconnectChoice.RESTORE,
                mount_root,
                restore_callback=lambda item, path: calls.append((item.export_id, path)),
            )

            self.assertEqual(calls, [(entry.export_id, mount_root)])
            self.assertEqual(updated.last_recovery_action, ReconnectChoice.RESTORE.value)
            self.assertIsNone(read_in_progress_manifest(mount_root))
            self.assertEqual(len(store.load_entries()), 1)

    def test_recovery_rejects_unrelated_mount_or_mismatched_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            store, mount_root, entry = self._setup_store(tmp_dir)
            wrong_mount = Path(tmp_dir) / "wrong_usb"
            wrong_mount.mkdir()
            write_in_progress_manifest(
                wrong_mount,
                _build_manifest(
                    export_id="exp-other",
                    state=MANIFEST_STATE_IN_PROGRESS,
                    completed_at_utc=None,
                ),
            )
            partition = build_partition(uuid=entry.volume_uuid, mount_points=(str(mount_root),))
            inspection = build_inspection(drive=entry.drive_identity, partitions=(partition,))
            match = store.match_reconnected_target(
                TargetCandidate(name="sdb", inspection=inspection, error=None)
            )
            assert match is not None
            calls: list[tuple[str, Path]] = []

            with self.assertRaises(ValueError):
                store.apply_reconnect_choice(
                    match,
                    ReconnectChoice.RESUME,
                    wrong_mount,
                    resume_callback=lambda item, path: calls.append((item.export_id, path)),
                )
            self.assertEqual(calls, [])
            self.assertIsNotNone(read_in_progress_manifest(wrong_mount))

            remove_in_progress_manifest(mount_root)
            with self.assertRaises(ValueError):
                store.apply_reconnect_choice(
                    match,
                    ReconnectChoice.RESTORE,
                    mount_root,
                    restore_callback=lambda item, path: calls.append((item.export_id, path)),
                )
            write_in_progress_manifest(
                mount_root,
                _build_manifest(
                    export_id="exp-mismatch",
                    state=MANIFEST_STATE_IN_PROGRESS,
                    completed_at_utc=None,
                ),
            )
            with self.assertRaises(ValueError):
                store.apply_reconnect_choice(
                    match,
                    ReconnectChoice.RESTORE,
                    mount_root,
                    restore_callback=lambda item, path: calls.append((item.export_id, path)),
                )
            self.assertEqual(calls, [])

    def test_journal_rejects_malformed_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            journal_path = Path(tmp_dir) / "usb_recovery_journal.json"
            store = UsbRecoveryJournalStore(journal_path)
            entry = _build_journal_entry(str(Path(tmp_dir) / "usb"))
            store.save_entries([entry])
            base_raw = json.loads(journal_path.read_text(encoding="utf-8"))

            for bad_payload in (
                {"version": 99, "entries": base_raw["entries"]},
                {
                    "version": 1,
                    "entries": [{**base_raw["entries"][0], "warned_expiry": "false"}],
                },
                {
                    "version": 1,
                    "entries": [{**base_raw["entries"][0], "last_recovery_action": "bad"}],
                },
                {
                    "version": 1,
                    "entries": [{**base_raw["entries"][0], "started_at_utc": "not-utc"}],
                },
                {
                    "version": 1,
                    "entries": [
                        {
                            **base_raw["entries"][0],
                            "drive_identity": {
                                **base_raw["entries"][0]["drive_identity"],
                                "is_removable": "false",
                            },
                        }
                    ],
                },
                {
                    "version": 1,
                    "entries": [
                        {
                            **base_raw["entries"][0],
                            "drive_identity": {
                                **base_raw["entries"][0]["drive_identity"],
                                "size_bytes": -10,
                            },
                        }
                    ],
                },
            ):
                journal_path.write_text(json.dumps(bad_payload), encoding="utf-8")
                with self.assertRaises(ValueError):
                    store.load_entries()

    def test_journal_rejects_symlink_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            outside = Path(tmp_dir) / "outside.json"
            outside.write_text('{"version": 1, "entries": []}\n', encoding="utf-8")
            journal_path = Path(tmp_dir) / "usb_recovery_journal.json"
            try:
                journal_path.symlink_to(outside)
            except OSError:
                self.skipTest("this platform cannot create a file symlink")
            store = UsbRecoveryJournalStore(journal_path)
            with self.assertRaises(ValueError):
                store.load_entries()


class UsbRecoveryExpiryTests(unittest.TestCase):
    """Verify 30-day default expiry, 1-day warning, overdue cleanup, and manual deletion."""

    def test_default_recovery_expiry_constant_is_30_days(self) -> None:
        self.assertEqual(DEFAULT_RECOVERY_EXPIRY_DAYS, 30)

    def test_one_day_warning_and_automatic_deletion_and_overdue_launch_cleanup(self) -> None:
        start = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = UsbRecoveryJournalStore(Path(tmp_dir) / "usb_recovery_journal.json")
            entry = _build_journal_entry("/media/dj/STICK")
            entry.started_at_utc = "2026-10-01T12:00:00Z"
            store.save_entries([entry])

            warn_scan = store.evaluate_expiry(start + timedelta(days=29, hours=1))
            self.assertEqual(len(warn_scan.warning_entries), 1)
            self.assertEqual(warn_scan.expired_deleted_entries, ())
            self.assertEqual(warn_scan.overdue_notice_entries, ())
            self.assertTrue(store.load_entries()[0].warned_expiry)

            expired_scan = store.evaluate_expiry(start + timedelta(days=30))
            self.assertEqual(expired_scan.warning_entries, ())
            self.assertEqual(len(expired_scan.expired_deleted_entries), 1)
            self.assertEqual(store.load_entries(), [])

            store.save_entries([entry])
            overdue_scan = store.evaluate_expiry(start + timedelta(days=31), at_launch=True)
            self.assertEqual(overdue_scan.warning_entries, ())
            self.assertEqual(overdue_scan.expired_deleted_entries, ())
            self.assertEqual(len(overdue_scan.overdue_notice_entries), 1)
            self.assertEqual(store.load_entries(), [])

    def test_disabled_expiry_keeps_entries_and_manual_cleanup_deletes(self) -> None:
        start = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = UsbRecoveryJournalStore(Path(tmp_dir) / "usb_recovery_journal.json")
            first = _build_journal_entry("/media/dj/STICK1", export_id="exp-1")
            second = _build_journal_entry("/media/dj/STICK2", export_id="exp-2")
            store.save_entries([first, second])

            disabled_scan = store.evaluate_expiry(
                start + timedelta(days=60), expiry_enabled=False, at_launch=True
            )
            self.assertEqual(disabled_scan.warning_entries, ())
            self.assertEqual(disabled_scan.expired_deleted_entries, ())
            self.assertEqual(disabled_scan.overdue_notice_entries, ())
            self.assertEqual(len(store.load_entries()), 2)

            removed = store.cleanup_unresolved(["exp-1"])
            self.assertEqual([item.export_id for item in removed], ["exp-1"])
            self.assertEqual([item.export_id for item in store.load_entries()], ["exp-2"])

            removed_rest = store.cleanup_unresolved()
            self.assertEqual([item.export_id for item in removed_rest], ["exp-2"])
            self.assertEqual(store.load_entries(), [])

    def test_expiry_and_manual_cleanup_preserve_resolved_and_record_history(self) -> None:
        start = EXPIRY_BASELINE_UTC
        with tempfile.TemporaryDirectory() as tmp_dir:
            store = UsbRecoveryJournalStore(Path(tmp_dir) / "usb_recovery_journal.json")
            resolved = replace(
                _build_journal_entry("/media/dj/STICK1", export_id="exp-resolved"),
                started_at_utc="2026-08-01T12:00:00Z",
                last_recovery_action=ReconnectChoice.RESUME.value,
            )
            expired = replace(
                _build_journal_entry("/media/dj/STICK2", export_id="exp-expired"),
                started_at_utc="2026-09-01T12:00:00Z",
            )
            active = replace(
                _build_journal_entry("/media/dj/STICK3", export_id="exp-active"),
                started_at_utc="2026-10-30T12:00:00Z",
            )
            store.save_entries([resolved, expired, active])

            scan = store.evaluate_expiry(start + OVERDUE_SCAN_OFFSET, at_launch=True)
            self.assertEqual(
                [item.export_id for item in scan.overdue_notice_entries], ["exp-expired"]
            )
            self.assertEqual(
                [item.export_id for item in store.load_entries()],
                ["exp-resolved", "exp-active"],
            )

            removed = store.cleanup_unresolved(now_utc=start + MANUAL_CLEANUP_OFFSET)
            self.assertEqual([item.export_id for item in removed], ["exp-active"])
            self.assertEqual([item.export_id for item in store.load_entries()], ["exp-resolved"])

            history = store.load_cleanup_history()
            self.assertEqual(
                [(item.export_id, item.reason) for item in history],
                [
                    ("exp-expired", "overdue"),
                    ("exp-active", "manual"),
                ],
            )

            bulk = [
                replace(
                    _build_journal_entry("/media/dj/STICK", export_id=f"exp-{idx}"),
                    started_at_utc="2026-10-01T12:00:00Z",
                )
                for idx in range(MAX_CLEANUP_HISTORY_RECORDS + BULK_HISTORY_OVERFLOW_COUNT)
            ]
            store.save_entries([resolved, *bulk])
            store.cleanup_unresolved(now_utc=start + BULK_CLEANUP_OFFSET)
            self.assertEqual(len(store.load_cleanup_history()), MAX_CLEANUP_HISTORY_RECORDS)
            self.assertEqual([item.export_id for item in store.load_entries()], ["exp-resolved"])

    def test_journal_store_rejects_posix_fifo_without_blocking(self) -> None:
        if not hasattr(os, "mkfifo"):
            self.skipTest("this platform has no named pipes")
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            fifo_path = root / "usb_recovery_journal.json"
            os.mkfifo(fifo_path)
            store = UsbRecoveryJournalStore(fifo_path, allowed_root=root)
            with self.assertRaises(ValueError):
                store.load_entries()

    def test_journal_store_rejects_symlink_parent_and_normalizes_offsets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = Path(tmp_dir)
            outside = root / "outside"
            outside.mkdir()
            linked_dir = root / "linked_parent"
            try:
                linked_dir.symlink_to(outside, target_is_directory=True)
            except OSError:
                linked_dir = None
            if linked_dir is not None:
                confined = UsbRecoveryJournalStore(
                    linked_dir / "nested" / "usb_recovery_journal.json",
                    allowed_root=root,
                )
                entry = _build_journal_entry("/media/dj/STICK1")
                with self.assertRaises(ValueError):
                    confined.save_entries([entry])
                outside_file = outside / "usb_recovery_journal.json"
                outside_file.write_text('{"version": 1, "entries": []}\n', encoding="utf-8")
                with self.assertRaises(ValueError):
                    confined.load_entries()

            journal_path = root / "usb_recovery_journal.json"
            direct_store = UsbRecoveryJournalStore(journal_path, allowed_root=root)
            direct_store.save_entries([_build_journal_entry("/media/dj/STICK2")])
            raw = json.loads(journal_path.read_text(encoding="utf-8"))
            raw["entries"][0]["started_at_utc"] = "2026-07-15T12:00:00-04:00"
            journal_path.write_text(json.dumps(raw), encoding="utf-8")
            reloaded = direct_store.load_entries()
            self.assertEqual(reloaded[0].started_at_utc, "2026-07-15T16:00:00Z")


class WorktreeCodeQualityInvariantsTests(unittest.TestCase):
    """AST, line length, and ASCII checks on worktree USB-5 source files."""

    TARGET_PATHS = (
        REPO_SRC / "infiniteprep" / "services" / "usb" / "_shared.py",
        REPO_SRC / "infiniteprep" / "services" / "usb" / "manifest.py",
        REPO_SRC / "infiniteprep" / "services" / "usb" / "recovery.py",
        REPO_SRC / "infiniteprep" / "services" / "usb" / "__init__.py",
        REPO_SRC / "infiniteprep" / "config" / "settings.py",
        REPO_SRC / "infiniteprep" / "ui" / "settings" / "storage.py",
        REPO_SRC / "infiniteprep" / "ui" / "usb_recovery_dialog.py",
    )

    def test_target_files_meet_quality_invariants(self) -> None:
        for file_path in self.TARGET_PATHS:
            raw_text = file_path.read_text(encoding="utf-8")
            # isascii() checks the whole text at C speed. Only a failing file is scanned
            # again to name the offset of the first non-ASCII character.
            if not raw_text.isascii():
                idx = next(idx for idx, ch in enumerate(raw_text) if ord(ch) >= ASCII_LIMIT)
                self.fail(f"Non-ASCII at {idx} in {file_path.name}")
            for line_num, line in enumerate(raw_text.splitlines(), start=1):
                self.assertLessEqual(
                    len(line), 100, f"Line {line_num} in {file_path.name} exceeds 100 chars"
                )
                if "#" in line:
                    comment_upper = line.partition("#")[2].upper()
                    for marker in FORBIDDEN_COMMENT_MARKERS:
                        self.assertNotIn(marker, comment_upper)
            tree = ast.parse(raw_text, filename=str(file_path))
            self._check_tree_invariants(tree, file_path.name)

    def _check_tree_invariants(self, tree: ast.Module, filename: str) -> None:
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end_line = getattr(node, "end_lineno", node.lineno) or node.lineno
                self.assertLessEqual(end_line - node.lineno + 1, MAX_FUNCTION_LINES, filename)
                self.assertLessEqual(count_local_assignments(node), MAX_LOCAL_VARIABLES, filename)
                self.assertLessEqual(compute_max_nesting(node), MAX_NESTING_LEVEL, filename)
                self.assertEqual(find_inner_loop_breaks(node), [], filename)
                self.assertIsNotNone(node.returns, f"Missing return annotation: {node.name}")
            elif isinstance(node, ast.Call):
                if isinstance(node.func, ast.Name):
                    self.assertNotIn(node.func.id, FORBIDDEN_CALL_NAMES, filename)
                elif isinstance(node.func, ast.Attribute):
                    self.assertNotIn(node.func.attr, FORBIDDEN_HASH_ATTRS, filename)


if __name__ == "__main__":
    unittest.main()
