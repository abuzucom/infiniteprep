"""Application settings persistence and SSRF-safe endpoint validation."""

from __future__ import annotations

import contextlib
import ipaddress
import json
import logging
import math
import os
import socket
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlparse

from infiniteprep import storage_paths
from infiniteprep.domain.enums import KeyDisplayNotation

logger = logging.getLogger(__name__)
DEFAULT_WAVEFORM_ZOOM_SECONDS: Final[int] = 8
SUPPORTED_WAVEFORM_ZOOM_SECONDS: Final[tuple[int, ...]] = (
    2,
    4,
    DEFAULT_WAVEFORM_ZOOM_SECONDS,
    16,
    32,
)
NONNEGATIVE_COUNT_SETTINGS: Final[frozenset[str]] = frozenset(
    {"file_table_column_count", "detail_table_column_count"}
)

ALLOWED_LOCAL_HOSTS: Final[frozenset[str]] = frozenset({"127.0.0.1", "localhost", "::1"})
BLOCKED_METADATA_HOSTS: Final[frozenset[str]] = frozenset(
    {
        "169.254.169.254",
        "metadata.google.internal",
        "metadata.goog",
        "instance-data",
        "localhost",
        "localhost.localdomain",
    }
)
CGNAT_NETWORK: Final[ipaddress.IPv4Network] = ipaddress.IPv4Network("100.64.0.0/10")
BYTES_PER_MIB: Final[int] = 1024 * 1024
# The USB export reserve is the larger of a percentage of the estimated export
# size and a fixed byte count (D97 to D99, D199).
MAX_USB_RESERVE_PERCENT: Final[float] = 100.0
# One TiB. No FAT32 volume holds more than 2 TiB.
MAX_USB_RESERVE_MIB: Final[int] = 1024 * 1024
MAX_USB_RESERVE_BYTES: Final[int] = MAX_USB_RESERVE_MIB * BYTES_PER_MIB


def _is_disallowed_ip(ip_text: str) -> bool:
    """Return True if ip_text parses as a non-public, loopback, private, or reserved IP."""
    try:
        addr = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv4Address) and addr in CGNAT_NETWORK:
        return True
    return (
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_multicast
        or addr.is_reserved
        or addr.is_unspecified
        or not addr.is_global
    )


def _looks_like_ip_or_numeric_host(host: str) -> bool:
    """Return True if host is an IP literal or numeric/hex/octal encoded IP."""
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return host.isdigit() or host.startswith("0x") or host.startswith("0.")


def _validate_resolved_dns_ips(host: str, port: int) -> tuple[bool, str | None]:
    """Resolve host via DNS and reject if any resolved IP is private, loopback, or reserved."""
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        return False, f"DNS resolution failed for '{host}': {exc}"
    if not infos:
        return False, f"No DNS records resolved for '{host}'."
    for info in infos:
        resolved_ip = str(info[4][0])
        if _is_disallowed_ip(resolved_ip):
            return False, f"Host '{host}' resolved to disallowed IP '{resolved_ip}'."
    return True, None


def _validate_https_host(host: str, port: int, *, resolve_dns: bool) -> tuple[bool, str | None]:
    """Verify an HTTPS hostname is not a metadata host, private/loopback IP, or DNS rebind."""
    if host in BLOCKED_METADATA_HOSTS or host.endswith((".internal", ".local")):
        return False, f"Blocked internal or metadata hostname '{host}'."
    if _looks_like_ip_or_numeric_host(host) and _is_disallowed_ip(host):
        return False, f"Blocked non-public or reserved IP address '{host}'."
    if host.isdigit() or host.startswith("0x") or host.startswith("0."):
        return False, f"Blocked numeric or ambiguous IP encoding '{host}'."
    if resolve_dns:
        return _validate_resolved_dns_ips(host, port)
    return True, None


def _validate_http_local_host(
    host: str, port: int, *, resolve_dns: bool
) -> tuple[bool, str | None]:
    """Verify an HTTP host is strictly a local loopback address or resolves only to loopback."""
    if host not in ALLOWED_LOCAL_HOSTS:
        return False, f"Plain HTTP is only permitted for localhost, got '{host}'."
    if not resolve_dns or host in {"127.0.0.1", "::1"}:
        return True, None
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        return False, f"Localhost DNS resolution failed for '{host}': {exc}"
    for info in infos:
        resolved_ip = str(info[4][0])
        if not ipaddress.ip_address(resolved_ip).is_loopback:
            return False, f"Host '{host}' resolved to non-loopback IP '{resolved_ip}'."
    return True, None


def validate_endpoint_url(url: str, *, resolve_dns: bool = False) -> tuple[bool, str | None]:
    """Validate an HTTP(S) endpoint URL against SSRF rules (localhost HTTP or public HTTPS)."""
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower().rstrip(".")
    if not scheme or not host:
        return False, "Endpoint URL must include a valid scheme and hostname."
    if parsed.username is not None or parsed.password is not None:
        return False, "Endpoint URL must not contain embedded credentials."
    if scheme == "http":
        return _validate_http_local_host(host, parsed.port or 80, resolve_dns=resolve_dns)
    if scheme != "https":
        return False, f"Unsupported URL scheme '{scheme}'; only http (local) or https allowed."
    return _validate_https_host(host, parsed.port or 443, resolve_dns=resolve_dns)


