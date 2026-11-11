"""Preflight facts that the per-OS probes add to a drive inspection (D93, D131, D138, D186, D187).

A probe runs fixed read-only commands. It never formats, repairs, remounts, or
changes permissions. A fact without a source stays None.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Final

from infiniteprep.services.usb.models import DriveInspection, FilesystemHealth

# MBR type codes of extended containers. A container holds logical partitions
# and never holds a filesystem.
MBR_TYPE_EXTENDED_CHS: Final[int] = 0x05
MBR_TYPE_EXTENDED_LBA: Final[int] = 0x0F
MBR_TYPE_EXTENDED_LINUX: Final[int] = 0x85
MBR_EXTENDED_TYPE_CODES: Final[frozenset[int]] = frozenset(
    {MBR_TYPE_EXTENDED_CHS, MBR_TYPE_EXTENDED_LBA, MBR_TYPE_EXTENDED_LINUX}
)
MBR_PRIMARY_SLOT_COUNT: Final[int] = 4
MBR_ACTIVE_FLAG: Final[int] = 0x80


@dataclass(frozen=True)
class PartitionFacts:
    """Probe results for one partition. None means no source supplied the fact."""

    is_active: bool | None = None
    is_primary: bool | None = None
    filesystem_health: FilesystemHealth | None = None


@dataclass(frozen=True)
class DriveFacts:
    """Probe results for one drive, keyed by partition number."""

    is_system: bool | None = None
    hardware_id: str | None = None
    partitions: Mapping[int, PartitionFacts] = field(default_factory=dict)
    inferred_facts: frozenset[str] = field(default_factory=frozenset)


def classify_mbr_partition(partition_number: int | None, type_code: int | None) -> bool | None:
    """Return whether an MBR partition is primary, or None when undecidable.

    Slots 1 to 4 hold primary partitions and extended containers. Logical
    partitions take numbers above 4.
    """
    if partition_number is None or partition_number <= 0:
        return None
    if partition_number > MBR_PRIMARY_SLOT_COUNT:
        return False
    if type_code is None:
        return None
    return type_code not in MBR_EXTENDED_TYPE_CODES


def apply_drive_facts(inspection: DriveInspection, facts: DriveFacts) -> DriveInspection:
    """Return a copy of an inspection that carries the probe facts."""
    partitions = []
    for partition in inspection.partitions:
        partition_facts = facts.partitions.get(partition.partition_number or 0)
        if partition_facts is None:
            partitions.append(partition)
            continue
        partitions.append(
            replace(
                partition,
                is_active=partition_facts.is_active,
                is_primary=partition_facts.is_primary,
                filesystem_health=partition_facts.filesystem_health,
            )
        )
    drive = replace(inspection.drive, is_system=facts.is_system, hardware_id=facts.hardware_id)
    return replace(
        inspection,
        drive=drive,
        partitions=tuple(partitions),
        inferred_facts=inspection.inferred_facts | facts.inferred_facts,
    )
