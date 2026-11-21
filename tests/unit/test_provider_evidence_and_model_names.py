"""Tests that AcoustID evidence holds only checked fields and that model names pass the grammar."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from infiniteprep.adapters.fingerprint.chromaprint_acoustid import ChromaprintAcoustIDAdapter
from infiniteprep.adapters.llm import ollama_bootstrap
from infiniteprep.adapters.llm.ollama_bootstrap import OllamaBootstrapService
from infiniteprep.adapters.provider_fields import MAX_PROVIDER_TEXT_CHARS
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from tests.fixtures.fake_http import FakeResponse

SAMPLE_DURATION_SEC = 480.0
SAMPLE_FINGERPRINT = "AQAA_TEST"
ACOUSTID_STUB_KEY = "synthetic"
EVIDENCE_KEYS = {"id", "title", "duration", "artists", "releasegroup_ids"}
LONG_TITLE = "t" * (MAX_PROVIDER_TEXT_CHARS * 3)


def test_acoustid_evidence_keeps_only_checked_fields(tmp_path: Path) -> None:
    """Unknown keys and nested objects stay out of the stored evidence."""
    recording: dict[str, Any] = {
        "id": "rec-1",
        "title": LONG_TITLE,
        "duration": SAMPLE_DURATION_SEC,
        "artists": [{"name": "Deep Dish"}],
        "releasegroups": [{"id": "rg-1"}],
        "unexpected": {"nested": ["x" * MAX_PROVIDER_TEXT_CHARS]},
    }
    payload = {"results": [{"id": "acoustid-1", "score": 0.9, "recordings": [recording]}]}
    adapter = ChromaprintAcoustIDAdapter(
        repository=Repository(DatabaseManager(":memory:")),
        api_key=ACOUSTID_STUB_KEY,
        fpcalc_runner=lambda _path: (SAMPLE_DURATION_SEC, SAMPLE_FINGERPRINT),
        http_get_json=lambda _url, _params: payload,
    )

    result = adapter.identify(tmp_path / "track.wav")

    stored = result.matches[0].raw_evidence["recording"]
    assert set(stored) == EVIDENCE_KEYS
    assert stored["artists"] == ["Deep Dish"]
    assert stored["releasegroup_ids"] == ["rg-1"]
    assert len(stored["title"]) == MAX_PROVIDER_TEXT_CHARS


def test_ollama_model_list_keeps_only_safe_references_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Names outside the model reference grammar drop out, and repeats appear once."""
    body = (
        b'{"models": [{"name": "qwen2.5:3b"}, {"name": "--help:x"}, {"name": "no-tag"},'
        b' {"name": "../escape:1"}, {"name": "bad name:1"}, {"name": "qwen2.5:3b"},'
        b' {"name": "hf.co/user/model:q4"}]}'
    )
    monkeypatch.setattr(
        ollama_bootstrap.urllib.request, "urlopen", lambda *_a, **_k: FakeResponse(body)
    )

    assert OllamaBootstrapService().list_installed_models() == [
        "qwen2.5:3b",
        "hf.co/user/model:q4",
    ]
