"""Computer-side USB recovery journal, matching, and expiry (D139, D166, D191-D198)."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Final

from infiniteprep import storage_paths
from infiniteprep.services.usb.manifest import (
    ExportScope,
    UsbExportManifest,
    read_in_progress_manifest,
    remove_in_progress_manifest,
    write_completed_manifest,
    write_in_progress_manifest,
)
from infiniteprep.services.usb.models import DriveIdentity, DriveInspection, PartitionInfo
from infiniteprep.services.usb.targets import TargetCandidate

logger = logging.getLogger(__name__)

DEFAULT_RECOVERY_EXPIRY_DAYS: Final[int] = 30
MIN_RECOVERY_EXPIRY_DAYS: Final[int] = 1
MAX_RECOVERY_EXPIRY_DAYS: Final[int] = 365
EXPIRY_WARNING_LEAD_DAYS: Final[int] = 1
JOURNAL_FILE_NAME: Final[str] = "usb_recovery_journal.json"


class ReconnectChoice(StrEnum):
    """User choices when an interrupted USB export target reconnects (D166)."""

    RESUME = "resume"
    RESTORE = "restore"
    LEAVE_UNCHANGED = "leave_unchanged"


_RESOLVED_RECOVERY_ACTIONS: Final[frozenset[str]] = frozenset(
    {ReconnectChoice.RESUME.value, ReconnectChoice.RESTORE.value}
)


@dataclass
class UsbJournalEntry:
    """Computer-side recovery journal entry for one USB export run (D192)."""

    export_id: str
    started_at_utc: str
    volume_uuid: str
    drive_identity: DriveIdentity
    mount_point: str
    scope: ExportScope
    playlist_names: tuple[str, ...]
    track_paths: tuple[str, ...]
    backup_path: str = ""
    warned_expiry: bool = False
    last_recovery_action: str = ""


@dataclass(frozen=True)
class UsbReconnectMatch:
    """Matched journal entry paired with the reconnected drive candidate (D191)."""

    entry: UsbJournalEntry
    candidate: TargetCandidate
    requires_confirmation: bool
    reason: str


@dataclass(frozen=True)
class UsbExpiryScanResult:
    """Outcome of checking unresolved recovery entries for warnings and expiry (D196-D198)."""

    warning_entries: tuple[UsbJournalEntry, ...]
    expired_deleted_entries: tuple[UsbJournalEntry, ...]
    overdue_notice_entries: tuple[UsbJournalEntry, ...]


def _default_journal_path() -> Path:
    """Resolve the default computer journal path."""
    return storage_paths.current().data_dir / JOURNAL_FILE_NAME


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    """Atomically persist JSON payload to path using a sibling temporary file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    descriptor, temp_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", text=True
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            temp_path.unlink(missing_ok=True)
        raise


def _optional_str(raw: object) -> str | None:
    """Return raw when it is a string or None."""
    if raw is None or isinstance(raw, str):
        return raw
    raise ValueError("Expected string or null field in USB recovery journal.")


def _optional_bool(raw: object) -> bool | None:
    """Return raw when it is a boolean or None."""
    if raw is None or isinstance(raw, bool):
        return raw
    raise ValueError("Expected boolean or null field in USB recovery journal.")


def _drive_from_dict(raw: object) -> DriveIdentity:
    """Deserialize DriveIdentity from journal JSON."""
    if not isinstance(raw, Mapping):
        raise ValueError("Journal entry 'drive_identity' must be an object.")
    size_raw = raw.get("size_bytes")
    if isinstance(size_raw, bool) or not isinstance(size_raw, int):
        raise ValueError("Drive identity 'size_bytes' must be an integer.")
    name = raw.get("name")
    path = raw.get("path")
    if not isinstance(name, str) or not isinstance(path, str):
        raise ValueError("Drive identity 'name' and 'path' must be strings.")
    return DriveIdentity(
        name=name,
        path=path,
        size_bytes=size_raw,
        model=_optional_str(raw.get("model")),
        vendor=_optional_str(raw.get("vendor")),
        serial=_optional_str(raw.get("serial")),
        is_removable=bool(raw.get("is_removable", False)),
        is_read_only=bool(raw.get("is_read_only", False)),
        bus_type=_optional_str(raw.get("bus_type")),
        subsystem=_optional_str(raw.get("subsystem")),
        hardware_id=_optional_str(raw.get("hardware_id")),
        is_system=_optional_bool(raw.get("is_system")),
    )


