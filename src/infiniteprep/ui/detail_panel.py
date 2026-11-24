"""Detail panel for inspecting file metadata, field proposals, locks, and warnings."""

from __future__ import annotations

import hashlib
import html
import unicodedata
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QCloseEvent, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
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
from infiniteprep.domain.models import AnalysisResult, ApprovedField, FieldProposal, FileAsset
from infiniteprep.services.fake_lossless import ANALYZER_VERSION as FAKE_LOSSLESS_VERSION
from infiniteprep.services.fake_lossless import FakeLosslessResult
from infiniteprep.services.review_service import MAX_MANUAL_FIELD_LENGTH, ReviewService
from infiniteprep.ui.action_handlers import show_plain_message
from infiniteprep.ui.audio_player import AudioPreviewWidget
from infiniteprep.ui.collapsible_section import CollapsibleSection
from infiniteprep.ui.column_sizing import WeightedColumnSizer
from infiniteprep.ui.contrast import warning_text_color
from infiniteprep.ui.detail_badges import (
    build_track_badges,
    is_field_changed,
    order_changed_first,
)
from infiniteprep.ui.technical_details import technical_detail_rows
from infiniteprep.ui.tokens import Palette
from infiniteprep.ui.track_badges_row import TrackBadgesRow
from infiniteprep.ui.waveform_worker import CANCEL_WAIT_MS, WaveformAnalysisPool

DISPLAY_FIELDS: Final[tuple[FieldName, ...]] = tuple(FieldName)
FIELDS_TABLE_COLUMN_COUNT: Final[int] = 6
PLAYER_SECTION_TITLE: Final[str] = "Waveform and player"
CHANGES_SECTION_TITLE: Final[str] = "Changes"
WARNINGS_SECTION_TITLE: Final[str] = "Issues"
OTHER_TAGS_SECTION_TITLE: Final[str] = "Other tags"
CANDIDATES_SECTION_TITLE: Final[str] = "Candidates"
TECHNICAL_SECTION_TITLE: Final[str] = "Technical details"
# The field table takes most of the height. The lists below share the rest.
SECTION_STRETCH: Final[dict[str, int]] = {
    CHANGES_SECTION_TITLE: 3,
    OTHER_TAGS_SECTION_TITLE: 1,
    CANDIDATES_SECTION_TITLE: 1,
}
# Each warning sentence sits on its own line.
WARNING_LINE_SEPARATOR: Final[str] = "\n"
TECHNICAL_ROW_SEPARATOR: Final[str] = ": "
# The Field cell of a changed row carries True under this role.
CHANGED_ROW_ROLE: Final[int] = Qt.ItemDataRole.UserRole + 1
OTHER_TAGS_COLUMN_COUNT: Final[int] = 2
COLUMN_OTHER_KEY: Final[int] = 0
COLUMN_OTHER_VALUE: Final[int] = 1
PLAY_SELECTED_LABEL: Final[str] = "Play"
NO_SELECTION_TEXT: Final[str] = "Pick a track to see its details."
NO_WARNINGS_TEXT: Final[str] = "No issues."
REPLACE_TRACK_TITLE: Final[str] = "Replace the playing track?"
REPLACE_TRACK_CANCEL_LABEL: Final[str] = "Cancel"
REPLACE_TRACK_CONFIRM_LABEL: Final[str] = "Load paused"
FIELD_TABLE_HEADERS: Final[tuple[str, ...]] = (
    "Field",
    "Now",
    "Suggested",
    "Source",
    "New value",
    "Actions",
)
FIELDS_TABLE_NAME: Final[str] = "Changes"
CANDIDATES_LIST_NAME: Final[str] = "Possible matches"
NO_SAVE_SUFFIX: Final[str] = " (can't save tags to this file)"
ORIGINAL_MIX_TEXT: Final[str] = "Original"
NOTHING_DETECTED_TEXT: Final[str] = "Nothing detected"
PERCENT_SCALE: Final[int] = 100


def format_match_percent(score: float | None) -> str:
    """Return a 0 to 1 match score as a whole percentage, such as "88% match"."""
    return f"{round((score or 0.0) * PERCENT_SCALE)}% match"


def format_header_text(file_name: str, *, write_supported: bool) -> str:
    """Return the panel header: the file name, and a note when tags can't be saved."""
    return file_name if write_supported else f"{file_name}{NO_SAVE_SUFFIX}"


def format_source_cell(provider: str, match_score: float | None) -> str:
    """Return the Source cell: the provider and, when known, its match percentage."""
    if match_score is None:
        return provider
    return f"{provider}, {format_match_percent(match_score)}"


def format_candidate_row(
    *,
    provider: str,
    artist: str,
    title: str,
    mix_name: str | None,
    label: str | None,
    catalog_number: str | None,
    match_score: float | None,
) -> str:
    """Return one candidate row, such as "A - T (Dub), Label CAT1. From discogs, 91% match"."""
    release = " ".join(part for part in (label, catalog_number) if part)
    track = f"{artist} - {title} ({mix_name or ORIGINAL_MIX_TEXT})"
    if release:
        track = f"{track}, {release}"
    return f"{track}. From {provider}, {format_match_percent(match_score)}"


