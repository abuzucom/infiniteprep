"""UTF-8 Extended M3U (.m3u8) crate exporter for Traktor Pro 4 and Rekordbox."""

from __future__ import annotations

import errno
import os
import uuid
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
# Parsers that split on every Unicode line boundary also break at NEL, U+2028, and U+2029.
NEXT_LINE_CODE_POINT: Final[int] = 0x85
LINE_SEPARATOR_CODE_POINT: Final[int] = 0x2028
PARAGRAPH_SEPARATOR_CODE_POINT: Final[int] = 0x2029
# The comma ends the duration field of #EXTINF.
EXTINF_REPLACED_CHARACTERS: Final[frozenset[str]] = UNSAFE_PATH_CHARACTERS | frozenset(
    [
        chr(NEXT_LINE_CODE_POINT),
        chr(LINE_SEPARATOR_CODE_POINT),
        chr(PARAGRAPH_SEPARATOR_CODE_POINT),
        ",",
    ]
)
EXTINF_TRANSLATION: Final[dict[int, str]] = str.maketrans(
    dict.fromkeys(EXTINF_REPLACED_CHARACTERS, " ")
)


def _m3u8_escape(value: str) -> str:
    """Replace line separators, control characters, and commas in #EXTINF text with spaces."""
    return value.translate(EXTINF_TRANSLATION).strip()


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


def _relative_path_line(path_line: str, playlist_dir: Path) -> str:
    """Return the track path relative to the playlist folder, or absolute across drives."""
    try:
        relative = os.path.relpath(path_line, playlist_dir).replace(os.sep, "/")
    except ValueError:
        # Windows cannot express a path on another drive relative to the playlist.
        return path_line
    # A line that starts with "#" reads as a comment or directive in M3U8.
    return f"./{relative}" if relative.startswith("#") else relative


def _write_without_following_links(destination: Path, content: str) -> None:
    """Write UTF-8 text to a new file beside destination, then swap it into place.

    A failed or interrupted write leaves the previous playlist whole. The export replaces
    the playlist the person chose, and the save dialog confirms that overwrite. A symlink
    found at the destination when the export starts raises OSError. The final swap
    replaces the directory entry itself. It never writes through a link to the link
    target, even when a link appears at the destination during the export.
    """
    if destination.is_symlink():
        raise OSError(
            errno.ELOOP,
            "The playlist destination is a symbolic link; choose a regular file name",
            str(destination),
        )
    # A fixed-length name stays within the file name limit for any playlist name.
    staged = destination.with_name(f".playlist-{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | NO_FOLLOW_FLAG | BINARY_FLAG
    descriptor = os.open(staged, flags, PLAYLIST_FILE_MODE)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            # The umask can strip bits from the open mode, so set the exact mode.
            # Windows has no fchmod and ignores these bits.
            if hasattr(os, "fchmod"):
                os.fchmod(handle.fileno(), PLAYLIST_FILE_MODE)
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staged, destination)
    finally:
        staged.unlink(missing_ok=True)


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

    def export_m3u8(
        self, file_ids: list[str], destination_path: Path, *, relative_paths: bool = False
    ) -> Path:
        """Write a UTF-8 .m3u8 playlist file for the given ordered file_ids."""
        return self.export_m3u8_report(
            file_ids, destination_path, relative_paths=relative_paths
        ).path

    def export_m3u8_report(
        self, file_ids: list[str], destination_path: Path, *, relative_paths: bool = False
    ) -> PlaylistExportResult:
        """Write the playlist and report written tracks and tracks left out for unsafe paths.

        With relative_paths, each track line is relative to the playlist folder, so the crate
        keeps working when the whole folder moves to another drive or computer.
        """
        lines: list[str] = ["#EXTM3U", "#EXTENC:UTF-8"]
        result = PlaylistExportResult(path=destination_path)
        playlist_dir = destination_path.parent.resolve()
        for file_id in file_ids:
            asset = self.repo.get_file_by_id(file_id)
            if asset is None:
                continue
            path_line = _safe_path_line(asset.current_path)
            if path_line is None:
                result.skipped_file_ids.append(file_id)
                continue
            if relative_paths:
                path_line = _relative_path_line(path_line, playlist_dir)
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
