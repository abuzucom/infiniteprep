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
from collections.abc import Collection
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlparse

from infiniteprep import storage_paths
from infiniteprep.domain.enums import KeyDisplayNotation
from infiniteprep.domain.tag_output import BPM_DECIMAL_PLACES, TagOutputFormat
from infiniteprep.private_files import create_private_dirs

logger = logging.getLogger(__name__)
DEFAULT_FAKE_LOSSLESS_METHOD: Final[str] = "signal_analysis"
SUPPORTED_FAKE_LOSSLESS_METHODS: Final[frozenset[str]] = frozenset({DEFAULT_FAKE_LOSSLESS_METHOD})
SUPPORTED_TAG_KEY_NOTATIONS: Final[frozenset[str]] = frozenset(KeyDisplayNotation)
# Where the rows without a value for the sorted column sit in the table.
UNSORTED_PLACEMENT_FIRST: Final[str] = "first"
UNSORTED_PLACEMENT_LAST: Final[str] = "last"
SUPPORTED_UNSORTED_PLACEMENTS: Final[tuple[str, ...]] = (
    UNSORTED_PLACEMENT_FIRST,
    UNSORTED_PLACEMENT_LAST,
)
# Theme identifiers name the project theme files under ui/themes.
DARK_THEME_ID: Final[str] = "infiniteprep_dark.xml"
LIGHT_THEME_ID: Final[str] = "infiniteprep_light.xml"
# Older settings files can name the qt-material teal themes or the native style.
# Those values load as the matching project theme. The pickers lack Native, so it
# loads as dark.
LEGACY_THEME_REPLACEMENTS: Final[dict[str, str]] = {
    "dark_teal.xml": DARK_THEME_ID,
    "light_teal.xml": LIGHT_THEME_ID,
    "native": DARK_THEME_ID,
}
TEXT_SIZE_COMPACT: Final[str] = "compact"
TEXT_SIZE_STANDARD: Final[str] = "standard"
TEXT_SIZE_LARGE: Final[str] = "large"
SUPPORTED_TEXT_SIZES: Final[tuple[str, ...]] = (
    TEXT_SIZE_COMPACT,
    TEXT_SIZE_STANDARD,
    TEXT_SIZE_LARGE,
)
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
DEFAULT_USB_RECOVERY_EXPIRY_DAYS: Final[int] = 30
MIN_USB_RECOVERY_EXPIRY_DAYS: Final[int] = 1
MAX_USB_RECOVERY_EXPIRY_DAYS: Final[int] = 365


