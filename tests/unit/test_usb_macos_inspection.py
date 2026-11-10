"""Unit tests for read-only macOS USB drive and filesystem inspection (USB-2, #154).

All diskutil replies are recorded plist fixtures. No test invokes the real
diskutil binary; the command runner stub records argv to prove that only fixed
argument arrays reach subprocess.
"""

from __future__ import annotations

import subprocess
import sys
import time
import unittest
from typing import Final
from unittest import mock

from infiniteprep.services.usb._shared import clean_str
from infiniteprep.services.usb.macos import (
    DISKUTIL_INFO_COMMAND,
    DISKUTIL_LIST_COMMAND,
    MAX_PLIST_OUTPUT_BYTES,
    MAX_STDERR_BYTES,
    MacOSDriveInspector,
    _terminate_process,
    bounded_command_runner,
)
from infiniteprep.services.usb.models import (
    CommandExecutionError,
    DriveInspection,
    DriveInspectionError,
    InspectionParseError,
    SpaceInfo,
)

PLIST_HEADER: Final[str] = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
    '<plist version="1.0">\n'
)

FAT32_DISK_ENTRY_XML: Final[str] = (
    "\t\t<dict>\n"
    + "\t\t\t<key>Content</key>\n\t\t\t<string>FDisk_partition_scheme</string>\n"
    + "\t\t\t<key>DeviceIdentifier</key>\n\t\t\t<string>disk2</string>\n"
    + "\t\t\t<key>Partitions</key>\n"
    + "\t\t\t<array>\n"
    + "\t\t\t\t<dict>\n"
    + "\t\t\t\t\t<key>Content</key>\n\t\t\t\t\t<string>DOS_FAT_32</string>\n"
    + "\t\t\t\t\t<key>DeviceIdentifier</key>\n\t\t\t\t\t<string>disk2s1</string>\n"
    + "\t\t\t\t\t<key>FilesystemType</key>\n\t\t\t\t\t<string>FAT32</string>\n"
    + "\t\t\t\t\t<key>MountPoint</key>\n\t\t\t\t\t<string>/Volumes/DJ_SET_1</string>\n"
    + "\t\t\t\t\t<key>PartitionType</key>\n\t\t\t\t\t<string>DOS_FAT_32</string>\n"
    + "\t\t\t\t\t<key>Size</key>\n\t\t\t\t\t<integer>32010928128</integer>\n"
    + "\t\t\t\t\t<key>VolumeName</key>\n\t\t\t\t\t<string>DJ_SET_1</string>\n"
    + "\t\t\t\t\t<key>VolumeUUID</key>\n\t\t\t\t\t<string>7B42-19E4</string>\n"
    + "\t\t\t\t</dict>\n"
    + "\t\t\t</array>\n"
    + "\t\t\t<key>Size</key>\n\t\t\t<integer>32010928128</integer>\n"
    + "\t\t</dict>\n"
)


def list_plist(entries_xml: str) -> str:
    """Wrap recorded AllDisksAndPartitions entry blocks in a full plist document."""
    return (
        PLIST_HEADER
        + "<dict>\n"
        + "\t<key>AllDisksAndPartitions</key>\n"
        + "\t<array>\n"
        + entries_xml
        + "\t</array>\n"
        + "</dict>\n"
        + "</plist>\n"
    )


FAT32_STICK_LIST_PLIST: Final[str] = list_plist(FAT32_DISK_ENTRY_XML)

FAT32_DISK_INFO_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>BusProtocol</key>\n\t<string>USB</string>\n"
    + "\t<key>DeviceIdentifier</key>\n\t<string>disk2</string>\n"
    + "\t<key>DeviceName</key>\n\t<string>Ultra USB 3.0</string>\n"
    + "\t<key>DeviceNode</key>\n\t<string>/dev/disk2</string>\n"
    + "\t<key>DeviceTreePath</key>\n\t<string>IODeviceTree:/PCI0@0/XHC1@14/disk@1</string>\n"
    + "\t<key>DiskSize</key>\n\t<integer>32010928128</integer>\n"
    + "\t<key>Ejectable</key>\n\t<true/>\n"
    + "\t<key>Internal</key>\n\t<false/>\n"
    + "\t<key>Read-Only Media</key>\n\t<false/>\n"
    + "\t<key>RemovableMedia</key>\n\t<true/>\n"
    + "\t<key>TotalSize</key>\n\t<integer>32010928128</integer>\n"
    + "\t<key>WholeDevice</key>\n\t<true/>\n"
    + "</dict>\n"
    + "</plist>\n"
)

FAT32_VOLUME_INFO_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>DeviceIdentifier</key>\n\t<string>disk2s1</string>\n"
    + "\t<key>DeviceNode</key>\n\t<string>/dev/disk2s1</string>\n"
    + "\t<key>FilesystemType</key>\n\t<string>FAT32</string>\n"
    + "\t<key>MountPoint</key>\n\t<string>/Volumes/DJ_SET_1</string>\n"
    + "\t<key>PartOfWhole</key>\n\t<string>disk2</string>\n"
    + "\t<key>Read-Only Volume</key>\n\t<false/>\n"
    + "\t<key>TotalSize</key>\n\t<integer>32010928128</integer>\n"
    + "\t<key>VolumeFreeSpace</key>\n\t<integer>30541987840</integer>\n"
    + "\t<key>VolumeName</key>\n\t<string>DJ_SET_1</string>\n"
    + "\t<key>WholeDevice</key>\n\t<false/>\n"
    + "</dict>\n"
    + "</plist>\n"
)

SYSTEM_DISK_LIST_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>AllDisksAndPartitions</key>\n"
    + "\t<array>\n"
    + "\t\t<dict>\n"
    + "\t\t\t<key>Content</key>\n\t\t\t<string>GUID_partition_scheme</string>\n"
    + "\t\t\t<key>DeviceIdentifier</key>\n\t\t\t<string>disk0</string>\n"
    + "\t\t\t<key>Partitions</key>\n"
    + "\t\t\t<array>\n"
    + "\t\t\t\t<dict>\n"
    + "\t\t\t\t\t<key>Content</key>\n\t\t\t\t\t<string>Apple EFI</string>\n"
    + "\t\t\t\t\t<key>DeviceIdentifier</key>\n\t\t\t\t\t<string>disk0s1</string>\n"
    + "\t\t\t\t\t<key>FilesystemType</key>\n\t\t\t\t\t<string>FAT32</string>\n"
    + "\t\t\t\t\t<key>PartitionUUID</key>\n"
    + "\t\t\t\t\t<string>C6A9D2E6-3B1F-4A0C-8D7E-5F6A7B8C9D0E</string>\n"
    + "\t\t\t\t\t<key>PartitionType</key>\n\t\t\t\t\t<string>Apple EFI</string>\n"
    + "\t\t\t\t\t<key>Size</key>\n\t\t\t\t\t<integer>524288000</integer>\n"
    + "\t\t\t\t\t<key>VolumeName</key>\n\t\t\t\t\t<string>EFI</string>\n"
    + "\t\t\t\t</dict>\n"
    + "\t\t\t\t<dict>\n"
    + "\t\t\t\t\t<key>Content</key>\n\t\t\t\t\t<string>Apple_APFS</string>\n"
    + "\t\t\t\t\t<key>DeviceIdentifier</key>\n\t\t\t\t\t<string>disk0s2</string>\n"
    + "\t\t\t\t\t<key>PartitionType</key>\n\t\t\t\t\t<string>Apple_APFS</string>\n"
    + "\t\t\t\t\t<key>Size</key>\n\t\t\t\t\t<integer>994138292224</integer>\n"
    + "\t\t\t\t</dict>\n"
    + "\t\t\t</array>\n"
    + "\t\t\t<key>Size</key>\n\t\t\t<integer>994662581248</integer>\n"
    + "\t\t</dict>\n"
    + "\t\t<dict>\n"
    + "\t\t\t<key>APFSPhysicalStores</key>\n"
    + "\t\t\t<array>\n"
    + "\t\t\t\t<dict>\n"
    + "\t\t\t\t\t<key>DeviceIdentifier</key>\n"
    + "\t\t\t\t\t<string>disk0s2</string>\n"
    + "\t\t\t\t</dict>\n"
    + "\t\t\t</array>\n"
    + "\t\t\t<key>APFSVolumes</key>\n"
    + "\t\t\t<array>\n"
    + "\t\t\t\t<dict>\n"
    + "\t\t\t\t\t<key>DeviceIdentifier</key>\n\t\t\t\t\t<string>disk1s1</string>\n"
    + "\t\t\t\t\t<key>FilesystemType</key>\n\t\t\t\t\t<string>APFS</string>\n"
    + "\t\t\t\t\t<key>MountPoint</key>\n\t\t\t\t\t<string>/</string>\n"
    + "\t\t\t\t\t<key>VolumeName</key>\n\t\t\t\t\t<string>Macintosh HD</string>\n"
    + "\t\t\t\t\t<key>VolumeUUID</key>\n"
    + "\t\t\t\t\t<string>1A2B3C4D-5E6F-7A8B-9C0D-1E2F3A4B5C6D</string>\n"
    + "\t\t\t\t</dict>\n"
    + "\t\t\t\t<dict>\n"
    + "\t\t\t\t\t<key>DeviceIdentifier</key>\n\t\t\t\t\t<string>disk1s2</string>\n"
    + "\t\t\t\t\t<key>FilesystemType</key>\n\t\t\t\t\t<string>APFS</string>\n"
    + "\t\t\t\t\t<key>MountPoint</key>\n"
    + "\t\t\t\t\t<string>/System/Volumes/Data</string>\n"
    + "\t\t\t\t\t<key>VolumeName</key>\n\t\t\t\t\t<string>Macintosh HD-Data</string>\n"
    + "\t\t\t\t\t<key>VolumeUUID</key>\n"
    + "\t\t\t\t\t<string>2B3C4D5E-6F7A-8B9C-0D1E-2F3A4B5C6D7E</string>\n"
    + "\t\t\t\t</dict>\n"
    + "\t\t\t\t<dict>\n"
    + "\t\t\t\t\t<key>DeviceIdentifier</key>\n\t\t\t\t\t<string>disk1s3</string>\n"
    + "\t\t\t\t\t<key>FilesystemType</key>\n\t\t\t\t\t<string>APFS</string>\n"
    + "\t\t\t\t\t<key>VolumeName</key>\n\t\t\t\t\t<string>Preboot</string>\n"
    + "\t\t\t\t\t<key>VolumeUUID</key>\n"
    + "\t\t\t\t\t<string>3C4D5E6F-7A8B-9C0D-1E2F-3A4B5C6D7E8F</string>\n"
    + "\t\t\t\t</dict>\n"
    + "\t\t\t</array>\n"
    + "\t\t\t<key>Content</key>\n\t\t\t<string>Apple_APFS Container</string>\n"
    + "\t\t\t<key>DeviceIdentifier</key>\n\t\t\t<string>disk1</string>\n"
    + "\t\t\t<key>Size</key>\n\t\t\t<integer>994138292224</integer>\n"
    + "\t\t</dict>\n"
    + "\t</array>\n"
    + "</dict>\n"
    + "</plist>\n"
)

