"""Detect the source format of a library file the DJ named by hand (D333).

A hand-picked file carries no folder name, so the framework reads the file
header to name the format. The reader that goes with the format then reads
the file. The file stays read-only.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Final

from infiniteprep.services.library_import.locations import FormatId

logger = logging.getLogger(__name__)

# The number of bytes the header check reads.
HEADER_READ_BYTES: Final[int] = 4_096

# The file names that name their own format.
FILE_NAME_FORMATS: Final[dict[str, FormatId]] = {
    "collection.nml": FormatId.TRAKTOR,
    "database v2": FormatId.SERATO,
    "mixxxdb.sqlite": FormatId.MIXXX,
    "master.db": FormatId.REKORDBOX,
    "rekordbox.xml": FormatId.REKORDBOX,
    "export.pdb": FormatId.REKORDBOX_USB,
    "exportlibrary.db": FormatId.REKORDBOX_USB,
}

# The markers that name the format in the header of a file.
HEADER_MARKERS: Final[tuple[tuple[bytes, FormatId], ...]] = (
    (b"<HEAD", FormatId.TRAKTOR),
    (b"<?xml", FormatId.TRAKTOR),
    (b"<DJ_PLAYLISTS", FormatId.REKORDBOX),
    (b"SQLite format 3", FormatId.MIXXX),
)


def detect_format(file_path: Path) -> FormatId:
    """Return the source format of a file, or the manual format when unknown.

    The file name names the format when it names one. The header names it
    otherwise. A file that names neither keeps the manual format.
    """
    named = FILE_NAME_FORMATS.get(file_path.name.casefold())
    if named is not None:
        return named
    header = _read_header(file_path)
    if header is None:
        return FormatId.MANUAL
    for marker, format_id in HEADER_MARKERS:
        if marker in header:
            return format_id
    return FormatId.MANUAL


def _read_header(file_path: Path) -> bytes | None:
    """Return the first bytes of a file, or None when it cannot be read."""
    try:
        with file_path.open("rb") as handle:
            return handle.read(HEADER_READ_BYTES)
    except OSError:
        logger.warning("Library import could not read the header of a chosen file.")
        return None
