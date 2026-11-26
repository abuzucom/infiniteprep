"""Tests that USB manifest and journal parsing rejects unsafe paths and fails with ValueError."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from infiniteprep.services.usb._shared import MAX_METADATA_TEXT_LENGTH, sanitize_mount_point
from infiniteprep.services.usb.manifest import (
    IN_PROGRESS_MANIFEST_FILE_NAME,
    MANIFEST_STATE_IN_PROGRESS,
    USB_METADATA_DIR_NAME,
    read_in_progress_manifest,
    write_in_progress_manifest,
)
from infiniteprep.services.usb.recovery import JOURNAL_FILE_NAME, UsbRecoveryJournalStore
from tests.unit.usb_manifest_support import build_journal_entry, build_manifest

# Far past the interpreter recursion limit, so the JSON parser cannot descend this deep.
NESTING_DEPTH = 200_000
DEEPLY_NESTED_JSON = "[" * NESTING_DEPTH + "]" * NESTING_DEPTH
# Valid ISO-8601 text whose conversion to UTC falls before year 1.
OUT_OF_RANGE_TIMESTAMP = "0001-01-01T00:00:00+01:00"
ZERO_WIDTH_SPACE = chr(0x200B)
ESCAPE = chr(0x1B)
NUL = chr(0)
UNSAFE_RELATIVE_PATHS = (
    "../outside.aiff",
    "crate/../../outside.aiff",
    "crate\\..\\..\\outside.aiff",
    "/etc/passwd",
    "\\\\server\\share\\x.aiff",
    "C:\\Windows\\x.aiff",
    "C:x.aiff",
    "crate/line\nbreak.aiff",
)
UNSAFE_MOUNT_POINTS = (
    "relative/stick",
    "/media/../etc",
    "/media/stick\nfake line",
    f"/media/{ESCAPE}[2Jstick",
)


def marker_path(mount_root: Path) -> Path:
    """Return the in-progress marker path on mount_root."""
    return mount_root / USB_METADATA_DIR_NAME / IN_PROGRESS_MANIFEST_FILE_NAME


def marker_payload(mount_root: Path) -> Any:
    """Write a valid in-progress marker and return its parsed JSON."""
    manifest = build_manifest(state=MANIFEST_STATE_IN_PROGRESS, completed_at_utc=None)
    write_in_progress_manifest(mount_root, manifest)
    return json.loads(marker_path(mount_root).read_text(encoding="utf-8"))


def journal_payload(tmp_path: Path) -> Any:
    """Save a valid one-entry recovery journal and return its parsed JSON."""
    store = UsbRecoveryJournalStore(tmp_path / JOURNAL_FILE_NAME)
    store.save_entries([build_journal_entry(str(tmp_path / "stick"))])
    return json.loads((tmp_path / JOURNAL_FILE_NAME).read_text(encoding="utf-8"))


def load_journal_text(tmp_path: Path, text: str) -> None:
    """Write text as the journal and load its entries."""
    (tmp_path / JOURNAL_FILE_NAME).write_text(text, encoding="utf-8")
    UsbRecoveryJournalStore(tmp_path / JOURNAL_FILE_NAME).load_entries()


def read_marker_text(mount_root: Path, text: str) -> None:
    """Replace the marker on mount_root with text and read it."""
    marker_payload(mount_root)
    marker_path(mount_root).write_text(text, encoding="utf-8")
    read_in_progress_manifest(mount_root)


def test_deep_marker_raises_value_error(tmp_path: Path) -> None:
    """A marker nested past the recursion limit fails as malformed, not with RecursionError."""
    with pytest.raises(ValueError, match="malformed"):
        read_marker_text(tmp_path, DEEPLY_NESTED_JSON)


def test_deep_journal_raises_value_error(tmp_path: Path) -> None:
    """A journal nested past the recursion limit fails as malformed."""
    with pytest.raises(ValueError, match="malformed"):
        load_journal_text(tmp_path, DEEPLY_NESTED_JSON)


def test_marker_time_outside_range_raises(tmp_path: Path) -> None:
    """A timestamp that overflows on conversion to UTC fails with ValueError."""
    payload = marker_payload(tmp_path)
    payload["started_at_utc"] = OUT_OF_RANGE_TIMESTAMP

    with pytest.raises(ValueError, match="started_at_utc"):
        read_marker_text(tmp_path, json.dumps(payload))


def test_journal_time_outside_range_raises(tmp_path: Path) -> None:
    """A journal entry timestamp that overflows on conversion fails with ValueError."""
    payload = journal_payload(tmp_path)
    payload["entries"][0]["started_at_utc"] = OUT_OF_RANGE_TIMESTAMP

    with pytest.raises(ValueError, match="started_at_utc"):
        load_journal_text(tmp_path, json.dumps(payload))


@pytest.mark.parametrize("relative_path", [*UNSAFE_RELATIVE_PATHS, ""])
def test_marker_refuses_unsafe_track_path(tmp_path: Path, relative_path: str) -> None:
    """An omission track path must stay below the drive root."""
    payload = marker_payload(tmp_path)
    payload["omissions"][0]["track"]["relative_path"] = relative_path

    with pytest.raises(ValueError, match="relative_path"):
        read_marker_text(tmp_path, json.dumps(payload))


@pytest.mark.parametrize("relative_path", [*UNSAFE_RELATIVE_PATHS, ""])
def test_journal_refuses_unsafe_track_path(tmp_path: Path, relative_path: str) -> None:
    """Each journal track path must stay below the drive root."""
    payload = journal_payload(tmp_path)
    payload["entries"][0]["track_paths"] = ["crate/a.aiff", relative_path]

    with pytest.raises(ValueError, match="track_paths"):
        load_journal_text(tmp_path, json.dumps(payload))


@pytest.mark.parametrize("backup_path", UNSAFE_RELATIVE_PATHS)
def test_journal_refuses_unsafe_backup_path(tmp_path: Path, backup_path: str) -> None:
    """A backup path must stay below the backup root. An empty value means no backup."""
    payload = journal_payload(tmp_path)
    payload["entries"][0]["backup_path"] = backup_path

    with pytest.raises(ValueError, match="backup_path"):
        load_journal_text(tmp_path, json.dumps(payload))


@pytest.mark.parametrize("mount_point", UNSAFE_MOUNT_POINTS)
def test_journal_refuses_unsafe_mount_point(tmp_path: Path, mount_point: str) -> None:
    """A journal mount point must be an absolute path without traversal or control text."""
    payload = journal_payload(tmp_path)
    payload["entries"][0]["mount_point"] = mount_point

    with pytest.raises(ValueError, match="mount_point"):
        load_journal_text(tmp_path, json.dumps(payload))


def test_journal_accepts_both_mount_styles(tmp_path: Path) -> None:
    """Valid absolute mount points from either platform load unchanged."""
    store = UsbRecoveryJournalStore(tmp_path / JOURNAL_FILE_NAME)
    store.save_entries(
        [
            build_journal_entry("/media/dj/STICK"),
            build_journal_entry("D:\\USB", export_id="exp-002"),
        ]
    )

    loaded = UsbRecoveryJournalStore(tmp_path / JOURNAL_FILE_NAME).load_entries()

    assert [item.mount_point for item in loaded] == ["/media/dj/STICK", "D:\\USB"]
    assert loaded[0].backup_path == "backups/exp-001"


@pytest.mark.parametrize(
    "raw_mount",
    [
        f"/media/a{ZERO_WIDTH_SPACE}b",
        f"{ESCAPE}[31m/media/stick",
        f"/media/stick{NUL}",
        " /media/stick",
    ],
)
def test_unsafe_mount_rejected_not_rewritten(raw_mount: str) -> None:
    """A cleaned copy could name another volume, so an unsafe mount point yields None."""
    assert sanitize_mount_point(raw_mount) is None


@pytest.mark.parametrize(
    "raw_mount",
    ["/media/dj/STICK", "/Volumes/My Stick ", "/media/" + "x" * (MAX_METADATA_TEXT_LENGTH + 10)],
)
def test_safe_mount_point_returned_unchanged(raw_mount: str) -> None:
    """A safe mount point comes back exactly, with no strip and no truncation."""
    assert sanitize_mount_point(raw_mount) == raw_mount
