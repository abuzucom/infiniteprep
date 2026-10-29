"""Consent-gated Chromaprint fpcalc binary discovery and SHA-256 verified bootstrap."""

from __future__ import annotations

import hashlib
import hmac
import io
import os
import platform
import shutil
import stat
import tarfile
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Final

import platformdirs

FPCALC_VERSION: Final[str] = "1.5.1"
MAX_FPCALC_BINARY_BYTES: Final[int] = 33_554_432  # 32 MiB cap for extracted binary
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
# SHA-256 of each OFFICIAL_FPCALC_URLS asset. A digest pins the exact release
# archive. A replaced asset or redirected download fails before extraction.
OFFICIAL_FPCALC_SHA256: Final[dict[str, str]] = {
    "windows": "36b478e16aa69f757f376645db0d436073a42c0097b6bb2677109e7835b59bbc",
    "darwin": "d4d8faff4b5f7c558d9be053da47804f9501eaa6c2f87906a9f040f38d61c860",
    "linux": "4d7433a7f778e5946d7225230681cbcd634e153316ecac87c538c33ac32387a5",
}


def _is_matching_fpcalc_name(member_path: str, exe_name: str) -> bool:
    """Return True if member_path basename matches fpcalc or fpcalc.exe."""
    return Path(member_path).name.lower() in (exe_name.lower(), "fpcalc", "fpcalc.exe")


def _verify_entry_size(size_bytes: int) -> None:
    """Raise ValueError if archive entry size exceeds MAX_FPCALC_BINARY_BYTES."""
    if size_bytes > MAX_FPCALC_BINARY_BYTES:
        raise ValueError("Extracted fpcalc binary exceeds maximum size limit.")


def _try_write_tar_member(
    tf: tarfile.TarFile,
    member: tarfile.TarInfo,
    target_bin: Path,
) -> bool:
    """Verify and write a matching tarfile member to target_bin."""
    _verify_entry_size(member.size)
    extracted = tf.extractfile(member)
    if extracted is None:
        return False
    target_bin.write_bytes(extracted.read(MAX_FPCALC_BINARY_BYTES))
    return True


def _read_bounded_zip_entry(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> bytes:
    """Read a ZIP entry and raise ValueError past MAX_FPCALC_BINARY_BYTES."""
    with zf.open(info) as entry:
        data = entry.read(MAX_FPCALC_BINARY_BYTES + 1)
    _verify_entry_size(len(data))
    return data


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
        """Locate fpcalc via validated custom_path, install_dir, or system PATH."""
        if (
            self.custom_path
            and self.custom_path.is_file()
            and self.custom_path.name.lower().startswith("fpcalc")
        ):
            return self.custom_path.resolve()
        managed = self.install_dir / self.binary_filename()
        if managed.is_file():
            return managed.resolve()
        sys_found = shutil.which(self.binary_filename()) or shutil.which("fpcalc")
        return Path(sys_found).resolve() if sys_found else None

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
        if sys_key not in OFFICIAL_FPCALC_URLS:
            sys_key = "windows"
        raw_bytes = self.downloader(OFFICIAL_FPCALC_URLS[sys_key])
        return self.install_from_archive_bytes(
            archive_bytes=raw_bytes,
            expected_sha256=expected_sha256 or OFFICIAL_FPCALC_SHA256[sys_key],
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
        if not expected_sha256:
            raise ValueError(
                "Missing SHA-256 digest for the fpcalc archive. "
                "Pass the digest of the official release archive."
            )
        actual_sha256 = hashlib.sha256(archive_bytes).hexdigest()
        if not hmac.compare_digest(actual_sha256, expected_sha256.lower()):
            raise ValueError(
                f"SHA-256 mismatch for fpcalc archive: expected {expected_sha256}, "
                f"got {actual_sha256}; aborting installation."
            )
        self.install_dir.mkdir(parents=True, exist_ok=True)
        target_bin = self.install_dir / self.binary_filename()
        self._extract_binary_from_archive(archive_bytes, target_bin)
        if not os.name.startswith("nt"):
            current_mode = target_bin.stat().st_mode
            target_bin.chmod(current_mode | stat.S_IXUSR | stat.S_IXGRP)
        return target_bin

    def _extract_binary_from_archive(self, archive_bytes: bytes, target_bin: Path) -> None:
        """Detect ZIP or TAR (.tar.gz / .tar.bz2) archive format and extract fpcalc."""
        if zipfile.is_zipfile(io.BytesIO(archive_bytes)):
            self._extract_binary_from_zip(archive_bytes, target_bin)
            return
        try:
            self._extract_binary_from_tar(archive_bytes, target_bin)
        except tarfile.TarError as exc:
            raise ValueError(f"Unsupported or corrupt fpcalc archive format: {exc}") from exc

    def _extract_binary_from_zip(self, archive_bytes: bytes, target_bin: Path) -> None:
        """Extract the fpcalc executable entry from a ZIP byte buffer."""
        exe_name = self.binary_filename()
        with zipfile.ZipFile(io.BytesIO(archive_bytes), "r") as zf:
            for info in zf.infolist():
                if not info.is_dir() and _is_matching_fpcalc_name(info.filename, exe_name):
                    _verify_entry_size(info.file_size)
                    target_bin.write_bytes(_read_bounded_zip_entry(zf, info))
                    return
        raise FileNotFoundError(
            f"ZIP archive did not contain '{exe_name}'; verify official Chromaprint bundle."
        )

    def _extract_binary_from_tar(self, archive_bytes: bytes, target_bin: Path) -> None:
        """Extract the fpcalc executable entry from a TAR (.tar.gz / .tar.bz2) byte buffer."""
        exe_name = self.binary_filename()
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as tf:
            for member in tf.getmembers():
                if (
                    member.isfile()
                    and _is_matching_fpcalc_name(member.name, exe_name)
                    and _try_write_tar_member(tf, member, target_bin)
                ):
                    return
        raise FileNotFoundError(
            f"TAR archive did not contain '{exe_name}'; verify official Chromaprint bundle."
        )

    def _default_download(self, url: str) -> bytes:
        """Download archive bytes over HTTPS."""
        if not url.startswith("https://"):
            raise ValueError(f"Bootstrap download URL must use https:// scheme: {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "InfinitePrep/0.1.0"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return bytes(resp.read(MAX_FPCALC_BINARY_BYTES))
