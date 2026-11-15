"""Behavioral tests for the configurable iTunes Search endpoint (M18)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from infiniteprep.adapters.catalog.itunes import ITUNES_SEARCH_URL, ItunesSearchAdapter
from infiniteprep.config.credentials import CredentialStore
from infiniteprep.config.settings import (
    DEFAULT_ITUNES_SEARCH_ENDPOINT,
    AppSettings,
    SettingsManager,
)
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.ports import MetadataSearchQuery
from infiniteprep.ui.settings.identification import IdentificationTab

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "itunes_search"
CUSTOM_ENDPOINT = "https://search.example.test/v1/search"


def _recorded(name: str) -> dict[str, Any]:
    """Return the recorded iTunes response stored under the fixture directory."""
    return json.loads((FIXTURE_DIR / f"{name}.json").read_text(encoding="utf-8"))


def _adapter(endpoint: str = ITUNES_SEARCH_URL) -> tuple[ItunesSearchAdapter, list[str]]:
    """Return an adapter that records the endpoint of every request."""
    urls: list[str] = []

    def fake_get(url: str, _params: dict[str, str]) -> dict[str, Any]:
        urls.append(url)
        return _recorded("match_with_genre_and_date")

    adapter = ItunesSearchAdapter(
        repository=Repository(DatabaseManager(":memory:")),
        http_get_json=fake_get,
        min_interval_sec=0.0,
        endpoint=endpoint,
    )
    return adapter, urls


def test_the_default_endpoint_is_the_published_search_endpoint() -> None:
    """The default endpoint names the published iTunes Search host."""
    assert DEFAULT_ITUNES_SEARCH_ENDPOINT == "https://itunes.apple.com/search"
    assert ITUNES_SEARCH_URL == DEFAULT_ITUNES_SEARCH_ENDPOINT
    assert AppSettings().itunes_search_endpoint == DEFAULT_ITUNES_SEARCH_ENDPOINT


def test_the_adapter_queries_the_configured_endpoint() -> None:
    """A configured endpoint reaches the request and the proposal evidence."""
    adapter, urls = _adapter(CUSTOM_ENDPOINT)

    matches = adapter.search(
        MetadataSearchQuery(artist="Deep Dish", title="The Dream"), file_id="file-1"
    )

    assert urls == [CUSTOM_ENDPOINT]
    assert matches[0].match_evidence["source_url"] == CUSTOM_ENDPOINT


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://itunes.apple.com/search",
        "https://10.0.0.1/search",
        "https://service.internal/search",
    ],
)
def test_the_adapter_refuses_an_endpoint_that_breaks_the_endpoint_rules(endpoint: str) -> None:
    """An endpoint that fails the shared rules never reaches the adapter."""
    with pytest.raises(ValueError, match="Invalid iTunes Search endpoint"):
        _adapter(endpoint)


def test_an_invalid_stored_endpoint_falls_back_to_the_default(tmp_path: Path) -> None:
    """A stored endpoint that breaks the rules loads as the default."""
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps({"itunes_search_endpoint": "http://itunes.apple.com/search"}),
        encoding="utf-8",
    )

    loaded = SettingsManager(settings_path).load()

    assert loaded.itunes_search_endpoint == DEFAULT_ITUNES_SEARCH_ENDPOINT


def test_an_update_with_an_invalid_endpoint_raises(tmp_path: Path) -> None:
    """An update that breaks the endpoint rules raises and writes nothing."""
    manager = SettingsManager(tmp_path / "settings.json")

    with pytest.raises(ValueError, match="itunes_search_endpoint"):
        manager.update(itunes_search_endpoint="http://itunes.apple.com/search")


def test_the_identification_tab_saves_the_endpoint(qtbot: Any) -> None:
    """The tab shows the endpoint and writes the stored value back."""
    settings = AppSettings()
    tab = IdentificationTab(settings, CredentialStore(use_memory_backend=True))
    qtbot.addWidget(tab)

    tab.itunes_endpoint_input.setText(CUSTOM_ENDPOINT)
    updated = AppSettings()
    tab.apply_to(updated)

    assert updated.itunes_search_endpoint == CUSTOM_ENDPOINT
