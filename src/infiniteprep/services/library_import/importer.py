"""The importer interface every DJ library reader implements (D333).

One interface serves the desktop dialog and the headless conversion pass of
EXPORT-7. A reader returns the neutral model and never touches the app
database, so the same reader runs with or without the user interface.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Final, Protocol, runtime_checkable

from infiniteprep.services.library_import.models import SourceLibrary


@runtime_checkable
class LibraryImporter(Protocol):
    """Reads one DJ library format into the neutral model."""

    format_id: str

    def read_library(self, source_path: Path) -> SourceLibrary:
        """Return the library held in the file at source_path."""


class ImporterRegistry:
    """Holds the importer of every supported source format.

    A format registers one importer. The registry hands the importer to the
    caller that reads a library, so the user interface and the headless
    conversion pass read the same library the same way.
    """

    def __init__(self) -> None:
        """Create an empty registry."""
        self._importers: dict[str, LibraryImporter] = {}

    def register(self, importer: LibraryImporter) -> None:
        """Add the importer of one format, replacing any earlier importer."""
        self._importers[importer.format_id] = importer

    def get(self, format_id: str) -> LibraryImporter | None:
        """Return the importer of one format, or None when no format registered it."""
        return self._importers.get(format_id)

    def format_ids(self) -> tuple[str, ...]:
        """Return the registered format identifiers in name order."""
        return tuple(sorted(self._importers))


class NoImporterError(ValueError):
    """Raised when no registered importer reads a source format."""


# The issue that adds the reader of each format. The framework carries no
# reader of its own, so the error names the issue that brings the reader.
FORMAT_READER_ISSUES: Final[dict[str, tuple[str, ...]]] = {
    "traktor": ("#145",),
    "serato": ("#146",),
    "mixxx": ("#257",),
    "rekordbox": ("#144", "#334"),
    "rekordbox_usb": ("#147",),
}


# The importers the app reads source libraries through. Each format reader
# issue adds its importer here, so the desktop dialog and the headless
# conversion pass of EXPORT-7 read every format the same way.
BUILTIN_IMPORTERS: Final[tuple[LibraryImporter, ...]] = ()


def production_registry(
    importers: Iterable[LibraryImporter] | None = None,
) -> ImporterRegistry:
    """Return the registry the app reads every source library through.

    The registry holds the importer of each format the app reads. The desktop
    dialog and the headless conversion pass both use it, so a library reads
    the same way in either flow.
    """
    registry = ImporterRegistry()
    for importer in BUILTIN_IMPORTERS if importers is None else importers:
        registry.register(importer)
    return registry


def import_library(source_path: Path, registry: ImporterRegistry, format_id: str) -> SourceLibrary:
    """Read the library at source_path through the importer of the format.

    Raises NoImporterError when no importer registered the format.
    """
    importer = registry.get(format_id)
    if importer is None:
        raise NoImporterError(_missing_reader_message(format_id, registry))
    return importer.read_library(source_path)


def _missing_reader_message(format_id: str, registry: ImporterRegistry) -> str:
    """Return the message that names the format and the issue that adds its reader."""
    registered = ", ".join(registry.format_ids())
    held = f"The registry holds {registered}." if registered else "The registry is empty."
    issues = FORMAT_READER_ISSUES.get(format_id)
    source = f" The reader arrives with {_issue_list(issues)}." if issues else ""
    return f"No importer reads the {format_id} format. {held}{source}"


def _issue_list(issues: tuple[str, ...]) -> str:
    """Return the issue numbers that add the reader of one format."""
    if len(issues) == 1:
        return issues[0]
    return " and ".join(issues)