SYSTEM_DISK_INFO_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>BusProtocol</key>\n\t<string>PCI-Express</string>\n"
    + "\t<key>DeviceIdentifier</key>\n\t<string>disk0</string>\n"
    + "\t<key>DeviceName</key>\n\t<string>APPLE SSD AP1024N</string>\n"
    + "\t<key>DeviceNode</key>\n\t<string>/dev/disk0</string>\n"
    + "\t<key>DeviceTreePath</key>\n"
    + "\t<string>IODeviceTree:/PCI0@0/ANS2@0/NVMe@0/disk@0</string>\n"
    + "\t<key>DiskSize</key>\n\t<integer>994662581248</integer>\n"
    + "\t<key>Ejectable</key>\n\t<false/>\n"
    + "\t<key>Internal</key>\n\t<true/>\n"
    + "\t<key>Read-Only Media</key>\n\t<false/>\n"
    + "\t<key>RemovableMedia</key>\n\t<false/>\n"
    + "\t<key>TotalSize</key>\n\t<integer>994662581248</integer>\n"
    + "\t<key>WholeDevice</key>\n\t<true/>\n"
    + "</dict>\n"
    + "</plist>\n"
)

SYSTEM_VOLUME_INFO_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>DeviceIdentifier</key>\n\t<string>disk1s1</string>\n"
    + "\t<key>FilesystemType</key>\n\t<string>APFS</string>\n"
    + "\t<key>MountPoint</key>\n\t<string>/</string>\n"
    + "\t<key>Read-Only Volume</key>\n\t<false/>\n"
    + "\t<key>TotalSize</key>\n\t<integer>994138292224</integer>\n"
    + "\t<key>VolumeFreeSpace</key>\n\t<integer>512003328000</integer>\n"
    + "\t<key>VolumeName</key>\n\t<string>Macintosh HD</string>\n"
    + "</dict>\n"
    + "</plist>\n"
)

DATA_VOLUME_INFO_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>DeviceIdentifier</key>\n\t<string>disk1s2</string>\n"
    + "\t<key>FilesystemType</key>\n\t<string>APFS</string>\n"
    + "\t<key>MountPoint</key>\n\t<string>/System/Volumes/Data</string>\n"
    + "\t<key>Read-Only Volume</key>\n\t<false/>\n"
    + "\t<key>TotalSize</key>\n\t<integer>994138292224</integer>\n"
    + "\t<key>VolumeFreeSpace</key>\n\t<integer>509937647616</integer>\n"
    + "\t<key>VolumeName</key>\n\t<string>Macintosh HD-Data</string>\n"
    + "</dict>\n"
    + "</plist>\n"
)

EXFAT_DISK_ENTRY_XML: Final[str] = (
    "\t\t<dict>\n"
    + "\t\t\t<key>Content</key>\n\t\t\t<string>FDisk_partition_scheme</string>\n"
    + "\t\t\t<key>DeviceIdentifier</key>\n\t\t\t<string>disk3</string>\n"
    + "\t\t\t<key>Partitions</key>\n"
    + "\t\t\t<array>\n"
    + "\t\t\t\t<dict>\n"
    + "\t\t\t\t\t<key>DeviceIdentifier</key>\n\t\t\t\t\t<string>disk3s1</string>\n"
    + "\t\t\t\t\t<key>FilesystemType</key>\n\t\t\t\t\t<string>exFAT</string>\n"
    + "\t\t\t\t\t<key>MountPoint</key>\n\t\t\t\t\t<string>/Volumes/DJ_SET_2</string>\n"
    + "\t\t\t\t\t<key>Size</key>\n\t\t\t\t\t<integer>64023056384</integer>\n"
    + "\t\t\t\t\t<key>VolumeName</key>\n\t\t\t\t\t<string>DJ_SET_2</string>\n"
    + "\t\t\t\t\t<key>VolumeUUID</key>\n"
    + "\t\t\t\t\t<string>AB12CD34-5678-9ABC-DEF0-112233445566</string>\n"
    + "\t\t\t\t</dict>\n"
    + "\t\t\t</array>\n"
    + "\t\t\t<key>Size</key>\n\t\t\t<integer>64023056384</integer>\n"
    + "\t\t</dict>\n"
)

EXFAT_STICK_LIST_PLIST: Final[str] = list_plist(EXFAT_DISK_ENTRY_XML)

COMBINED_LIST_PLIST: Final[str] = list_plist(FAT32_DISK_ENTRY_XML + EXFAT_DISK_ENTRY_XML)

EXFAT_DISK_INFO_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>BusProtocol</key>\n\t<string>USB</string>\n"
    + "\t<key>DeviceIdentifier</key>\n\t<string>disk3</string>\n"
    + "\t<key>DeviceName</key>\n\t<string>SanDisk Extreme</string>\n"
    + "\t<key>DiskSize</key>\n\t<integer>64023056384</integer>\n"
    + "\t<key>RemovableMedia</key>\n\t<true/>\n"
    + "\t<key>Read-Only Media</key>\n\t<false/>\n"
    + "</dict>\n"
    + "</plist>\n"
)

EXFAT_VOLUME_INFO_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>DeviceIdentifier</key>\n\t<string>disk3s1</string>\n"
    + "\t<key>MountPoint</key>\n\t<string>/Volumes/DJ_SET_2</string>\n"
    + "\t<key>Read-Only Volume</key>\n\t<false/>\n"
    + "\t<key>TotalSize</key>\n\t<integer>64023056384</integer>\n"
    + "\t<key>VolumeFreeSpace</key>\n\t<integer>63756756992</integer>\n"
    + "\t<key>VolumeName</key>\n\t<string>DJ_SET_2</string>\n"
    + "</dict>\n"
    + "</plist>\n"
)

