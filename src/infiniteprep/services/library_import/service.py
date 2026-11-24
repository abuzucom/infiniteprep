"""The service that imports a DJ library into the app (D333).

The service runs the whole flow. It finds the library, reads it through the
registered importer, maps the paths onto this machine, merges the values, and
commits everything in one transaction. EXPORT-7 calls the same service without
the user interface.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path

from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import TaskStatus
from infiniteprep.domain.models import (
    FileAsset,
    ImportedLibrary,
    LibraryPathMapping,
    utc_now_iso,
)
from infiniteprep.services.library_import.filesystem import is_link_or_junction, open_inside_root
from infiniteprep.services.library_import.format_detection import detect_format
from infiniteprep.services.library_import.importer import (
    ImporterRegistry,
    import_library,
    production_registry,
)
from infiniteprep.services.library_import.locations import FormatId
from infiniteprep.services.library_import.merge import (
    FieldConflict,
    MergeContext,
    MergeOutcome,
    merge_library,
)
from infiniteprep.services.library_import.models import (
    ImportedPendingValue,
    ImportedTrack,
    SourceLibrary,
)
from infiniteprep.services.library_import.path_mapping import (
    PathMapping,
    PathMappingError,
    apply_mapping,
    resolve_inside_root,
)
from infiniteprep.services.library_import.plan import ImportPlan, build_source_group
from infiniteprep.services.library_import.relocation import (
    MissingReference,
    RelocationResult,
    find_relocated_files,
)
from infiniteprep.services.library_import.track_files import (
    TrackFileFacts,
    read_opened_file_facts,
)


@dataclass
class LibraryImportRequest:
    """What one library import asks for.

    source_path is the library file to read. mappings are the prefix mappings
    the DJ defined. root is the folder every imported file must sit inside.
    relocation_root is the folder the search for a moved file walks.
    """

    source_path: Path
    format_id: str
    root: Path
    mappings: tuple[PathMapping, ...] = ()
    relocation_root: Path | None = None


@dataclass
class LibraryImportResult:
    """The outcome of one library import."""

    library: ImportedLibrary
    plan: ImportPlan
    conflicts: list[FieldConflict] = field(default_factory=list)
    unresolved_paths: list[str] = field(default_factory=list)
    pending_fields: list[str] = field(default_factory=list)
    relocations: list[RelocationResult] = field(default_factory=list)
    path_mappings: list[LibraryPathMapping] = field(default_factory=list)
    pending_values: list[ImportedPendingValue] = field(default_factory=list)
    error: str = ""

    @property
    def has_changes(self) -> bool:
        """Return True when the import holds a change worth committing."""
        return self.plan.has_changes()


class LibraryImportService:
    """Read a DJ library and hold the pending changes of the import."""

    def __init__(self, repository: Repository, registry: ImporterRegistry | None = None) -> None:
        """Create the service on a repository and a registry of importers.

        The registry holds the importer of every format the app reads. The
        desktop dialog and the headless conversion pass of EXPORT-7 both build
        the service this way, so a library reads the same way in either flow.
        """
        self.repo = repository
        self.registry = production_registry() if registry is None else registry

    def read_library(self, request: LibraryImportRequest) -> SourceLibrary:
        """Read the library file of the request through the registered importer.

        A request that names the manual format gets the format of the file,
        so the registry picks the reader that goes with it.
        """
        format_id = request.format_id
        if format_id == FormatId.MANUAL:
            format_id = detect_format(request.source_path)
        return import_library(request.source_path, self.registry, format_id)

    def build_plan(self, request: LibraryImportRequest) -> LibraryImportResult:
        """Read the library and hold every pending change of the import.

        Nothing reaches the database. The caller commits the plan when the DJ
        accepts it, and drops it when the DJ cancels.

        A root that cannot hold an import stops the run before the plan
        starts. The result names the problem and holds no change.
        """
        problem = _root_problem(request.root)
        if problem:
            return _failed_result(request, problem)
        library = self.read_library(request)
        plan = ImportPlan(library=library)
        folder = str(request.source_path.parent)
        plan.source_groups.append(build_source_group(library, folder, plan.source_group_id))
        plan.library_record = _library_record(library, plan.library_id)
        plan.path_mappings = _path_mappings(request, plan.library_id)
        record = plan.library_record
        mappings = plan.path_mappings
        stored_values, file_ids = self._resolve_tracks(request, library, plan)
        outcome = merge_library(
            MergeContext(
                library=library,
                stored_values=stored_values,
                file_ids=file_ids,
            )
        )
        plan.apply_merge(outcome)
        return LibraryImportResult(
            library=record,
            plan=plan,
            conflicts=list(outcome.conflicts),
            unresolved_paths=list(plan.unresolved_paths),
            pending_fields=_pending_fields(outcome),
            relocations=list(plan.relocations),
            path_mappings=list(mappings),
            pending_values=list(plan.pending_values),
        )

    def commit(self, result: LibraryImportResult) -> None:
        """Write every pending change of the import in one transaction.

        The library record and the path mappings join the same transaction as
        the assets, the approved fields, and the proposals. A failure rolls
        the whole import back.
        """
        result.plan.commit(self.repo)

    def _resolve_tracks(
        self,
        request: LibraryImportRequest,
        library: SourceLibrary,
        plan: ImportPlan,
    ) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
        """Return the stored values and the file identifiers of every track.

        A track whose audio file cannot be read stays unresolved, unless the
        search finds the file at another path. The plan holds no row for an
        unresolved track. A track the library already holds keeps its row, so
        a second import merges into the same track.
        """
        stored_values: dict[str, dict[str, str]] = {}
        file_ids: dict[str, str] = {}
        for track in library.tracks:
            resolved = self._resolve_track(request, plan, track)
            if resolved is None:
                plan.unresolved_paths.append(track.source_path)
                continue
            asset = resolved[1]
            file_ids[track.source_path] = asset.id
            stored_values[track.source_path] = {
                name: field.approved_value
                for name, field in self.repo.get_approved_fields_map(asset.id).items()
            }
            if asset.id not in {item.id for item in plan.assets}:
                plan.assets.append(asset)
        return stored_values, file_ids

    def _resolve_track(
        self,
        request: LibraryImportRequest,
        plan: ImportPlan,
        track: ImportedTrack,
    ) -> tuple[Path, FileAsset] | None:
        """Return the path and the library row of one track, or None when unresolved.

        A track the library already holds keeps its row, so a second import
        merges into the same track. A track whose file moved gets a row at the
        new path.
        """
        mapped = self._mapped_path(request, track.source_path)
        if mapped is None:
            return None
        existing = self.repo.get_file_by_path(str(mapped))
        if existing is None:
            return self._new_track(request, plan, mapped)
        if mapped.is_file():
            return mapped, existing
        moved = self._relocated_path(request, plan, existing, mapped)
        if moved is None:
            return None
        return self._new_track(request, plan, moved, existing)

    def _new_track(
        self,
        request: LibraryImportRequest,
        plan: ImportPlan,
        path: Path,
        existing: FileAsset | None = None,
    ) -> tuple[Path, FileAsset] | None:
        """Return the path and a fresh library row for one audio file.

        The path is checked against the root again right before the read, so
        a folder swapped for a link after the first check cannot carry the
        read out of the root.

        A track that moved keeps the row the app already holds, so the import
        leaves no second row for the same track.
        """
        confirmed = resolve_inside_root(path, request.root)
        if confirmed is None:
            return None
        opened = open_inside_root(path, request.root)
        if opened is None:
            return None
        with opened:
            facts = read_opened_file_facts(opened)
            if facts is None:
                return None
        asset = _file_asset(plan.library_id, opened.path, facts, plan.source_group_id)
        if existing is not None:
            asset.id = existing.id
            asset.original_path = existing.original_path
            asset.import_batch_id = existing.import_batch_id
            asset.source_group_id = existing.source_group_id
        return opened.path, asset

    def _mapped_path(self, request: LibraryImportRequest, source_path: str) -> Path | None:
        """Return the path of a track on this computer, or None when it escapes.

        Every path, mapped or not, must resolve inside the root the DJ chose.
        A source library names untrusted paths, so a path outside the root is
        never read.
        """
        mapped = source_path
        for mapping in request.mappings:
            try:
                mapped = apply_mapping(mapped, mapping)
            except PathMappingError:
                return None
        candidate = Path(mapped)
        resolved = resolve_inside_root(candidate, request.root)
        if resolved is None:
            return None
        return resolved

    def _relocated_path(
        self,
        request: LibraryImportRequest,
        plan: ImportPlan,
        existing: FileAsset | None,
        mapped: Path,
    ) -> Path | None:
        """Return the path a moved file now sits at, or None when none was found.

        The search needs the size and the hash of the file. The app holds both
        when the track already sits in the library under that path. The found
        file must sit inside the root the DJ chose.
        """
        if request.relocation_root is None or existing is None:
            return None
        reference = MissingReference(
            path=str(mapped),
            file_size=existing.file_size,
            content_hash=existing.content_hash,
        )
        results = find_relocated_files([reference], request.relocation_root)
        for result in results:
            if not result.confirmed or result.found_path is None:
                continue
            resolved = resolve_inside_root(result.found_path, request.root)
            if resolved is None:
                continue
            plan.relocations.append(result)
            return resolved
        return None


def _file_asset(
    library_id: str, path: Path, facts: TrackFileFacts, source_group_id: str
) -> FileAsset:
    """Return the library row of one imported track.

    The row names the source group of the import, so the imported tracks stay
    with the group that holds them.
    """
    return FileAsset(
        id=str(uuid.uuid4()),
        current_path=str(path),
        original_path=str(path),
        file_size=facts.file_size,
        content_hash=facts.content_hash,
        format=facts.audio_format,
        duration=facts.duration,
        original_tags=facts.original_tags,
        import_batch_id=library_id,
        source_group_id=source_group_id,
        task_status=TaskStatus.IDLE,
    )


def _library_record(library: SourceLibrary, library_id: str) -> ImportedLibrary:
    """Return the stored record of one imported library.

    The record shares the identifier of the plan, so every imported asset
    names the library it came from.
    """
    return ImportedLibrary(
        id=library_id,
        format_id=library.source_format,
        source_path=library.source_path,
        detected_version=library.version or "",
        imported_at=utc_now_iso(),
    )


def _path_mappings(request: LibraryImportRequest, library_id: str) -> list[LibraryPathMapping]:
    """Return the stored records of the prefix mappings of one import."""
    return [
        LibraryPathMapping(
            id=str(uuid.uuid4()),
            library_id=library_id,
            source_prefix=mapping.source_prefix,
            target_prefix=mapping.target_prefix,
            root_path=str(request.root),
        )
        for mapping in request.mappings
    ]


def _pending_fields(outcome: MergeOutcome) -> list[str]:
    """Return the names of the values that wait for their storage."""
    names: list[str] = []
    for track in outcome.tracks:
        names.extend(track.pending_fields)
    return names


def _root_problem(root: Path) -> str:
    """Return the reason the root cannot hold an import, or an empty text.

    The root must be an absolute folder that exists. A link never holds an
    import, because the files it names can sit outside the folder the DJ
    picked.
    """
    if not str(root).strip():
        return "The import root is empty. Choose the folder the music lives in."
    if not root.is_absolute():
        return f"The import root {root} is not an absolute path."
    if is_link_or_junction(root):
        return f"The import root {root} is a link."
    if not root.exists():
        return f"The import root {root} does not exist."
    if not root.is_dir():
        return f"The import root {root} is not a folder."
    return ""


def _failed_result(request: LibraryImportRequest, problem: str) -> LibraryImportResult:
    """Return the result of an import that cannot run.

    The result holds no change, so a caller cannot commit an import
    configuration that resolves no track.
    """
    source = SourceLibrary(
        source_format=request.format_id,
        source_path=str(request.source_path),
        version="",
    )
    plan = ImportPlan(library=source)
    return LibraryImportResult(
        library=_library_record(source, plan.library_id),
        plan=plan,
        error=problem,
    )


def find_moved_files(missing: list[MissingReference], root: Path) -> list[RelocationResult]:
    """Return the outcome of the search for every missing file."""
    return find_relocated_files(missing, root)
