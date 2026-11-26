"""Focused coverage for offline catalog, stub, and mock adapters."""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Any

import pytest
from keyring.errors import KeyringError, PasswordDeleteError

from infiniteprep.adapters.catalog.beatport_stub import (
    BEATPORT_NOT_IMPLEMENTED,
    BeatportStubAdapter,
)
from infiniteprep.adapters.catalog.mock_catalog import MockCatalogProviderAdapter
from infiniteprep.adapters.llm.mock_llm import MockReasoningAdapter
from infiniteprep.adapters.unconfigured import (
    NOT_CONNECTED_REASON,
    UnconfiguredCatalogProvider,
    UnconfiguredFingerprintProvider,
)
from infiniteprep.config import credentials as credentials_module
from infiniteprep.config import settings as settings_module
from infiniteprep.config.credentials import CredentialStore, CredentialStoreError
from infiniteprep.config.settings import validate_endpoint_url
from infiniteprep.domain.enums import EntityType
from infiniteprep.domain.ports import MetadataSearchQuery


def test_mock_catalog_returns_deterministic_defaults() -> None:
    """An empty query produces the documented offline candidate."""
    adapter = MockCatalogProviderAdapter()

    assert adapter.is_available() == (True, None)
    result = adapter.search(MetadataSearchQuery(), file_id="file-1")

    assert len(result) == 1
    candidate = result[0]
    assert candidate.id == "mock-cat-file-1-1"
    assert candidate.file_id == "file-1"
    assert candidate.provider == "discogs_mock"
    assert candidate.provider_entity_id == "rel-triuk014"
    assert candidate.entity_type is EntityType.RELEASE
    assert candidate.artist == "Deep Dish"
    assert candidate.title == "The Dream"
    assert candidate.mix_name == "Sharam's Deep Dish Dreamscape Mix"
    assert candidate.remixer == "Sharam"
    assert candidate.duration == 480.0
    assert candidate.match_score == 0.93
    assert candidate.match_evidence == {
        "source": "mock_catalog",
        "catalog_number": "TRIUK014",
    }
    details = candidate.release_details
    assert details is not None
    assert details.release_title == "The Dream (12-Inch Single)"
    assert details.label == "Tribal America"
    assert details.catalog_number == "TRIUK014"
    assert details.country == "UK"
    assert details.format_description == 'Vinyl, 12", 33 1/3 RPM'
    assert details.vinyl_position == "A1"
    assert details.year == "1994"
    assert details.track_number == "A1"
    assert details.disc_number == "1"
    assert details.genre == "Progressive House"


def test_mock_catalog_uses_every_supplied_query_field() -> None:
    """Supplied query fields replace each offline default."""
    adapter = MockCatalogProviderAdapter()
    query = MetadataSearchQuery(
        artist="Artist",
        title="Track",
        mix_name="Mix",
        remixer="Remixer",
        release_title="Release",
        label="Label",
        catalog_number="CAT-1",
        duration=123.0,
    )

    candidate = adapter.search(query)[0]

    assert candidate.id == "mock-cat-default-1"
    assert candidate.provider_entity_id == "rel-cat-1"
    assert candidate.artist == "Artist"
    assert candidate.title == "Track"
    assert candidate.mix_name == "Mix"
    assert candidate.remixer == "Remixer"
    assert candidate.duration == 123.0
    details = candidate.release_details
    assert details is not None
    assert details.release_title == "Release"
    assert details.label == "Label"
    assert details.catalog_number == "CAT-1"


def test_mock_catalog_search_candidates_and_fetch_override_identity() -> None:
    """The convenience search passes its file ID, and fetch overrides entity identity."""
    adapter = MockCatalogProviderAdapter()

    searched = adapter.search_candidates(
        file_id="file-2",
        artist="Artist",
        title="Track",
        release_title="Release",
        catalog_number="CAT-2",
        duration_sec=99.0,
    )
    fetched = adapter.fetch("entity-9", EntityType.RECORDING, file_id="file-3")

    assert searched[0].id == "mock-cat-file-2-1"
    assert searched[0].artist == "Artist"
    assert searched[0].title == "Track"
    assert searched[0].duration == 99.0
    assert fetched[0].provider_entity_id == "entity-9"
    assert fetched[0].entity_type is EntityType.RECORDING
    assert fetched[0].file_id == "file-3"


