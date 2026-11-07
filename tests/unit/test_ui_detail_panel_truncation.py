"""Behavioral tests: Other Tags truncation never splits a visible character."""

from __future__ import annotations

import pytest

from infiniteprep.ui.detail_panel import (
    OTHER_TAG_DISPLAY_LIMIT,
    TRUNCATION_MARK,
    truncate_for_cell,
)

# The first character the plain cut would drop.
CUT_INDEX = OTHER_TAG_DISPLAY_LIMIT - len(TRUNCATION_MARK)
TAIL = "z" * OTHER_TAG_DISPLAY_LIMIT
COMBINING_ACUTE = "́"
ZERO_WIDTH_JOINER = "‍"
MAN = "\U0001f468"
WOMAN = "\U0001f469"
THUMBS_UP = "\U0001f44d"
MEDIUM_SKIN_TONE = "\U0001f3fd"
HEART = "❤"
EMOJI_PRESENTATION = "️"


@pytest.mark.parametrize(
    ("grapheme", "kept_prefix_length"),
    [
        (f"e{COMBINING_ACUTE}", CUT_INDEX - 1),
        (f"{THUMBS_UP}{MEDIUM_SKIN_TONE}", CUT_INDEX - 1),
        (f"{HEART}{EMOJI_PRESENTATION}", CUT_INDEX - 1),
        (f"{MAN}{ZERO_WIDTH_JOINER}{WOMAN}", CUT_INDEX - 2),
    ],
    ids=["combining-accent", "skin-tone", "variation-selector", "zwj-sequence"],
)
def test_cut_moves_before_a_grapheme_that_spans_it(grapheme: str, kept_prefix_length: int) -> None:
    """A grapheme that crosses the cut point drops whole, with the mark after the text."""
    # The grapheme's last code point sits at CUT_INDEX, so the plain cut splits it.
    prefix = "a" * (CUT_INDEX + 1 - len(grapheme))
    text = prefix + grapheme + TAIL

    shown = truncate_for_cell(text)

    assert shown == "a" * kept_prefix_length + TRUNCATION_MARK
    assert len(shown) <= OTHER_TAG_DISPLAY_LIMIT


def test_plain_text_cuts_at_the_limit() -> None:
    """Text with no joining characters fills the cell to the limit."""
    shown = truncate_for_cell("x" * (OTHER_TAG_DISPLAY_LIMIT * 2))

    assert shown == "x" * CUT_INDEX + TRUNCATION_MARK
    assert len(shown) == OTHER_TAG_DISPLAY_LIMIT


def test_run_of_marks_steps_back_to_its_base() -> None:
    """A long run of marks on one base keeps only the text before that base."""
    text = "b" + COMBINING_ACUTE * (OTHER_TAG_DISPLAY_LIMIT * 2)

    assert truncate_for_cell(text) == TRUNCATION_MARK


def test_short_text_stays_whole() -> None:
    """Text at or under the limit shows unchanged, marks included."""
    text = f"e{COMBINING_ACUTE}" * (OTHER_TAG_DISPLAY_LIMIT // 2)

    assert truncate_for_cell(text) == text