MSDOS_FAT32_LIST_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>AllDisksAndPartitions</key>\n"
    + "\t<array>\n"
    + "\t\t<dict>\n"
    + "\t\t\t<key>DeviceIdentifier</key>\n\t\t\t<string>disk4</string>\n"
    + "\t\t\t<key>Partitions</key>\n"
    + "\t\t\t<array>\n"
    + "\t\t\t\t<dict>\n"
    + "\t\t\t\t\t<key>DeviceIdentifier</key>\n\t\t\t\t\t<string>disk4s1</string>\n"
    + "\t\t\t\t\t<key>FilesystemType</key>\n\t\t\t\t\t<string>MS-DOS FAT32</string>\n"
    + "\t\t\t\t\t<key>MountPoint</key>\n\t\t\t\t\t<string>/Volumes/OLD_MIX</string>\n"
    + "\t\t\t\t\t<key>Size</key>\n\t\t\t\t\t<integer>1000</integer>\n"
    + "\t\t\t\t</dict>\n"
    + "\t\t\t</array>\n"
    + "\t\t\t<key>Size</key>\n\t\t\t<integer>1000</integer>\n"
    + "\t\t</dict>\n"
    + "\t</array>\n"
    + "</dict>\n"
    + "</plist>\n"
)

MSDOS_FAT32_DISK_INFO_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>DeviceIdentifier</key>\n\t<string>disk4</string>\n"
    + "\t<key>DeviceName</key>\n\t<string>Generic Storage</string>\n"
    + "\t<key>DiskSize</key>\n\t<integer>1000</integer>\n"
    + "\t<key>SerialNumber</key>\n\t<string>SN123456</string>\n"
    + "\t<key>Vendor</key>\n\t<string>SanDisk</string>\n"
    + "</dict>\n"
    + "</plist>\n"
)

MSDOS_FAT32_VOLUME_INFO_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>DeviceIdentifier</key>\n\t<string>disk4s1</string>\n"
    + "\t<key>MountPoint</key>\n\t<string>/Volumes/OLD_MIX</string>\n"
    + "\t<key>TotalSize</key>\n\t<integer>1000</integer>\n"
    + "\t<key>VolumeFreeSpace</key>\n\t<integer>500</integer>\n"
    + "</dict>\n"
    + "</plist>\n"
)

OVERFLOW_DISK_INFO_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>DeviceIdentifier</key>\n\t<string>disk5</string>\n"
    + "\t<key>DeviceName</key>\n\t<string>Generic Storage</string>\n"
    + "\t<key>DiskSize</key>\n\t<integer>100</integer>\n"
    + "</dict>\n"
    + "</plist>\n"
)

OVERFLOW_VOLUME_INFO_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>DeviceIdentifier</key>\n\t<string>disk5s1</string>\n"
    + "\t<key>MountPoint</key>\n\t<string>/Volumes/OVER</string>\n"
    + "\t<key>TotalSize</key>\n\t<integer>100</integer>\n"
    + "\t<key>VolumeFreeSpace</key>\n\t<integer>900</integer>\n"
    + "</dict>\n"
    + "</plist>\n"
)

OVERFLOW_LIST_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>AllDisksAndPartitions</key>\n"
    + "\t<array>\n"
    + "\t\t<dict>\n"
    + "\t\t\t<key>DeviceIdentifier</key>\n\t\t\t<string>disk5</string>\n"
    + "\t\t\t<key>Partitions</key>\n"
    + "\t\t\t<array>\n"
    + "\t\t\t\t<dict>\n"
    + "\t\t\t\t\t<key>DeviceIdentifier</key>\n\t\t\t\t\t<string>disk5s1</string>\n"
    + "\t\t\t\t\t<key>FilesystemType</key>\n\t\t\t\t\t<string>FAT32</string>\n"
    + "\t\t\t\t\t<key>MountPoint</key>\n\t\t\t\t\t<string>/Volumes/OVER</string>\n"
    + "\t\t\t\t\t<key>Size</key>\n\t\t\t\t\t<integer>100</integer>\n"
    + "\t\t\t\t</dict>\n"
    + "\t\t\t</array>\n"
    + "\t\t\t<key>Size</key>\n\t\t\t<integer>100</integer>\n"
    + "\t\t</dict>\n"
    + "\t</array>\n"
    + "</dict>\n"
    + "</plist>\n"
)

LEGACY_APFS_LIST_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>AllDisksAndPartitions</key>\n"
    + "\t<array>\n"
    + "\t\t<dict>\n"
    + "\t\t\t<key>APFSVolumes</key>\n"
    + "\t\t\t<array>\n"
    + "\t\t\t\t<dict>\n"
    + "\t\t\t\t\t<key>DeviceIdentifier</key>\n\t\t\t\t\t<string>disk7s2</string>\n"
    + "\t\t\t\t\t<key>FilesystemType</key>\n\t\t\t\t\t<string>APFS</string>\n"
    + "\t\t\t\t\t<key>MountPoint</key>\n\t\t\t\t\t<string>/Volumes/Legacy</string>\n"
    + "\t\t\t\t\t<key>VolumeName</key>\n\t\t\t\t\t<string>Legacy HD</string>\n"
    + "\t\t\t\t</dict>\n"
    + "\t\t\t</array>\n"
    + "\t\t\t<key>Content</key>\n\t\t\t<string>GUID_partition_scheme</string>\n"
    + "\t\t\t<key>DeviceIdentifier</key>\n\t\t\t<string>disk7</string>\n"
    + "\t\t\t<key>Partitions</key>\n"
    + "\t\t\t<array>\n"
    + "\t\t\t\t<dict>\n"
    + "\t\t\t\t\t<key>Content</key>\n\t\t\t\t\t<string>Apple EFI</string>\n"
    + "\t\t\t\t\t<key>DeviceIdentifier</key>\n\t\t\t\t\t<string>disk7s1</string>\n"
    + "\t\t\t\t\t<key>FilesystemType</key>\n\t\t\t\t\t<string>FAT32</string>\n"
    + "\t\t\t\t\t<key>Size</key>\n\t\t\t\t\t<integer>524288000</integer>\n"
    + "\t\t\t\t\t<key>VolumeName</key>\n\t\t\t\t\t<string>EFI</string>\n"
    + "\t\t\t\t</dict>\n"
    + "\t\t\t</array>\n"
    + "\t\t\t<key>Size</key>\n\t\t\t<integer>4001000</integer>\n"
    + "\t\t</dict>\n"
    + "\t</array>\n"
    + "</dict>\n"
    + "</plist>\n"
)

LEGACY_APFS_DISK_INFO_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>DeviceIdentifier</key>\n\t<string>disk7</string>\n"
    + "\t<key>DeviceName</key>\n\t<string>Generic Storage</string>\n"
    + "\t<key>DiskSize</key>\n\t<integer>4001000</integer>\n"
    + "</dict>\n"
    + "</plist>\n"
)

LEGACY_APFS_VOLUME_INFO_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>DeviceIdentifier</key>\n\t<string>disk7s2</string>\n"
    + "\t<key>MountPoint</key>\n\t<string>/Volumes/Legacy</string>\n"
    + "\t<key>TotalSize</key>\n\t<integer>1000</integer>\n"
    + "\t<key>VolumeFreeSpace</key>\n\t<integer>400</integer>\n"
    + "</dict>\n"
    + "</plist>\n"
)

CONTAINER_ORPHAN_LIST_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>AllDisksAndPartitions</key>\n"
    + "\t<array>\n"
    + "\t\t<dict>\n"
    + "\t\t\t<key>Content</key>\n\t\t\t<string>GUID_partition_scheme</string>\n"
    + "\t\t\t<key>DeviceIdentifier</key>\n\t\t\t<string>disk8</string>\n"
    + "\t\t\t<key>Partitions</key>\n"
    + "\t\t\t<array>\n"
    + "\t\t\t\t<dict>\n"
    + "\t\t\t\t\t<key>Content</key>\n\t\t\t\t\t<string>Apple_APFS</string>\n"
    + "\t\t\t\t\t<key>DeviceIdentifier</key>\n\t\t\t\t\t<string>disk8s1</string>\n"
    + "\t\t\t\t\t<key>PartitionType</key>\n\t\t\t\t\t<string>Apple_APFS</string>\n"
    + "\t\t\t\t\t<key>Size</key>\n\t\t\t\t\t<integer>1000</integer>\n"
    + "\t\t\t\t</dict>\n"
    + "\t\t\t</array>\n"
    + "\t\t\t<key>Size</key>\n\t\t\t<integer>1000</integer>\n"
    + "\t\t</dict>\n"
    + "\t\t<dict>\n"
    + "\t\t\t<key>APFSPhysicalStores</key>\n"
    + "\t\t\t<array>\n"
    + "\t\t\t\t<dict>\n"
    + "\t\t\t\t\t<key>DeviceIdentifier</key>\n"
    + "\t\t\t\t\t<string>disk9s1</string>\n"
    + "\t\t\t\t</dict>\n"
    + "\t\t\t</array>\n"
    + "\t\t\t<key>APFSVolumes</key>\n"
    + "\t\t\t<array>\n"
    + "\t\t\t\t<dict>\n"
    + "\t\t\t\t\t<key>DeviceIdentifier</key>\n\t\t\t\t\t<string>disk1s1</string>\n"
    + "\t\t\t\t\t<key>FilesystemType</key>\n\t\t\t\t\t<string>APFS</string>\n"
    + "\t\t\t\t\t<key>MountPoint</key>\n\t\t\t\t\t<string>/Volumes/Ghost</string>\n"
    + "\t\t\t\t\t<key>VolumeName</key>\n\t\t\t\t\t<string>Ghost</string>\n"
    + "\t\t\t\t</dict>\n"
    + "\t\t\t</array>\n"
    + "\t\t\t<key>DeviceIdentifier</key>\n\t\t\t<string>disk1</string>\n"
    + "\t\t</dict>\n"
    + "\t</array>\n"
    + "</dict>\n"
    + "</plist>\n"
)

