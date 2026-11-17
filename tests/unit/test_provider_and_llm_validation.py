"""Tests for provider response field checks, the Ollama tag probe, and the LLM output check."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from infiniteprep.adapters.catalog.discogs import DiscogsAdapter
from infiniteprep.adapters.catalog.musicbrainz import MusicBrainzAdapter
from infiniteprep.adapters.fingerprint.chromaprint_acoustid import ChromaprintAcoustIDAdapter
from infiniteprep.adapters.llm import ollama_bootstrap
from infiniteprep.adapters.llm.ollama_bootstrap import OllamaBootstrapService
from infiniteprep.adapters.llm.ollama_openai import (
    MAX_RATIONALE_CHARS,
    OllamaOpenAIReasoningAdapter,
)
from infiniteprep.adapters.llm.validator import MAX_LLM_FIELD_CHARS, ClosedWorldValidator
from infiniteprep.adapters.provider_fields import (
    MAX_PROVIDER_LIST_ITEMS,
    MAX_PROVIDER_TEXT_CHARS,
    provider_dicts,
    provider_number,
    provider_text,
)
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.ports import MetadataSearchQuery

ALLOWED = [
    {"id": "cand-1", "value": "Dreamscape Mix"},
    {"id": "cand-2", "value": "Danny Tenaglia Dub"},
]
LONG_TEXT = "x" * (MAX_PROVIDER_TEXT_CHARS * 3)
SAMPLE_DURATION_SEC = 480.0
SAMPLE_FINGERPRINT = "AQAA_TEST"
TAGS_TOO_LARGE = b"{" + b" " * 64
# A structurally fake key. The fake transport never sends it anywhere.
DISCOGS_STUB_KEY = "synthetic"


def _repository() -> Repository:
    """Return a repository over an empty in-memory database."""
    return Repository(DatabaseManager(":memory:"))


def _llm_reply(content: str) -> Any:
    """Return a fake chat completion endpoint that answers with content."""

    def post(_url: str, _body: dict[str, Any]) -> dict[str, Any]:
        return {"choices": [{"message": {"content": content}}]}

    return post


def _adapter(post: Any) -> OllamaOpenAIReasoningAdapter:
    """Return a reasoning adapter on the loopback endpoint with a fake transport."""
    return OllamaOpenAIReasoningAdapter(base_url="http://127.0.0.1:11434/v1", http_post_json=post)


def test_validator_rejects_an_id_paired_with_another_candidates_value() -> None:
    """The ID of one candidate and the value of another never pass together."""
    output = {"selected_candidate_id": "cand-1", "proposed_value": "Danny Tenaglia Dub"}

    is_valid, error = ClosedWorldValidator().validate_response(output, ALLOWED)

    assert is_valid is False
    assert error is not None
    assert "does not belong" in error


def test_validator_rejects_a_value_when_no_candidate_carries_values() -> None:
    """A proposed value with no candidate value to match counts as invented."""
    allowed = [{"id": "cand-1"}, {"id": "cand-2"}]

    is_valid, _ = ClosedWorldValidator().validate_response(
        {"proposed_value": "Invented Mix"}, allowed
    )

    assert is_valid is False


def test_validator_rejects_a_value_for_a_candidate_without_one() -> None:
    """A value proposed for a candidate that carries no value counts as invented."""
    allowed = [{"id": "cand-1"}]

    is_valid, _ = ClosedWorldValidator().validate_response(
        {"selected_candidate_id": "cand-1", "proposed_value": "Invented Mix"}, allowed
    )

    assert is_valid is False


@pytest.mark.parametrize(
    "output",
    [
        {"selected_candidate_id": 1},
        {"selected_candidate_id": ["cand-1"]},
        {"proposed_value": {"text": "Dreamscape Mix"}},
        {"proposed_value": "y" * (MAX_LLM_FIELD_CHARS + 1)},
    ],
)
def test_validator_rejects_wrong_types_and_oversized_fields(output: dict[str, Any]) -> None:
    """A non-string field or a field above the length cap fails validation."""
    is_valid, _ = ClosedWorldValidator().validate_response(output, ALLOWED)

    assert is_valid is False


def test_validator_accepts_a_matching_pair_an_id_alone_and_a_value_alone() -> None:
    """A matching pair, a lone known ID, and a lone known value each pass."""
    validator = ClosedWorldValidator()

    pair = {"selected_candidate_id": "cand-2", "proposed_value": "  danny tenaglia DUB "}
    assert validator.validate_response(pair, ALLOWED) == (True, None)
    assert validator.validate_response({"selected_candidate_id": "cand-1"}, ALLOWED) == (
        True,
        None,
    )
    assert validator.validate_response({"proposed_value": "Dreamscape Mix"}, ALLOWED) == (
        True,
        None,
    )


def test_adapter_returns_the_candidates_own_value_and_a_bounded_rationale() -> None:
    """The adapter returns the stored candidate value, and the rationale is plain and short."""
    content = (
        '{"selected_candidate_id": "cand-1", "proposed_value": "DREAMSCAPE MIX", '
        '"rationale": "line one\\nline two' + "z" * (MAX_RATIONALE_CHARS * 2) + '"}'
    )

    result = _adapter(_llm_reply(content)).arbitrate({"allowed_candidates": ALLOWED})

    assert result["selected_candidate_id"] == "cand-1"
    assert result["proposed_value"] == "Dreamscape Mix"
    assert "\n" not in result["rationale"]
    assert len(result["rationale"]) <= MAX_RATIONALE_CHARS


def test_adapter_failure_names_only_the_exception_class() -> None:
    """A failed request reports the exception class and never the exception text."""
    body_text = "response body with private details"

    def failing_post(_url: str, _body: dict[str, Any]) -> dict[str, Any]:
        raise ValueError(body_text)

    result = _adapter(failing_post).arbitrate({"allowed_candidates": ALLOWED})

    assert result["selected_candidate_id"] is None
    assert "ValueError" in result["rationale"]
    assert body_text not in result["rationale"]


def test_provider_text_accepts_text_and_integers_only() -> None:
    """Text and integers pass. Booleans, floats, and containers read as absent."""
    assert provider_text("  Deep Dish  ") == "Deep Dish"
    assert provider_text(1994) == "1994"
    assert provider_text(True) is None
    assert provider_text(19.94) is None
    assert provider_text(["Deep Dish"]) is None
    assert provider_text("   ") is None


def test_provider_text_drops_control_characters_and_caps_length() -> None:
    """Line breaks and tabs drop out, and long text stops at the cap."""
    assert provider_text("Deep\nDish\t") == "DeepDish"
    capped = provider_text(LONG_TEXT)
    assert capped is not None
    assert len(capped) == MAX_PROVIDER_TEXT_CHARS


def test_provider_number_and_dicts_reject_bad_shapes() -> None:
    """Numbers must be finite and non-negative. Lists keep dict items up to the cap."""
    assert provider_number(480) == SAMPLE_DURATION_SEC
    assert provider_number("480") is None
    assert provider_number(float("nan")) is None
    assert provider_number(-1) is None
    assert provider_number(False) is None
    assert provider_dicts("not a list") == []
    mixed = [{"a": 1}, "skip", 3] + [{}] * (MAX_PROVIDER_LIST_ITEMS * 2)
    assert len(provider_dicts(mixed)) == MAX_PROVIDER_LIST_ITEMS - 2


def test_discogs_search_survives_malformed_fields() -> None:
    """Wrong field types read as absent values instead of failing the lookup."""
    payload = {
        "results": [
            {
                "id": 42,
                "title": "Deep Dish - The Dream",
                "label": [{"x": 1}, "Tribal America"],
                "catno": {"nested": True},
                "year": [1994],
                "format": "Vinyl",
                "style": None,
                "genre": ["House"],
            },
            "not a result",
        ]
    }
    adapter = DiscogsAdapter(
        _repository(),
        user_token=DISCOGS_STUB_KEY,
        http_get_json=lambda _url, _params: payload,
        min_interval_sec=0.0,
    )

    candidates = adapter.search(MetadataSearchQuery(artist="Deep Dish"))

    assert len(candidates) == 1
    details = candidates[0].release_details
    assert details is not None
    assert candidates[0].provider_entity_id == "42"
    assert details.label == "Tribal America"
    assert details.catalog_number is None
    assert details.year is None
    assert details.genre == "House"
    assert details.format_description is None


def test_discogs_never_caches_a_response_that_is_not_an_object() -> None:
    """A JSON array response fails the lookup before the cache write, so a retry asks again."""
    requested: list[str] = []

    def array_response(url: str, _params: dict[str, str]) -> Any:
        requested.append(url)
        return ["unexpected"]

    adapter = DiscogsAdapter(
        _repository(),
        user_token=DISCOGS_STUB_KEY,
        http_get_json=array_response,
        min_interval_sec=0.0,
    )
    query = MetadataSearchQuery(artist="Deep Dish")

    assert adapter.search(query) == []
    assert adapter.search(query) == []
    assert len(requested) == 2


def test_musicbrainz_caps_long_text_and_ignores_a_text_length() -> None:
    """A long title stops at the cap, and a length given as text reads as no duration."""
    payload = {"recordings": [{"id": "rec-1", "title": LONG_TEXT, "length": "480000"}]}
    adapter = MusicBrainzAdapter(
        _repository(), http_get_json=lambda _url, _params: payload, min_interval_sec=0.0
    )

    candidates = adapter.search(MetadataSearchQuery(title="The Dream"))

    assert len(candidates) == 1
    assert len(candidates[0].title or "") == MAX_PROVIDER_TEXT_CHARS
    assert candidates[0].duration is None


def test_acoustid_rejects_an_out_of_range_score_and_bad_list_items(tmp_path: Path) -> None:
    """A score above 1 reads as no confidence, and non-dict list items drop out."""
    payload = {
        "results": [
            {
                "id": "acoustid-1",
                "score": 7.5,
                "recordings": [
                    {"id": "rec-1", "title": "The Dream", "artists": ["Deep Dish", {"name": 3}]},
                    "not a recording",
                ],
            }
        ]
    }
    adapter = ChromaprintAcoustIDAdapter(
        repository=_repository(),
        api_key="key",
        fpcalc_runner=lambda _path: (SAMPLE_DURATION_SEC, SAMPLE_FINGERPRINT),
        http_get_json=lambda _url, _params: payload,
    )

    result = adapter.identify(tmp_path / "track.wav")

    assert len(result.matches) == 1
    assert result.matches[0].confidence == 0.0
    assert result.matches[0].artist == "3"
    assert result.matches[0].title == "The Dream"


class _FakeResponse:
    """Stands in for a urlopen response and serves fixed bytes."""

    def __init__(self, body: bytes) -> None:
        """Store the body that read returns."""
        self._body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        """Return at most size bytes of the body."""
        return self._body if size < 0 else self._body[:size]


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        (b'{"models": [{"name": "qwen2.5:3b"}, {"name": 5}, "bad"]}', ["qwen2.5:3b"]),
        (b'["not", "an", "object"]', []),
        (b"not json", []),
    ],
)
def test_ollama_model_list_reads_only_valid_names(
    monkeypatch: pytest.MonkeyPatch, body: bytes, expected: list[str]
) -> None:
    """The tag probe keeps valid names and returns nothing for a malformed body."""
    monkeypatch.setattr(
        ollama_bootstrap.urllib.request, "urlopen", lambda *_a, **_k: _FakeResponse(body)
    )

    assert OllamaBootstrapService().list_installed_models() == expected


def test_ollama_model_list_refuses_a_body_above_the_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tag response larger than the cap returns no models."""
    monkeypatch.setattr(ollama_bootstrap, "MAX_TAGS_RESPONSE_BYTES", len(TAGS_TOO_LARGE) - 1)
    monkeypatch.setattr(
        ollama_bootstrap.urllib.request,
        "urlopen",
        lambda *_a, **_k: _FakeResponse(TAGS_TOO_LARGE),
    )

    assert OllamaBootstrapService().list_installed_models() == []
