"""The one User-Agent every outbound HTTP request sends (D221)."""

from __future__ import annotations

from typing import Final

from infiniteprep import __version__

PROJECT_URL: Final[str] = "https://github.com/abuzucom/infiniteprep"
# MusicBrainz and Discogs ask for an app name, a version, and a contact URL. No email.
USER_AGENT: Final[str] = f"InfinitePrep/{__version__} (+{PROJECT_URL})"