@dataclass
class AppSettings:
    """User-configurable settings stored in JSON (excluding secrets)."""

    key_display_notation: str = KeyDisplayNotation.CAMELOT.value
    rename_template: str = "{artist} - {title} ({mix_name})"
    enable_acoustid: bool = True
    enable_musicbrainz: bool = True
    enable_discogs: bool = True
    enable_acrcloud_opt_in: bool = False
    enable_llm_arbitration: bool = False
    ollama_base_url: str = "http://127.0.0.1:11434/v1"
    ollama_model: str = "qwen2.5:3b"
    fpcalc_custom_path: str = ""
    transcode_mode: str = "sibling_export"
    transcode_preset: str = "XDJ_LOSSLESS_AUTO"
    playlist_relative_paths: bool = True
    theme: str = "dark_teal.xml"
    file_table_layout: str = ""  # Base64-encoded QHeaderView state.
    file_table_column_count: int = 0  # Column count when the layout was saved.
    detail_table_layout: str = ""  # Base64-encoded QHeaderView state.
    detail_table_column_count: int = 0  # Column count when the layout was saved.
    waveform_zoom_seconds: int = DEFAULT_WAVEFORM_ZOOM_SECONDS
    usb_reserve_percent: float = 0.0  # Percent of the estimated export size.
    usb_reserve_bytes: int = 0


def _find_setting_value_problem(name: str, value: object, default: object) -> str | None:
    """Return why a value is invalid for a setting, or None for a valid value."""
    if type(value) is not type(default):
        return "an invalid type"
    if name == "waveform_zoom_seconds" and value not in SUPPORTED_WAVEFORM_ZOOM_SECONDS:
        return "an unsupported waveform zoom duration"
    if name in NONNEGATIVE_COUNT_SETTINGS and value < 0:
        return "a negative count"
    if name == "usb_reserve_percent" and not _is_supported_reserve_percent(value):
        return "a reserve percentage outside 0 to 100"
    if name == "usb_reserve_bytes" and not 0 <= value <= MAX_USB_RESERVE_BYTES:
        return "a reserve size outside the supported range"
    return None


def _is_supported_reserve_percent(value: float) -> bool:
    """Return True for a finite reserve percentage from 0 to 100."""
    return math.isfinite(value) and 0 <= value <= MAX_USB_RESERVE_PERCENT


def _validated_setting_value(name: str, value: object, default: object) -> object:
    """Use a field default when a persisted value has an invalid type or range."""
    problem = _find_setting_value_problem(name, value, default)
    if problem is None:
        return value
    logger.warning("Ignoring setting %s with %s.", name, problem)
    return default


class SettingsManager:
    """Loads and saves non-secret AppSettings to a JSON file."""

    def __init__(self, settings_path: Path | None = None) -> None:
        """Initialize SettingsManager with an explicit path or the configured settings folder."""
        if settings_path is None:
            settings_path = storage_paths.current().config_dir / "settings.json"
        self.settings_path = settings_path
        self._lock = threading.RLock()

    def load(self) -> AppSettings:
        """Load AppSettings from disk or return defaults if not present."""
        with self._lock:
            return self._load_unlocked()

    def _load_unlocked(self) -> AppSettings:
        """Load settings while the manager lock is held."""
        if not self.settings_path.exists():
            return AppSettings()
        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
            logger.warning("Failed to parse settings file %s: %s", self.settings_path, exc)
            return AppSettings()
        if not isinstance(raw, dict):
            logger.warning(
                "Settings file %s must contain a JSON object; using defaults.",
                self.settings_path,
            )
            return AppSettings()
        defaults = asdict(AppSettings())
        merged: dict[str, object] = {}
        for name, default in defaults.items():
            merged[name] = _validated_setting_value(name, raw.get(name, default), default)
        return AppSettings(**merged)

    def save(self, settings: AppSettings) -> None:
        """Persist AppSettings to disk as formatted JSON."""
        with self._lock:
            self._write_unlocked(settings)

    def _write_unlocked(self, settings: AppSettings) -> None:
        """Replace the settings file atomically while the manager lock is held.

        A reader in another manager instance or process sees the old file or the
        new file, never a partial write. A failed write keeps the old file.
        """
        directory = self.settings_path.parent
        directory.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(settings), indent=2, ensure_ascii=True)
        descriptor, temporary_name = tempfile.mkstemp(
            dir=directory, prefix=f"{self.settings_path.name}.", suffix=".tmp", text=True
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
            os.replace(temporary_path, self.settings_path)
        except BaseException:
            # Every failure, including an interrupt, removes the temporary file.
            # A failed removal must not replace the original error.
            with contextlib.suppress(OSError):
                temporary_path.unlink(missing_ok=True)
            raise

    def update(self, **changes: object) -> AppSettings:
        """Merge selected fields into the latest settings and persist them.

        An unknown name or an invalid value raises ValueError and writes nothing.
        The default fallback applies only to values read from disk. The write
        stores the sanitized settings, so a stored value that failed validation
        on load becomes its default on disk, as with load followed by save.
        """
        with self._lock:
            settings = self._load_unlocked()
            defaults = asdict(AppSettings())
            for name, value in changes.items():
                if name not in defaults:
                    raise ValueError(f"Unknown setting: {name}")
                problem = _find_setting_value_problem(name, value, defaults[name])
                if problem is not None:
                    raise ValueError(f"Setting {name} has {problem}. Supply a supported value.")
                setattr(settings, name, value)
            self._write_unlocked(settings)
            return settings
