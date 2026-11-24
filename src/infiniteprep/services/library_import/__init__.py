"""Shared framework for importing a DJ library (D333, D349).

The framework holds the parts every format reader needs. A reader returns the
neutral model. The merge holds the pending changes. The locator finds the
installed libraries. The path mapping moves a source path onto this machine.
The relocation search finds a file that moved.
"""

from infiniteprep.services.library_import.format_detection import detect_format
from infiniteprep.services.library_import.importer import (
    BUILTIN_IMPORTERS,
    ImporterRegistry,
    LibraryImporter,
    NoImporterError,
    import_library,
    production_registry,
)
from infiniteprep.services.library_import.locations import (
    ACCESS_DATE,
    FormatId,
    KnownLocation,
    Platform,
    locations_for,
)
from infiniteprep.services.library_import.locator import (
    LocatedLibrary,
    find_installed_libraries,
)
from infiniteprep.services.library_import.merge import (
    FieldConflict,
    MergeContext,
    MergedTrack,
    MergeOutcome,
    merge_library,
)
from infiniteprep.services.library_import.models import (
    HOT_CUE_AUTO_LOAD_FIELD,
    ImportedPendingValue,
    ImportedPlaylist,
    ImportedTrack,
    ImportedValue,
    SourceCollectionKind,
    SourceLibrary,
)
from infiniteprep.services.library_import.path_mapping import (
    PathMapping,
    PathMappingError,
    PathStyle,
    apply_mapping,
    detect_path_style,
    normalize_path,
    reverse_mapping,
)
from infiniteprep.services.library_import.plan import ImportPlan, build_source_group
from infiniteprep.services.library_import.relocation import (
    MissingReference,
    RelocationResult,
    find_relocated_files,
)
from infiniteprep.services.library_import.service import (
    LibraryImportRequest,
    LibraryImportResult,
    LibraryImportService,
)
from infiniteprep.services.library_import.track_files import (
    TrackFileFacts,
    read_track_file_facts,
)

__all__ = [
    "ACCESS_DATE",
    "BUILTIN_IMPORTERS",
    "HOT_CUE_AUTO_LOAD_FIELD",
    "FieldConflict",
    "FormatId",
    "ImportPlan",
    "ImportedPendingValue",
    "ImportedPlaylist",
    "ImportedTrack",
    "ImportedValue",
    "ImporterRegistry",
    "KnownLocation",
    "LibraryImportRequest",
    "LibraryImportResult",
    "LibraryImportService",
    "LibraryImporter",
    "LocatedLibrary",
    "MergeContext",
    "MergeOutcome",
    "MergedTrack",
    "MissingReference",
    "NoImporterError",
    "PathMapping",
    "PathMappingError",
    "PathStyle",
    "Platform",
    "RelocationResult",
    "SourceCollectionKind",
    "SourceLibrary",
    "TrackFileFacts",
    "apply_mapping",
    "build_source_group",
    "detect_format",
    "detect_path_style",
    "find_installed_libraries",
    "find_relocated_files",
    "import_library",
    "locations_for",
    "merge_library",
    "normalize_path",
    "production_registry",
    "read_track_file_facts",
    "reverse_mapping",
]
