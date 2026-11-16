"""Behavioral tests for the iTunes Search disclosure text (D404)."""

from __future__ import annotations

from typing import Any

from PySide6.QtWidgets import QLabel

from infiniteprep.adapters.catalog.itunes import DISCLOSURE_TEXT, ITUNES_SEARCH_HOST
from infiniteprep.config.credentials import CredentialStore
from infiniteprep.config.settings import AppSettings
from infiniteprep.ui.settings.identification import IdentificationTab


def test_the_disclosure_names_the_host_and_the_query_data() -> None:
    """The disclosure text names the host and the data every lookup sends."""
    assert ITUNES_SEARCH_HOST == "itunes.apple.com"
    assert ITUNES_SEARCH_HOST in DISCLOSURE_TEXT
    assert "artist name" in DISCLOSURE_TEXT
    assert "track title" in DISCLOSURE_TEXT
    assert "country" in DISCLOSURE_TEXT
    assert "IP address" in DISCLOSURE_TEXT


def test_the_identification_tab_shows_the_disclosure(qtbot: Any) -> None:
    """The Identification settings tab carries the disclosure text."""
    tab = IdentificationTab(AppSettings(), CredentialStore(use_memory_backend=True))
    qtbot.addWidget(tab)

    texts = [label.text() for label in tab.findChildren(QLabel)]

    assert DISCLOSURE_TEXT in texts
