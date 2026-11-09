"""Detail panel for inspecting file metadata, field proposals, locks, and warnings."""

from __future__ import annotations

import hashlib
import html
import unicodedata
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Final

from PySide6.QtWidgets import (
    QAbstractItemView,
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

from infiniteprep.config.settings import SettingsManager
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
from infiniteprep.ui.waveform_worker import WaveformAnalysisPool

DISPLAY_FIELDS: Final[tuple[FieldName, ...]] = tuple(FieldName)
FIELDS_TABLE_COLUMN_COUNT: Final[int] = 6
FIELDS_TABLE_STRETCH: Final[int] = 3
OTHER_TAGS_STRETCH: Final[int] = 1
CANDIDATE_BOX_STRETCH: Final[int] = 1
OTHER_TAGS_COLUMN_COUNT: Final[int] = 2
COL_OTHER_KEY: Final[int] = 0
COL_OTHER_VALUE: Final[int] = 1
# Longer values show cut short in the cell. The tooltip holds the full text.
OTHER_TAG_DISPLAY_LIMIT: Final[int] = 200
TRUNCATION_MARK: Final[str] = "..."
# Code points that attach to the character before them. A truncation cut never lands
# before one of these, so a visible character never splits.
MARK_CATEGORIES: Final[frozenset[str]] = frozenset({"Mn", "Mc", "Me"})
ZERO_WIDTH_JOINER: Final[str] = chr(0x200D)
VARIATION_SELECTORS: Final[tuple[int, int]] = (0xFE00, 0xFE0F)
VARIATION_SELECTORS_SUPPLEMENT: Final[tuple[int, int]] = (0xE0100, 0xE01EF)
EMOJI_SKIN_TONE_MODIFIERS: Final[tuple[int, int]] = (0x1F3FB, 0x1F3FF)
# The app adds keys with this prefix itself, for example _bits_per_sample (D245).
INTERNAL_TAG_PREFIX: Final[str] = "_"
LIST_VALUE_SEPARATOR: Final[str] = "; "
# The paragraph keeps line breaks and spaces as the tag holds them.
TOOLTIP_PARAGRAPH_OPEN: Final[str] = "<p style='white-space:pre-wrap'>"
TOOLTIP_PARAGRAPH_CLOSE: Final[str] = "</p>"
COL_FIELD_NAME: Final[int] = 0
COL_EXISTING_TAG: Final[int] = 1
COL_TOP_PROPOSAL: Final[int] = 2
COL_PROPOSAL_SOURCE: Final[int] = 3
COL_APPROVED_EDIT: Final[int] = 4
COL_ACTIONS: Final[int] = 5
ACTION_ROW_MARGIN_PX: Final[int] = 2


def _format_scalar_tag_value(value: object) -> str:
    """Return bytes as their size and SHA-256, and any other value as its text.

    The summary matches the one the tag reader stores for binary frames.
    """
    if isinstance(value, bytes | bytearray):
        digest = hashlib.sha256(value).hexdigest()
        return f"<{len(value)} bytes, sha256 {digest}>"
    return str(value)


def format_other_tag_value(value: object) -> str:
    """Return the full display text of one raw custom tag value.

    A list joins its items with a separator. Only one level unpacks, so nesting never
    recurses.
    """
    if isinstance(value, list | tuple):
        return LIST_VALUE_SEPARATOR.join(_format_scalar_tag_value(item) for item in value)
    return _format_scalar_tag_value(value)


def _extends_previous_character(char: str) -> bool:
    """Return True when char joins the character before it into one visible grapheme."""
    code_point = ord(char)
    return (
        unicodedata.category(char) in MARK_CATEGORIES
        or char == ZERO_WIDTH_JOINER
        or VARIATION_SELECTORS[0] <= code_point <= VARIATION_SELECTORS[1]
        or VARIATION_SELECTORS_SUPPLEMENT[0] <= code_point <= VARIATION_SELECTORS_SUPPLEMENT[1]
        or EMOJI_SKIN_TONE_MODIFIERS[0] <= code_point <= EMOJI_SKIN_TONE_MODIFIERS[1]
    )


def _grapheme_safe_cut(text: str, cut: int) -> int:
    """Move cut back until it falls between two visible graphemes.

    A cut before a combining mark, a joiner, a variation selector, or a skin tone
    modifier, or right after a joiner, would split one visible character.
    """
    while cut > 0 and (
        _extends_previous_character(text[cut]) or text[cut - 1] == ZERO_WIDTH_JOINER
    ):
        cut -= 1
    return cut


def truncate_for_cell(text: str) -> str:
    """Return text cut to at most OTHER_TAG_DISPLAY_LIMIT characters, marked when cut.

    The cut never splits a visible character.
    """
    if len(text) <= OTHER_TAG_DISPLAY_LIMIT:
        return text
    cut = _grapheme_safe_cut(text, OTHER_TAG_DISPLAY_LIMIT - len(TRUNCATION_MARK))
    return text[:cut] + TRUNCATION_MARK


def plain_text_tooltip(text: str) -> str:
    """Return a tooltip that shows text literally.

    Qt renders a tooltip as rich text when it looks like HTML, and no plain-text
    tooltip mode exists. Escaping the text inside a fixed paragraph keeps a tag value
    such as <img src=...> from loading or formatting anything.
    """
    return f"{TOOLTIP_PARAGRAPH_OPEN}{html.escape(text, quote=True)}{TOOLTIP_PARAGRAPH_CLOSE}"


def other_tag_rows(raw_custom_tags: Mapping[str, object]) -> list[tuple[str, str]]:
    """Return (key, full value text) rows sorted by key, without internal keys."""
    return [
        (key, format_other_tag_value(raw_custom_tags[key]))
        for key in sorted(raw_custom_tags, key=str.casefold)
        if not key.startswith(INTERNAL_TAG_PREFIX)
    ]


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
        settings_manager: SettingsManager | None = None,
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
        # The file ID in the player. A refresh of the same file ID keeps playback (D246).
        self._loaded_audio_id: str | None = None
        self._analysis_generation = 0
        self._analysis_file_id: str | None = None
        self._analysis_path: Path | None = None
        self.key_notation: KeyDisplayNotation = KeyDisplayNotation.CAMELOT

        self.header_label = QLabel("Select a track to inspect metadata and proposals.", self)
        self.warnings_label = QLabel("", self)
        self.warnings_label.setStyleSheet("color: #d97706; font-weight: bold;")
        self.audio_player = AudioPreviewWidget(
            self,
            enable_multimedia_backend=enable_audio_backend,
            settings_manager=settings_manager,
        )
        self.waveform_pool = WaveformAnalysisPool(self)
        self.waveform_pool.completed.connect(self._on_waveform_completed)
        self.waveform_pool.failed.connect(self._on_waveform_failed)
        self.fields_table = QTableWidget(0, FIELDS_TABLE_COLUMN_COUNT, self)
        self.other_tags_table = QTableWidget(0, OTHER_TAGS_COLUMN_COUNT, self)
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
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setStretchLastSection(True)
        root.addWidget(self.fields_table, stretch=FIELDS_TABLE_STRETCH)

        other_box = QGroupBox("Other Tags", self)
        other_layout = QVBoxLayout(other_box)
        self.other_tags_table.setHorizontalHeaderLabels(["Key", "Value"])
        self.other_tags_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.other_tags_table.verticalHeader().setVisible(False)
        other_header = self.other_tags_table.horizontalHeader()
        other_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        other_header.setStretchLastSection(True)
        other_layout.addWidget(self.other_tags_table)
        root.addWidget(other_box, stretch=OTHER_TAGS_STRETCH)

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
        if file_id != self.current_file_id:
            self._analysis_generation += 1
            self._analysis_file_id = None
            self._analysis_path = None
        self.current_file_id = file_id
        asset = self.repo.get_file_by_id(file_id)
        if asset is None:
            return

        self.header_label.setText(
            f"File: {Path(asset.current_path).name} | Format: {asset.format} | "
            f"Write Supported: {asset.is_write_supported}"
        )
        self._populate_warnings(asset)
        self._load_audio_if_changed(asset)
        self._request_waveform_analysis(asset)
        self._populate_fields_table(asset)
        self._populate_other_tags(asset)
        self._populate_candidates_list(file_id)

    def _load_audio_if_changed(self, asset: FileAsset) -> None:
        """Load the asset into the player only when a different file ID is selected.

        Notation changes and field actions refresh the same file. Reloading would stop
        playback and reset the position. A new stored path under the same ID also keeps
        the loaded track (D246).
        """
        if asset.id == self._loaded_audio_id:
            return
        self._loaded_audio_id = asset.id
        self.audio_player.load_file(Path(asset.current_path), fallback_duration_sec=asset.duration)

    def _request_waveform_analysis(self, asset: FileAsset) -> None:
        """Submit one job for the selected track and retain a fallback overview."""
        path = Path(asset.current_path)
        if asset.id == self._analysis_file_id and path == self._analysis_path:
            return
        self._analysis_generation += 1
        self._analysis_file_id = asset.id
        self._analysis_path = path
        self.audio_player.show_analysis_pending()
        self.waveform_pool.submit(path, asset.id, self._analysis_generation)

    def _on_waveform_completed(self, file_id: str, generation: int, waveform: object) -> None:
        """Display only results that still match the selected track generation."""
        if file_id != self.current_file_id or generation != self._analysis_generation:
            return
        self.audio_player.set_spectrum_waveform(waveform)
        self.audio_player.show_status("")

    def _on_waveform_failed(self, file_id: str, generation: int, message: str) -> None:
        """Display current analysis failures without clearing the overview."""
        if file_id != self.current_file_id or generation != self._analysis_generation:
            return
        self.audio_player.show_analysis_failure(message)

    def _populate_other_tags(self, asset: FileAsset) -> None:
        """Fill the read-only Other Tags table from the file's raw custom tags."""
        raw_custom_tags = asset.original_tags.get("raw_custom_tags") or {}
        rows = other_tag_rows(raw_custom_tags)
        self.other_tags_table.setRowCount(len(rows))
        for row_idx, (key, full_text) in enumerate(rows):
            key_item = QTableWidgetItem(key)
            value_item = QTableWidgetItem(truncate_for_cell(full_text))
            value_item.setToolTip(plain_text_tooltip(full_text))
            self.other_tags_table.setItem(row_idx, COL_OTHER_KEY, key_item)
            self.other_tags_table.setItem(row_idx, COL_OTHER_VALUE, value_item)

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