def test_beatport_stub_never_reports_availability_or_results() -> None:
    """Beatport stays unavailable with and without a token and returns no candidates."""
    without_token = BeatportStubAdapter()
    with_token = BeatportStubAdapter("synthetic-token")

    assert without_token.is_available()[0] is False
    assert "partner OAuth2 credentials" in without_token.is_available()[1]
    assert with_token.is_available() == (False, BEATPORT_NOT_IMPLEMENTED)
    assert without_token.search(MetadataSearchQuery(), file_id="file-1") == []
    assert without_token.fetch("entity-1", EntityType.RECORDING, file_id="file-1") == []


def test_mock_reasoning_adapter_selects_the_first_allowed_candidate() -> None:
    """The mock selects the first dictionary candidate and preserves its fields."""
    adapter = MockReasoningAdapter()

    result = adapter.arbitrate(
        {"allowed_candidates": [{"id": "candidate-1", "value": "First"}]}
    )

    assert adapter.is_available() == (True, None)
    assert result["selected_candidate_id"] == "candidate-1"
    assert result["proposed_value"] == "First"
    assert "top allowed candidate" in result["rationale"]


def test_mock_reasoning_adapter_handles_empty_and_scalar_candidates() -> None:
    """An empty packet returns no selection, and a scalar candidate becomes text."""
    adapter = MockReasoningAdapter()

    empty = adapter.arbitrate({"allowed_candidates": []})
    scalar = adapter.arbitrate({"allowed_candidates": ["candidate-2"]})

    assert empty["selected_candidate_id"] is None
    assert empty["proposed_value"] is None
    assert "No allowed candidates" in empty["rationale"]
    assert scalar["selected_candidate_id"] == "candidate-2"
    assert scalar["proposed_value"] == "candidate-2"


def test_unconfigured_providers_report_the_same_reason_and_find_nothing() -> None:
    """Unconfigured providers stay unavailable and never invent an identification."""
    fingerprint = UnconfiguredFingerprintProvider()
    catalog = UnconfiguredCatalogProvider()

    assert fingerprint.is_available() == (False, NOT_CONNECTED_REASON)
    assert catalog.is_available() == (False, NOT_CONNECTED_REASON)
    result = fingerprint.identify(Path("track.wav"), file_id="file-1")
    assert result.provider == "fingerprint_unconfigured"
    assert result.raw_fingerprint is None
    assert result.duration is None
    assert result.matches == []
    assert result.warnings == [NOT_CONNECTED_REASON]
    assert catalog.search(MetadataSearchQuery(), file_id="file-1") == []
    assert catalog.fetch("entity-1", EntityType.RECORDING, file_id="file-1") == []


def test_mock_reasoning_adapter_ignores_unrelated_packet_fields() -> None:
    """Only allowed_candidates controls deterministic mock arbitration."""
    packet: dict[str, Any] = {"allowed_candidates": [{"id": "candidate-3", "value": "Value"}]}

    result = MockReasoningAdapter().arbitrate(packet)

    assert result["selected_candidate_id"] == "candidate-3"
    assert result["proposed_value"] == "Value"


