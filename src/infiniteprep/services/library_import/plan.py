"""The pending changes of one library import, held until one commit (D333).

The plan holds every change the import wants to make. Nothing reaches the
database until the commit. A cancel drops the plan, so the database never
holds a half-finished import.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field, replace

from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import GroupingBasis, ReviewStatus, SourceType, TaskStatus
from infiniteprep.domain.models import (
    ApprovedField,
    FieldProposal,
    FileAsset,
    ImportedLibrary,
    LibraryPathMapping,
    SourceGroup,
)
from infiniteprep.services.library_import.merge import FieldConflict, MergeOutcome, build_proposal
from infiniteprep.services.library_import.models import ImportedPendingValue, SourceLibrary
from infiniteprep.services.library_import.relocation import RelocationResult


@dataclass
class ImportPlan:
    """Every pending change of one library import."""

    library: SourceLibrary
    library_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source_group_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    assets: list[FileAsset] = field(default_factory=list)
    approved_fields: list[ApprovedField] = field(default_factory=list)
    field_proposals: list[FieldProposal] = field(default_factory=list)
    conflicts: list[FieldConflict] = field(default_factory=list)
    source_groups: list[SourceGroup] = field(default_factory=list)
    relocations: list[RelocationResult] = field(default_factory=list)
    unresolved_paths: list[str] = field(default_factory=list)
    observed_stored: dict[tuple[str, str], str | None] = field(default_factory=dict)
    library_record: ImportedLibrary | None = None
    path_mappings: list[LibraryPathMapping] = field(default_factory=list)
    pending_values: list[ImportedPendingValue] = field(default_factory=list)

    def apply_merge(self, outcome: MergeOutcome) -> None:
        """Take the approved fields, the proposals, and the conflicts of a merge."""
        self.approved_fields.extend(outcome.approved_fields)
        self.field_proposals.extend(outcome.proposals)
        self.conflicts.extend(outcome.conflicts)
        self.observed_stored.update(outcome.observed_stored)
        self.pending_values.extend(outcome.pending_values)

    def has_changes(self) -> bool:
        """Return True when the plan holds a change worth committing.

        Every row the commit can write counts. The source group, the library
        record, and the path mappings hold real state, so an import that
        found no readable track still reports a change.
        """
        return bool(
            self.assets
            or self.approved_fields
            or self.field_proposals
            or self.source_groups
            or self.library_record
            or self.path_mappings
        )

    def commit(
        self,
        repository: Repository,
        library: ImportedLibrary | None = None,
        path_mappings: list[LibraryPathMapping] | None = None,
    ) -> None:
        """Write every pending change in one transaction.

        The source library stays read-only. Nothing outside the app database
        changes. A failure rolls the whole commit back. The library record
        and the path mappings join the same transaction as the assets, so a
        failed import leaves nothing behind.

        Every approved field is read back before it is written. A field that
        changed after the merge becomes a conflict proposal instead of an
        overwrite, so a concurrent edit survives the import.
        """
        record = library if library is not None else self.library_record
        mappings = path_mappings if path_mappings is not None else self.path_mappings
        with repository.batch(immediate=True):
            for group in self.source_groups:
                repository.upsert_source_group(group)
            for asset in self.assets:
                repository.upsert_file_asset(asset)
            approved, proposals = self._revalidate(repository)
            for item in approved:
                repository.upsert_approved_field(item)
            for proposal in proposals:
                repository.upsert_field_proposal(proposal)
            if record is not None:
                repository.upsert_imported_library(record)
            for mapping in mappings:
                repository.upsert_path_mapping(mapping)
            for asset in self.assets:
                repository.update_file_status(
                    asset.id,
                    task_status=TaskStatus.IDLE,
                    review_status=ReviewStatus.PROPOSED,
                )

    def _revalidate(
        self, repository: Repository
    ) -> tuple[list[ApprovedField], list[FieldProposal]]:
        """Return the approved fields that still hold, and the proposals to write.

        The merge read the stored value of every field it compared. An
        approved field whose stored value changed after the merge keeps the
        change and becomes a proposal, so the commit never overwrites a value
        the DJ changed while the import waited for review. A conflict whose
        stored value changed keeps the newer value in the proposal, so the
        DJ reviews the value that is really stored.
        """
        approved: list[ApprovedField] = []
        proposals: list[FieldProposal] = []
        for item in self.approved_fields:
            key = (item.file_id, item.field_name)
            if key not in self.observed_stored:
                approved.append(item)
                continue
            current = _stored_value(repository, item.file_id, item.field_name)
            if current == self.observed_stored[key]:
                approved.append(item)
                continue
            proposals.append(
                build_proposal(
                    item.file_id,
                    item.field_name,
                    item.approved_value,
                    self.library,
                )
            )
        for proposal in self.field_proposals:
            proposals.append(self._refresh_proposal(repository, proposal))
        return approved, proposals

    def _refresh_proposal(self, repository: Repository, proposal: FieldProposal) -> FieldProposal:
        """Return a proposal that names the value the database holds now.

        The merge read the stored value of every field it compared. A conflict
        whose stored value changed after the merge keeps the newer value, so
        the DJ reviews the value that is really stored.
        """
        key = (proposal.file_id, proposal.field_name)
        if key not in self.observed_stored:
            return proposal
        current = _stored_value(repository, proposal.file_id, proposal.field_name)
        if current == self.observed_stored[key]:
            return proposal
        evidence = dict(proposal.evidence)
        evidence["stored_value_at_merge"] = self.observed_stored[key] or ""
        evidence["stored_value_at_commit"] = current or ""
        return replace(proposal, evidence=evidence)


def _stored_value(repository: Repository, file_id: str, field_name: str) -> str | None:
    """Return the stored value of a field, or None when the field is absent."""
    stored = repository.get_approved_field(file_id, field_name)
    return stored.approved_value if stored is not None else None


def build_source_group(library: SourceLibrary, folder_path: str, group_id: str) -> SourceGroup:
    """Return the source group that holds the tracks of one imported library."""
    return SourceGroup(
        id=group_id,
        user_label=library.source_format,
        grouping_basis=GroupingBasis.USER_ASSIGNED,
        original_folder=folder_path,
        source_type=SourceType.DIGITAL,
        notes=f"Imported from {library.source_format} at {library.source_path}",
    )
