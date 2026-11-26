"""Behavioral tests for the format detection of a hand-picked library file (D333).

The DJ names a file by hand. The framework reads its header to name the
format, so the registry picks the reader that goes with it.
"""

from __future__ import annotations

from pathlib import Path

from infiniteprep.services.library_import.format_detection import detect_format
from infiniteprep.services.library_import.locations import FormatId


def test_file_name_detects_traktor(tmp_path: Path) -> None:
    """A collection file names the Traktor format."""
    path = tmp_path / "collection.nml"
    path.write_bytes(b'<HEAD PROGRAM="Traktor"/>')

    assert detect_format(path) is FormatId.TRAKTOR


def test_file_name_detects_serato(tmp_path: Path) -> None:
    """A database file names the Serato format."""
    path = tmp_path / "database V2"
    path.write_bytes(b"serato bytes")

    assert detect_format(path) is FormatId.SERATO


def test_file_name_detects_mixxx(tmp_path: Path) -> None:
    """A Mixxx database names the Mixxx format."""
    path = tmp_path / "mixxxdb.sqlite"
    path.write_bytes(b"SQLite format 3\x00")

    assert detect_format(path) is FormatId.MIXXX


def test_file_name_detects_rekordbox(tmp_path: Path) -> None:
    """A master database names the rekordbox format."""
    path = tmp_path / "master.db"
    path.write_bytes(b"rekordbox bytes")

    assert detect_format(path) is FormatId.REKORDBOX


def test_file_name_detects_rekordbox_usb(tmp_path: Path) -> None:
    """A device library names the rekordbox USB export format."""
    path = tmp_path / "export.pdb"
    path.write_bytes(b"device library bytes")

    assert detect_format(path) is FormatId.REKORDBOX_USB


def test_header_detects_rekordbox(tmp_path: Path) -> None:
    """An exported XML file names the rekordbox format."""
    path = tmp_path / "export.xml"
    path.write_bytes(b'<DJ_PLAYLISTS Version="1.0.0">')

    assert detect_format(path) is FormatId.REKORDBOX


def test_header_detects_traktor(tmp_path: Path) -> None:
    """A file that starts with the Traktor header names the Traktor format."""
    path = tmp_path / "library.txt"
    path.write_bytes(b'<HEAD PROGRAM="Traktor" VERSION="3.5.1"/>')

    assert detect_format(path) is FormatId.TRAKTOR


def test_unknown_file_keeps_manual_format(tmp_path: Path) -> None:
    """A file that names no format keeps the manual format."""
    path = tmp_path / "notes.bin"
    path.write_bytes(b"unknown bytes")

    assert detect_format(path) is FormatId.MANUAL


def test_unreadable_file_keeps_manual_format(tmp_path: Path) -> None:
    """A file that cannot be read keeps the manual format."""
    path = tmp_path / "missing.nml"

    assert detect_format(path) is FormatId.MANUAL