def _str_tuple(raw: object, field_name: str) -> tuple[str, ...]:
    """Deserialize a JSON array of strings into a tuple."""
    if raw is None:
        return ()
    if not isinstance(raw, list) or not all(isinstance(item, str) for item in raw):
        raise ValueError(f"Journal entry '{field_name}' must be a list of strings.")
    return tuple(raw)


def _entry_from_dict(raw: object) -> UsbJournalEntry:
    """Deserialize one UsbJournalEntry from journal JSON."""
    if not isinstance(raw, Mapping):
        raise ValueError("Each USB recovery journal entry must be an object.")
    export_id = raw.get("export_id")
    started_at = raw.get("started_at_utc")
    volume_uuid = raw.get("volume_uuid")
    mount_point = raw.get("mount_point")
    scope = raw.get("scope")
    required_fields = (export_id, started_at, volume_uuid, mount_point, scope)
    if not all(isinstance(item, str) for item in required_fields):
        raise ValueError("Required USB recovery journal string fields are missing.")
    return UsbJournalEntry(
        export_id=str(export_id),
        started_at_utc=str(started_at),
        volume_uuid=str(volume_uuid),
        drive_identity=_drive_from_dict(raw.get("drive_identity")),
        mount_point=str(mount_point),
        scope=ExportScope(str(scope)),
        playlist_names=_str_tuple(raw.get("playlist_names"), "playlist_names"),
        track_paths=_str_tuple(raw.get("track_paths"), "track_paths"),
        backup_path=str(raw.get("backup_path", "")),
        warned_expiry=bool(raw.get("warned_expiry", False)),
        last_recovery_action=str(raw.get("last_recovery_action", "")),
    )


def _entry_to_dict(entry: UsbJournalEntry) -> dict[str, object]:
    """Convert one UsbJournalEntry into a JSON-serializable dictionary."""
    data = asdict(entry)
    data["scope"] = entry.scope.value
    return data


def _has_matching_in_progress_marker(mount_points: tuple[str, ...], export_id: str) -> bool:
    """Check whether any mounted path holds an in-progress marker matching export_id."""
    for mount_text in mount_points:
        try:
            marker = read_in_progress_manifest(Path(mount_text))
        except (OSError, ValueError) as exc:
            logger.warning("Skipping unreadable USB marker check: %s", type(exc).__name__)
            continue
        if marker is not None and marker.export_id == export_id:
            return True
    return False


def _match_entry_partition(
    entry: UsbJournalEntry, drive: DriveIdentity, partition: PartitionInfo
) -> tuple[bool, str] | None:
    """Evaluate one candidate partition against a journal entry (D191)."""
    if entry.volume_uuid and partition.uuid == entry.volume_uuid:
        exact_drive = drive == entry.drive_identity
        exact_mount = entry.mount_point in partition.mount_points
        if exact_drive and exact_mount:
            return False, "Verified volume ID and drive identity match."
        return True, "Volume ID matches, but drive identity or mount path changed."
    serial = drive.serial or ""
    if (
        not partition.uuid
        and entry.drive_identity.serial
        and serial == entry.drive_identity.serial
        and _has_matching_in_progress_marker(partition.mount_points, entry.export_id)
    ):
        return True, "Volume ID unavailable. Matched drive serial and USB in-progress marker."
    return None


def _match_entry_against_inspection(
    entry: UsbJournalEntry, inspection: DriveInspection
) -> tuple[bool, str] | None:
    """Find the first partition match for entry inside inspection."""
    for partition in inspection.partitions:
        matched = _match_entry_partition(entry, inspection.drive, partition)
        if matched is not None:
            return matched
    return None


def _parse_utc_timestamp(value: str) -> datetime:
    """Parse an ISO-8601 UTC timestamp string into a UTC-aware datetime."""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Journal timestamp must carry a timezone offset.")
    return parsed.astimezone(UTC)