def format_analysis_row(bpm: str | None, key_text: str | None) -> str:
    """Return one analysis row, such as "Detected: 128 BPM, key 8A"."""
    parts = []
    if bpm:
        parts.append(f"{bpm} BPM")
    if key_text:
        parts.append(f"key {key_text}")
    if not parts:
        return NOTHING_DETECTED_TEXT
    return "Detected: " + ", ".join(parts)


# Stored in the fake-lossless record configuration when the newest run fails.
FAKE_LOSSLESS_FAILED_STATUS: Final[str] = "failed"
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
COLUMN_FIELD_NAME: Final[int] = 0
COLUMN_EXISTING_TAG: Final[int] = 1
COLUMN_TOP_PROPOSAL: Final[int] = 2
COLUMN_PROPOSAL_SOURCE: Final[int] = 3
COLUMN_APPROVED_EDIT: Final[int] = 4
COLUMN_ACTIONS: Final[int] = 5
# Existing Tag, Top Proposal, and Approved / Edit share the spare width equally. The
# other columns fit their cells, so the Actions column is only as wide as its buttons.
FIELDS_TABLE_WEIGHTS: Final[dict[int, int]] = {
    COLUMN_EXISTING_TAG: 1,
    COLUMN_TOP_PROPOSAL: 1,
    COLUMN_APPROVED_EDIT: 1,
}
FIELDS_TABLE_MIN_SHARE_PX: Final[int] = 80
# The Field column shows these names. Stored rows and approvals keep the field keys.
# The keys are the stored string values, the same strings the table rows carry.
FIELD_LABELS: Final[dict[str, str]] = {
    FieldName.ARTIST.value: "Artist",
    FieldName.TITLE.value: "Title",
    FieldName.MIX_NAME.value: "Mix name",
    FieldName.REMIXER.value: "Remixer",
    FieldName.RELEASE_TITLE.value: "Release title",
    FieldName.LABEL.value: "Label",
    FieldName.CATALOG_NUMBER.value: "Catalog number",
    FieldName.TRACK_NUMBER.value: "Track number",
    FieldName.DISC_NUMBER.value: "Disc number",
    FieldName.VINYL_POSITION.value: "Vinyl position",
    FieldName.GENRE.value: "Genre",
    FieldName.STYLE.value: "Style",
    FieldName.BPM.value: "BPM",
    FieldName.KEY.value: "Key",
    FieldName.YEAR.value: "Year",
    FieldName.ACOUSTID_ID.value: "AcoustID ID",
    FieldName.MB_RECORDING_ID.value: "MusicBrainz recording ID",
    FieldName.MB_RELEASE_ID.value: "MusicBrainz release ID",
    FieldName.DISCOGS_RELEASE_ID.value: "Discogs release ID",
}
ACTION_ROW_MARGIN_PX: Final[int] = 2
# Row widget roles. A row widget carries the object name "<field>:<role>", so focus
# finds the same control again after the table rebuilds.
ROLE_EDIT: Final[str] = "edit"
ROLE_ACCEPT: Final[str] = "accept"
ROLE_SAVE: Final[str] = "save"
ROLE_LOCK: Final[str] = "lock"
ROW_FIELD_PROPERTY: Final[str] = "field_name"
LOADED_TEXT_PROPERTY: Final[str] = "loaded_text"
ROW_NAME_SEPARATOR: Final[str] = ":"
LOCK_TITLE: Final[str] = "Lock field"
LOCK_NEEDS_VALUE_TEXT: Final[str] = (
    "The {field} field has no approved value to lock. Save or accept a value first."
)
# Events after which the label background can differ, so the warning amber can too.
THEME_CHANGE_EVENTS: Final[frozenset[QEvent.Type]] = frozenset(
    {QEvent.Type.StyleChange, QEvent.Type.PaletteChange}
)


@dataclass(frozen=True)
class FieldRowState:
    """Unsaved edit box text and the focused row control, kept across a table rebuild."""

    unsaved_edits: dict[str, str]
    focused_name: str | None


def row_widget_name(field_name: str, role: str) -> str:
    """Return the object name of one row control."""
    return f"{field_name}{ROW_NAME_SEPARATOR}{role}"


def field_label(field_name: str) -> str:
    """Return the spoken form of a field name, such as "catalog number"."""
    return field_name.replace("_", " ")


