"""Computer-side USB recovery journal, matching, and expiry (D139, D166, D191-D198)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Final

from infiniteprep import storage_paths
from infiniteprep.services.usb._shared import (
    MAX_USB_JSON_BYTES,
    is_symlink_or_reparse_point,
    normalize_iso_utc,
    open_confined_directory_context,
    parse_utc_datetime,
    read_confined_dir_json_text,
    write_json_atomic,
)
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
JOURNAL_SCHEMA_VERSION: Final[int] = 1
MAX_CLEANUP_HISTORY_RECORDS: Final[int] = 100
JOURNAL_FILE_NAME: Final[str] = "usb_recovery_journal.json"


class ReconnectChoice(StrEnum):
    """User choices when an interrupted USB export target reconnects (D166)."""

    RESUME = "resume"
    RESTORE = "restore"
    LEAVE_UNCHANGED = "leave_unchanged"


_RESOLVED_RECOVERY_ACTIONS: Final[frozenset[str]] = frozenset(
    {ReconnectChoice.RESUME.value, ReconnectChoice.RESTORE.value}
)
_ALLOWED_RECOVERY_ACTIONS: Final[frozenset[str]] = frozenset(
    {
        "",
        ReconnectChoice.RESUME.value,
        ReconnectChoice.RESTORE.value,
        ReconnectChoice.LEAVE_UNCHANGED.value,
    }
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


@dataclass(frozen=True)
class UsbCleanupRecord:
    """Durable audit entry recorded when expiry or manual cleanup deletes journal data."""

    export_id: str
    started_at_utc: str
    mount_point: str
    deleted_at_utc: str
    reason: str


def _default_journal_path() -> Path:
    """Resolve the default computer journal path."""
    return storage_paths.current().data_dir / JOURNAL_FILE_NAME


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


def _require_journal_bool(raw: Mapping[str, object], field_name: str, default: bool) -> bool:
    """Return a boolean field from raw when absent as default, rejecting non-booleans."""
    if field_name not in raw:
        return default
    value = raw[field_name]
    if not isinstance(value, bool):
        raise ValueError(f"USB recovery journal field '{field_name}' must be a boolean.")
    return value


def _require_journal_str(raw: object, field_name: str, *, allow_empty: bool = False) -> str:
    """Validate that raw is a string and optionally non-empty."""
    if not isinstance(raw, str):
        raise ValueError(f"USB recovery journal field '{field_name}' must be a string.")
    if not allow_empty and not raw.strip():
        raise ValueError(f"USB recovery journal field '{field_name}' cannot be empty.")
    return raw


def _require_journal_str_default(raw: Mapping[str, object], field_name: str, default: str) -> str:
    """Return an optional string field from raw when absent as default, rejecting non-strings."""
    if field_name not in raw:
        return default
    return _require_journal_str(raw[field_name], field_name, allow_empty=True)


def _drive_from_dict(raw: object) -> DriveIdentity:
    """Deserialize DriveIdentity from journal JSON."""
    if not isinstance(raw, Mapping):
        raise ValueError("Journal entry 'drive_identity' must be an object.")
    size_raw = raw.get("size_bytes")
    if isinstance(size_raw, bool) or not isinstance(size_raw, int) or size_raw < 0:
        raise ValueError("Drive identity 'size_bytes' must be a non-negative integer.")
    name = _require_journal_str(raw.get("name"), "drive_identity.name", allow_empty=True)
    path = _require_journal_str(raw.get("path"), "drive_identity.path", allow_empty=True)
    return DriveIdentity(
        name=name,
        path=path,
        size_bytes=size_raw,
        model=_optional_str(raw.get("model")),
        vendor=_optional_str(raw.get("vendor")),
        serial=_optional_str(raw.get("serial")),
        is_removable=_require_journal_bool(raw, "is_removable", False),
        is_read_only=_require_journal_bool(raw, "is_read_only", False),
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
    export_id = _require_journal_str(raw.get("export_id"), "export_id")
    raw_started = _require_journal_str(raw.get("started_at_utc"), "started_at_utc")
    started_at = normalize_iso_utc(raw_started, "started_at_utc")
    volume_uuid = _require_journal_str(raw.get("volume_uuid"), "volume_uuid", allow_empty=True)
    mount_point = _require_journal_str(raw.get("mount_point"), "mount_point")
    scope = ExportScope(_require_journal_str(raw.get("scope"), "scope"))
    action = _require_journal_str_default(raw, "last_recovery_action", "")
    if action not in _ALLOWED_RECOVERY_ACTIONS:
        raise ValueError(f"Invalid USB recovery journal action: {action!r}")
    return UsbJournalEntry(
        export_id=export_id,
        started_at_utc=started_at,
        volume_uuid=volume_uuid,
        drive_identity=_drive_from_dict(raw.get("drive_identity")),
        mount_point=mount_point,
        scope=scope,
        playlist_names=_str_tuple(raw.get("playlist_names"), "playlist_names"),
        track_paths=_str_tuple(raw.get("track_paths"), "track_paths"),
        backup_path=_require_journal_str_default(raw, "backup_path", ""),
        warned_expiry=_require_journal_bool(raw, "warned_expiry", False),
        last_recovery_action=action,
    )


def _entry_to_dict(entry: UsbJournalEntry) -> dict[str, object]:
    """Convert one UsbJournalEntry into a JSON-serializable dictionary."""
    data = asdict(entry)
    data["scope"] = entry.scope.value
    return data


def _cleanup_record_from_dict(raw: object) -> UsbCleanupRecord:
    """Deserialize one durable cleanup audit record from journal JSON."""
    if not isinstance(raw, Mapping):
        raise ValueError("Each USB recovery cleanup record must be an object.")
    return UsbCleanupRecord(
        export_id=_require_journal_str(raw.get("export_id"), "cleanup.export_id"),
        started_at_utc=normalize_iso_utc(
            _require_journal_str(raw.get("started_at_utc"), "cleanup.started_at_utc"),
            "cleanup.started_at_utc",
        ),
        mount_point=_require_journal_str(raw.get("mount_point"), "cleanup.mount_point"),
        deleted_at_utc=normalize_iso_utc(
            _require_journal_str(raw.get("deleted_at_utc"), "cleanup.deleted_at_utc"),
            "cleanup.deleted_at_utc",
        ),
        reason=_require_journal_str(raw.get("reason"), "cleanup.reason"),
    )


def _build_cleanup_record(
    entry: UsbJournalEntry, deleted_at_utc: datetime, reason: str
) -> UsbCleanupRecord:
    """Build a bounded audit record describing an expired or removed journal entry."""
    return UsbCleanupRecord(
        export_id=entry.export_id,
        started_at_utc=normalize_iso_utc(entry.started_at_utc, "started_at_utc"),
        mount_point=entry.mount_point,
        deleted_at_utc=deleted_at_utc.astimezone(UTC).isoformat().replace("+00:00", "Z"),
        reason=reason,
    )


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
    return parse_utc_datetime(value, "started_at_utc")


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


def _partition_resolved_mounts(partition: PartitionInfo) -> list[Path]:
    """Resolve existing mount points for one partition into canonical directory paths."""
    resolved: list[Path] = []
    for mount_text in partition.mount_points:
        try:
            candidate = Path(mount_text).resolve(strict=True)
        except OSError:
            continue
        if candidate.is_dir():
            resolved.append(candidate)
    return resolved


def _matched_mount_points(match: UsbReconnectMatch) -> set[Path]:
    """Collect canonical mount points belonging to matched partitions on candidate."""
    inspection = match.candidate.inspection
    if inspection is None:
        raise ValueError("Matched reconnect candidate lacks drive inspection data.")
    permitted: set[Path] = set()
    for partition in inspection.partitions:
        if _match_entry_partition(match.entry, inspection.drive, partition) is not None:
            permitted.update(_partition_resolved_mounts(partition))
    return permitted


def _validate_reconnect_mount_root(match: UsbReconnectMatch, mount_root: Path) -> None:
    """Verify mount_root is a real directory mounted on the matched candidate partition."""
    if is_symlink_or_reparse_point(mount_root):
        raise ValueError("Target mount root cannot be a symbolic link.")
    try:
        resolved_target = mount_root.resolve(strict=True)
    except OSError as exc:
        raise ValueError(f"Target mount root is missing or inaccessible: {mount_root}") from exc
    if not resolved_target.is_dir():
        raise ValueError(f"Target mount root is not a directory: {mount_root}")
    if resolved_target not in _matched_mount_points(match):
        raise ValueError("Target mount path does not match the reconnected candidate drive.")


def _partition_cleanup_targets(
    entries: Sequence[UsbJournalEntry],
    export_ids: Sequence[str] | None,
) -> tuple[list[UsbJournalEntry], list[UsbJournalEntry]]:
    """Select unresolved journal entries for manual cleanup while keeping resolved history."""
    target_ids = None if export_ids is None else set(export_ids)
    removed: list[UsbJournalEntry] = []
    remaining: list[UsbJournalEntry] = []
    for entry in entries:
        is_resolved = entry.last_recovery_action in _RESOLVED_RECOVERY_ACTIONS
        matches_target = target_ids is None or entry.export_id in target_ids
        if not is_resolved and matches_target:
            removed.append(entry)
        else:
            remaining.append(entry)
    return removed, remaining


class UsbRecoveryJournalStore:
    """Reads, updates, matches, and expires computer-side USB export journal entries."""

    def __init__(
        self,
        journal_path: Path | None = None,
        *,
        allowed_root: Path | None = None,
    ) -> None:
        """Initialize the store with a path confined under allowed_root or data_dir."""
        default_root = storage_paths.current().data_dir
        if journal_path is None:
            self.journal_path = default_root / JOURNAL_FILE_NAME
            self._allowed_root = allowed_root if allowed_root is not None else default_root
            return
        self.journal_path = journal_path
        self._allowed_root = allowed_root if allowed_root is not None else journal_path.parent

    def _load_journal_state(self) -> tuple[list[UsbJournalEntry], list[UsbCleanupRecord]]:
        """Load persisted entries and bounded cleanup history through confined handles."""
        with open_confined_directory_context(
            self.journal_path.parent, self._allowed_root, create=False
        ) as opened:
            if opened is None:
                return [], []
            resolved_dir, dir_fd = opened
            if is_symlink_or_reparse_point(resolved_dir / self.journal_path.name):
                raise ValueError(f"Refusing symlink JSON file: {self.journal_path}")
            content = read_confined_dir_json_text(
                resolved_dir,
                self.journal_path.name,
                dir_fd=dir_fd,
                max_bytes=MAX_USB_JSON_BYTES,
            )
        if content is None:
            return [], []
        return self._parse_journal_payload(json.loads(content))

    def _parse_journal_payload(
        self, raw: object
    ) -> tuple[list[UsbJournalEntry], list[UsbCleanupRecord]]:
        """Validate parsed journal JSON root, schema version, entries, and cleanup history."""
        if not isinstance(raw, Mapping):
            raise ValueError("USB recovery journal root must be a JSON object.")
        version = raw.get("version", JOURNAL_SCHEMA_VERSION)
        if (
            isinstance(version, bool)
            or not isinstance(version, int)
            or version != JOURNAL_SCHEMA_VERSION
        ):
            raise ValueError("Unsupported USB recovery journal schema version.")
        items = raw.get("entries", [])
        if not isinstance(items, list):
            raise ValueError("USB recovery journal 'entries' must be a list.")
        history_raw = raw.get("cleanup_history", [])
        if not isinstance(history_raw, list):
            raise ValueError("USB recovery journal 'cleanup_history' must be a list.")
        entries = [_entry_from_dict(item) for item in items]
        history = [_cleanup_record_from_dict(item) for item in history_raw]
        return entries, history

    def _save_journal_state(
        self,
        entries: Sequence[UsbJournalEntry],
        cleanup_history: Sequence[UsbCleanupRecord],
    ) -> None:
        """Persist journal entries and bounded cleanup history atomically under allowed_root."""
        bounded_history = list(cleanup_history)[-MAX_CLEANUP_HISTORY_RECORDS:]
        payload: dict[str, object] = {
            "version": JOURNAL_SCHEMA_VERSION,
            "entries": [_entry_to_dict(entry) for entry in entries],
            "cleanup_history": [asdict(record) for record in bounded_history],
        }
        with open_confined_directory_context(
            self.journal_path.parent, self._allowed_root, create=True
        ) as opened:
            assert opened is not None
            resolved_dir, dir_fd = opened
            write_json_atomic(
                resolved_dir,
                self.journal_path.name,
                payload,
                dir_fd=dir_fd,
                create_parent=False,
                reject_target_symlink=True,
            )

    def load_entries(self) -> list[UsbJournalEntry]:
        """Load persisted journal entries from disk, returning empty when absent."""
        return self._load_journal_state()[0]

    def load_cleanup_history(self) -> list[UsbCleanupRecord]:
        """Load durable cleanup audit records retained inside the journal."""
        return self._load_journal_state()[1]

    def save_entries(self, entries: Sequence[UsbJournalEntry]) -> None:
        """Persist journal entries atomically to disk while preserving cleanup history."""
        _, existing_history = self._load_journal_state()
        self._save_journal_state(entries, existing_history)

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
        _validate_reconnect_mount_root(match, mount_root)
        if choice in (ReconnectChoice.RESTORE, ReconnectChoice.RESUME):
            marker = read_in_progress_manifest(mount_root)
            if marker is None or marker.export_id != match.entry.export_id:
                raise ValueError(
                    "Target drive does not contain a matching in-progress export manifest."
                )
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
        """Partition unresolved entries by warning, expiry, or launch overdue cleanup."""
        entries, history = self._load_journal_state()
        warnings: list[UsbJournalEntry] = []
        expired: list[UsbJournalEntry] = []
        overdue: list[UsbJournalEntry] = []
        remaining: list[UsbJournalEntry] = []
        for entry in entries:
            if entry.last_recovery_action in _RESOLVED_RECOVERY_ACTIONS:
                remaining.append(entry)
                continue
            outcome = _classify_expiry(entry, now_utc, warning_delta, expiry_delta, at_launch)
            if outcome == "overdue":
                overdue.append(entry)
                history.append(_build_cleanup_record(entry, now_utc, "overdue"))
                continue
            if outcome == "expired":
                expired.append(entry)
                history.append(_build_cleanup_record(entry, now_utc, "expired"))
                continue
            if outcome == "warn":
                entry.warned_expiry = True
                warnings.append(entry)
            remaining.append(entry)
        if warnings or expired or overdue:
            self._save_journal_state(remaining, history)
        return UsbExpiryScanResult(tuple(warnings), tuple(expired), tuple(overdue))

    def cleanup_unresolved(
        self,
        export_ids: Sequence[str] | None = None,
        *,
        now_utc: datetime | None = None,
    ) -> tuple[UsbJournalEntry, ...]:
        """Remove targeted or all unresolved entries while preserving resolved history."""
        entries, history = self._load_journal_state()
        removed, remaining = _partition_cleanup_targets(entries, export_ids)
        if not removed:
            return ()
        deleted_at = (now_utc or datetime.now(UTC)).astimezone(UTC)
        for entry in removed:
            history.append(_build_cleanup_record(entry, deleted_at, "manual"))
        self._save_journal_state(remaining, history)
        return tuple(removed)
