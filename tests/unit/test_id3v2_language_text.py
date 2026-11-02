"""Behavioral tests for decoding the text of ID3v2 COMM and USLT frames."""

from __future__ import annotations

import pytest

from infiniteprep.adapters.metadata_io.id3v2 import Id3FormatError, Id3Frame, decode_language_text
from tests.fixtures.id3v2_bytes import utf16


def test_utf16_comment_text_follows_language_and_description() -> None:
    """The text after the language code and the terminated description comes back."""
    body = b"\x01eng" + utf16("Note") + b"\x00\x00" + utf16("Played at sunrise")
    assert decode_language_text(Id3Frame("COMM", body)) == ["Played at sunrise"]


def test_latin1_lyrics_drop_the_trailing_terminator() -> None:
    """A Latin-1 USLT frame with a final terminator yields one value without an empty tail."""
    body = b"\x00deu" + b"\x00" + "Strophe eins".encode("latin-1") + b"\x00"
    assert decode_language_text(Id3Frame("USLT", body)) == ["Strophe eins"]


def test_truncated_language_frame_raises() -> None:
    """A body shorter than its encoding byte and language code raises Id3FormatError."""
    with pytest.raises(Id3FormatError):
        decode_language_text(Id3Frame("COMM", b"\x00en"))
