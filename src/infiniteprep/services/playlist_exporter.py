"""UTF-8 Extended M3U (.m3u8) crate exporter for Traktor Pro 4 and Rekordbox."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName
from infiniteprep.domain.models import FileAsset

# A line break inside a path line would split one entry into several playlist lines. Other C0
# control characters and DEL have no place in a file name and confuse player parsers.
DEL_CODE_POINT: Final[int] = 0x7F
UNSAFE_PATH_CHARACTERS: Final[frozenset[str]] = frozenset(
    [chr(code) for code in range(ord(" "))] + [chr(DEL_CODE_POINT)]
)
# O_NOFOLLOW refuses a symlink at the destination. Windows lacks the flag, so it is 0 there.
NO_FOLLOW_FLAG: Final[int] = getattr(os, "O_NOFOLLOW", 0)
# O_BINARY stops the Windows C runtime from translating line endings a second time.
BINARY_FLAG: Final[int] = getattr(os, "O_BINARY", 0)
# Owner read and write only. DJ software runs as the same user, and FAT media ignore the mode.
PLAYLIST_FILE_MODE: Final[int] = 0o600


def _m3u8_escape(value: str) -> str:
    """Strip newlines, carriage returns, and commas from M3U8 #EXTINF text values."""
    return value.replace("\n", " ").replace("\r", " ").replace(",", " ").strip()


def has_unsafe_path_characters(value: str) -> bool:
    """Return True when the text holds a C0 control character or DEL."""
    return not UNSAFE_PATH_CHARACTERS.isdisjoint(value)


def _safe_path_line(stored_path: str) -> str | None:
    """Return the resolved path line, or None when the stored or resolved path is unsafe."""
    # Check the stored text first, because resolving a path with NUL raises ValueError.
    if has_unsafe_path_characters(stored_path):
        return None
    # Resolution can follow a link to a different name, so check the emitted line too.
    path_line = str(Path(stored_path).resolve())
    return None if has_unsafe_path_characters(path_line) else path_line


def _write_without_following_links(destination: Path, content: str) -> None:
    """Write UTF-8 text, refusing a symlink at the destination where the OS supports it."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | NO_FOLLOW_FLAG | BINARY_FLAG
    descriptor = os.open(destination, flags, PLAYLIST_FILE_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        # The open mode applies only to a new file, so reset an overwritten playlist too.
        # Windows has no fchmod and ignores these bits.
        if hasattr(os, "fchmod"):
            os.fchmod(handle.fileno(), PLAYLIST_FILE_MODE)
        handle.write(content)


@dataclass
class PlaylistExportResult:
    """Written playlist path, written track count, and file IDs left out for unsafe paths."""

    path: Path
    written_count: int = 0
    skipped_file_ids: list[str] = field(default_factory=list)


class PlaylistExporter:
    """Exports ordered tracklists to UTF-8 .m3u8 files with #EXTINF artist and title headers."""

    def __init__(self, repository: Repository) -> None:
        """Initialize PlaylistExporter with a Repository."""
        self.repo = repository

    def export_m3u8(self, file_ids: list[str], destination_path: Path) -> Path:
        """Write a UTF-8 .m3u8 playlist file for the given ordered file_ids."""
        return self.export_m3u8_report(file_ids, destination_path).path

    def export_m3u8_report(
        self, file_ids: list[str], destination_path: Path
    ) -> PlaylistExportResult:
        """Write the playlist and report written tracks and tracks left out for unsafe paths."""
        lines: list[str] = ["#EXTM3U", "#EXTENC:UTF-8"]
        result = PlaylistExportResult(path=destination_path)
        for file_id in file_ids:
            asset = self.repo.get_file_by_id(file_id)
            if asset is None:
                continue
            path_line = _safe_path_line(asset.current_path)
            if path_line is None:
                result.skipped_file_ids.append(file_id)
                continue
            lines.append(self._extinf_line(asset))
            lines.append(path_line)
            result.written_count += 1

        destination_path.parent.mkdir(parents=True, exist_ok=True)
        _write_without_following_links(destination_path, "\n".join(lines) + "\n")
        return result

    def _extinf_line(self, asset: FileAsset) -> str:
        """Build the #EXTINF line from approved or tagged artist and title values."""
        std = asset.original_tags.get("standard_tags", {})
        approved = self.repo.get_approved_fields_map(asset.id)
        artist = _m3u8_escape(
            approved[FieldName.ARTIST.value].approved_value
            if FieldName.ARTIST.value in approved
            else std.get("artist", "Unknown Artist")
        )
        title = _m3u8_escape(
            approved[FieldName.TITLE.value].approved_value
            if FieldName.TITLE.value in approved
            else std.get("title", Path(asset.current_path).stem)
        )
        dur_int = round(asset.duration) if asset.duration is not None else -1
        return f"#EXTINF:{dur_int},{artist} - {title}"
