"""Shared builders for USB preflight tests: one FAT32 stick that passes every rule."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from infiniteprep.services.usb.models import (
    DriveIdentity,
    DriveInspection,
    FilesystemHealth,
    PartitionInfo,
    SpaceInfo,
)
from infiniteprep.services.usb.preflight import DjProfileRequest, PreflightRequest

MOUNT_PATH = "/media/dj/STICK"
STICK_BYTES = 32_000_000_000
FREE_BYTES = 16_000_000_000
REQUEST_FILE_SIZES = (10_000_000, 20_000_000)
REQUEST_DIRECTORY_COUNT = 2
REQUEST_DATABASE_ALLOWANCE_BYTES = 1_000_000


def build_partition(**changes: Any) -> PartitionInfo:
    """Return the passing FAT32 partition with the given fields replaced."""
    partition = PartitionInfo(
        name="sdb1",
        path="/dev/sdb1",
        size_bytes=STICK_BYTES,
        partition_number=1,
        partition_scheme="dos",
        partition_type="0xc",
        filesystem="vfat",
        label="STICK",
        uuid="1A2B-3C4D",
        mount_points=(MOUNT_PATH,),
        is_read_only=False,
        is_active=False,
        is_primary=True,
        filesystem_health=FilesystemHealth.HEALTHY,
    )
    return replace(partition, **changes)


def build_drive(**changes: Any) -> DriveIdentity:
    """Return the passing removable USB drive with the given fields replaced."""
    drive = DriveIdentity(
        name="sdb",
        path="/dev/sdb",
        size_bytes=STICK_BYTES,
        model="Ultra USB 3.0",
        vendor="SanDisk",
        serial="0401568289410948",
        is_removable=True,
        is_read_only=False,
        bus_type="usb",
        hardware_id="0781:5581",
        is_system=False,
    )
    return replace(drive, **changes)


def build_inspection(
    *,
    drive: DriveIdentity | None = None,
    partitions: tuple[PartitionInfo, ...] | None = None,
    available_bytes: int | None = FREE_BYTES,
    inferred_facts: frozenset[str] = frozenset(),
) -> DriveInspection:
    """Return an inspection of the passing stick with the given parts replaced."""
    if partitions is None:
        partitions = (build_partition(),)
    space: tuple[tuple[str, SpaceInfo], ...] = ()
    if available_bytes is not None:
        info = SpaceInfo(
            total_bytes=STICK_BYTES,
            used_bytes=STICK_BYTES - available_bytes,
            available_bytes=available_bytes,
        )
        space = ((MOUNT_PATH, info),)
    return DriveInspection(
        drive=drive or build_drive(),
        partitions=partitions,
        space_by_mount=space,
        inferred_facts=inferred_facts,
    )


def build_request(**changes: Any) -> PreflightRequest:
    """Return a small passing export request with the given fields replaced."""
    request = PreflightRequest(
        profiles=(DjProfileRequest(name="CDJ-3000", database_format_supported=True),),
        file_sizes=REQUEST_FILE_SIZES,
        directory_count=REQUEST_DIRECTORY_COUNT,
        database_allowance_bytes=REQUEST_DATABASE_ALLOWANCE_BYTES,
        planned_changes=("Add 2 tracks", "Update 1 playlist"),
    )
    return replace(request, **changes)