def _classify_expiry(
    entry: UsbJournalEntry,
    now_utc: datetime,
    warning_delta: timedelta,
    expiry_delta: timedelta,
    at_launch: bool,
) -> str:
    """Classify an entry as keep, warn, expired, or overdue."""
    elapsed = now_utc - _parse_utc_timestamp(entry.started_at_utc)
    if elapsed >= expiry_delta:
        return "overdue" if at_launch else "expired"
    if elapsed >= warning_delta and not entry.warned_expiry:
        return "warn"
    return "keep"


def _validate_expiry_inputs(now_utc: datetime, expiry_days: int) -> datetime:
    """Validate expiry check inputs and normalize now_utc to UTC."""
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware.")
    if not MIN_RECOVERY_EXPIRY_DAYS <= expiry_days <= MAX_RECOVERY_EXPIRY_DAYS:
        raise ValueError("expiry_days must stay between 1 and 365 days.")
    return now_utc.astimezone(UTC)


def _run_required_recovery_callback(
    action: str,
    callback: Callable[[UsbJournalEntry, Path], None] | None,
    entry: UsbJournalEntry,
    mount_root: Path,
) -> None:
    """Execute a required recovery handler before retiring an interrupted export marker."""
    if callback is None:
        raise ValueError(f"Recovery action '{action}' requires a callback.")
    callback(entry, mount_root)


