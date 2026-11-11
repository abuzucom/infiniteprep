"""Pre-write recheck of a confirmed USB target (D130, D132).

The export writer calls the recheck right before the first write. Any abort
reason stops the export.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from infiniteprep.services.usb.models import DriveInspection
from infiniteprep.services.usb.preflight import (
    PreflightRequest,
    PreflightResult,
    evaluate_preflight,
)
from infiniteprep.services.usb.space import ReservePolicy

# Device node names such as sdb or disk4 change between plug events. The
# snapshot leaves them out and compares hardware and volume identifiers.
DRIVE_IDENTITY_FIELDS: Final[tuple[str, ...]] = (
    "serial",
    "hardware_id",
    "model",
    "vendor",
    "size_bytes",
)
PARTITION_IDENTITY_FIELDS: Final[tuple[str, ...]] = (
    "partition_number",
    "partition_scheme",
    "partition_type",
    "size_bytes",
    "filesystem",
    "label",
    "uuid",
)

IdentityRecord = tuple[tuple[str, object], ...]


class AbortCode(StrEnum):
    """Stable identifiers for every recheck abort reason."""

    IDENTITY_CHANGED = "identity_changed"
    MOUNT_PATH_CHANGED = "mount_path_changed"
    NOT_WRITABLE = "not_writable"
    PREFLIGHT_REFUSED = "preflight_refused"


@dataclass(frozen=True)
class AbortReason:
    """One reason the recheck stops the export."""

    code: AbortCode
    message: str


@dataclass(frozen=True)
class TargetSnapshot:
    """Identity and mount path of the target at confirmation time."""

    identity: IdentityRecord
    mount_path: str


@dataclass(frozen=True)
class RecheckResult:
    """Outcome of the pre-write recheck."""

    abort_reasons: tuple[AbortReason, ...]
    preflight: PreflightResult

    @property
    def may_write(self) -> bool:
        """Return True when nothing changed and every rule still passes."""
        return not self.abort_reasons


def check_path_writable(path: str) -> bool:
    """Return whether the current process may write below a mount path."""
    return os.access(path, os.W_OK)


def _read_identity(inspection: DriveInspection) -> IdentityRecord:
    """Return every recorded identifier of a drive and its partitions."""
    record: list[tuple[str, object]] = [
        (name, getattr(inspection.drive, name)) for name in DRIVE_IDENTITY_FIELDS
    ]
    record.append(("partition_count", len(inspection.partitions)))
    for index, partition in enumerate(inspection.partitions):
        record.extend(
            (f"partition[{index}].{name}", getattr(partition, name))
            for name in PARTITION_IDENTITY_FIELDS
        )
    return tuple(record)


def _read_mount_path(inspection: DriveInspection) -> str | None:
    """Return the first mount path of the first partition, if any."""
    if not inspection.partitions:
        return None
    mount_points = inspection.partitions[0].mount_points
    return mount_points[0] if mount_points else None


def capture_target_snapshot(inspection: DriveInspection) -> TargetSnapshot:
    """Record the target identity and mount path shown in the confirm step.

    Raises ValueError for an unmounted target. Run the preflight first.
    """
    mount_path = _read_mount_path(inspection)
    if mount_path is None:
        raise ValueError("The target has no mount path. Run the preflight before a snapshot.")
    return TargetSnapshot(identity=_read_identity(inspection), mount_path=mount_path)


def _list_changed_fields(confirmed: IdentityRecord, fresh: IdentityRecord) -> list[str]:
    """Return names of identifiers that differ, appear, or disappear."""
    confirmed_values = dict(confirmed)
    fresh_values = dict(fresh)
    names = sorted(set(confirmed_values) | set(fresh_values))
    missing = object()
    return [
        name
        for name in names
        if confirmed_values.get(name, missing) != fresh_values.get(name, missing)
    ]


def recheck_target(
    snapshot: TargetSnapshot,
    fresh_inspection: DriveInspection,
    request: PreflightRequest,
    reserve: ReservePolicy | None = None,
    *,
    is_path_writable: Callable[[str], bool] = check_path_writable,
) -> RecheckResult:
    """Compare a fresh inspection with the confirmed snapshot and rerun the policy.

    Identity, mount path, writability, and every preflight rule are checked.
    A free-space change aborts only when the space rule no longer passes.
    Abort messages name changed fields and omit their values.
    """
    reasons: list[AbortReason] = []
    changed = _list_changed_fields(snapshot.identity, _read_identity(fresh_inspection))
    if changed:
        names = ", ".join(changed)
        reasons.append(
            AbortReason(AbortCode.IDENTITY_CHANGED, f"The drive identity changed: {names}.")
        )
    mount_path = _read_mount_path(fresh_inspection)
    if mount_path != snapshot.mount_path:
        reasons.append(AbortReason(AbortCode.MOUNT_PATH_CHANGED, "The mount path changed."))
    elif not is_path_writable(snapshot.mount_path):
        reasons.append(AbortReason(AbortCode.NOT_WRITABLE, "The mount path is not writable."))
    preflight = evaluate_preflight(fresh_inspection, request, reserve)
    if not preflight.passed:
        reasons.append(
            AbortReason(AbortCode.PREFLIGHT_REFUSED, "The drive no longer passes the preflight.")
        )
    return RecheckResult(abort_reasons=tuple(reasons), preflight=preflight)