def test_credentials_prefer_environment_then_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Environment variables take precedence over memory values."""
    store = CredentialStore(use_memory_backend=True)
    store.set_secret("acoustid_api_key", "memory-value")
    assert store.get_secret("acoustid_api_key") == "memory-value"

    monkeypatch.setenv("INFINITEPREP_ACOUSTID_API_KEY", "environment-value")
    assert store.get_secret("acoustid_api_key") == "environment-value"
    monkeypatch.delenv("INFINITEPREP_ACOUSTID_API_KEY")

    store.delete_secret("acoustid_api_key")
    assert store.get_secret("acoustid_api_key") is None
    store.set_secret("llm_api_key", "value")
    store.clear_memory()
    assert store.get_secret("llm_api_key") is None


def test_credential_store_converts_keyring_get_and_set_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Keyring read and write failures fail closed with CredentialStoreError."""
    store = CredentialStore()
    monkeypatch.setattr(
        credentials_module.keyring,
        "get_password",
        lambda *_args: (_ for _ in ()).throw(KeyringError("backend unavailable")),
    )
    with pytest.raises(CredentialStoreError, match="Secure OS keyring unavailable"):
        store.get_secret("discogs_token")

    monkeypatch.setattr(
        credentials_module.keyring,
        "set_password",
        lambda *_args: (_ for _ in ()).throw(OSError("read only")),
    )
    with pytest.raises(CredentialStoreError, match="refusing plaintext fallback"):
        store.set_secret("discogs_token", "secret")
    assert store._memory == {}


def test_credential_store_ignores_missing_delete_and_reports_other_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing key is harmless, while a backend failure reports an error."""
    store = CredentialStore()
    monkeypatch.setattr(
        credentials_module.keyring,
        "delete_password",
        lambda *_args: (_ for _ in ()).throw(PasswordDeleteError("missing")),
    )
    store.delete_secret("discogs_token")

    monkeypatch.setattr(
        credentials_module.keyring,
        "delete_password",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("backend failed")),
    )
    with pytest.raises(CredentialStoreError, match="Failed to delete credential"):
        store.delete_secret("discogs_token")


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://127.0.0.1:11434/v1", True),
        ("https://api.openai.com/v1", True),
        ("http://example.com/v1", False),
        ("https://127.0.0.1/v1", False),
        ("https://10.0.0.1/v1", False),
        ("https://100.64.0.1/v1", False),
        ("https://metadata.google.internal/v1", False),
        ("https://service.internal/v1", False),
        ("https://user:password@api.openai.com/v1", False),
        ("file:///tmp/socket", False),
        ("not a url", False),
    ],
)
def test_validate_endpoint_url_rejects_unsafe_endpoints(url: str, expected: bool) -> None:
    """Endpoint validation accepts only local HTTP or public HTTPS endpoints."""
    assert validate_endpoint_url(url)[0] is expected


def test_validate_endpoint_url_checks_dns_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """HTTPS and localhost endpoints reject DNS answers that leave the allowed range."""

    def records(address: str) -> list[tuple[Any, ...]]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]

    monkeypatch.setattr(
        settings_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: records("127.0.0.1"),
    )
    assert validate_endpoint_url("https://example.test/v1", resolve_dns=True)[0] is False

    monkeypatch.setattr(
        settings_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: records("93.184.216.34"),
    )
    assert validate_endpoint_url("https://example.test/v1", resolve_dns=True)[0] is True

    monkeypatch.setattr(
        settings_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: records("127.0.0.1"),
    )
    assert validate_endpoint_url("http://localhost:8080/v1", resolve_dns=True)[0] is True

    monkeypatch.setattr(
        settings_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: records("93.184.216.34"),
    )
    assert validate_endpoint_url("http://localhost:8080/v1", resolve_dns=True)[0] is False

    def fail_dns(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("DNS unavailable")

    monkeypatch.setattr(settings_module.socket, "getaddrinfo", fail_dns)
    assert validate_endpoint_url("https://example.test/v1", resolve_dns=True)[0] is False
    assert validate_endpoint_url("http://localhost:8080/v1", resolve_dns=True)[0] is False

    monkeypatch.setattr(settings_module.socket, "getaddrinfo", lambda *_args, **_kwargs: [])
    assert validate_endpoint_url("https://example.test/v1", resolve_dns=True)[0] is False