class UsbRecoveryJournalStore:
    """Reads, updates, matches, and expires computer-side USB export journal entries."""

    def __init__(self, journal_path: Path | None = None) -> None:
        """Initialize the store with an explicit path or default data folder file."""
        self.journal_path = journal_path if journal_path is not None else _default_journal_path()

    def load_entries(self) -> list[UsbJournalEntry]:
        """Load persisted journal entries from disk, returning empty when absent."""
        if not self.journal_path.exists():
            return []
        raw = json.loads(self.journal_path.read_text(encoding="utf-8"))
        if not isinstance(raw, Mapping):
            raise ValueError("USB recovery journal root must be a JSON object.")
        items = raw.get("entries", [])
        if not isinstance(items, list):
            raise ValueError("USB recovery journal 'entries' must be a list.")
        return [_entry_from_dict(item) for item in items]

    def save_entries(self, entries: Sequence[UsbJournalEntry]) -> None:
        """Persist journal entries atomically to disk."""
        payload: dict[str, object] = {
            "version": 1,
            "entries": [_entry_to_dict(entry) for entry in entries],
        }
        _write_json_atomic(self.journal_path, payload)

    def start_export(
        self, entry: UsbJournalEntry, mount_root: Path, manifest: UsbExportManifest
    ) -> Path:
        """Write the USB in-progress manifest and record entry in the computer journal."""
        marker_path = write_in_progress_manifest(mount_root, manifest)
        entries = [item for item in self.load_entries() if item.export_id != entry.export_id]
        entries.append(entry)
        self.save_entries(entries)
        return marker_path

    def complete_export(
        self, export_id: str, mount_root: Path, manifest: UsbExportManifest
    ) -> Path:
        """Publish the completed USB manifest and retire the pending journal entry."""
        completed_path = write_completed_manifest(mount_root, manifest)
        remaining = [item for item in self.load_entries() if item.export_id != export_id]
        self.save_entries(remaining)
        return completed_path

    def match_reconnected_target(self, candidate: TargetCandidate) -> UsbReconnectMatch | None:
        """Match a reconnected USB drive candidate against unresolved journal entries."""
        if candidate.inspection is None or not candidate.inspection.partitions:
            return None
        for entry in self.load_entries():
            if entry.last_recovery_action in _RESOLVED_RECOVERY_ACTIONS:
                continue
            matched = _match_entry_against_inspection(entry, candidate.inspection)
            if matched is not None:
                requires_confirm, reason = matched
                return UsbReconnectMatch(
                    entry=entry,
                    candidate=candidate,
                    requires_confirmation=requires_confirm,
                    reason=reason,
                )
        return None

    def apply_reconnect_choice(
        self,
        match: UsbReconnectMatch,
        choice: ReconnectChoice,
        mount_root: Path,
        *,
        confirmed: bool = False,
        restore_callback: Callable[[UsbJournalEntry, Path], None] | None = None,
        resume_callback: Callable[[UsbJournalEntry, Path], None] | None = None,
    ) -> UsbJournalEntry:
        """Execute Resume, Restore, or Leave unchanged on a matched USB drive (D139, D166)."""
        if (
            match.requires_confirmation
            and choice != ReconnectChoice.LEAVE_UNCHANGED
            and not confirmed
        ):
            raise ValueError("Confirm the matched drive before modifying it.")
        if choice == ReconnectChoice.RESTORE:
            _run_required_recovery_callback("restore", restore_callback, match.entry, mount_root)
            remove_in_progress_manifest(mount_root)
        elif choice == ReconnectChoice.RESUME:
            _run_required_recovery_callback("resume", resume_callback, match.entry, mount_root)
            remove_in_progress_manifest(mount_root)
        return self._record_recovery_action(match.entry, choice)

    def _record_recovery_action(
        self, entry: UsbJournalEntry, choice: ReconnectChoice
    ) -> UsbJournalEntry:
        """Update the journal entry action while retaining the journal record (D139)."""
        updated = UsbJournalEntry(
            export_id=entry.export_id,
            started_at_utc=entry.started_at_utc,
            volume_uuid=entry.volume_uuid,
            drive_identity=entry.drive_identity,
            mount_point=entry.mount_point,
            scope=entry.scope,
            playlist_names=entry.playlist_names,
            track_paths=entry.track_paths,
            backup_path=entry.backup_path,
            warned_expiry=entry.warned_expiry,
            last_recovery_action=choice.value,
        )
        entries = self.load_entries()
        replaced = [updated if item.export_id == entry.export_id else item for item in entries]
        if all(item.export_id != entry.export_id for item in replaced):
            replaced.append(updated)
        self.save_entries(replaced)
        return updated

    def evaluate_expiry(
        self,
        now_utc: datetime,
        *,
        expiry_enabled: bool = True,
        expiry_days: int = DEFAULT_RECOVERY_EXPIRY_DAYS,
        at_launch: bool = False,
    ) -> UsbExpiryScanResult:
        """Evaluate warning and expiry windows across stored journal entries (D193-D198)."""
        normalized_now = _validate_expiry_inputs(now_utc, expiry_days)
        if not expiry_enabled:
            return UsbExpiryScanResult((), (), ())
        warning_delta = timedelta(days=max(0, expiry_days - EXPIRY_WARNING_LEAD_DAYS))
        expiry_delta = timedelta(days=expiry_days)
        return self._apply_expiry_scan(normalized_now, warning_delta, expiry_delta, at_launch)

    def _apply_expiry_scan(
        self,
        now_utc: datetime,
        warning_delta: timedelta,
        expiry_delta: timedelta,
        at_launch: bool,
    ) -> UsbExpiryScanResult:
        """Partition journal entries by warning, expiry, or launch overdue cleanup."""
        warnings: list[UsbJournalEntry] = []
        expired: list[UsbJournalEntry] = []
        overdue: list[UsbJournalEntry] = []
        remaining: list[UsbJournalEntry] = []
        for entry in self.load_entries():
            outcome = _classify_expiry(entry, now_utc, warning_delta, expiry_delta, at_launch)
            if outcome == "overdue":
                overdue.append(entry)
                continue
            if outcome == "expired":
                expired.append(entry)
                continue
            if outcome == "warn":
                entry.warned_expiry = True
                warnings.append(entry)
            remaining.append(entry)
        if warnings or expired or overdue:
            self.save_entries(remaining)
        return UsbExpiryScanResult(tuple(warnings), tuple(expired), tuple(overdue))

    def cleanup_unresolved(
        self,
        export_ids: Sequence[str] | None = None,
    ) -> tuple[UsbJournalEntry, ...]:
        """Remove targeted or all unresolved recovery journal entries manually (D193)."""
        entries = self.load_entries()
        if export_ids is None:
            self.save_entries([])
            return tuple(entries)
        target_ids = set(export_ids)
        removed = [entry for entry in entries if entry.export_id in target_ids]
        remaining = [entry for entry in entries if entry.export_id not in target_ids]
        if removed:
            self.save_entries(remaining)
        return tuple(removed)