CONTAINER_FALLBACK_KEY_LIST_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>AllDisksAndPartitions</key>\n"
    + "\t<array>\n"
    + "\t\t<dict>\n"
    + "\t\t\t<key>Content</key>\n\t\t\t<string>GUID_partition_scheme</string>\n"
    + "\t\t\t<key>DeviceIdentifier</key>\n\t\t\t<string>disk8</string>\n"
    + "\t\t\t<key>Partitions</key>\n"
    + "\t\t\t<array>\n"
    + "\t\t\t\t<dict>\n"
    + "\t\t\t\t\t<key>Content</key>\n\t\t\t\t\t<string>Apple_APFS</string>\n"
    + "\t\t\t\t\t<key>DeviceIdentifier</key>\n\t\t\t\t\t<string>disk8s1</string>\n"
    + "\t\t\t\t\t<key>PartitionType</key>\n\t\t\t\t\t<string>Apple_APFS</string>\n"
    + "\t\t\t\t\t<key>Size</key>\n\t\t\t\t\t<integer>1000</integer>\n"
    + "\t\t\t\t</dict>\n"
    + "\t\t\t</array>\n"
    + "\t\t\t<key>Size</key>\n\t\t\t<integer>1000</integer>\n"
    + "\t\t</dict>\n"
    + "\t\t<dict>\n"
    + "\t\t\t<key>APFSPhysicalStores</key>\n"
    + "\t\t\t<array>\n"
    + "\t\t\t\t<dict>\n"
    + "\t\t\t\t\t<key>APFSPhysicalStore</key>\n"
    + "\t\t\t\t\t<string>disk8s1</string>\n"
    + "\t\t\t\t</dict>\n"
    + "\t\t\t</array>\n"
    + "\t\t\t<key>APFSVolumes</key>\n"
    + "\t\t\t<array>\n"
    + "\t\t\t\t<dict>\n"
    + "\t\t\t\t\t<key>DeviceIdentifier</key>\n\t\t\t\t\t<string>disk1s1</string>\n"
    + "\t\t\t\t\t<key>FilesystemType</key>\n\t\t\t\t\t<string>APFS</string>\n"
    + "\t\t\t\t\t<key>MountPoint</key>\n\t\t\t\t\t<string>/Volumes/Ghost</string>\n"
    + "\t\t\t\t\t<key>VolumeName</key>\n\t\t\t\t\t<string>Ghost</string>\n"
    + "\t\t\t\t</dict>\n"
    + "\t\t\t</array>\n"
    + "\t\t\t<key>DeviceIdentifier</key>\n\t\t\t<string>disk1</string>\n"
    + "\t\t</dict>\n"
    + "\t</array>\n"
    + "</dict>\n"
    + "</plist>\n"
)

CONTAINER_FALLBACK_DISK_INFO_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>DeviceIdentifier</key>\n\t<string>disk8</string>\n"
    + "\t<key>DeviceName</key>\n\t<string>Generic Storage</string>\n"
    + "\t<key>DiskSize</key>\n\t<integer>1000</integer>\n"
    + "</dict>\n"
    + "</plist>\n"
)

CONTAINER_FALLBACK_VOLUME_INFO_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>DeviceIdentifier</key>\n\t<string>disk1s1</string>\n"
    + "\t<key>MountPoint</key>\n\t<string>/Volumes/Ghost</string>\n"
    + "\t<key>TotalSize</key>\n\t<integer>900</integer>\n"
    + "\t<key>VolumeFreeSpace</key>\n\t<integer>300</integer>\n"
    + "</dict>\n"
    + "</plist>\n"
)

MULTI_STORE_LIST_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>AllDisksAndPartitions</key>\n"
    + "\t<array>\n"
    + "\t\t<dict>\n"
    + "\t\t\t<key>Content</key>\n\t\t\t<string>GUID_partition_scheme</string>\n"
    + "\t\t\t<key>DeviceIdentifier</key>\n\t\t\t<string>disk0</string>\n"
    + "\t\t\t<key>Partitions</key>\n"
    + "\t\t\t<array>\n"
    + "\t\t\t\t<dict>\n"
    + "\t\t\t\t\t<key>Content</key>\n\t\t\t\t\t<string>Apple_APFS</string>\n"
    + "\t\t\t\t\t<key>DeviceIdentifier</key>\n\t\t\t\t\t<string>disk0s2</string>\n"
    + "\t\t\t\t\t<key>PartitionType</key>\n\t\t\t\t\t<string>Apple_APFS</string>\n"
    + "\t\t\t\t\t<key>Size</key>\n\t\t\t\t\t<integer>2000</integer>\n"
    + "\t\t\t\t</dict>\n"
    + "\t\t\t</array>\n"
    + "\t\t\t<key>Size</key>\n\t\t\t<integer>2000</integer>\n"
    + "\t\t</dict>\n"
    + "\t\t<dict>\n"
    + "\t\t\t<key>Content</key>\n\t\t\t<string>GUID_partition_scheme</string>\n"
    + "\t\t\t<key>DeviceIdentifier</key>\n\t\t\t<string>disk1</string>\n"
    + "\t\t\t<key>Partitions</key>\n"
    + "\t\t\t<array>\n"
    + "\t\t\t\t<dict>\n"
    + "\t\t\t\t\t<key>Content</key>\n\t\t\t\t\t<string>Apple_APFS</string>\n"
    + "\t\t\t\t\t<key>DeviceIdentifier</key>\n\t\t\t\t\t<string>disk1s2</string>\n"
    + "\t\t\t\t\t<key>PartitionType</key>\n\t\t\t\t\t<string>Apple_APFS</string>\n"
    + "\t\t\t\t\t<key>Size</key>\n\t\t\t\t\t<integer>3000</integer>\n"
    + "\t\t\t\t</dict>\n"
    + "\t\t\t</array>\n"
    + "\t\t\t<key>Size</key>\n\t\t\t<integer>3000</integer>\n"
    + "\t\t</dict>\n"
    + "\t\t<dict>\n"
    + "\t\t\t<key>APFSPhysicalStores</key>\n"
    + "\t\t\t<array>\n"
    + "\t\t\t\t<dict>\n"
    + "\t\t\t\t\t<key>DeviceIdentifier</key>\n\t\t\t\t\t<string>disk0s2</string>\n"
    + "\t\t\t\t</dict>\n"
    + "\t\t\t\t<dict>\n"
    + "\t\t\t\t\t<key>DeviceIdentifier</key>\n\t\t\t\t\t<string>disk1s2</string>\n"
    + "\t\t\t\t</dict>\n"
    + "\t\t\t</array>\n"
    + "\t\t\t<key>APFSVolumes</key>\n"
    + "\t\t\t<array>\n"
    + "\t\t\t\t<dict>\n"
    + "\t\t\t\t\t<key>DeviceIdentifier</key>\n\t\t\t\t\t<string>disk2s1</string>\n"
    + "\t\t\t\t\t<key>FilesystemType</key>\n\t\t\t\t\t<string>APFS</string>\n"
    + "\t\t\t\t\t<key>MountPoint</key>\n\t\t\t\t\t<string>/Volumes/Fusion</string>\n"
    + "\t\t\t\t\t<key>VolumeName</key>\n\t\t\t\t\t<string>Fusion HD</string>\n"
    + "\t\t\t\t</dict>\n"
    + "\t\t\t</array>\n"
    + "\t\t\t<key>DeviceIdentifier</key>\n\t\t\t<string>disk2</string>\n"
    + "\t\t</dict>\n"
    + "\t</array>\n"
    + "</dict>\n"
    + "</plist>\n"
)

