"""Read-only macOS drive and partition inspection using diskutil (USB-2, #154).

This module executes fixed argument arrays without shell interpolation to inspect
connected storage devices, partition schemes, filesystems, and available space.
It captures stdout and stderr through bounded pipe reads under a hard deadline,
parses only plist output, and performs strictly read-only queries (list, info).
It correlates APFS container entries through APFSPhysicalStores and attaches
their volumes to every owning physical drive. It treats all USB-supplied
metadata as untrusted input. Construction requires macOS.
"""

from __future__ import annotations

import contextlib
import os
import plistlib
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from typing import Any, BinaryIO, Final
from xml.parsers.expat import ExpatError

from infiniteprep.services.usb._shared import (
    DEFAULT_COMMAND_TIMEOUT_SECONDS,
    clean_int,
    clean_str,
    sanitize_mount_point,
)
from infiniteprep.services.usb.models import (
    CommandExecutionError,
    DriveIdentity,
    DriveInspection,
    DriveInspectionError,
    InspectionParseError,
    PartitionInfo,
    SpaceInfo,
)

DISKUTIL_LIST_COMMAND: Final[tuple[str, ...]] = ("diskutil", "list", "-plist")
DISKUTIL_INFO_COMMAND: Final[tuple[str, ...]] = ("diskutil", "info", "-plist")
# diskutil emits whole disks (diskN) or single-suffix partitions (diskNsM) in
# list output; identifiers with deeper sN chains are outside the accepted shape.
MACOS_DEVICE_SAFE_RE: Final[re.Pattern[str]] = re.compile(r"^disk\d+(?:s\d+)?$")
MAX_PLIST_OUTPUT_BYTES: Final[int] = 8 * 1024 * 1024
MAX_STDERR_BYTES: Final[int] = 64 * 1024
CHUNK_BYTES: Final[int] = 64 * 1024
REAP_WAIT_SECONDS: Final[float] = 2.0
WAIT_GRACE_SECONDS: Final[float] = 1.0
KILL_SIGNAL: Final[int] = getattr(signal, "SIGKILL", signal.SIGTERM)


def _signal_child(process: subprocess.Popen[bytes], sig: int) -> bool:
    """Deliver a signal to the child, targeting its process group on POSIX.

    The child runs in its own session, so the group signal reaches descendants
    as well. The call falls back to process scope when the group is gone and
    returns False when the child already exited.
    """
    if hasattr(os, "killpg"):
        with contextlib.suppress(OSError):
            os.killpg(os.getpgid(process.pid), sig)
            return True
    try:
        process.send_signal(sig)
        return True
    except ProcessLookupError:
        return False


def _terminate_process(process: subprocess.Popen[bytes], deadline: float) -> None:
    """Terminate, kill, and reap the child under bounded waits.

    Both signals target the process group from the start, so spawned
    grandchildren receive them too. Escalation tolerates a child that already
    exited (ProcessLookupError) and a child stuck in an uninterruptible state,
    which reaps when the inspector process exits.
    """
    for sig in (signal.SIGTERM, KILL_SIGNAL):
        if not _signal_child(process, sig):
            with contextlib.suppress(ChildProcessError):
                process.wait(timeout=WAIT_GRACE_SECONDS)
            return
        remaining = max(deadline - time.monotonic(), 0.0)
        try:
            process.wait(timeout=remaining if remaining > 0 else WAIT_GRACE_SECONDS)
            return
        except subprocess.TimeoutExpired:
            continue
    with contextlib.suppress(subprocess.TimeoutExpired):
        process.wait(timeout=REAP_WAIT_SECONDS)


