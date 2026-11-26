"""Behavioral tests for the importer interface that EXPORT-7 calls (D333)."""

from __future__ import annotations

from pathlib import Path

import pytest

from infiniteprep.services.library_import.importer import (
    BUILTIN_IMPORTERS,
    ImporterRegistry,
    LibraryImporter,
    NoImporterError,
    import_library,
    production_registry,
)
from infiniteprep.services.library_import.models import (
    ImportedTrack,
    ImportedValue,
    SourceLibrary,
)


class FakeImporter:
    """Read one fake library and record the paths it was asked for."""

    def __init__(self, format_id: str = "fake") -> None:
        self.format_id = format_id
        self.read_paths: list[Path] = []

    def read_library(self, source_path: Path) -> SourceLibrary:
        """Return a fake library and record the path that was read."""
        self.read_paths.append(source_path)
        return SourceLibrary(
            source_format=self.format_id,
            source_path=str(source_path),
            version="1.0",
            tracks=(
                ImportedTrack(
                    source_path="/music/track.wav",
                    values=(ImportedValue("title", "Song"),),
                ),
            ),
        )


def test_registry_holds_importer_per_format() -> None:
    """Registering two formats keeps both, and the identifiers come back in name order."""
    registry = ImporterRegistry()
    registry.register(FakeImporter("traktor"))
    registry.register(FakeImporter("serato"))

    assert registry.format_ids() == ("serato", "traktor")
    assert registry.get("traktor") is not None
    assert registry.get("mixxx") is None


def test_registry_replaces_format_importer() -> None:
    """Registering a format again replaces the earlier importer of that format."""
    registry = ImporterRegistry()
    first = FakeImporter("traktor")
    second = FakeImporter("traktor")
    registry.register(first)
    registry.register(second)

    assert registry.get("traktor") is second
    assert registry.format_ids() == ("traktor",)


def test_import_library_uses_registered_importer(tmp_path: Path) -> None:
    """The registry hands the library to the importer of the named format."""
    importer = FakeImporter("traktor")
    registry = ImporterRegistry()
    registry.register(importer)
    source = tmp_path / "collection.nml"

    library = import_library(source, registry, "traktor")

    assert importer.read_paths == [source]
    assert library.source_format == "traktor"
    assert library.source_path == str(source)
    assert library.tracks[0].values[0].value == "Song"


def test_import_library_rejects_unregistered_format(tmp_path: Path) -> None:
    """A format with no importer raises rather than reading the file."""
    registry = ImporterRegistry()
    source = tmp_path / "collection.nml"

    with pytest.raises(NoImporterError, match="mixxx"):
        import_library(source, registry, "mixxx")


def test_missing_importer_error_names_formats(tmp_path: Path) -> None:
    """The error names the formats the registry can read."""
    registry = ImporterRegistry()
    registry.register(FakeImporter("traktor"))
    source = tmp_path / "collection.nml"

    with pytest.raises(NoImporterError, match="traktor"):
        import_library(source, registry, "mixxx")


def test_production_registry_has_default_importers() -> None:
    """The registry the app reads libraries through holds the built-in importers."""
    registry = production_registry()

    assert registry.format_ids() == tuple(
        sorted(importer.format_id for importer in BUILTIN_IMPORTERS)
    )


def test_production_registry_takes_given_importers() -> None:
    """A caller can build a registry that holds a chosen set of importers."""
    registry = production_registry([FakeImporter("traktor"), FakeImporter("serato")])

    assert registry.format_ids() == ("serato", "traktor")


def test_production_registry_runs_default_importer(tmp_path: Path) -> None:
    """Every built-in importer satisfies the interface the framework calls."""
    registry = production_registry()

    for format_id in registry.format_ids():
        importer = registry.get(format_id)
        assert importer is not None
        assert isinstance(importer, LibraryImporter)


def test_fake_importer_satisfies_protocol() -> None:
    """Any reader with the format identifier and the read method serves as an importer."""
    assert isinstance(FakeImporter(), LibraryImporter)
