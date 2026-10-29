"""Consent-gated Chromaprint fpcalc binary discovery and SHA-256 verified bootstrap."""

from __future__ import annotations

import hashlib
import io
import os
import platform
import shutil
import stat
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Final

import platformdirs

FPCALC_VERSION: Final[str] = "1.5.1"
OFFICIAL_FPCALC_URLS: Final[dict[str, str]] = {
    "windows": (
        "https://github.com/acoustid/chromaprint/releases/download/"
        "v1.5.1/chromaprint-fpcalc-1.5.1-windows-x86_64.zip"
    ),
    "darwin": (
        "https://github.com/acoustid/chromaprint/releases/download/"
        "v1.5.1/chromaprint-fpcalc-1.5.1-macos-universal.tar.gz"
    ),
    "linux": (
        "https://github.com/acoustid/chromaprint/releases/download/"
        "v1.5.1/chromaprint-fpcalc-1.5.1-linux-x86_64.tar.gz"
    ),
}


class FpcalcBootstrapService:
    """Locates or installs the Chromaprint fpcalc executable with explicit user consent."""

    def __init__(
        self,
        install_dir: Path | None = None,
        custom_path: Path | None = None,
        downloader: Callable[[str], bytes] | None = None,
    ) -> None:
        """Initialize FpcalcBootstrapService with an application bin directory."""
        if install_dir is None:
            base = Path(platformdirs.user_data_dir("InfinitePrep", "InfinitePrep"))
            install_dir = base / "bin"
        self.install_dir = install_dir
        self.custom_path = custom_path
        self.downloader = downloader or self._default_download

    def binary_filename(self) -> str:
        """Return OS-specific fpcalc binary filename."""
        return "fpcalc.exe" if platform.system().lower().startswith("win") else "fpcalc"

    def resolve_fpcalc_path(self) -> Path | None:
        """Locate fpcalc via custom_path, install_dir, or system PATH."""
        if self.custom_path and self.custom_path.exists():
            return self.custom_path
        managed = self.install_dir / self.binary_filename()
        if managed.exists():
            return managed
        sys_found = shutil.which(self.binary_filename()) or shutil.which("fpcalc")
        return Path(sys_found) if sys_found else None

    def install_fpcalc(
        self,
        *,
        user_consented: bool,
        expected_sha256: str | None = None,
    ) -> Path:
        """Download and install fpcalc only when user_consented is explicitly True."""
        if not user_consented:
            raise PermissionError(
                "User consent is required before downloading or installing fpcalc; "
                "pass user_consented=True after user approval."
            )
        sys_key = platform.system().lower()
        url = OFFICIAL_FPCALC_URLS.get(sys_key, OFFICIAL_FPCALC_URLS["windows"])
        raw_bytes = self.downloader(url)
        return self.install_from_archive_bytes(
            archive_bytes=raw_bytes,
            expected_sha256=expected_sha256,
            user_consented=user_consented,
        )

    def install_from_archive_bytes(
        self,
        *,
        archive_bytes: bytes,
        expected_sha256: str | None,
        user_consented: bool,
    ) -> Path:
        """Verify archive SHA-256 and extract the fpcalc binary into install_dir."""
        if not user_consented:
            raise PermissionError(
                "User consent is required before extracting fpcalc; aborting setup."
            )
        actual_sha256 = hashlib.sha256(archive_bytes).hexdigest()
        if expected_sha256 and actual_sha256.lower() != expected_sha256.lower():
            raise ValueError(
                f"SHA-256 mismatch for fpcalc archive: expected {expected_sha256}, "
                f"got {actual_sha256}; aborting installation."
            )
        self.install_dir.mkdir(parents=True, exist_ok=True)
        target_bin = self.install_dir / self.binary_filename()
        self._extract_binary_from_zip(archive_bytes, target_bin)
        if not os.name.startswith("nt"):
            current_mode = target_bin.stat().st_mode
            target_bin.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP)
        return target_bin

    def _extract_binary_from_zip(self, archive_bytes: bytes, target_bin: Path) -> None:
        """Extract the fpcalc executable entry from a ZIP byte buffer."""
        exe_name = self.binary_filename()
        with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as zf:
            for member in zf.namelist():
                if Path(member).name.lower() == exe_name.lower():
                    target_bin.write_bytes(zf.read(member))
                    return
        raise FileNotFoundError(
            f"Archive did not contain '{exe_name}'; verify official Chromaprint release bundle."
        )

    def _default_download(self, url: str) -> bytes:
        """Download archive bytes over HTTPS."""
        if not url.startswith("https://"):
            raise ValueError(f"Bootstrap download URL must use https:// scheme: {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "InfinitePrep/0.1.0"})  # noqa: S310
        with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
            return bytes(resp.read())