MULTI_STORE_DISK0_INFO_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>DeviceIdentifier</key>\n\t<string>disk0</string>\n"
    + "\t<key>DeviceName</key>\n\t<string>Fusion SSD</string>\n"
    + "\t<key>DiskSize</key>\n\t<integer>2000</integer>\n"
    + "</dict>\n"
    + "</plist>\n"
)

MULTI_STORE_DISK1_INFO_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>DeviceIdentifier</key>\n\t<string>disk1</string>\n"
    + "\t<key>DeviceName</key>\n\t<string>Fusion HDD</string>\n"
    + "\t<key>DiskSize</key>\n\t<integer>3000</integer>\n"
    + "</dict>\n"
    + "</plist>\n"
)

MULTI_STORE_VOLUME_INFO_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>DeviceIdentifier</key>\n\t<string>disk2s1</string>\n"
    + "\t<key>MountPoint</key>\n\t<string>/Volumes/Fusion</string>\n"
    + "\t<key>TotalSize</key>\n\t<integer>5000</integer>\n"
    + "\t<key>VolumeFreeSpace</key>\n\t<integer>1000</integer>\n"
    + "</dict>\n"
    + "</plist>\n"
)

TRAVERSAL_DISK_INFO_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>DeviceIdentifier</key>\n\t<string>disk6</string>\n"
    + "\t<key>DeviceName</key>\n\t<string>Generic Storage</string>\n"
    + "\t<key>DiskSize</key>\n\t<integer>10</integer>\n"
    + "</dict>\n"
    + "</plist>\n"
)

TRAVERSAL_LIST_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>AllDisksAndPartitions</key>\n"
    + "\t<array>\n"
    + "\t\t<dict>\n"
    + "\t\t\t<key>DeviceIdentifier</key>\n\t\t\t<string>disk6</string>\n"
    + "\t\t\t<key>Partitions</key>\n"
    + "\t\t\t<array>\n"
    + "\t\t\t\t<dict>\n"
    + "\t\t\t\t\t<key>DeviceIdentifier</key>\n\t\t\t\t\t<string>disk6s1</string>\n"
    + "\t\t\t\t\t<key>FilesystemType</key>\n\t\t\t\t\t<string>FAT32</string>\n"
    + "\t\t\t\t\t<key>MountPoint</key>\n\t\t\t\t\t<string>/Volumes/../etc</string>\n"
    + "\t\t\t\t\t<key>Size</key>\n\t\t\t\t\t<integer>10</integer>\n"
    + "\t\t\t\t\t<key>VolumeName</key>\n\t\t\t\t\t<string>"
    + ("X" * 600)
    + "</string>\n"
    + "\t\t\t\t</dict>\n"
    + "\t\t\t</array>\n"
    + "\t\t\t<key>Size</key>\n\t\t\t<integer>10</integer>\n"
    + "\t\t</dict>\n"
    + "\t</array>\n"
    + "</dict>\n"
    + "</plist>\n"
)

UNSAFE_IDENTIFIER_LIST_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>AllDisksAndPartitions</key>\n"
    + "\t<array>\n"
    + "\t\t<dict>\n"
    + "\t\t\t<key>DeviceIdentifier</key>\n\t\t\t<string>disk0</string>\n"
    + "\t\t\t<key>Partitions</key>\n"
    + "\t\t\t<array>\n"
    + "\t\t\t\t<dict>\n"
    + "\t\t\t\t\t<key>DeviceIdentifier</key>\n"
    + "\t\t\t\t\t<string>disk0/../etc</string>\n"
    + "\t\t\t\t</dict>\n"
    + "\t\t\t</array>\n"
    + "\t\t</dict>\n"
    + "\t</array>\n"
    + "</dict>\n"
    + "</plist>\n"
)

MALFORMED_LIST_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>AllDisksAndPartitions</key>\n"
    + "\t<array><oops></array>\n"
    + "</dict>\n"
    + "</plist>\n"
)

NON_MAPPING_LIST_PLIST: Final[str] = PLIST_HEADER + "<string>oops</string>\n" + "</plist>\n"

MISSING_KEY_LIST_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>AllDisks</key>\n\t<array>\n\t\t<string>disk0</string>\n\t</array>\n"
    + "</dict>\n"
    + "</plist>\n"
)

NOT_A_LIST_LIST_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>AllDisksAndPartitions</key>\n\t<string>disk0</string>\n"
    + "</dict>\n"
    + "</plist>\n"
)

DISK_ENTRY_NOT_MAPPING_LIST_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>AllDisksAndPartitions</key>\n"
    + "\t<array>\n\t\t<string>disk0</string>\n\t</array>\n"
    + "</dict>\n"
    + "</plist>\n"
)

PARTITION_ENTRY_NOT_MAPPING_LIST_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>AllDisksAndPartitions</key>\n"
    + "\t<array>\n"
    + "\t\t<dict>\n"
    + "\t\t\t<key>DeviceIdentifier</key>\n\t\t\t<string>disk0</string>\n"
    + "\t\t\t<key>Partitions</key>\n"
    + "\t\t\t<array>\n\t\t\t\t<integer>7</integer>\n\t\t\t</array>\n"
    + "\t\t</dict>\n"
    + "\t</array>\n"
    + "</dict>\n"
    + "</plist>\n"
)

PARTITIONS_NOT_LIST_LIST_PLIST: Final[str] = (
    PLIST_HEADER
    + "<dict>\n"
    + "\t<key>AllDisksAndPartitions</key>\n"
    + "\t<array>\n"
    + "\t\t<dict>\n"
    + "\t\t\t<key>DeviceIdentifier</key>\n\t\t\t<string>disk0</string>\n"
    + "\t\t\t<key>Partitions</key>\n\t\t\t<string>all</string>\n"
    + "\t\t</dict>\n"
    + "\t</array>\n"
    + "</dict>\n"
    + "</plist>\n"
)


class FakeRunner:
    """Command runner stub that replays recorded diskutil replies and records argv."""

    def __init__(
        self,
        list_plist: str,
        info_by_device: dict[str, str],
        returncode: int = 0,
        stderr: str = "",
        error: Exception | None = None,
    ) -> None:
        self.commands: list[list[str]] = []
        self._list_plist = list_plist
        self._info_by_device = info_by_device
        self._returncode = returncode
        self._stderr = stderr
        self._error = error

    def __call__(self, cmd: list[str]) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(cmd))
        if self._error is not None:
            raise self._error
        if self._returncode != 0:
            return subprocess.CompletedProcess(cmd, self._returncode, "", self._stderr)
        if cmd[:2] == ["diskutil", "list"]:
            return subprocess.CompletedProcess(cmd, 0, self._list_plist, "")
        if cmd[:2] == ["diskutil", "info"] and len(cmd) == 4 and cmd[3] in self._info_by_device:
            return subprocess.CompletedProcess(cmd, 0, self._info_by_device[cmd[3]], "")
        return subprocess.CompletedProcess(cmd, 1, "", "Unable to find disk for the given device.")


def fat32_stick_runner() -> FakeRunner:
    """Return a runner replaying the recorded FAT32 stick session."""
    return FakeRunner(
        list_plist=FAT32_STICK_LIST_PLIST,
        info_by_device={
            "disk2": FAT32_DISK_INFO_PLIST,
            "disk2s1": FAT32_VOLUME_INFO_PLIST,
        },
    )


def system_disk_runner() -> FakeRunner:
    """Return a runner replaying the recorded system disk session."""
    return FakeRunner(
        list_plist=SYSTEM_DISK_LIST_PLIST,
        info_by_device={
            "disk0": SYSTEM_DISK_INFO_PLIST,
            "disk1s1": SYSTEM_VOLUME_INFO_PLIST,
            "disk1s2": DATA_VOLUME_INFO_PLIST,
        },
    )