def _read_capped_pipe(
    pipe: BinaryIO,
    byte_cap: int,
    deadline: float,
    name: str,
) -> tuple[threading.Thread, dict[str, Any]]:
    """Read a pipe up to byte_cap under the deadline in a daemon thread.

    The read stops as soon as the cap is reached, so oversized replies never
    buffer beyond the cap. A reader that outlives the deadline gets the pipe
    closed by the caller, which unblocks and ends the daemon thread.
    """
    captured: dict[str, Any] = {"data": b"", "error": None}

    def read_capped() -> None:
        chunks: list[bytes] = []
        total = 0
        while True:
            remaining = byte_cap - total
            if remaining <= 0:
                break
            try:
                chunk = pipe.read(min(CHUNK_BYTES, remaining))
            except (OSError, ValueError) as exc:
                captured["error"] = exc
                break
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        captured["data"] = b"".join(chunks)

    reader = threading.Thread(target=read_capped, daemon=True, name=name)
    reader.start()
    join_wait = max(deadline - time.monotonic(), 0.0)
    reader.join(join_wait)
    return reader, captured


def _capture_bounded(
    process: subprocess.Popen[bytes],
    deadline: float,
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    """Capture both pipes under the byte caps and deadline, then wait."""
    stdout_reader, stdout_state = _read_capped_pipe(
        process.stdout,
        MAX_PLIST_OUTPUT_BYTES + 1,
        deadline,
        "usb-bounded-stdout",
    )
    stderr_reader, stderr_state = _read_capped_pipe(
        process.stderr,
        MAX_STDERR_BYTES,
        deadline,
        "usb-bounded-stderr",
    )
    for reader in (stdout_reader, stderr_reader):
        if reader.is_alive():
            _terminate_process(process, deadline)
    for reader in (stdout_reader, stderr_reader):
        reader.join(REAP_WAIT_SECONDS)
    for reader in (stdout_reader, stderr_reader):
        if reader.is_alive():
            raise CommandExecutionError("command pipe reader did not finish after terminate.")
    for state in (stdout_state, stderr_state):
        if state["error"] is not None:
            raise state["error"]
    stdout_bytes = stdout_state["data"]
    if len(stdout_bytes) > MAX_PLIST_OUTPUT_BYTES:
        raise InspectionParseError(
            f"diskutil output exceeds the {MAX_PLIST_OUTPUT_BYTES} byte limit."
        )
    remaining = max(deadline - time.monotonic(), 0.0)
    try:
        process.wait(timeout=remaining if remaining > 0 else WAIT_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        raise subprocess.TimeoutExpired(cmd=process.args, timeout=timeout_seconds) from None
    return subprocess.CompletedProcess(
        process.args,
        process.returncode,
        stdout_bytes.decode("utf-8", errors="replace"),
        stderr_state["data"].decode("utf-8", errors="replace"),
    )


def bounded_command_runner(
    cmd: list[str],
    timeout_seconds: float,
) -> subprocess.CompletedProcess[str]:
    """Execute a fixed command array with bounded capture and a hard deadline.

    The stdout pipe read stops at MAX_PLIST_OUTPUT_BYTES plus one byte and the
    stderr read stops at MAX_STDERR_BYTES, both under the deadline, so oversized
    or stuck replies never block the caller or fully enter Python memory. Every
    failure path terminates and reaps the child before re-raising. On POSIX the
    child runs in its own session and the timeout kill targets the group.
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive.")
    deadline = time.monotonic() + timeout_seconds
    popen_kwargs: dict[str, Any] = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "shell": False,
    }
    if sys.platform != "win32":
        popen_kwargs["start_new_session"] = True
    process = subprocess.Popen(cmd, **popen_kwargs)
    try:
        return _capture_bounded(process, deadline, timeout_seconds)
    except Exception:
        _terminate_process(process, deadline)
        raise
    finally:
        for pipe in (process.stdout, process.stderr):
            if pipe is not None:
                with contextlib.suppress(OSError, ValueError):
                    pipe.close()


def _sanitize_diskutil_identifier(val: Any) -> str:
    """Validate and sanitize a diskutil device identifier from untrusted output."""
    raw = clean_str(val)
    if not raw or ".." in raw or "/" in raw or not MACOS_DEVICE_SAFE_RE.match(raw):
        raise InspectionParseError(f"Invalid or unsafe diskutil device identifier: {val!r}")
    return raw


def _partition_number_from_identifier(identifier: str, fallback_index: int) -> int:
    """Derive the partition number from a diskNsM identifier suffix.

    APFS container volumes keep their container volume index as the partition
    number. Validation guarantees at most one sN suffix per identifier, so the
    suffix is unambiguous.
    """
    match = re.search(r"s(\d+)$", identifier)
    if match and int(match.group(1)) > 0:
        return int(match.group(1))
    return fallback_index


def _normalize_filesystem(val: Any) -> str | None:
    """Normalize macOS filesystem names so FAT32 and exFAT variants are detectable."""
    raw = clean_str(val)
    if not raw:
        return None
    lowered = raw.casefold()
    if "fat32" in lowered:
        return "FAT32"
    if "exfat" in lowered:
        return "exFAT"
    return raw


def _info_flag(info: dict[str, Any], key: str) -> bool:
    """Return a boolean plist flag, treating missing keys as False."""
    return bool(info.get(key))


class MacOSDriveInspector:
    """Inspects macOS drives, partitions, filesystems, and free space in read-only mode.

    Construction requires macOS and raises :class:`DriveInspectionError` on any
    other platform. The class stays importable everywhere; only construction is
    platform gated.
    """

    def __init__(
        self,
        command_runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
        timeout_seconds: float = DEFAULT_COMMAND_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize with optional command runner and timeout override for testing.

        Injected command runners own their resource behavior and ignore
        ``timeout_seconds``; the bound applies to the built-in bounded runner.
        """
        if sys.platform != "darwin":
            raise DriveInspectionError(
                f"MacOSDriveInspector requires macOS; current platform is '{sys.platform}'."
            )
        if timeout_seconds <= 0:
            raise DriveInspectionError("timeout_seconds must be positive.")
        self._timeout_seconds = timeout_seconds
        self._command_runner = command_runner or self._bounded_runner

    def _bounded_runner(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        """Run a fixed command array, discarding stdout above the byte cap."""
        return bounded_command_runner(cmd, self._timeout_seconds)

    def inspect_all(self) -> tuple[DriveInspection, ...]:
        """Inspect all physical disks and return DriveInspection records."""
        entries = self._run_and_parse_list()
        volumes_by_disk = self._collect_apfs_volumes(entries)
        return tuple(
            self._build_drive_inspection(entry, volumes_by_disk)
            for entry in entries
            if not self._is_container_entry(entry)
        )

    def inspect_device(self, device_name_or_path: str) -> DriveInspection:
        """Inspect a device by identifier (e.g. 'disk2') or path (e.g. '/dev/disk2s1')."""
        target = clean_str(device_name_or_path)
        if not target or ".." in target:
            raise CommandExecutionError(f"Device '{device_name_or_path}' is invalid or unsafe.")
        target_name = target.removeprefix("/dev/")
        if "/" in target_name or not MACOS_DEVICE_SAFE_RE.match(target_name):
            raise CommandExecutionError(f"Device '{device_name_or_path}' is invalid or unsafe.")
        entries = self._run_and_parse_list()
        volumes_by_disk = self._collect_apfs_volumes(entries)
        for entry in entries:
            if self._is_container_entry(entry):
                continue
            identifier = _sanitize_diskutil_identifier(entry.get("DeviceIdentifier"))
            matches = identifier == target_name or self._entry_has_device(
                entry,
                target_name,
                volumes_by_disk,
            )
            if matches:
                return self._build_drive_inspection(entry, volumes_by_disk)

        raise CommandExecutionError(f"Device '{device_name_or_path}' was not found.")

    def _run_and_parse_list(self) -> list[dict[str, Any]]:
        """Run diskutil list -plist and validate the disk entry list."""
        try:
            completed = self._command_runner(list(DISKUTIL_LIST_COMMAND))
        except (subprocess.SubprocessError, OSError) as exc:
            raise CommandExecutionError(f"diskutil list execution failed: {exc}") from exc

        if completed.returncode != 0:
            err = completed.stderr.strip() or f"exit code {completed.returncode}"
            raise CommandExecutionError(f"diskutil list failed with {err}")

        data = self._parse_plist_dict(completed.stdout, "diskutil list")
        if "AllDisksAndPartitions" not in data:
            raise InspectionParseError("diskutil list output missing 'AllDisksAndPartitions' list.")

        entries = data["AllDisksAndPartitions"]
        if not isinstance(entries, list):
            raise InspectionParseError("diskutil list 'AllDisksAndPartitions' is not a list.")

        for entry in entries:
            if not isinstance(entry, dict):
                raise InspectionParseError("diskutil list disk entry is not a mapping.")

        self._validate_entries(entries)
        return entries

    @staticmethod
    def _validate_entries(entries: list[dict[str, Any]]) -> None:
        """Validate identifiers and partition structure before any info invocation."""
        for entry in entries:
            _sanitize_diskutil_identifier(entry.get("DeviceIdentifier"))
            for part_dict in MacOSDriveInspector._partition_entries(entry):
                _sanitize_diskutil_identifier(part_dict.get("DeviceIdentifier"))

    def _run_info_plist(self, identifier: str) -> dict[str, Any]:
        """Run diskutil info -plist for one device identifier and validate the mapping."""
        command = [*DISKUTIL_INFO_COMMAND, identifier]
        try:
            completed = self._command_runner(list(command))
        except (subprocess.SubprocessError, OSError) as exc:
            raise CommandExecutionError(f"diskutil info execution failed: {exc}") from exc

        if completed.returncode != 0:
            err = completed.stderr.strip() or f"exit code {completed.returncode}"
            raise CommandExecutionError(f"diskutil info failed with {err}")

        return self._parse_plist_dict(completed.stdout, "diskutil info")

    @staticmethod
    def _parse_plist_dict(stdout: str, source: str) -> dict[str, Any]:
        """Parse plist bytes into a mapping under the module byte cap.

        The cap guards injected command runners; the built-in bounded runner
        already enforces it during capture. Encoding failures raise
        InspectionParseError instead of leaking UnicodeEncodeError.
        """
        try:
            encoded = stdout.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise InspectionParseError(f"{source} output is not decodable UTF-8: {exc}") from exc
        if len(encoded) > MAX_PLIST_OUTPUT_BYTES:
            raise InspectionParseError(
                f"{source} output exceeds the {MAX_PLIST_OUTPUT_BYTES} byte limit."
            )
        try:
            data = plistlib.loads(encoded)
        except (ValueError, ExpatError) as exc:
            # plistlib.InvalidFileException subclasses ValueError; XML faults raise ExpatError.
            raise InspectionParseError(f"{source} returned malformed plist: {exc}") from exc
        if not isinstance(data, dict):
            raise InspectionParseError(f"{source} output is not a mapping.")
        return data

    def _build_drive_inspection(
        self,
        entry: dict[str, Any],
        volumes_by_disk: dict[str, list[dict[str, Any]]],
    ) -> DriveInspection:
        """Construct a DriveInspection record from one AllDisksAndPartitions entry."""
        identifier = _sanitize_diskutil_identifier(entry.get("DeviceIdentifier"))
        disk_info = self._run_info_plist(identifier)
        identity = self._build_drive_identity(entry, disk_info, identifier)
        scheme = clean_str(entry.get("Content"))
        partitions: list[PartitionInfo] = []
        space_entries: list[tuple[str, SpaceInfo]] = []
        fallback_index = 1
        for part_dict in self._partition_and_volume_entries(entry, volumes_by_disk):
            partition, spaces = self._build_partition(part_dict, scheme, fallback_index)
            fallback_index += 1
            partitions.append(partition)
            space_entries.extend(spaces)
        return DriveInspection(
            drive=identity,
            partitions=tuple(partitions),
            space_by_mount=self._deduplicate_space_entries(space_entries),
        )

    def _build_drive_identity(
        self,
        entry: dict[str, Any],
        disk_info: dict[str, Any],
        identifier: str,
    ) -> DriveIdentity:
        """Construct sanitized DriveIdentity from list and info plist dictionaries.

        vendor and serial map from the diskutil Vendor, Manufacturer, and
        SerialNumber keys and stay None when the media does not report them.
        Size candidates fall through only on a missing key, not on a valid zero.
        """
        raw_size = entry.get("Size")
        if raw_size is None:
            raw_size = disk_info.get("DiskSize")
        if raw_size is None:
            raw_size = disk_info.get("TotalSize")
        return DriveIdentity(
            name=identifier,
            path=f"/dev/{identifier}",
            size_bytes=clean_int(raw_size),
            model=clean_str(disk_info.get("DeviceName")) or clean_str(disk_info.get("MediaName")),
            vendor=clean_str(disk_info.get("Vendor")) or clean_str(disk_info.get("Manufacturer")),
            serial=clean_str(disk_info.get("SerialNumber")),
            is_removable=_info_flag(disk_info, "RemovableMedia")
            or _info_flag(disk_info, "Ejectable"),
            is_read_only=_info_flag(disk_info, "Read-Only Media"),
            bus_type=clean_str(disk_info.get("BusProtocol"))
            or clean_str(disk_info.get("Protocol")),
            subsystem=clean_str(disk_info.get("DeviceTreePath")),
        )

    @staticmethod
    def _partition_entries(entry: dict[str, Any]) -> list[dict[str, Any]]:
        """Collect partition and APFS volume mappings, validating plist structure."""
        collected: list[dict[str, Any]] = []
        for key in ("Partitions", "APFSVolumes"):
            raw = entry.get(key)
            if raw is None:
                continue
            if not isinstance(raw, list):
                raise InspectionParseError(f"diskutil '{key}' must be a list or null.")
            for item in raw:
                if not isinstance(item, dict):
                    raise InspectionParseError(f"diskutil '{key}' entry is not a mapping.")
                collected.append(item)
        return collected

    @staticmethod
    def _is_container_entry(entry: dict[str, Any]) -> bool:
        """Return True for synthesized APFS container entries carrying physical stores."""
        return "APFSPhysicalStores" in entry

    @staticmethod
    def _owning_disk_identifier(store_identifier: str) -> str:
        """Reduce a physical store identifier to its owning disk identifier."""
        match = re.match(r"^disk\d+", store_identifier)
        if not match:
            raise InspectionParseError(
                f"Invalid APFS physical store identifier: {store_identifier!r}"
            )
        return match.group(0)

    def _collect_apfs_volumes(
        self,
        entries: list[dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        """Map APFS volumes to their owning physical disks before any info invocation.

        Container entries own APFSPhysicalStores and APFSVolumes. Physical store
        entries carry ``DeviceIdentifier``; the ``APFSPhysicalStore`` key remains
        a compatibility fallback. Volumes attach to the owning disk of every
        validated store so multi-store containers lose no records. APFSVolumes
        listed directly on a physical disk entry (legacy shape) attach to that
        disk.
        """
        volumes_by_disk: dict[str, list[dict[str, Any]]] = {}
        known_identifiers = {
            _sanitize_diskutil_identifier(entry.get("DeviceIdentifier")) for entry in entries
        }
        for entry in entries:
            volumes = entry.get("APFSVolumes")
            if not volumes:
                continue
            identifier = _sanitize_diskutil_identifier(entry.get("DeviceIdentifier"))
            if not self._is_container_entry(entry):
                volumes_by_disk.setdefault(identifier, []).extend(volumes)
                continue
            stores = entry.get("APFSPhysicalStores")
            if not isinstance(stores, list) or not stores:
                raise InspectionParseError("diskutil 'APFSPhysicalStores' must be a nonempty list.")
            for store in stores:
                if not isinstance(store, dict):
                    raise InspectionParseError(
                        "diskutil 'APFSPhysicalStores' entry is not a mapping."
                    )
            store_ids = [
                _sanitize_diskutil_identifier(
                    store.get("DeviceIdentifier") or store.get("APFSPhysicalStore")
                )
                for store in stores
            ]
            owning_disks = list(dict.fromkeys(map(self._owning_disk_identifier, store_ids)))
            for owning_disk in owning_disks:
                if owning_disk not in known_identifiers:
                    raise InspectionParseError(
                        f"APFS physical store names unknown disk '{owning_disk}'."
                    )
                volumes_by_disk.setdefault(owning_disk, []).extend(volumes)
        return volumes_by_disk

    def _partition_and_volume_entries(
        self,
        entry: dict[str, Any],
        volumes_by_disk: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        """Collect partition mappings plus correlated APFS volumes for one disk.

        APFS volumes arrive exclusively through the correlation map, which covers
        the container-entry shape and the legacy direct-on-disk shape.
        """
        identifier = _sanitize_diskutil_identifier(entry.get("DeviceIdentifier"))
        raw_partitions = entry.get("Partitions")
        partitions = list(raw_partitions) if isinstance(raw_partitions, list) else []
        return partitions + volumes_by_disk.get(identifier, [])

    def _entry_has_device(
        self,
        entry: dict[str, Any],
        target_name: str,
        volumes_by_disk: dict[str, list[dict[str, Any]]],
    ) -> bool:
        """Return True when one of the entry's devices matches the target identifier."""
        part_dicts = self._partition_and_volume_entries(entry, volumes_by_disk)
        return any(
            _sanitize_diskutil_identifier(part_dict.get("DeviceIdentifier")) == target_name
            for part_dict in part_dicts
        )

    def _build_partition(
        self,
        part_dict: dict[str, Any],
        parent_scheme: str | None,
        fallback_index: int,
    ) -> tuple[PartitionInfo, list[tuple[str, SpaceInfo]]]:
        """Parse one partition entry, querying volume info for mounted volumes only."""
        identifier = _sanitize_diskutil_identifier(part_dict.get("DeviceIdentifier"))
        mount_point = sanitize_mount_point(part_dict.get("MountPoint"))
        mount_points = (mount_point,) if mount_point else ()
        volume_info = self._run_info_plist(identifier) if mount_point else None
        space_entries: list[tuple[str, SpaceInfo]] = []
        if volume_info is not None:
            space = self._space_from_volume_info(volume_info)
            if space is not None:
                space_entries.append((mount_point, space))
        raw_size = part_dict.get("Size")
        if raw_size is None and volume_info is not None:
            raw_size = volume_info.get("TotalSize")
        partition = PartitionInfo(
            name=identifier,
            path=f"/dev/{identifier}",
            size_bytes=clean_int(raw_size),
            partition_number=_partition_number_from_identifier(identifier, fallback_index),
            partition_scheme=parent_scheme,
            partition_type=clean_str(part_dict.get("PartitionType"))
            or clean_str(part_dict.get("Content")),
            filesystem=_normalize_filesystem(part_dict.get("FilesystemType")),
            label=clean_str(part_dict.get("VolumeName")),
            uuid=clean_str(part_dict.get("VolumeUUID"))
            or clean_str(part_dict.get("PartitionUUID")),
            mount_points=mount_points,
            is_read_only=_info_flag(volume_info, "Read-Only Volume") if volume_info else False,
        )
        return partition, space_entries

    @staticmethod
    def _space_from_volume_info(info: dict[str, Any]) -> SpaceInfo | None:
        """Build SpaceInfo from volume info, skipping entries without size fields."""
        if "TotalSize" not in info or "VolumeFreeSpace" not in info:
            return None
        total_bytes = clean_int(info.get("TotalSize"))
        available_bytes = clean_int(info.get("VolumeFreeSpace"))
        used_bytes = max(0, total_bytes - available_bytes)
        return SpaceInfo(
            total_bytes=total_bytes,
            used_bytes=used_bytes,
            available_bytes=available_bytes,
        )

    @staticmethod
    def _deduplicate_space_entries(
        space_entries: list[tuple[str, SpaceInfo]],
    ) -> tuple[tuple[str, SpaceInfo], ...]:
        """Deduplicate space entries by mount point while preserving order."""
        seen_mounts: set[str] = set()
        unique: list[tuple[str, SpaceInfo]] = []
        for mount, space in space_entries:
            if mount in seen_mounts:
                continue
            seen_mounts.add(mount)
            unique.append((mount, space))
        return tuple(unique)
