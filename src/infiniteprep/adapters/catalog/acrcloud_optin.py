"""Opt-in ACRCloud audio recognition adapter (disabled by default)."""

from __future__ import annotations

from pathlib import Path

from infiniteprep.domain.ports import FingerprintResult


class ACRCloudOptInAdapter:
    """Opt-in cloud audio recognition adapter; never runs unless explicitly enabled."""

    provider_name: str = "acrcloud"
    is_cloud_opt_in: bool = True

    def __init__(
        self,
        enabled_by_user: bool = False,
        access_key: str | None = None,
        access_secret: str | None = None,
    ) -> None:
        """Initialize ACRCloudOptInAdapter with explicit opt-in flag and credentials."""
        self.enabled_by_user = enabled_by_user
        self.access_key = access_key
        self.access_secret = access_secret

    def is_available(self) -> tuple[bool, str | None]:
        """Return True only when user has explicitly opted in and supplied credentials."""
        if not self.enabled_by_user:
            return (
                False,
                "ACRCloud cloud audio recognition is opt-in and disabled by default.",
            )
        if not self.access_key or not self.access_secret:
            return False, "ACRCloud credentials are not configured in Settings."
        return True, None

    def identify(self, audio_reference: Path, file_id: str | None = None) -> FingerprintResult:
        """Return unavailable warning unless opt-in and credentials are configured."""
        _ = (audio_reference, file_id)
        ready, reason = self.is_available()
        if not ready:
            return FingerprintResult(
                provider=self.provider_name,
                raw_fingerprint=None,
                duration=None,
                matches=[],
                warnings=[reason or "ACRCloud opt-in disabled."],
            )
        return FingerprintResult(
            provider=self.provider_name,
            raw_fingerprint=None,
            duration=None,
            matches=[],
            warnings=[],
        )