class TestUsbMacosInspection(unittest.TestCase):
    """Hermetic tests parsing recorded diskutil plist output."""

    REAL_PLATFORM: Final[str] = sys.platform

    def setUp(self) -> None:
        platform_patcher = mock.patch("sys.platform", "darwin")
        platform_patcher.start()
        self.addCleanup(platform_patcher.stop)

    def assert_fixed_diskutil_commands(self, runner: FakeRunner) -> None:
        """Assert every executed command is a fixed diskutil argument array."""
        self.assertTrue(runner.commands)
        for cmd in runner.commands:
            self.assertEqual(cmd[0], "diskutil")
            self.assertIn(cmd[1], ("list", "info"))
            if cmd[1] == "list":
                self.assertEqual(cmd, list(DISKUTIL_LIST_COMMAND))
            else:
                self.assertEqual(cmd[:3], list(DISKUTIL_INFO_COMMAND))
                self.assertLessEqual(len(cmd), 4)

    def test_inspect_fat32_stick(self) -> None:
        """A recorded FAT32 stick session yields identity, partition, and space records."""
        runner = fat32_stick_runner()
        inspections = MacOSDriveInspector(command_runner=runner).inspect_all()
        self.assert_fixed_diskutil_commands(runner)
        self.assertEqual(len(runner.commands), 3)
        self.assertEqual(len(inspections), 1)
        inspection = inspections[0]
        self.assertIsInstance(inspection, DriveInspection)
        drive = inspection.drive
        self.assertEqual(drive.name, "disk2")
        self.assertEqual(drive.path, "/dev/disk2")
        self.assertEqual(drive.size_bytes, 32010928128)
        self.assertEqual(drive.model, "Ultra USB 3.0")
        self.assertIsNone(drive.vendor)
        self.assertIsNone(drive.serial)
        self.assertTrue(drive.is_removable)
        self.assertFalse(drive.is_read_only)
        self.assertEqual(drive.bus_type, "USB")
        self.assertEqual(drive.subsystem, "IODeviceTree:/PCI0@0/XHC1@14/disk@1")
        self.assertEqual(len(inspection.partitions), 1)
        partition = inspection.partitions[0]
        self.assertEqual(partition.name, "disk2s1")
        self.assertEqual(partition.path, "/dev/disk2s1")
        self.assertEqual(partition.size_bytes, 32010928128)
        self.assertEqual(partition.partition_number, 1)
        self.assertEqual(partition.partition_scheme, "FDisk_partition_scheme")
        self.assertEqual(partition.partition_type, "DOS_FAT_32")
        self.assertEqual(partition.filesystem, "FAT32")
        self.assertEqual(partition.label, "DJ_SET_1")
        self.assertEqual(partition.uuid, "7B42-19E4")
        self.assertEqual(partition.mount_points, ("/Volumes/DJ_SET_1",))
        self.assertFalse(partition.is_read_only)
        self.assertTrue(partition.is_fat32)
        self.assertFalse(partition.is_exfat)
        self.assertTrue(inspection.has_fat32_partition)
        self.assertFalse(inspection.has_exfat_partition)
        self.assertEqual(
            inspection.space_for_mount("/Volumes/DJ_SET_1"),
            SpaceInfo(
                total_bytes=32010928128,
                used_bytes=1468940288,
                available_bytes=30541987840,
            ),
        )

    def test_inspect_system_disk(self) -> None:
        """A recorded APFS system disk session records EFI, container, and volumes."""
        runner = system_disk_runner()
        inspections = MacOSDriveInspector(command_runner=runner).inspect_all()
        self.assertEqual(len(runner.commands), 4)
        self.assertEqual(len(inspections), 1)
        self.assertFalse(any(inspection.drive.name == "disk1" for inspection in inspections))
        inspection = inspections[0]
        drive = inspection.drive
        self.assertEqual(drive.name, "disk0")
        self.assertEqual(drive.path, "/dev/disk0")
        self.assertEqual(drive.size_bytes, 994662581248)
        self.assertEqual(drive.model, "APPLE SSD AP1024N")
        self.assertFalse(drive.is_removable)
        self.assertEqual(drive.bus_type, "PCI-Express")
        self.assertEqual(len(inspection.partitions), 5)
        efi = inspection.partitions[0]
        self.assertEqual(efi.name, "disk0s1")
        self.assertEqual(efi.partition_number, 1)
        self.assertEqual(efi.partition_scheme, "GUID_partition_scheme")
        self.assertEqual(efi.partition_type, "Apple EFI")
        self.assertEqual(efi.filesystem, "FAT32")
        self.assertEqual(efi.mount_points, ())
        container = inspection.partitions[1]
        self.assertEqual(container.partition_type, "Apple_APFS")
        self.assertIsNone(container.filesystem)
        system_volume = inspection.partitions[2]
        self.assertEqual(system_volume.name, "disk1s1")
        self.assertEqual(system_volume.partition_number, 1)
        self.assertEqual(system_volume.partition_scheme, "GUID_partition_scheme")
        self.assertEqual(system_volume.filesystem, "APFS")
        self.assertEqual(system_volume.mount_points, ("/",))
        self.assertEqual(system_volume.size_bytes, 994138292224)
        self.assertEqual(
            inspection.space_for_mount("/"),
            SpaceInfo(
                total_bytes=994138292224,
                used_bytes=482134964224,
                available_bytes=512003328000,
            ),
        )
        data_volume = inspection.partitions[3]
        self.assertEqual(data_volume.mount_points, ("/System/Volumes/Data",))
        preboot = inspection.partitions[4]
        self.assertEqual(preboot.name, "disk1s3")
        self.assertEqual(preboot.mount_points, ())

    def test_inspect_exfat_stick(self) -> None:
        """A recorded exFAT stick session is detected through the exFAT property."""
        runner = FakeRunner(
            list_plist=EXFAT_STICK_LIST_PLIST,
            info_by_device={
                "disk3": EXFAT_DISK_INFO_PLIST,
                "disk3s1": EXFAT_VOLUME_INFO_PLIST,
            },
        )
        inspections = MacOSDriveInspector(command_runner=runner).inspect_all()
        self.assertEqual(len(inspections), 1)
        inspection = inspections[0]
        self.assertTrue(inspection.has_exfat_partition)
        self.assertFalse(inspection.has_fat32_partition)
        partition = inspection.partitions[0]
        self.assertEqual(partition.filesystem, "exFAT")
        self.assertTrue(partition.is_exfat)
        self.assertEqual(
            inspection.space_for_mount("/Volumes/DJ_SET_2"),
            SpaceInfo(
                total_bytes=64023056384,
                used_bytes=266299392,
                available_bytes=63756756992,
            ),
        )

    def test_ms_dos_fat32_variant_detected(self) -> None:
        """The MS-DOS FAT32 filesystem name normalizes to a detectable FAT32 record."""
        runner = FakeRunner(
            list_plist=MSDOS_FAT32_LIST_PLIST,
            info_by_device={
                "disk4": MSDOS_FAT32_DISK_INFO_PLIST,
                "disk4s1": MSDOS_FAT32_VOLUME_INFO_PLIST,
            },
        )
        inspections = MacOSDriveInspector(command_runner=runner).inspect_all()
        partition = inspections[0].partitions[0]
        self.assertEqual(partition.filesystem, "FAT32")
        self.assertTrue(partition.is_fat32)

    def test_legacy_apfs_volumes_on_disk_entry(self) -> None:
        """APFS volumes listed directly on a disk entry attach to that disk."""
        runner = FakeRunner(
            list_plist=LEGACY_APFS_LIST_PLIST,
            info_by_device={
                "disk7": LEGACY_APFS_DISK_INFO_PLIST,
                "disk7s2": LEGACY_APFS_VOLUME_INFO_PLIST,
            },
        )
        inspections = MacOSDriveInspector(command_runner=runner).inspect_all()
        self.assertEqual(len(inspections), 1)
        inspection = inspections[0]
        self.assertEqual(len(inspection.partitions), 2)
        volume = inspection.partitions[1]
        self.assertEqual(volume.name, "disk7s2")
        self.assertEqual(volume.partition_number, 2)
        self.assertEqual(volume.mount_points, ("/Volumes/Legacy",))
        self.assertEqual(
            inspection.space_for_mount("/Volumes/Legacy"),
            SpaceInfo(total_bytes=1000, used_bytes=600, available_bytes=400),
        )

    def test_container_with_unknown_physical_store_raises(self) -> None:
        """A container naming an unknown physical store raises before any info call."""
        runner = FakeRunner(list_plist=CONTAINER_ORPHAN_LIST_PLIST, info_by_device={})
        with self.assertRaises(InspectionParseError):
            MacOSDriveInspector(command_runner=runner).inspect_all()
        self.assertEqual(runner.commands, [list(DISKUTIL_LIST_COMMAND)])

    def test_apfs_physical_store_fallback_key(self) -> None:
        """Containers using the APFSPhysicalStore key still resolve their volumes."""
        runner = FakeRunner(
            list_plist=CONTAINER_FALLBACK_KEY_LIST_PLIST,
            info_by_device={
                "disk8": CONTAINER_FALLBACK_DISK_INFO_PLIST,
                "disk1s1": CONTAINER_FALLBACK_VOLUME_INFO_PLIST,
            },
        )
        inspections = MacOSDriveInspector(command_runner=runner).inspect_all()
        self.assertEqual(len(inspections), 1)
        volume = inspections[0].partitions[1]
        self.assertEqual(volume.name, "disk1s1")
        self.assertEqual(volume.mount_points, ("/Volumes/Ghost",))
        self.assertEqual(volume.size_bytes, 900)

    def test_multi_store_container_attaches_to_all_owners(self) -> None:
        """A multi-store container attaches its volumes to every store owner."""
        runner = FakeRunner(
            list_plist=MULTI_STORE_LIST_PLIST,
            info_by_device={
                "disk0": MULTI_STORE_DISK0_INFO_PLIST,
                "disk1": MULTI_STORE_DISK1_INFO_PLIST,
                "disk2s1": MULTI_STORE_VOLUME_INFO_PLIST,
            },
        )
        inspections = MacOSDriveInspector(command_runner=runner).inspect_all()
        self.assertEqual([inspection.drive.name for inspection in inspections], ["disk0", "disk1"])
        for inspection in inspections:
            volume = inspection.partitions[1]
            self.assertEqual(volume.name, "disk2s1")
            self.assertEqual(volume.size_bytes, 5000)
            self.assertEqual(
                inspection.space_for_mount("/Volumes/Fusion"),
                SpaceInfo(total_bytes=5000, used_bytes=4000, available_bytes=1000),
            )

    def test_constructor_rejects_non_macos_platform(self) -> None:
        """Construction raises a clear error off macOS."""
        if sys.platform == "darwin":
            self.skipTest("the platform guard passes on macOS hosts")
        with (
            mock.patch("sys.platform", self.REAL_PLATFORM),
            self.assertRaisesRegex(DriveInspectionError, "requires macOS"),
        ):
            MacOSDriveInspector(command_runner=FakeRunner(list_plist="", info_by_device={}))

    def test_constructor_rejects_nonpositive_timeout(self) -> None:
        """Construction rejects zero and negative timeout overrides."""
        runner = fat32_stick_runner()
        for bad_timeout in (0, -5.0):
            with self.assertRaisesRegex(DriveInspectionError, "positive"):
                MacOSDriveInspector(command_runner=runner, timeout_seconds=bad_timeout)

    def test_constructor_accepts_custom_timeout(self) -> None:
        """A positive timeout override keeps inspection behavior intact."""
        inspector = MacOSDriveInspector(command_runner=fat32_stick_runner(), timeout_seconds=5.0)
        inspections = inspector.inspect_all()
        self.assertEqual(inspections[0].drive.name, "disk2")

    def test_bounded_runner_captures_small_output(self) -> None:
        """The bounded runner captures small stdout and stderr without loss."""
        result = bounded_command_runner([sys.executable, "-c", "print('hello')"], 15.0)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "hello")
        self.assertEqual(result.stderr, "")

    def test_bounded_runner_rejects_oversized_output(self) -> None:
        """The bounded runner discards stdout above the cap during capture."""
        oversized = f"print('x' * {MAX_PLIST_OUTPUT_BYTES + 100})"
        with self.assertRaisesRegex(InspectionParseError, "byte limit"):
            bounded_command_runner([sys.executable, "-c", oversized], 15.0)

    def test_bounded_runner_kills_stuck_child(self) -> None:
        """A child that never closes stdout fails within the deadline, not forever."""
        started = time.monotonic()
        result = bounded_command_runner([sys.executable, "-c", "import time; time.sleep(60)"], 2.0)
        elapsed = time.monotonic() - started
        self.assertNotEqual(result.returncode, 0)
        self.assertLess(elapsed, 30.0)

    def test_bounded_runner_survives_stderr_flood(self) -> None:
        """A child flooding stderr fails within the deadline with bounded storage."""
        started = time.monotonic()
        flood = "import sys; sys.stderr.write('e' * 200000); sys.stderr.flush()"
        result = bounded_command_runner([sys.executable, "-c", flood], 2.0)
        elapsed = time.monotonic() - started
        self.assertNotEqual(result.returncode, 0)
        self.assertLessEqual(len(result.stderr), MAX_STDERR_BYTES)
        self.assertLess(elapsed, 30.0)

    def test_bounded_runner_rejects_nonpositive_timeout(self) -> None:
        """The runner validates its timeout argument directly."""
        for bad_timeout in (0, -1.0):
            with self.assertRaises(ValueError):
                bounded_command_runner(["python", "-c", "pass"], bad_timeout)

    def test_terminate_process_tolerates_exit_race(self) -> None:
        """Terminate and kill tolerate a child that already exited."""

        class ExitedProcess:
            def terminate(self) -> None:
                raise ProcessLookupError

            def kill(self) -> None:
                raise ProcessLookupError

            def wait(self, timeout: float | None = None) -> int:
                return 0

        _terminate_process(ExitedProcess(), time.monotonic() + 10.0)

    def test_terminate_process_escalates_to_kill(self) -> None:
        """A child ignoring terminate escalates to kill with bounded waits."""

        class StubbornProcess:
            def __init__(self) -> None:
                self.calls: list[str] = []

            def terminate(self) -> None:
                self.calls.append("terminate")

            def kill(self) -> None:
                self.calls.append("kill")

            def wait(self, timeout: float | None = None) -> int:
                if "kill" not in self.calls:
                    raise subprocess.TimeoutExpired(cmd=[], timeout=timeout or 0.0)
                return 0

        process = StubbornProcess()
        _terminate_process(process, time.monotonic() + 30.0)
        self.assertEqual(process.calls, ["terminate", "kill"])

    def test_oversized_plist_raises_parse_error(self) -> None:
        """Plist output above the byte cap raises before parsing."""
        oversized = "x" * (MAX_PLIST_OUTPUT_BYTES + 1)
        with self.assertRaisesRegex(InspectionParseError, "byte limit"):
            MacOSDriveInspector._parse_plist_dict(oversized, "diskutil list")

    def test_parse_plist_dict_rejects_undecodable_output(self) -> None:
        """Output with lone surrogates raises InspectionParseError, not UnicodeEncodeError."""
        with self.assertRaises(InspectionParseError):
            MacOSDriveInspector._parse_plist_dict("\ud800 not a plist", "diskutil list")

    def test_inspect_device_rejects_nested_suffix(self) -> None:
        """Identifiers with more than one sN suffix fail validation."""
        inspector = MacOSDriveInspector(command_runner=fat32_stick_runner())
        for unsafe in ("disk2s1s2", "/dev/disk2s1s2"):
            with self.assertRaises(CommandExecutionError):
                inspector.inspect_device(unsafe)

    def test_drive_identity_maps_vendor_and_serial(self) -> None:
        """Vendor, Manufacturer, and SerialNumber info keys map onto the identity."""
        runner = FakeRunner(
            list_plist=MSDOS_FAT32_LIST_PLIST,
            info_by_device={
                "disk4": MSDOS_FAT32_DISK_INFO_PLIST,
                "disk4s1": MSDOS_FAT32_VOLUME_INFO_PLIST,
            },
        )
        inspection = MacOSDriveInspector(command_runner=runner).inspect_all()[0]
        self.assertEqual(inspection.drive.vendor, "SanDisk")
        self.assertEqual(inspection.drive.serial, "SN123456")

    def test_inspect_device_info_calls_limited_to_target(self) -> None:
        """inspect_device runs info calls only for the matching drive."""
        runner = FakeRunner(
            list_plist=COMBINED_LIST_PLIST,
            info_by_device={
                "disk2": FAT32_DISK_INFO_PLIST,
                "disk2s1": FAT32_VOLUME_INFO_PLIST,
                "disk3": EXFAT_DISK_INFO_PLIST,
                "disk3s1": EXFAT_VOLUME_INFO_PLIST,
            },
        )
        inspection = MacOSDriveInspector(command_runner=runner).inspect_device("disk3")
        self.assertEqual(
            runner.commands,
            [
                list(DISKUTIL_LIST_COMMAND),
                [*DISKUTIL_INFO_COMMAND, "disk3"],
                [*DISKUTIL_INFO_COMMAND, "disk3s1"],
            ],
        )
        self.assertEqual(inspection.drive.name, "disk3")
        self.assertTrue(inspection.has_exfat_partition)

    def test_malformed_list_plist_raises_parse_error(self) -> None:
        """A malformed list reply raises InspectionParseError."""
        runner = FakeRunner(list_plist=MALFORMED_LIST_PLIST, info_by_device={})
        with self.assertRaises(InspectionParseError):
            MacOSDriveInspector(command_runner=runner).inspect_all()

    def test_non_mapping_plist_raises_parse_error(self) -> None:
        """A non-mapping list reply raises InspectionParseError."""
        runner = FakeRunner(list_plist=NON_MAPPING_LIST_PLIST, info_by_device={})
        with self.assertRaises(InspectionParseError):
            MacOSDriveInspector(command_runner=runner).inspect_all()

    def test_missing_all_disks_and_partitions_raises_parse_error(self) -> None:
        """A list reply without AllDisksAndPartitions raises InspectionParseError."""
        runner = FakeRunner(list_plist=MISSING_KEY_LIST_PLIST, info_by_device={})
        with self.assertRaises(InspectionParseError):
            MacOSDriveInspector(command_runner=runner).inspect_all()

    def test_all_disks_and_partitions_not_list_raises_parse_error(self) -> None:
        """A non-list AllDisksAndPartitions value raises InspectionParseError."""
        runner = FakeRunner(list_plist=NOT_A_LIST_LIST_PLIST, info_by_device={})
        with self.assertRaises(InspectionParseError):
            MacOSDriveInspector(command_runner=runner).inspect_all()

    def test_disk_entry_not_mapping_raises_parse_error(self) -> None:
        """A non-mapping disk entry raises InspectionParseError."""
        runner = FakeRunner(list_plist=DISK_ENTRY_NOT_MAPPING_LIST_PLIST, info_by_device={})
        with self.assertRaises(InspectionParseError):
            MacOSDriveInspector(command_runner=runner).inspect_all()

    def test_partition_entry_not_mapping_raises_parse_error(self) -> None:
        """A non-mapping partition entry raises InspectionParseError."""
        runner = FakeRunner(
            list_plist=PARTITION_ENTRY_NOT_MAPPING_LIST_PLIST,
            info_by_device={},
        )
        with self.assertRaises(InspectionParseError):
            MacOSDriveInspector(command_runner=runner).inspect_all()

    def test_partitions_not_list_raises_parse_error(self) -> None:
        """A non-list Partitions value raises InspectionParseError."""
        runner = FakeRunner(list_plist=PARTITIONS_NOT_LIST_LIST_PLIST, info_by_device={})
        with self.assertRaises(InspectionParseError):
            MacOSDriveInspector(command_runner=runner).inspect_all()

    def test_unsafe_disk_identifier_raises_parse_error(self) -> None:
        """An unsafe device identifier in a recorded reply raises InspectionParseError."""
        runner = FakeRunner(list_plist=UNSAFE_IDENTIFIER_LIST_PLIST, info_by_device={})
        with self.assertRaises(InspectionParseError):
            MacOSDriveInspector(command_runner=runner).inspect_all()

    def test_list_command_failure_raises_execution_error(self) -> None:
        """A failing diskutil list command raises CommandExecutionError."""
        runner = FakeRunner(
            list_plist="",
            info_by_device={},
            returncode=1,
            stderr="diskutil: could not list disks",
        )
        with self.assertRaises(CommandExecutionError):
            MacOSDriveInspector(command_runner=runner).inspect_all()

    def test_info_command_failure_raises_execution_error(self) -> None:
        """A failing diskutil info command raises CommandExecutionError."""

        def failing_info_runner(cmd: list[str]) -> subprocess.CompletedProcess[str]:
            if cmd[:2] == ["diskutil", "info"]:
                return subprocess.CompletedProcess(cmd, 1, "", "info boom")
            return subprocess.CompletedProcess(cmd, 0, FAT32_STICK_LIST_PLIST, "")

        with self.assertRaises(CommandExecutionError):
            MacOSDriveInspector(command_runner=failing_info_runner).inspect_all()

    def test_timeout_raises_execution_error(self) -> None:
        """A timed-out diskutil command raises CommandExecutionError."""
        runner = FakeRunner(
            list_plist="",
            info_by_device={},
            error=subprocess.TimeoutExpired(cmd="diskutil", timeout=15),
        )
        with self.assertRaises(CommandExecutionError):
            MacOSDriveInspector(command_runner=runner).inspect_all()

    def test_missing_diskutil_raises_execution_error(self) -> None:
        """A missing diskutil binary raises CommandExecutionError."""
        runner = FakeRunner(
            list_plist="",
            info_by_device={},
            error=FileNotFoundError(2, "No such file or directory", "diskutil"),
        )
        with self.assertRaises(CommandExecutionError):
            MacOSDriveInspector(command_runner=runner).inspect_all()

    def test_inspect_device_by_name_and_path(self) -> None:
        """inspect_device resolves bare identifiers and /dev paths to one record."""
        inspector = MacOSDriveInspector(command_runner=fat32_stick_runner())
        by_name = inspector.inspect_device("disk2")
        by_path = inspector.inspect_device("/dev/disk2")
        self.assertEqual(by_name.drive.name, "disk2")
        self.assertEqual(by_path.drive.name, "disk2")

    def test_inspect_device_partition_identifier_returns_drive(self) -> None:
        """inspect_device resolves a partition identifier to its owning drive."""
        inspector = MacOSDriveInspector(command_runner=fat32_stick_runner())
        by_partition = inspector.inspect_device("/dev/disk2s1")
        self.assertEqual(by_partition.drive.name, "disk2")

    def test_inspect_device_not_found_raises(self) -> None:
        """inspect_device raises CommandExecutionError for unknown devices."""
        inspector = MacOSDriveInspector(command_runner=fat32_stick_runner())
        with self.assertRaises(CommandExecutionError):
            inspector.inspect_device("disk9")

    def test_inspect_device_rejects_unsafe_input(self) -> None:
        """inspect_device raises CommandExecutionError for unsafe or malformed targets."""
        inspector = MacOSDriveInspector(command_runner=fat32_stick_runner())
        for unsafe in ("", "/dev/../disk2", "disk2/../x", "/etc/passwd", "rdisk2"):
            with self.assertRaises(CommandExecutionError):
                inspector.inspect_device(unsafe)

    def test_unmounted_volume_has_no_space_and_no_info_call(self) -> None:
        """Unmounted volumes produce no space record and no info invocation."""
        runner = system_disk_runner()
        inspections = MacOSDriveInspector(command_runner=runner).inspect_all()
        inspected_identifiers = [cmd[3] for cmd in runner.commands if cmd[1] == "info"]
        self.assertEqual(inspected_identifiers, ["disk0", "disk1s1", "disk1s2"])
        inspection = inspections[0]
        self.assertIsNone(inspection.space_for_mount("/System/Volumes/Preboot"))
        self.assertEqual(
            inspection.space_for_mount("/System/Volumes/Data"),
            SpaceInfo(
                total_bytes=994138292224,
                used_bytes=484200644608,
                available_bytes=509937647616,
            ),
        )

    def test_space_used_clamped_non_negative(self) -> None:
        """Free space exceeding total space clamps used bytes to zero."""
        runner = FakeRunner(
            list_plist=OVERFLOW_LIST_PLIST,
            info_by_device={
                "disk5": OVERFLOW_DISK_INFO_PLIST,
                "disk5s1": OVERFLOW_VOLUME_INFO_PLIST,
            },
        )
        inspections = MacOSDriveInspector(command_runner=runner).inspect_all()
        self.assertEqual(
            inspections[0].space_for_mount("/Volumes/OVER"),
            SpaceInfo(total_bytes=100, used_bytes=0, available_bytes=900),
        )

    def test_untrusted_metadata_truncated_and_mount_traversal_filtered(self) -> None:
        """Overlong labels are truncated and traversal mount points are filtered."""
        runner = FakeRunner(
            list_plist=TRAVERSAL_LIST_PLIST,
            info_by_device={"disk6": TRAVERSAL_DISK_INFO_PLIST},
        )
        inspection = MacOSDriveInspector(command_runner=runner).inspect_all()[0]
        partition = inspection.partitions[0]
        self.assertEqual(len(partition.label or ""), 512)
        self.assertEqual(partition.mount_points, ())
        self.assertIsNone(inspection.space_for_mount("/Volumes/../etc"))

    def test_only_fixed_diskutil_commands_executed(self) -> None:
        """A full session executes exactly the fixed argument arrays, nothing else."""
        runner = fat32_stick_runner()
        MacOSDriveInspector(command_runner=runner).inspect_all()
        self.assertEqual(
            runner.commands,
            [
                list(DISKUTIL_LIST_COMMAND),
                [*DISKUTIL_INFO_COMMAND, "disk2"],
                [*DISKUTIL_INFO_COMMAND, "disk2s1"],
            ],
        )

    def test_clean_str_strips_ansi_and_control_characters(self) -> None:
        """The shared sanitizer strips ANSI escapes and control characters."""
        self.assertEqual(clean_str("\x1b[31mJUNK\x00NAME\x07"), "JUNKNAME")
        self.assertIsNone(clean_str("   "))
        self.assertIsNone(clean_str(None))


if __name__ == "__main__":
    unittest.main()
