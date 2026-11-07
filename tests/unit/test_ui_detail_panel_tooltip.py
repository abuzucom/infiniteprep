"""Behavioral tests: Other Tags tooltips show tag text literally, never as HTML."""

from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtGui import QTextDocument

from infiniteprep.ui.detail_panel import plain_text_tooltip

IMG_VALUE = "<img src='file:///etc/hosts'>"
MARKUP_VALUES = [
    "<b>bold</b>",
    IMG_VALUE,
    "<a href='https://example.invalid/'>link</a> & more",
    "<12 bytes, sha256 00ff>",
    "Tom & Jerry \"quoted\" 'single'",
]


def _render(tooltip: str) -> QTextDocument:
    """Return the document Qt builds when it renders the tooltip as rich text."""
    document = QTextDocument()
    document.setHtml(tooltip)
    return document


@pytest.mark.parametrize("raw_value", MARKUP_VALUES)
def test_tooltip_renders_markup_as_literal_text(qapp: Any, raw_value: str) -> None:
    """Markup in a tag value shows as its own characters, with no element created.

    The qapp fixture starts the QApplication that QTextDocument needs.
    """
    document = _render(plain_text_tooltip(raw_value))

    assert document.toPlainText() == raw_value
    rendered_html = document.toHtml()
    assert "<img" not in rendered_html
    assert "<a href" not in rendered_html


def test_img_tag_becomes_escaped_text(qapp: Any) -> None:
    """An img tag in a value stays escaped text in the rendered tooltip."""
    rendered_html = _render(plain_text_tooltip(IMG_VALUE)).toHtml()

    assert "&lt;img" in rendered_html
