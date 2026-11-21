"""The UI-R style sheet that the theme adds after the qt-material style sheet.

qt-material styles every widget from the theme file. This sheet sets the UI-R text
sizes, button variants, focus ring, and table lines from the design tokens. A later
rule with the same selector wins, so these rules override the qt-material rules.
"""

from __future__ import annotations

from typing import Final

from infiniteprep.ui.tokens import (
    CORNER_RADIUS,
    FOCUS_RING_WIDTH,
    STANDARD_ICON_BUTTON_SIZE,
    STANDARD_PRIMARY_BUTTON_HEIGHT,
    STANDARD_SECONDARY_BUTTON_HEIGHT,
    Palette,
    scaled_size,
    spacing_for,
    text_sizes_for,
)

# Widgets set this dynamic property to pick a button variant. Buttons without it
# use the secondary (outlined) style.
BUTTON_VARIANT_PROPERTY: Final[str] = "variant"
PRIMARY_BUTTON_VARIANT: Final[str] = "primary"
ICON_BUTTON_VARIANT: Final[str] = "icon"
# Labels set this dynamic property to pick a text role other than body text.
TEXT_ROLE_PROPERTY: Final[str] = "textRole"
CAPTION_TEXT_ROLE: Final[str] = "caption"
SECTION_HEADING_TEXT_ROLE: Final[str] = "sectionHeading"
DIALOG_TITLE_TEXT_ROLE: Final[str] = "dialogTitle"
# Index of the 8 px step in the spacing scale. Buttons use it as side padding.
BUTTON_PADDING_STEP: Final[int] = 1


def _text_rules(palette: Palette, preset: str) -> list[str]:
    """Return the rules that size each text role."""
    sizes = text_sizes_for(preset)
    return [
        f"* {{ font-size: {sizes.body}pt; }}",
        f"QHeaderView::section {{ font-size: {sizes.caption}pt; color: {palette.text_muted}; }}",
        f'QLabel[{TEXT_ROLE_PROPERTY}="{CAPTION_TEXT_ROLE}"] '
        f"{{ font-size: {sizes.caption}pt; color: {palette.text_muted}; }}",
        f'QLabel[{TEXT_ROLE_PROPERTY}="{SECTION_HEADING_TEXT_ROLE}"] '
        f"{{ font-size: {sizes.section_heading}pt; font-weight: 600; }}",
        f'QLabel[{TEXT_ROLE_PROPERTY}="{DIALOG_TITLE_TEXT_ROLE}"] '
        f"{{ font-size: {sizes.dialog_title}pt; font-weight: 600; }}",
    ]


def _button_rules(palette: Palette, preset: str) -> list[str]:
    """Return the rules for the secondary, primary, and icon-only buttons."""
    padding = spacing_for(preset)[BUTTON_PADDING_STEP]
    secondary_height = scaled_size(STANDARD_SECONDARY_BUTTON_HEIGHT, preset)
    primary_height = scaled_size(STANDARD_PRIMARY_BUTTON_HEIGHT, preset)
    icon_size = scaled_size(STANDARD_ICON_BUTTON_SIZE, preset)
    primary = f'QPushButton[{BUTTON_VARIANT_PROPERTY}="{PRIMARY_BUTTON_VARIANT}"]'
    icon = f'[{BUTTON_VARIANT_PROPERTY}="{ICON_BUTTON_VARIANT}"]'
    return [
        f"QPushButton {{ min-height: {secondary_height}px; padding: 0 {padding}px; "
        f"border: 1px solid {palette.line}; border-radius: {CORNER_RADIUS}px; "
        f"color: {palette.text}; background-color: transparent; }}",
        f"{primary} {{ min-height: {primary_height}px; border: none; "
        f"color: {palette.on_primary}; background-color: {palette.primary}; }}",
        f"QPushButton{icon}, QToolButton{icon} {{ min-width: {icon_size}px; "
        f"min-height: {icon_size}px; max-width: {icon_size}px; max-height: {icon_size}px; "
        f"padding: 0; border: none; border-radius: {CORNER_RADIUS}px; "
        "background-color: transparent; }",
        f"QPushButton{icon}:hover, QToolButton{icon}:hover "
        f"{{ background-color: {palette.surface}; }}",
        f"QPushButton:focus, QToolButton:focus {{ border: {FOCUS_RING_WIDTH}px solid "
        f"{palette.highlight}; }}",
    ]


def _table_rules(palette: Palette) -> list[str]:
    """Return the rules that draw a line under each table row."""
    return [
        f"QTableView {{ gridline-color: {palette.line}; }}",
        f"QTableView::item {{ border-bottom: 1px solid {palette.line}; }}",
    ]


def build_style_sheet(palette: Palette, preset: str) -> str:
    """Return the UI-R style sheet for a palette and a text size preset."""
    rules = _text_rules(palette, preset) + _button_rules(palette, preset) + _table_rules(palette)
    return "\n".join(rules)
