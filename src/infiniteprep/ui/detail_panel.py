"""Detail panel for inspecting file metadata, field proposals, locks, and warnings."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Final

from PySide6.QtWidgets import (
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import (
    FieldName,
    KeyDisplayNotation,
    format_canonical_key,
    parse_key_string,
)
from infiniteprep.domain.models import ApprovedField, FieldProposal, FileAsset
from infiniteprep.services.review_service import MAX_MANUAL_FIELD_LENGTH, ReviewService
from infiniteprep.ui.audio_player import AudioPreviewWidget

DISPLAY_FIELDS: Final[tuple[FieldName, ...]] = (
    FieldName.ARTIST,
    FieldName.TITLE,
    FieldName.MIX_NAME,
    FieldName.REMIXER,
    FieldName.RELEASE_TITLE,
    FieldName.LABEL,
    FieldName.CATALOG_NUMBER,
    FieldName.VINYL_POSITION,
    FieldName.YEAR,
    FieldName.GENRE,
    FieldName.BPM,
    FieldName.KEY,
)
FIELDS_TABLE_COLUMN_COUNT: Final[int] = 6
FIELDS_TABLE_STRETCH: Final[int] = 3
CANDIDATE_BOX_STRETCH: Final[int] = 1
COL_FIELD_NAME: Final[int] = 0
COL_EXISTING_TAG: Final[int] = 1
COL_TOP_PROPOSAL: Final[int] = 2
COL_PROPOSAL_SOURCE: Final[int] = 3
COL_APPROVED_EDIT: Final[int] = 4
COL_ACTIONS: Final[int] = 5
ACTION_ROW_MARGIN_PX: Final[int] = 2


class DetailPanel(QWidget):
    """Side-by-side inspector for current tags, proposals, approved values, and audio preview."""

    def __init__(
        self,
        repository: Repository,
        review_service: ReviewService,
        on_change_callback: Callable[[], None] | None = None,
        parent: QWidget | None = None,
        *,
        enable_audio_backend: bool = False,
    ) -> None:
        """Initialize DetailPanel with Repository, ReviewService, and AudioPreviewWidget.

        enable_audio_backend creates a real QMediaPlayer for the preview. The desktop app
        passes True. Tests keep the silent default, so no audio device is needed.
        """
        super().__init__(parent)
        self.repo = repository
        self.review_service = review_service
        self.on_change_callback = on_change_callback
        self.current_file_id: str | None = None
        self.key_notation: KeyDisplayNotation = KeyDisplayNotation.CAMELOT

        self.header_label = QLabel("Select a track to inspect metadata and proposals.", self)
        self.warnings_label = QLabel("", self)
        self.warnings_label.setStyleSheet("color: #d97706; font-weight: bold;")
        self.audio_player = AudioPreviewWidget(self, enable_multimedia_backend=enable_audio_backend)
        self.fields_table = QTableWidget(0, FIELDS_TABLE_COLUMN_COUNT, self)
        self.candidates_list = QListWidget(self)

        self._build_layout()

    def _build_layout(self) -> None:
        """Assemble the vertical layout of DetailPanel."""
        root = QVBoxLayout(self)
        root.addWidget(self.header_label)
        root.addWidget(self.warnings_label)
        root.addWidget(self.audio_player)

        self.fields_table.setHorizontalHeaderLabels(
            [
                "Field",
                "Existing Tag",
                "Top Proposal",
                "Source & Score",
                "Approved / Edit",
                "Actions",
            ]
        )
        header = self.fields_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        root.addWidget(self.fields_table, stretch=FIELDS_TABLE_STRETCH)

        cand_box = QGroupBox("Candidate Releases & Analysis Evidence", self)
        cand_layout = QVBoxLayout(cand_box)
        cand_layout.addWidget(self.candidates_list)
        root.addWidget(cand_box, stretch=CANDIDATE_BOX_STRETCH)

    def set_key_notation(self, notation: KeyDisplayNotation) -> None:
        """Change key display notation and refresh the current file view."""
        self.key_notation = notation
        if self.current_file_id is not None:
            self.load_file_asset(self.current_file_id)

    def load_file_asset(self, file_id: str) -> None:
        """Populate the detail panel for the specified file_id."""
        self.current_file_id = file_id
        asset = self.repo.get_file_by_id(file_id)
        if asset is None:
            return

        self.header_label.setText(
            f"File: {Path(asset.current_path).name} | Format: {asset.format} | "
            f"Write Supported: {asset.is_write_supported}"
        )
        self._populate_warnings(asset)
        self.audio_player.load_file(Path(asset.current_path), fallback_duration_sec=asset.duration)
        self._populate_fields_table(asset)
        self._populate_candidates_list(file_id)

    def _populate_warnings(self, asset: FileAsset) -> None:
        """Render XDJ-RX3 hardware warnings, pitch drift alerts, and rip diagnostics."""
        tech = asset.original_tags.get("technical", {})
        warnings: list[str] = list(tech.get("warnings", []))
        for ana in self.repo.list_analysis_results(asset.id):
            warnings.extend(ana.warnings)
        self.warnings_label.setText(" | ".join(warnings) if warnings else "No hardware warnings.")

    def _populate_fields_table(self, asset: FileAsset) -> None:
        """Populate rows for each standard DJ field."""
        existing_std = asset.original_tags.get("standard_tags", {})
        approved_map = self.repo.get_approved_fields_map(asset.id)
        proposals = self.repo.list_field_proposals(asset.id)
        by_field: dict[str, list[FieldProposal]] = {}
        for prop in proposals:
            by_field.setdefault(prop.field_name, []).append(prop)

        self.fields_table.setRowCount(len(DISPLAY_FIELDS))
        for row_idx, field_enum in enumerate(DISPLAY_FIELDS):
            f_name = field_enum.value
            self._populate_single_field_row(
                row_idx=row_idx,
                file_id=asset.id,
                field_name=f_name,
                existing_val=str(existing_std.get(f_name, "")),
                field_props=by_field.get(f_name, []),
                approved=approved_map.get(f_name),
            )

    def _populate_single_field_row(
        self,
        *,
        row_idx: int,
        file_id: str,
        field_name: str,
        existing_val: str,
        field_props: list[FieldProposal],
        approved: ApprovedField | None,
    ) -> None:
        """Render a single field row with inline edit and Accept/Lock controls."""
        top_prop = field_props[0] if field_props else None
        prop_val = self._format_display_val(field_name, top_prop.proposed_value) if top_prop else ""
        src_text = self._format_proposal_source(top_prop) if top_prop else "-"
        app_val = self._format_display_val(field_name, approved.approved_value if approved else "")

        self.fields_table.setItem(row_idx, COL_FIELD_NAME, QTableWidgetItem(field_name))
        self.fields_table.setItem(
            row_idx,
            COL_EXISTING_TAG,
            QTableWidgetItem(self._format_display_val(field_name, existing_val)),
        )
        self.fields_table.setItem(row_idx, COL_TOP_PROPOSAL, QTableWidgetItem(prop_val))
        self.fields_table.setItem(row_idx, COL_PROPOSAL_SOURCE, QTableWidgetItem(src_text))

        edit_box = QLineEdit(app_val, self.fields_table)
        edit_box.setMaxLength(MAX_MANUAL_FIELD_LENGTH)
        self.fields_table.setCellWidget(row_idx, COL_APPROVED_EDIT, edit_box)

        action_widget = self._create_row_action_widget(
            file_id, field_name, top_prop, approved, edit_box
        )
        self.fields_table.setCellWidget(row_idx, COL_ACTIONS, action_widget)

    def _create_row_action_widget(
        self,
        file_id: str,
        field_name: str,
        top_prop: FieldProposal | None,
        approved: ApprovedField | None,
        edit_box: QLineEdit,
    ) -> QWidget:
        """Create Accept, Save Edit, and Lock toggle buttons for a field row."""
        container = QWidget(self.fields_table)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(
            ACTION_ROW_MARGIN_PX,
            ACTION_ROW_MARGIN_PX,
            ACTION_ROW_MARGIN_PX,
            ACTION_ROW_MARGIN_PX,
        )

        if top_prop is not None:
            acc_btn = QPushButton("Accept", container)
            acc_btn.clicked.connect(lambda _c=False, pid=top_prop.id: self._on_accept_proposal(pid))
            layout.addWidget(acc_btn)

        save_btn = QPushButton("Save", container)
        save_btn.clicked.connect(
            lambda _c=False, fid=file_id, fn=field_name, eb=edit_box: self._on_save_manual(
                fid, fn, eb.text()
            )
        )
        layout.addWidget(save_btn)

        is_locked = bool(approved and approved.locked)
        lock_btn = QPushButton("Unlock" if is_locked else "Lock", container)
        lock_btn.clicked.connect(
            lambda _c=False, fid=file_id, fn=field_name, lk=not is_locked: self._on_toggle_lock(
                fid, fn, lk
            )
        )
        layout.addWidget(lock_btn)
        return container

    def _format_display_val(self, field_name: str, raw_val: str) -> str:
        """Format canonical key according to active KeyDisplayNotation when displaying KEY."""
        if field_name != FieldName.KEY.value or not raw_val:
            return raw_val
        parsed = parse_key_string(raw_val)
        if parsed is None:
            return raw_val
        return format_canonical_key(parsed[0], parsed[1], self.key_notation)

    def _format_proposal_source(self, prop: FieldProposal) -> str:
        """Format provider attribution, provider confidence, and application match score."""
        p_conf = f"{prop.provider_confidence:.2f}" if prop.provider_confidence is not None else "-"
        m_score = (
            f"{prop.application_match_score:.2f}"
            if prop.application_match_score is not None
            else "-"
        )
        return f"{prop.provider} (conf={p_conf}, match={m_score})"

    def _populate_candidates_list(self, file_id: str) -> None:
        """Populate candidate matches and analysis results in the bottom evidence list."""
        self.candidates_list.clear()
        for cand in self.repo.list_candidate_matches(file_id):
            rel = cand.release_details
            self.candidates_list.addItem(
                f"[{cand.provider}] {cand.artist} - {cand.title} "
                f"({cand.mix_name or 'Original'}) | Label: {rel.label or '-'} "
                f"| Cat#: {rel.catalog_number or '-'} | Score: {cand.match_score or 0.0:.2f}"
            )
        for ana in self.repo.list_analysis_results(file_id):
            key_str = "-"
            if ana.key_tonic and ana.key_mode:
                key_str = format_canonical_key(ana.key_tonic, ana.key_mode, self.key_notation)
            self.candidates_list.addItem(
                f"[Analyzer: {ana.analyzer_name}] BPM: {ana.bpm or '-'} | Key: {key_str}"
            )

    def accept_first_proposal_for_field(self, field_name: str) -> bool:
        """Programmatically accept the top proposal for field_name on the current file."""
        if self.current_file_id is None:
            return False
        props = self.repo.list_field_proposals(self.current_file_id, field_name)
        if not props:
            return False
        return self._on_accept_proposal(props[0].id)

    def _on_accept_proposal(self, proposal_id: str) -> bool:
        """Accept a proposal and refresh UI state."""
        ok = self.review_service.accept_proposal(proposal_id)
        if self.current_file_id:
            self.load_file_asset(self.current_file_id)
        if self.on_change_callback:
            self.on_change_callback()
        return ok

    def _on_save_manual(self, file_id: str, field_name: str, value: str) -> None:
        """Save a manual field edit with lock=True and refresh UI state."""
        cleaned = value.strip()
        if len(cleaned) > MAX_MANUAL_FIELD_LENGTH:
            cleaned = cleaned[:MAX_MANUAL_FIELD_LENGTH]
        self.review_service.set_manual_field_value(file_id, field_name, cleaned, lock=True)
        self.load_file_asset(file_id)
        if self.on_change_callback:
            self.on_change_callback()

    def _on_toggle_lock(self, file_id: str, field_name: str, locked: bool) -> None:
        """Toggle field lock and refresh UI state."""
        self.review_service.set_field_lock(file_id, field_name, locked=locked)
        self.load_file_asset(file_id)
        if self.on_change_callback:
            self.on_change_callback()
