"""Application settings persistence and SSRF-safe endpoint validation."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Final
from urllib.parse import urlparse

import platformdirs

from infiniteprep.domain.enums import KeyDisplayNotation

ALLOWED_LOCAL_HOSTS: Final[frozenset[str]] = frozenset({"127.0.0.1", "localhost", "::1"})
BLOCKED_SSRF_HOSTS: Final[frozenset[str]] = frozenset({"169.254.169.254"})


def validate_endpoint_url(url: str) -> tuple[bool, str | None]:
    """Validate an HTTP(S) endpoint URL against SSRF rules (localhost HTTP or remote HTTPS)."""
    parsed = urlparse(url.strip())
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").lower()
    if not scheme or not host:
        return False, "Endpoint URL must include a valid scheme and hostname."
    if scheme == "http" and host in ALLOWED_LOCAL_HOSTS:
        return True, None
    if scheme == "https" and host not in BLOCKED_SSRF_HOSTS and not host.startswith("0."):
        return True, None
    return (
        False,
        f"Blocked endpoint URL '{url}': only http://localhost, http://127.0.0.1, or https:// "
        "endpoints are permitted.",
    )


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
    prefer_essentia_cli: bool = False
    essentia_cli_path: str = ""
    fpcalc_custom_path: str = ""
    transcode_mode: str = "sibling_export"
    transcode_preset: str = "XDJ_RX3_LOSSLESS_AUTO"


class SettingsManager:
    """Loads and saves non-secret AppSettings to a JSON file."""

    def __init__(self, settings_path: Path | None = None) -> None:
        """Initialize SettingsManager with an explicit path or platformdirs default."""
        if settings_path is None:
            cfg_dir = Path(platformdirs.user_config_dir("InfinitePrep", "InfinitePrep"))
            settings_path = cfg_dir / "settings.json"
        self.settings_path = settings_path

    def load(self) -> AppSettings:
        """Load AppSettings from disk or return defaults if not present."""
        if not self.settings_path.exists():
            return AppSettings()
        try:
            raw = json.loads(self.settings_path.read_text(encoding="utf-8"))
        except Exception:
            return AppSettings()
        defaults = asdict(AppSettings())
        merged = {k: raw.get(k, v) for k, v in defaults.items()}
        return AppSettings(**merged)

    def save(self, settings: AppSettings) -> None:
        """Persist AppSettings to disk as formatted JSON."""
        self.settings_path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(settings), indent=2, ensure_ascii=True)
        self.settings_path.write_text(payload, encoding="utf-8")
