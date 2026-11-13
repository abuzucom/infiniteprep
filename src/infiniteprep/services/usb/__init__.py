"""USB and removable drive inspection and preflight services (D93, D160, D161, D181, D183)."""

from __future__ import annotations

from infiniteprep.services.usb.linux import LinuxDriveInspector
from infiniteprep.services.usb.macos import MacOSDriveInspector
from infiniteprep.services.usb.models import (
    CommandExecutionError,
    DriveIdentity,
    DriveInspection,
    DriveInspectionError,
    FilesystemHealth,
    InspectionParseError,
    PartitionInfo,
    SpaceInfo,
)
from infiniteprep.services.usb.preflight import (
    DjProfileRequest,
    Note,
    NoteCode,
    PreflightRequest,
    PreflightResult,
    Refusal,
    RefusalCode,
    evaluate_preflight,
)
from infiniteprep.services.usb.recheck import (
    AbortCode,
    AbortReason,
    RecheckResult,
    TargetSnapshot,
    capture_target_snapshot,
    recheck_target,
)
from infiniteprep.services.usb.space import (
    ReservePolicy,
    compute_reserve_bytes,
    estimate_export_bytes,
)
from infiniteprep.services.usb.targets import TargetCandidate, load_removable_targets
from infiniteprep.services.usb.windows import WindowsDriveInspector

__all__ = [
    "AbortCode",
    "AbortReason",
    "CommandExecutionError",
    "DjProfileRequest",
    "DriveIdentity",
    "DriveInspection",
    "DriveInspectionError",
    "FilesystemHealth",
    "InspectionParseError",
    "LinuxDriveInspector",
    "MacOSDriveInspector",
    "Note",
    "NoteCode",
    "PartitionInfo",
    "PreflightRequest",
    "PreflightResult",
    "RecheckResult",
    "Refusal",
    "RefusalCode",
    "ReservePolicy",
    "SpaceInfo",
    "TargetCandidate",
    "TargetSnapshot",
    "WindowsDriveInspector",
    "capture_target_snapshot",
    "compute_reserve_bytes",
    "estimate_export_bytes",
    "evaluate_preflight",
    "load_removable_targets",
    "recheck_target",
]
