"""UTF-8 Extended M3U (.m3u8) crate exporter for Traktor Pro 4 and Rekordbox."""

from __future__ import annotations

from pathlib import Path

from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName


def _m3u8_escape(value: str) -> str:
    """Strip newlines, carriage returns, and commas from M3U8 #EXTINF text values."""
    return value.replace("\n", " ").replace("\r", " ").replace(",", " ").strip()


class PlaylistExporter:
    """Exports ordered tracklists to UTF-8 .m3u8 files with #EXTINF artist and title headers."""

    def __init__(self, repository: Repository) -> None:
        """Initialize PlaylistExporter with a Repository."""
        self.repo = repository

    def export_m3u8(self, file_ids: list[str], destination_path: Path) -> Path:
        """Write a UTF-8 .m3u8 playlist file for the given ordered file_ids."""
        lines: list[str] = ["#EXTM3U", "#EXTENC:UTF-8"]
        for file_id in file_ids:
            asset = self.repo.get_file_by_id(file_id)
            if asset is None:
                continue
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
            lines.append(f"#EXTINF:{dur_int},{artist} - {title}")
            lines.append(str(Path(asset.current_path).resolve()))

        destination_path.parent.mkdir(parents=True, exist_ok=True)
        destination_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return destination_path
