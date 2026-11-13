"""Loader for USB export targets (D131, D188, D372).

The loader inspects every drive, keeps the drives on a USB, SD, or MMC bus,
and probes each. Drives on other buses never reach a probe or the result.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from typing import Final, Protocol

from infiniteprep.services.usb.linux import LinuxDriveInspector
from infiniteprep.services.usb.linux_probe import LinuxPreflightProbe
from infiniteprep.services.usb.macos import MacOSDriveInspector
from infiniteprep.services.usb.macos_probe import MacOSPreflightProbe
from infiniteprep.services.usb.models import DriveInspection, DriveInspectionError
from infiniteprep.services.usb.preflight import is_supported_bus_class
from infiniteprep.services.usb.windows import WindowsDriveInspector
from infiniteprep.services.usb.windows_probe import WindowsPreflightProbe

logger = logging.getLogger(__name__)

# Shown in place of raw command errors, which hold untrusted drive output.
INSPECTION_FAILED_TEXT: Final[str] = "Inspection failed. Reconnect the drive and rescan."
PLATFORM_LINUX_PREFIX: Final[str] = "linux"
PLATFORM_MACOS: Final[str] = "darwin"
PLATFORM_WINDOWS: Final[str] = "win32"


class DriveInspector(Protocol):
    """Lists every drive of the computer in read-only mode."""

    def inspect_all(self) -> tuple[DriveInspection, ...]:
        """Return one inspection per drive."""


class PreflightProbe(Protocol):
    """Adds preflight facts to one drive inspection in read-only mode."""

    def probe(self, inspection: DriveInspection) -> DriveInspection:
        """Return the inspection with preflight facts."""


@dataclass(frozen=True)
class TargetCandidate:
    """One drive on a supported bus. A failed probe leaves the inspection absent."""

    name: str
    inspection: DriveInspection | None
    error: str | None


def build_platform_tools(platform: str) -> tuple[DriveInspector, PreflightProbe]:
    """Return the inspector and probe of a platform name from sys.platform.

    Raises DriveInspectionError for a platform without an inspector.
    """
    if platform.startswith(PLATFORM_LINUX_PREFIX):
        return LinuxDriveInspector(), LinuxPreflightProbe()
    if platform == PLATFORM_MACOS:
        return MacOSDriveInspector(), MacOSPreflightProbe()
    if platform == PLATFORM_WINDOWS:
        return WindowsDriveInspector(), WindowsPreflightProbe()
    raise DriveInspectionError("USB export supports Linux, macOS, and Windows only.")


def load_removable_targets(
    inspector: DriveInspector | None = None,
    probe: PreflightProbe | None = None,
) -> tuple[TargetCandidate, ...]:
    """Inspect all drives and return the probed ones on a supported bus.

    A failed probe yields a candidate with fixed error text. The other drives
    still load. Raises DriveInspectionError when the drive listing fails.
    """
    if inspector is None or probe is None:
        default_inspector, default_probe = build_platform_tools(sys.platform)
        inspector = inspector or default_inspector
        probe = probe or default_probe
    candidates: list[TargetCandidate] = []
    for inspection in inspector.inspect_all():
        if not is_supported_bus_class(inspection.drive):
            continue
        name = inspection.drive.name
        try:
            probed = probe.probe(inspection)
        except DriveInspectionError as exc:
            # The raw message holds untrusted command output. Log the type only.
            logger.warning("Preflight probe failed: %s", type(exc).__name__)
            candidates.append(TargetCandidate(name, None, INSPECTION_FAILED_TEXT))
            continue
        candidates.append(TargetCandidate(name, probed, None))
    return tuple(candidates)
