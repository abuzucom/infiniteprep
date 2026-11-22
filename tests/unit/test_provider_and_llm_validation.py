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
from infiniteprep.domain.ports import MetadataSearchQuery
from tests.fixtures.call_recorder import CallRecorder
from tests.fixtures.fake_http import FakeResponse
from tests.fixtures.memory_repository import build_memory_repository

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


def _llm_reply(content: str) -> Any:
    """Return a fake chat completion endpoint that answers with content."""

    def post(_url: str, _body: dict[str, Any]) -> dict[str, Any]:
        return {"choices": [{"message": {"content": content}}]}

    return post


def _adapter(post: Any) -> OllamaOpenAIReasoningAdapter:
    """Return a reasoning adapter on the loopback endpoint with a fake transport."""
    return OllamaOpenAIReasoningAdapter(base_url="http://127.0.0.1:11434/v1", http_post_json=post)


def test_validator_rejects_mismatched_pair() -> None:
    """The ID of one candidate and the value of another never pass together."""
    output = {"selected_candidate_id": "cand-1", "proposed_value": "Danny Tenaglia Dub"}

    is_valid, error = ClosedWorldValidator().validate_response(output, ALLOWED)

    assert is_valid is False
    assert error is not None
    assert "does not belong" in error


def test_rejects_value_when_none_have_values() -> None:
    """A proposed value with no candidate value to match counts as invented."""
    allowed = [{"id": "cand-1"}, {"id": "cand-2"}]

    is_valid, _ = ClosedWorldValidator().validate_response(
        {"proposed_value": "Invented Mix"}, allowed
    )

    assert is_valid is False


def test_rejects_value_on_valueless_candidate() -> None:
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
def test_validator_rejects_bad_types_sizes(output: dict[str, Any]) -> None:
    """A non-string field or a field above the length cap fails validation."""
    is_valid, _ = ClosedWorldValidator().validate_response(output, ALLOWED)

    assert is_valid is False


def test_validator_accepts_pair_id_or_value() -> None:
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


def test_adapter_returns_value_and_rationale() -> None:
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


def test_adapter_failure_names_only_class() -> None:
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


def test_provider_text_drops_controls_and_caps() -> None:
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
        build_memory_repository(),
        user_token=DISCOGS_STUB_KEY,
        http_get_json=CallRecorder(reply=payload),
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


def test_discogs_never_caches_non_object() -> None:
    """A JSON array response fails the lookup before the cache write, so a retry asks again."""
    requested: list[str] = []

    def array_response(url: str, _params: dict[str, str]) -> Any:
        requested.append(url)
        return ["unexpected"]

    adapter = DiscogsAdapter(
        build_memory_repository(),
        user_token=DISCOGS_STUB_KEY,
        http_get_json=array_response,
        min_interval_sec=0.0,
    )
    query = MetadataSearchQuery(artist="Deep Dish")

    assert adapter.search(query) == []
    assert adapter.search(query) == []
    assert len(requested) == 2


def test_musicbrainz_caps_text_skips_text_length() -> None:
    """A long title stops at the cap, and a length given as text reads as no duration."""
    payload = {"recordings": [{"id": "rec-1", "title": LONG_TEXT, "length": "480000"}]}
    adapter = MusicBrainzAdapter(
        build_memory_repository(), http_get_json=CallRecorder(reply=payload), min_interval_sec=0.0
    )

    candidates = adapter.search(MetadataSearchQuery(title="The Dream"))

    assert len(candidates) == 1
    assert len(candidates[0].title or "") == MAX_PROVIDER_TEXT_CHARS
    assert candidates[0].duration is None


def test_acoustid_rejects_bad_score_and_items(tmp_path: Path) -> None:
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
        repository=build_memory_repository(),
        api_key="key",
        fpcalc_runner=CallRecorder(reply=(SAMPLE_DURATION_SEC, SAMPLE_FINGERPRINT)),
        http_get_json=CallRecorder(reply=payload),
    )

    result = adapter.identify(tmp_path / "track.wav")

    assert len(result.matches) == 1
    assert result.matches[0].confidence == 0.0
    assert result.matches[0].artist == "3"
    assert result.matches[0].title == "The Dream"


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
        ollama_bootstrap.urllib.request, "urlopen", CallRecorder(reply=FakeResponse(body))
    )

    assert OllamaBootstrapService().list_installed_models() == expected


def test_ollama_model_list_refuses_big_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """A tag response larger than the cap returns no models."""
    monkeypatch.setattr(ollama_bootstrap, "MAX_TAGS_RESPONSE_BYTES", len(TAGS_TOO_LARGE) - 1)
    monkeypatch.setattr(
        ollama_bootstrap.urllib.request,
        "urlopen",
        CallRecorder(reply=FakeResponse(TAGS_TOO_LARGE)),
    )

    assert OllamaBootstrapService().list_installed_models() == []