def field_display_name(field_name: str) -> str:
    """Return the name the Field column shows, such as "Catalog number".

    An unknown key shows as stored, so no row loses its name.
    """
    return FIELD_LABELS.get(field_name, field_name)


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

    # The file the field rows show. The class defaults keep __init__ inside its budget.
    _fields_file_id: str | None = None
    # The one row whose unsaved text the next rebuild replaces with the stored value.
    # A successful Save or Accept sets it. Every other row keeps its unsaved text. Unset,
    # the rebuild keeps unsaved text in every row, including a row whose Accept failed.
    _acted_field: str | None = None

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

        The panel shares one SettingsManager with its audio preview. A default
        manager resolves the application settings path, the same file every other
        default instance uses.
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
        self._latest_generation_by_file: dict[str, int] = {}
        self.key_notation: KeyDisplayNotation = KeyDisplayNotation.CAMELOT

        self.header_label = QLabel(NO_SELECTION_TEXT, self)
        self.warnings_label = QLabel("", self)
        # File names and decoder messages come from imported files. The default
        # AutoText format renders markup in them as HTML.
        self.header_label.setTextFormat(Qt.TextFormat.PlainText)
        self.warnings_label.setTextFormat(Qt.TextFormat.PlainText)
        self._apply_warning_color()
        self.play_selected_btn = QPushButton(PLAY_SELECTED_LABEL, self)
        self.play_selected_btn.setEnabled(False)
        self.play_selected_btn.setAccessibleName(PLAY_SELECTED_LABEL)
        self.play_selected_btn.clicked.connect(self._play_selected_file)
        self.settings_manager = settings_manager or SettingsManager()
        self.audio_player = AudioPreviewWidget(
            self,
            enable_multimedia_backend=enable_audio_backend,
            settings_manager=self.settings_manager,
        )
        self.audio_player.on_play_selection_requested = self._on_player_play_requested
        self.audio_player.on_file_dropped = self._on_file_id_dropped
        self._build_waveform_pool()
        self.fields_table = QTableWidget(0, FIELDS_TABLE_COLUMN_COUNT, self)
        self.other_tags_table = QTableWidget(0, OTHER_TAGS_COLUMN_COUNT, self)
        self.candidates_list = QListWidget(self)

        self._build_layout()

    def _build_waveform_pool(self) -> None:
        """Create the waveform analysis pool and connect its results."""
        self.waveform_pool = WaveformAnalysisPool(self)
        self.waveform_pool.overview_ready.connect(self._on_overview_ready)
        self.waveform_pool.completed.connect(self._on_waveform_completed)
        self.waveform_pool.fake_lossless_ready.connect(self._on_fake_lossless_ready)
        self.waveform_pool.fake_lossless_failed.connect(self._on_fake_lossless_failed)
        self.waveform_pool.failed.connect(self._on_waveform_failed)
        application = QApplication.instance()
        if application is not None:
            application.aboutToQuit.connect(self.waveform_pool.shutdown)

    def _build_layout(self) -> None:
        """Stack the track header and the sections, each with its collapse arrow.

        Every section opens by default. Technical details starts closed, so tag frame
        names, the content hash, the backup path, and decoder messages stay out of sight.
        """
        root = QVBoxLayout(self)
        header_row = QHBoxLayout()
        header_row.addWidget(self.header_label, stretch=1)
        header_row.addWidget(self.play_selected_btn)
        root.addLayout(header_row)
        self.badges_row = TrackBadgesRow(self)
        root.addWidget(self.badges_row)
        self._setup_fields_table()
        self._setup_other_tags_table()
        self.candidates_list.setAccessibleName(CANDIDATES_LIST_NAME)
        self.warnings_label.setWordWrap(True)
        self.technical_label = QLabel("", self)
        self.technical_label.setTextFormat(Qt.TextFormat.PlainText)
        self.technical_label.setWordWrap(True)
        self.technical_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        sections = (
            (PLAYER_SECTION_TITLE, self.audio_player, True),
            (CHANGES_SECTION_TITLE, self.fields_table, True),
            (WARNINGS_SECTION_TITLE, self.warnings_label, True),
            (OTHER_TAGS_SECTION_TITLE, self.other_tags_table, True),
            (CANDIDATES_SECTION_TITLE, self.candidates_list, True),
            (TECHNICAL_SECTION_TITLE, self.technical_label, False),
        )
        self.sections: dict[str, CollapsibleSection] = {}
        for title, content, expanded in sections:
            section = CollapsibleSection(title, content, self, expanded=expanded)
            self.sections[title] = section
            root.addWidget(section, stretch=SECTION_STRETCH.get(title, 0))
        self.technical_section = self.sections[TECHNICAL_SECTION_TITLE]

    def _setup_fields_table(self) -> None:
        """Set the headers, sizing, and keyboard behavior of the field table."""
        self.fields_table.setHorizontalHeaderLabels(list(FIELD_TABLE_HEADERS))
        self.fields_sizer = WeightedColumnSizer(
            self.fields_table, FIELDS_TABLE_WEIGHTS, FIELDS_TABLE_MIN_SHARE_PX
        )
        # Tab moves through the edit box and the buttons of each row. With cell
        # navigation on, Tab returns focus to the table and skips the buttons.
        self.fields_table.setTabKeyNavigation(False)
        # Edits go through the edit box of each row. A typed cell value was discarded.
        self.fields_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.fields_table.setAccessibleName(FIELDS_TABLE_NAME)

    def _setup_other_tags_table(self) -> None:
        """Set the headers and the read-only behavior of the Other tags table."""
        self.other_tags_table.setHorizontalHeaderLabels(["Key", "Value"])
        self.other_tags_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.other_tags_table.setTabKeyNavigation(False)
        self.other_tags_table.setAccessibleName("Other tags")
        self.other_tags_table.verticalHeader().setVisible(False)
        other_header = self.other_tags_table.horizontalHeader()
        other_header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        other_header.setStretchLastSection(True)

    def closeEvent(self, event: QCloseEvent) -> None:
        """Stop analysis work when this panel is closed independently.

        A closed panel can reopen. The pool stays usable, and the cleared request
        identity makes the next selection submit a fresh analysis. The wait is
        bounded so a read stalled on a slow volume cannot freeze the window. The
        panel still owns the pool, and a late worker emits nothing.
        """
        self.waveform_pool.cancel_all(CANCEL_WAIT_MS)
        self._analysis_file_id = None
        self._analysis_path = None
        super().closeEvent(event)

    def set_key_notation(self, notation: KeyDisplayNotation) -> None:
        """Change key display notation and refresh the current file view."""
        self.key_notation = notation
        if self.current_file_id is not None:
            self.select_file_asset(self.current_file_id)

    def clear_selection(self) -> None:
        """Show that no file is selected and leave the player untouched.

        A cleared table selection leaves no file behind in the panel. The loaded
        track stays in the player, and the Play button turns off.
        """
        self.current_file_id = None
        self.header_label.setText(NO_SELECTION_TEXT)
        self.badges_row.clear()
        self.warnings_label.clear()
        self.fields_table.setRowCount(0)
        self.other_tags_table.setRowCount(0)
        self.candidates_list.clear()
        self.technical_label.clear()
        self.play_selected_btn.setEnabled(False)

    def set_palette(self, palette: Palette) -> None:
        """Draw the badges in the theme's colors and show the current track again."""
        self.badges_row.set_palette(palette)
        if self.current_file_id is not None:
            self.select_file_asset(self.current_file_id)

    def select_file_asset(self, file_id: str) -> FileAsset | None:
        """Populate the detail panel for the specified file_id without touching the player.

        A selection never stops playback. The player keeps its track, its position,
        and its play state until an explicit load (D407).
        """
        if file_id != self.current_file_id:
            self._analysis_file_id = None
            self._analysis_path = None
        self.current_file_id = file_id
        asset = self.repo.get_file_by_id(file_id)
        if asset is None:
            return None

        self.header_label.setText(
            format_header_text(
                Path(asset.current_path).name, write_supported=asset.is_write_supported
            )
        )
        self._populate_warnings(asset)
        self._populate_fields_table(asset)
        self._populate_other_tags(asset)
        self._populate_candidates_list(file_id)
        self._populate_technical_details(asset)
        self.play_selected_btn.setEnabled(True)
        return asset

    def _populate_technical_details(self, asset: FileAsset) -> None:
        """List tag frame names, the content hash, the backup, and the decoder message."""
        records = self.repo.list_change_records(asset.id)
        rows = technical_detail_rows(asset, records, field_display_name)
        self.technical_label.setText(
            WARNING_LINE_SEPARATOR.join(
                f"{label}{TECHNICAL_ROW_SEPARATOR}{value}" for label, value in rows
            )
        )

    def load_file_asset(self, file_id: str) -> None:
        """Populate the detail panel for the specified file_id and load the track."""
        asset = self.select_file_asset(file_id)
        if asset is None:
            return
        self._load_audio_if_changed(asset)
        self._request_waveform_analysis(asset)

    def load_and_play_file_asset(self, file_id: str) -> None:
        """Load the track into the player and start it.

        The selection stays where it is. The loaded track label shows the track
        the player holds.
        """
        asset = self.repo.get_file_by_id(file_id)
        if asset is None:
            return
        if asset.id == self._loaded_audio_id:
            # The same track keeps its position, so the request resumes it.
            self.audio_player.start_playback()
            return
        self._load_audio(asset)
        self._request_waveform_analysis(asset)
        self.audio_player.start_playback()

    def load_track_paused(self, file_id: str) -> None:
        """Load the track into the player paused at the start.

        The selection stays where it is, so the panel can show another file than
        the player holds.
        """
        asset = self.repo.get_file_by_id(file_id)
        if asset is None:
            return
        self.audio_player.stop_playback()
        self._load_audio(asset)
        self._request_waveform_analysis(asset)

    @property
    def loaded_file_id(self) -> str | None:
        """Return the file ID the player holds."""
        return self._loaded_audio_id

    def _play_selected_file(self) -> None:
        """Load and play the selected file.

        The button always plays the selection. A selection that the player
        already holds resumes at its current position.
        """
        if self.current_file_id is None:
            return
        self.load_and_play_file_asset(self.current_file_id)

    def _on_player_play_requested(self) -> bool:
        """Load the selection for the player play button when it differs from the loaded track.

        The player button toggles the loaded track when the selection matches it.
        """
        if self.current_file_id is None:
            return False
        if self.current_file_id == self._loaded_audio_id:
            return False
        self.load_and_play_file_asset(self.current_file_id)
        return True

    def _load_audio(self, asset: FileAsset) -> None:
        """Load the asset into the player and mark it as the loaded track."""
        self._loaded_audio_id = asset.id
        self.audio_player.load_file(Path(asset.current_path), fallback_duration_sec=asset.duration)

    def _load_audio_if_changed(self, asset: FileAsset) -> None:
        """Load the asset into the player only when a different file ID is selected.

        Notation changes and field actions refresh the same file. Reloading would stop
        playback and reset the position. A new stored path under the same ID also keeps
        the loaded track (D246).
        """
        if asset.id == self._loaded_audio_id:
            return
        self._load_audio(asset)

    def _request_waveform_analysis(self, asset: FileAsset) -> None:
        """Submit one job for the loaded track and retain a fallback overview."""
        path = Path(asset.current_path)
        if asset.id == self._analysis_file_id and path == self._analysis_path:
            return
        self._analysis_generation += 1
        self._analysis_file_id = asset.id
        self._analysis_path = path
        self._latest_generation_by_file[asset.id] = self._analysis_generation
        self.audio_player.show_analysis_pending()
        method = self.settings_manager.load().fake_lossless_method
        self.waveform_pool.submit(
            path,
            asset.id,
            self._analysis_generation,
            fake_lossless_method=method,
        )

    def _on_file_id_dropped(self, file_id: str) -> None:
        """Load a dropped track into the player paused at the start.

        A playing track needs a confirmation first. The warning names both tracks,
        so the user knows what the drop replaces.
        """
        asset = self.repo.get_file_by_id(file_id)
        if asset is None:
            return
        dropped_name = Path(asset.current_path).name
        if self.audio_player.is_playing:
            playing_name = self._loaded_track_name()
            if not self._confirm_replace_playing_track(playing_name, dropped_name):
                return
        self.load_track_paused(asset.id)

    def _loaded_track_name(self) -> str:
        """Return the name of the track the player holds."""
        if self.audio_player.current_file_path is None:
            return "The current track"
        return self.audio_player.current_file_path.name

    def _confirm_replace_playing_track(self, playing_name: str, dropped_name: str) -> bool:
        """Ask before a drop replaces the playing track."""
        box = self._build_replace_track_box(playing_name, dropped_name)
        box.exec()
        clicked = box.clickedButton()
        return clicked is not None and clicked.text() == REPLACE_TRACK_CONFIRM_LABEL

    def _build_replace_track_box(self, playing_name: str, dropped_name: str) -> QMessageBox:
        """Build the warning that a drop replaces the playing track."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(REPLACE_TRACK_TITLE)
        box.setText(
            f"'{playing_name}' is playing. "
            f"Loading '{dropped_name}' stops it and starts the new track paused."
        )
        cancel_button = box.addButton(REPLACE_TRACK_CANCEL_LABEL, QMessageBox.ButtonRole.RejectRole)
        box.addButton(REPLACE_TRACK_CONFIRM_LABEL, QMessageBox.ButtonRole.AcceptRole)
        # Cancel stays the default, so a stray Enter keeps the playing track.
        box.setDefaultButton(cancel_button)
        return box

    def _on_overview_ready(self, file_id: str, generation: int, envelope: object) -> None:
        """Display only a fallback overview that matches the loaded track generation."""
        if file_id != self._loaded_audio_id or generation != self._analysis_generation:
            return
        self.audio_player.set_overview_envelope(list(envelope))

    def _on_waveform_completed(self, file_id: str, generation: int, waveform: object) -> None:
        """Display only results that still match the loaded track generation."""
        if file_id != self._loaded_audio_id or generation != self._analysis_generation:
            return
        self.audio_player.set_spectrum_waveform(waveform)
        self.audio_player.show_analysis_complete()

    def _on_waveform_failed(self, file_id: str, generation: int, message: str) -> None:
        """Display current analysis failures without clearing the overview."""
        if file_id != self._loaded_audio_id or generation != self._analysis_generation:
            return
        self.audio_player.show_analysis_failure(message)

    def _on_fake_lossless_ready(self, file_id: str, generation: int, result: object) -> None:
        """Persist fake-lossless diagnostics and refresh warnings for the active track."""
        if not isinstance(result, FakeLosslessResult):
            return
        if generation != self._latest_generation_by_file.get(file_id):
            return
        warning = result.format_warning()
        self._store_fake_lossless(
            file_id,
            generation,
            configuration={
                "method": result.method,
                "sample_rate": result.sample_rate,
                "cutoff_hz": result.cutoff_hz,
                "traces": list(result.traces),
            },
            strength=result.confidence,
            warnings=[warning] if warning is not None else [],
        )

    def _on_fake_lossless_failed(self, file_id: str, generation: int, message: str) -> None:
        """Replace stored fake-lossless diagnostics when the newest re-analysis fails.

        A failed run leaves no current verdict. Keeping the earlier warning would
        show a stale diagnosis as current in the panel and in transcode preflight.
        """
        if generation != self._latest_generation_by_file.get(file_id):
            return
        self._store_fake_lossless(
            file_id,
            generation,
            configuration={"status": FAKE_LOSSLESS_FAILED_STATUS, "error": message},
            strength=None,
            warnings=[],
        )

    def _store_fake_lossless(
        self,
        file_id: str,
        generation: int,
        *,
        configuration: dict[str, object],
        strength: float | None,
        warnings: list[str],
    ) -> None:
        """Upsert the single fake-lossless record and refresh warnings for the active track."""
        self.repo.upsert_analysis_result(
            AnalysisResult(
                id=f"fake-lossless-{file_id}",
                file_id=file_id,
                analyzer_name="fake_lossless",
                analyzer_version=str(FAKE_LOSSLESS_VERSION),
                configuration=configuration,
                bpm=None,
                key_tonic=None,
                key_mode=None,
                analysis_strength=strength,
                warnings=warnings,
            )
        )
        if file_id != self.current_file_id or generation != self._analysis_generation:
            return
        asset = self.repo.get_file_by_id(file_id)
        if asset is not None:
            self._populate_warnings(asset)

    def _populate_other_tags(self, asset: FileAsset) -> None:
        """Fill the read-only Other Tags table from the file's raw custom tags."""
        raw_custom_tags = asset.original_tags.get("raw_custom_tags") or {}
        rows = other_tag_rows(raw_custom_tags)
        self.other_tags_table.setRowCount(len(rows))
        for row_index, (key, full_text) in enumerate(rows):
            key_item = QTableWidgetItem(key)
            value_item = QTableWidgetItem(truncate_for_cell(full_text))
            value_item.setToolTip(plain_text_tooltip(full_text))
            self.other_tags_table.setItem(row_index, COLUMN_OTHER_KEY, key_item)
            self.other_tags_table.setItem(row_index, COLUMN_OTHER_VALUE, value_item)

    def _apply_warning_color(self) -> None:
        """Color the warnings with the amber that reaches 4.5:1 on the label background.

        A fixed amber read at about 3:1 on a light theme. The label background
        follows the theme, so the amber follows it too.
        """
        # Polish first. Before polish, the palette lacks the theme style sheet colors.
        self.warnings_label.ensurePolished()
        background = self.warnings_label.palette().color(QPalette.ColorRole.Window)
        color = warning_text_color(background).name()
        self.warnings_label.setStyleSheet(f"color: {color}; font-weight: bold;")

    def changeEvent(self, event: QEvent) -> None:
        """Pick the warning amber again after a theme or palette change."""
        super().changeEvent(event)
        if event.type() in THEME_CHANGE_EVENTS:
            self._apply_warning_color()

    def _populate_warnings(self, asset: FileAsset) -> None:
        """Render XDJ-RX3 hardware warnings, pitch drift alerts, and rip diagnostics."""
        technical_tags = asset.original_tags.get("technical", {})
        warnings: list[str] = list(technical_tags.get("warnings", []))
        for analysis in self.repo.list_analysis_results(asset.id):
            warnings.extend(analysis.warnings)
        self.warnings_label.setText(
            WARNING_LINE_SEPARATOR.join(warnings) if warnings else NO_WARNINGS_TEXT
        )

    def _populate_fields_table(self, asset: FileAsset) -> None:
        """Populate rows for each standard DJ field.

        A refresh of the same file keeps unsaved text in other rows and keeps focus on
        the same row control. A row action rebuilds every row, and the rebuild dropped
        both before.
        """
        same_file = asset.id == self._fields_file_id
        kept = self._capture_row_state() if same_file else FieldRowState({}, None)
        existing_standard_tags = asset.original_tags.get("standard_tags", {})
        approved_map = self.repo.get_approved_fields_map(asset.id)
        proposals = self.repo.list_field_proposals(asset.id)
        by_field: dict[str, list[FieldProposal]] = {}
        for proposal in proposals:
            by_field.setdefault(proposal.field_name, []).append(proposal)
        changed = {
            field: is_field_changed(
                str(existing_standard_tags.get(field.value, "")),
                approved_map.get(field.value),
                by_field.get(field.value, []),
            )
            for field in DISPLAY_FIELDS
        }

        self.fields_table.setRowCount(len(DISPLAY_FIELDS))
        for row_index, field_enum in enumerate(order_changed_first(changed)):
            f_name = field_enum.value
            self._populate_single_field_row(
                row_index=row_index,
                file_id=asset.id,
                field_name=f_name,
                existing_value=str(existing_standard_tags.get(f_name, "")),
                field_proposals=by_field.get(f_name, []),
                approved=approved_map.get(f_name),
            )
            if changed[field_enum]:
                self._mark_changed_row(row_index)
        self.badges_row.show_badges(
            build_track_badges(asset, approved_map, proposals, self.key_notation)
        )
        self._fields_file_id = asset.id
        self._acted_field = None
        self._restore_row_state(kept)
        self.fields_sizer.refit()

    def _mark_changed_row(self, row_index: int) -> None:
        """Show a changed field's name in bold, so changes stand out from unchanged rows."""
        name_item = self.fields_table.item(row_index, COLUMN_FIELD_NAME)
        if name_item is None:
            return
        font = name_item.font()
        font.setBold(True)
        name_item.setFont(font)
        name_item.setData(CHANGED_ROW_ROLE, True)

    def _row_controls(self) -> Iterator[QWidget]:
        """Yield the edit box and the buttons of every current field row."""
        for row_index in range(self.fields_table.rowCount()):
            edit_box = self.fields_table.cellWidget(row_index, COLUMN_APPROVED_EDIT)
            if edit_box is not None:
                yield edit_box
            actions = self.fields_table.cellWidget(row_index, COLUMN_ACTIONS)
            if actions is not None:
                yield from actions.findChildren(QPushButton)

    def _capture_row_state(self) -> FieldRowState:
        """Record unsaved edit text, except in the acted row, and the focused control."""
        unsaved: dict[str, str] = {}
        for control in self._row_controls():
            if not isinstance(control, QLineEdit):
                continue
            field_name = str(control.property(ROW_FIELD_PROPERTY))
            changed = control.text() != control.property(LOADED_TEXT_PROPERTY)
            if changed and field_name != self._acted_field:
                unsaved[field_name] = control.text()
        focused = QApplication.focusWidget()
        in_rows = focused is not None and self.fields_table.isAncestorOf(focused)
        return FieldRowState(unsaved, focused.objectName() if in_rows else None)

    def _restore_row_state(self, state: FieldRowState) -> None:
        """Put back unsaved edit text and move focus to the same row control.

        When the rebuilt row lacks that control, focus moves to the row's edit box.
        """
        by_name: dict[str, QWidget] = {}
        for control in self._row_controls():
            by_name[control.objectName()] = control
            field_name = str(control.property(ROW_FIELD_PROPERTY))
            if isinstance(control, QLineEdit) and field_name in state.unsaved_edits:
                control.setText(state.unsaved_edits[field_name])
        if not state.focused_name:
            return
        field_name = state.focused_name.split(ROW_NAME_SEPARATOR, 1)[0]
        target = by_name.get(state.focused_name) or by_name.get(
            row_widget_name(field_name, ROLE_EDIT)
        )
        if target is not None:
            target.setFocus()

    def _populate_single_field_row(
        self,
        *,
        row_index: int,
        file_id: str,
        field_name: str,
        existing_value: str,
        field_proposals: list[FieldProposal],
        approved: ApprovedField | None,
    ) -> None:
        """Render a single field row with inline edit and Accept/Lock controls."""
        top_proposal = field_proposals[0] if field_proposals else None
        proposal_value = (
            self._format_display_val(field_name, top_proposal.proposed_value)
            if top_proposal
            else ""
        )
        source_text = self._format_proposal_source(top_proposal) if top_proposal else "-"
        approved_value = self._format_display_val(
            field_name, approved.approved_value if approved else ""
        )

        self.fields_table.setItem(
            row_index, COLUMN_FIELD_NAME, QTableWidgetItem(field_display_name(field_name))
        )
        self.fields_table.setItem(
            row_index,
            COLUMN_EXISTING_TAG,
            QTableWidgetItem(self._format_display_val(field_name, existing_value)),
        )
        self.fields_table.setItem(row_index, COLUMN_TOP_PROPOSAL, QTableWidgetItem(proposal_value))
        self.fields_table.setItem(row_index, COLUMN_PROPOSAL_SOURCE, QTableWidgetItem(source_text))

        edit_box = QLineEdit(approved_value, self.fields_table)
        edit_box.setMaxLength(MAX_MANUAL_FIELD_LENGTH)
        edit_box.setObjectName(row_widget_name(field_name, ROLE_EDIT))
        edit_box.setProperty(ROW_FIELD_PROPERTY, field_name)
        edit_box.setProperty(LOADED_TEXT_PROPERTY, approved_value)
        edit_box.setAccessibleName(f"New value for {field_label(field_name)}")
        self.fields_table.setCellWidget(row_index, COLUMN_APPROVED_EDIT, edit_box)

        action_widget = self._create_row_action_widget(
            file_id, field_name, top_proposal, approved, edit_box
        )
        self.fields_table.setCellWidget(row_index, COLUMN_ACTIONS, action_widget)

    def _create_row_action_widget(
        self,
        file_id: str,
        field_name: str,
        top_proposal: FieldProposal | None,
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

        if top_proposal is not None:
            accept_button = self._add_row_button(layout, field_name, ROLE_ACCEPT, "Accept")
            accept_button.setAccessibleName(f"Accept suggestion for {field_label(field_name)}")
            accept_button.clicked.connect(
                lambda _c=False, field_name=field_name, proposal_id=top_proposal.id: (
                    self._accept_row_proposal(field_name, proposal_id)
                )
            )

        save_button = self._add_row_button(layout, field_name, ROLE_SAVE, "Save")
        save_button.clicked.connect(
            lambda _c=False, file_id=file_id, field_name=field_name, edit_box=edit_box: (
                self._on_save_manual(file_id, field_name, edit_box.text())
            )
        )

        is_locked = bool(approved and approved.locked)
        lock_text = "Unlock" if is_locked else "Lock"
        lock_button = self._add_row_button(layout, field_name, ROLE_LOCK, lock_text)
        lock_button.clicked.connect(
            lambda _c=False, file_id=file_id, field_name=field_name, locked=not is_locked: (
                self._on_toggle_lock(file_id, field_name, locked)
            )
        )
        return container

    def _add_row_button(
        self, layout: QHBoxLayout, field_name: str, role: str, text: str
    ) -> QPushButton:
        """Add one row button named for its field, so each row's buttons sound different."""
        button = QPushButton(text, layout.parentWidget())
        button.setObjectName(row_widget_name(field_name, role))
        button.setProperty(ROW_FIELD_PROPERTY, field_name)
        button.setAccessibleName(f"{text} {field_label(field_name)}")
        layout.addWidget(button)
        return button

    def _accept_row_proposal(self, field_name: str, proposal_id: str) -> bool:
        """Accept a row's proposal. Only an accepted value replaces unsaved text in that row.

        A refused accept keeps the row's typed text. The marker clears even when the
        accept raises, so a later refresh never drops that row's text.
        """
        try:
            accepted = self.review_service.accept_proposal(proposal_id)
            if accepted:
                self._acted_field = field_name
            self._refresh_after_row_action()
            return accepted
        finally:
            self._acted_field = None

    def _refresh_after_row_action(self) -> None:
        """Refresh the current file's rows and tell the owner the library changed.

        A refresh selects the file without loading its track, so playback continues (D407).
        """
        if self.current_file_id:
            self.select_file_asset(self.current_file_id)
        if self.on_change_callback is not None:
            self.on_change_callback()

    def _format_display_val(self, field_name: str, raw_value: str) -> str:
        """Format canonical key according to active KeyDisplayNotation when displaying KEY."""
        if field_name != FieldName.KEY.value or not raw_value:
            return raw_value
        parsed = parse_key_string(raw_value)
        if parsed is None:
            return raw_value
        return format_canonical_key(parsed[0], parsed[1], self.key_notation)

    def _format_proposal_source(self, proposal: FieldProposal) -> str:
        """Return the provider of a proposal and its match percentage."""
        return format_source_cell(proposal.provider, proposal.application_match_score)

    def _populate_candidates_list(self, file_id: str) -> None:
        """Populate candidate matches and analysis results in the bottom evidence list."""
        self.candidates_list.clear()
        for candidate in self.repo.list_candidate_matches(file_id):
            release = candidate.release_details
            self.candidates_list.addItem(
                format_candidate_row(
                    provider=candidate.provider,
                    artist=candidate.artist,
                    title=candidate.title,
                    mix_name=candidate.mix_name,
                    label=release.label,
                    catalog_number=release.catalog_number,
                    match_score=candidate.match_score,
                )
            )
        for analysis in self.repo.list_analysis_results(file_id):
            key_text = None
            if analysis.key_tonic and analysis.key_mode:
                key_text = format_canonical_key(
                    analysis.key_tonic, analysis.key_mode, self.key_notation
                )
            bpm_text = str(analysis.bpm) if analysis.bpm else None
            self.candidates_list.addItem(format_analysis_row(bpm_text, key_text))

    def accept_first_proposal_for_field(self, field_name: str) -> bool:
        """Programmatically accept the top proposal for field_name on the current file."""
        if self.current_file_id is None:
            return False
        proposals = self.repo.list_field_proposals(self.current_file_id, field_name)
        if not proposals:
            return False
        return self._on_accept_proposal(proposals[0].id)

    def _on_accept_proposal(self, proposal_id: str) -> bool:
        """Accept a proposal and refresh UI state."""
        accepted = self.review_service.accept_proposal(proposal_id)
        if self.current_file_id:
            self.select_file_asset(self.current_file_id)
        if self.on_change_callback is not None:
            self.on_change_callback()
        return accepted

    def _on_save_manual(self, file_id: str, field_name: str, value: str) -> None:
        """Save a manual field edit with lock=True and refresh UI state.

        The marker clears even when the save raises, so a later refresh never drops
        that row's unsaved text.
        """
        self._acted_field = field_name
        try:
            cleaned = value.strip()
            if len(cleaned) > MAX_MANUAL_FIELD_LENGTH:
                cleaned = cleaned[:MAX_MANUAL_FIELD_LENGTH]
            self.review_service.set_manual_field_value(file_id, field_name, cleaned, lock=True)
            self.select_file_asset(file_id)
            if self.on_change_callback is not None:
                self.on_change_callback()
        finally:
            self._acted_field = None

    def _on_toggle_lock(self, file_id: str, field_name: str, locked: bool) -> None:
        """Toggle field lock and refresh UI state.

        The repository locks only a stored approved value. Lock on a field without one
        changed nothing and said nothing, so it now says what to do first.
        """
        if locked and self.repo.get_approved_field(file_id, field_name) is None:
            show_plain_message(
                self,
                QMessageBox.Icon.Information,
                LOCK_TITLE,
                LOCK_NEEDS_VALUE_TEXT.format(field=field_label(field_name)),
            )
            return
        self.review_service.set_field_lock(file_id, field_name, locked=locked)
        self.select_file_asset(file_id)
        if self.on_change_callback is not None:
            self.on_change_callback()