def _is_disallowed_ip(ip_text: str) -> bool:
    """Return True if ip_text parses as a non-public, loopback, private, or reserved IP."""
    try:
        ip_address = ipaddress.ip_address(ip_text)
    except ValueError:
        return False
    if isinstance(ip_address, ipaddress.IPv4Address) and ip_address in CGNAT_NETWORK:
        return True
    return (
        ip_address.is_private
        or ip_address.is_loopback
        or ip_address.is_link_local
        or ip_address.is_multicast
        or ip_address.is_reserved
        or ip_address.is_unspecified
        or not ip_address.is_global
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
    fake_lossless_method: str = DEFAULT_FAKE_LOSSLESS_METHOD
    playlist_relative_paths: bool = True
    # Ends an Analyze or write batch at its first failed file. Imports ignore it.
    stop_batch_on_first_failure: bool = False
    # Places the rows without a value for the sorted column first or last.
    unsorted_rows_placement: str = UNSORTED_PLACEMENT_FIRST
    theme: str = DARK_THEME_ID
    text_size: str = TEXT_SIZE_STANDARD
    file_table_layout: str = ""  # Base64-encoded QHeaderView state.
    file_table_column_count: int = 0  # Column count when the layout was saved.
    detail_table_layout: str = ""  # Base64-encoded QHeaderView state.
    detail_table_column_count: int = 0  # Column count when the layout was saved.
    window_geometry: str = ""  # Base64-encoded QWidget geometry of the main window.
    # JSON map of library column index to shown (true) or hidden (false), set in the
    # header menu. Columns without an entry follow the width rules.
    file_table_column_choices: str = ""
    waveform_zoom_seconds: int = DEFAULT_WAVEFORM_ZOOM_SECONDS
    usb_reserve_percent: float = 0.0  # Percent of the estimated export size.
    usb_reserve_bytes: int = 0
    usb_recovery_expiry_enabled: bool = True
    usb_recovery_expiry_days: int = DEFAULT_USB_RECOVERY_EXPIRY_DAYS
    tag_key_notation: str = KeyDisplayNotation.CAMELOT.value
    tag_key_leading_zero: bool = False
    tag_bpm_decimals: int = 0


def replace_legacy_theme(theme: str) -> str:
    """Return the project theme for a legacy theme value, or the value unchanged."""
    return LEGACY_THEME_REPLACEMENTS.get(theme, theme)


# Settings that accept only a fixed set of values, with the problem a log names otherwise.
CHOICE_SETTINGS: Final[dict[str, tuple[Collection[object], str]]] = {
    "waveform_zoom_seconds": (
        SUPPORTED_WAVEFORM_ZOOM_SECONDS,
        "an unsupported waveform zoom duration",
    ),
    "fake_lossless_method": (
        SUPPORTED_FAKE_LOSSLESS_METHODS,
        "an unsupported fake-lossless detection method",
    ),
    "tag_key_notation": (SUPPORTED_TAG_KEY_NOTATIONS, "an unsupported written key notation"),
    "unsorted_rows_placement": (
        SUPPORTED_UNSORTED_PLACEMENTS,
        "an unsupported unsorted row placement",
    ),
    "text_size": (SUPPORTED_TEXT_SIZES, "an unsupported text size"),
}


def _find_setting_value_problem(name: str, value: object, default: object) -> str | None:
    """Return why a value is invalid for a setting, or None for a valid value."""
    if type(value) is not type(default):
        return "an invalid type"
    choice = CHOICE_SETTINGS.get(name)
    if choice is not None and value not in choice[0]:
        return choice[1]
    return _find_numeric_setting_problem(name, value)


def _find_numeric_setting_problem(name: str, value: object) -> str | None:
    """Return why a numeric setting value is out of range, or None when valid.

    The caller has already matched the value type to the default type exactly, so an
    int setting holds an int here and never a bool.
    """
    if name == "tag_bpm_decimals" and value not in BPM_DECIMAL_PLACES:
        return "an unsupported BPM precision"
    if name in NONNEGATIVE_COUNT_SETTINGS and value < 0:
        return "a negative count"
    if name == "usb_reserve_percent" and not _is_supported_reserve_percent(value):
        return "a reserve percentage outside 0 to 100"
    if name == "usb_reserve_bytes" and not 0 <= value <= MAX_USB_RESERVE_BYTES:
        return "a reserve size outside the supported range"
    if (
        name == "usb_recovery_expiry_days"
        and not MIN_USB_RECOVERY_EXPIRY_DAYS <= value <= MAX_USB_RECOVERY_EXPIRY_DAYS
    ):
        return (
            f"an expiry window outside {MIN_USB_RECOVERY_EXPIRY_DAYS} to "
            f"{MAX_USB_RECOVERY_EXPIRY_DAYS} days"
        )
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


def _sync_directory(directory: Path) -> None:
    """Flush the folder entry that the settings rename changed, on POSIX.

    Windows cannot open a folder with os.open. NTFS journals the rename itself. A failed
    flush leaves the new settings in place, so it only logs.
    """
    if os.name == "nt":
        return
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError as error:
        logger.warning("Could not open the settings folder to flush it: %s", error.strerror)
        return
    try:
        os.fsync(descriptor)
    except OSError as error:
        logger.warning("Could not flush the settings folder: %s", error.strerror)
    finally:
        os.close(descriptor)


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

    def load_tag_output(self) -> TagOutputFormat:
        """Read the saved key and BPM tag output preferences."""
        settings = self.load()
        return TagOutputFormat(
            KeyDisplayNotation(settings.tag_key_notation),
            settings.tag_key_leading_zero,
            settings.tag_bpm_decimals,
        )

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
        merged["theme"] = replace_legacy_theme(str(merged["theme"]))
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
        create_private_dirs(directory)
        payload = json.dumps(asdict(settings), indent=2, ensure_ascii=True)
        # mkstemp creates the file with mode 0600.
        descriptor, temporary_name = tempfile.mkstemp(
            dir=directory, prefix=f"{self.settings_path.name}.", suffix=".tmp", text=True
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(payload)
                # A power loss after the rename must not leave an empty settings file.
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.settings_path)
        except BaseException:
            # Every failure, including an interrupt, removes the temporary file.
            # A failed removal must not replace the original error.
            with contextlib.suppress(OSError):
                temporary_path.unlink(missing_ok=True)
            raise
        _sync_directory(directory)

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
