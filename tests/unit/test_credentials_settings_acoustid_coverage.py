"""Focused coverage for credential failures, endpoint rules, settings, and AcoustID cache paths."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from infiniteprep.adapters.fingerprint.chromaprint_acoustid import ChromaprintAcoustIDAdapter
from infiniteprep.config.settings import AppSettings, SettingsManager
from tests.fixtures.memory_repository import build_memory_repository

STUB_KEY = "synthetic"
STUB_FINGERPRINT = "AQAA_TEST"


def test_settings_load_validates_types_choices_and_ranges(tmp_path: Path) -> None:
    """Persisted settings replace invalid values with their field defaults."""
    path = tmp_path / "settings.json"
    path.write_text(
        json.dumps(
            {
                "enable_acoustid": "yes",
                "waveform_zoom_seconds": 7,
                "fake_lossless_method": "unknown",
                "tag_key_notation": "invalid",
                "unsorted_rows_placement": "middle",
                "text_size": "huge",
                "tag_bpm_decimals": 9,
                "file_table_column_count": -1,
                "usb_reserve_percent": float("nan"),
                "usb_reserve_bytes": -1,
                "usb_recovery_expiry_days": 0,
                "theme": "light_teal.xml",
            }
        ),
        encoding="utf-8",
    )

    loaded = SettingsManager(path).load()
    defaults = AppSettings()

    assert loaded.enable_acoustid == defaults.enable_acoustid
    assert loaded.waveform_zoom_seconds == defaults.waveform_zoom_seconds
    assert loaded.fake_lossless_method == defaults.fake_lossless_method
    assert loaded.tag_key_notation == defaults.tag_key_notation
    assert loaded.unsorted_rows_placement == defaults.unsorted_rows_placement
    assert loaded.text_size == defaults.text_size
    assert loaded.tag_bpm_decimals == defaults.tag_bpm_decimals
    assert loaded.file_table_column_count == defaults.file_table_column_count
    assert loaded.usb_reserve_percent == defaults.usb_reserve_percent
    assert loaded.usb_reserve_bytes == defaults.usb_reserve_bytes
    assert loaded.usb_recovery_expiry_days == defaults.usb_recovery_expiry_days
    assert loaded.theme == "infiniteprep_light.xml"


def test_settings_load_handles_missing_malformed_and_non_object_files(tmp_path: Path) -> None:
    """Missing, malformed, and non-object settings files use the defaults."""
    manager = SettingsManager(tmp_path / "settings.json")
    assert manager.load() == AppSettings()

    manager.settings_path.write_text("not json", encoding="utf-8")
    assert manager.load() == AppSettings()

    manager.settings_path.write_text("[]", encoding="utf-8")
    assert manager.load() == AppSettings()


def test_settings_update_rejects_unknown_and_invalid_values(tmp_path: Path) -> None:
    """Update rejects unknown fields and invalid values without writing them."""
    path = tmp_path / "settings.json"
    manager = SettingsManager(path)

    with pytest.raises(ValueError, match="Unknown setting"):
        manager.update(unknown_setting=1)
    with pytest.raises(ValueError, match="unsupported waveform"):
        manager.update(waveform_zoom_seconds=7)
    with pytest.raises(ValueError, match="negative count"):
        manager.update(file_table_column_count=-1)
    assert not path.exists()


def test_settings_update_persists_a_valid_value(tmp_path: Path) -> None:
    """A valid update persists and retains unrelated settings."""
    path = tmp_path / "settings.json"
    manager = SettingsManager(path)
    manager.save(AppSettings(rename_template="custom"))

    updated = manager.update(waveform_zoom_seconds=16)

    assert updated.waveform_zoom_seconds == 16
    assert manager.load().rename_template == "custom"


class FakeBootstrap:
    """Return a controlled fpcalc path for adapter tests."""

    def __init__(self, path: Path | None) -> None:
        self.path = path

    def resolve_fpcalc_path(self) -> Path | None:
        """Return the configured fpcalc path."""
        return self.path


def test_acoustid_availability_requires_key_and_fpcalc(tmp_path: Path) -> None:
    """The adapter reports each missing prerequisite separately."""
    missing_key = ChromaprintAcoustIDAdapter(
        repository=build_memory_repository(),
        bootstrap_service=FakeBootstrap(tmp_path / "fpcalc"),
    )
    assert missing_key.is_available()[0] is False
    assert "API key" in missing_key.is_available()[1]

    missing_binary = ChromaprintAcoustIDAdapter(
        repository=build_memory_repository(),
        api_key=STUB_KEY,
        bootstrap_service=FakeBootstrap(None),
    )
    assert missing_binary.is_available()[0] is False
    assert "fpcalc" in missing_binary.is_available()[1]

    ready = ChromaprintAcoustIDAdapter(
        repository=build_memory_repository(),
        api_key=STUB_KEY,
        bootstrap_service=FakeBootstrap(tmp_path / "fpcalc"),
    )
    assert ready.is_available() == (True, None)


def test_acoustid_identify_returns_empty_result_when_unavailable(tmp_path: Path) -> None:
    """An unavailable adapter returns no fingerprint and one warning."""
    adapter = ChromaprintAcoustIDAdapter(repository=build_memory_repository())

    result = adapter.identify(tmp_path / "track.wav")

    assert result.raw_fingerprint is None
    assert result.matches == []
    assert len(result.warnings) == 1


@pytest.mark.parametrize(
    "error",
    [OSError("missing"), ValueError("bad output"), subprocess.SubprocessError("failed")],
)
def test_acoustid_reports_fpcalc_errors(error: Exception) -> None:
    """Every supported fpcalc failure produces a warning without matches."""

    def failing_runner(_path: Path) -> tuple[float, str]:
        raise error

    adapter = ChromaprintAcoustIDAdapter(
        repository=build_memory_repository(),
        api_key=STUB_KEY,
        fpcalc_runner=failing_runner,
    )

    result = adapter.identify(Path("track.wav"))

    assert result.raw_fingerprint is None
    assert result.matches == []
    assert "fpcalc execution failed" in result.warnings[0]


def test_acoustid_lookup_failure_keeps_fingerprint(tmp_path: Path) -> None:
    """A lookup failure keeps the computed fingerprint and reports no API key."""

    def failing_http(_url: str, _params: dict[str, str]) -> dict[str, Any]:
        raise OSError("offline")

    adapter = ChromaprintAcoustIDAdapter(
        repository=build_memory_repository(),
        api_key=STUB_KEY,
        fpcalc_runner=lambda _path: (240.0, STUB_FINGERPRINT),
        http_get_json=failing_http,
    )

    result = adapter.identify(tmp_path / "track.wav")

    assert result.raw_fingerprint == STUB_FINGERPRINT
    assert result.duration == 240.0
    assert result.matches == []
    assert "AcoustID lookup failed" in result.warnings[0]
    assert STUB_KEY not in result.warnings[0]


def test_acoustid_cache_serves_repeated_lookup(tmp_path: Path) -> None:
    """A cache hit avoids the HTTP request and returns the stored payload."""
    repository = build_memory_repository()
    calls: list[str] = []

    def http_get_json(url: str, _params: dict[str, str]) -> dict[str, Any]:
        calls.append(url)
        return {"results": []}

    adapter = ChromaprintAcoustIDAdapter(
        repository=repository,
        api_key=STUB_KEY,
        fpcalc_runner=lambda _path: (240.0, STUB_FINGERPRINT),
        http_get_json=http_get_json,
    )

    first = adapter.identify(tmp_path / "track.wav")
    second = adapter.identify(tmp_path / "track.wav")

    assert first.raw_fingerprint == STUB_FINGERPRINT
    assert second.raw_fingerprint == STUB_FINGERPRINT
    assert len(calls) == 1


def test_acoustid_does_not_cache_non_object_response(tmp_path: Path) -> None:
    """A malformed response fails the lookup and remains outside the cache."""
    calls: list[str] = []

    def http_get_json(url: str, _params: dict[str, str]) -> Any:
        calls.append(url)
        return ["not an object"]

    adapter = ChromaprintAcoustIDAdapter(
        repository=build_memory_repository(),
        api_key=STUB_KEY,
        fpcalc_runner=lambda _path: (240.0, STUB_FINGERPRINT),
        http_get_json=http_get_json,
    )

    first = adapter.identify(tmp_path / "track.wav")
    second = adapter.identify(tmp_path / "track.wav")

    assert first.matches == []
    assert second.matches == []
    assert len(calls) == 2


def test_acoustid_default_fpcalc_subprocess_parses_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The default fpcalc seam parses duration and fingerprint from JSON output."""
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"duration": 12.5, "fingerprint": "AQ"}',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", run)
    adapter = ChromaprintAcoustIDAdapter(
        repository=build_memory_repository(),
        api_key=STUB_KEY,
        bootstrap_service=FakeBootstrap(tmp_path / "fpcalc"),
        http_get_json=lambda _url, _params: {"results": []},
    )

    result = adapter.identify(tmp_path / "track.wav")

    assert result.duration == 12.5
    assert result.raw_fingerprint == "AQ"
    assert calls[0][0].endswith("fpcalc")


def test_acoustid_parses_scores_artists_and_release_groups(tmp_path: Path) -> None:
    """The response parser preserves valid evidence and drops unsafe scores."""
    payload = {
        "results": [
            {
                "id": "acoustid-1",
                "score": 1.5,
                "recordings": [
                    {
                        "id": "recording-1",
                        "title": "Track",
                        "duration": 12,
                        "artists": [{"name": "Artist"}, {"name": 7}],
                        "releasegroups": [{"id": "group-1"}, "invalid"],
                    }
                ],
            },
            {"score": 0.5, "recordings": "invalid"},
        ]
    }
    adapter = ChromaprintAcoustIDAdapter(
        repository=build_memory_repository(),
        api_key=STUB_KEY,
        fpcalc_runner=lambda _path: (12.0, STUB_FINGERPRINT),
        http_get_json=lambda _url, _params: payload,
    )

    result = adapter.identify(tmp_path / "track.wav")

    assert len(result.matches) == 1
    match = result.matches[0]
    assert match.confidence == 0.0
    assert match.artist == "Artist, 7"
    assert match.recording_ids == ["recording-1"]
    assert match.release_group_ids == ["group-1"]
    assert match.raw_evidence["acoustid_id"] == "acoustid-1"


def test_acoustid_missing_default_fpcalc_is_reported(tmp_path: Path) -> None:
    """A missing default fpcalc binary becomes a warning through the normal path."""
    adapter = ChromaprintAcoustIDAdapter(
        repository=build_memory_repository(),
        api_key=STUB_KEY,
        bootstrap_service=FakeBootstrap(None),
        http_get_json=lambda _url, _params: {"results": []},
    )

    result = adapter.identify(tmp_path / "track.wav")

    assert result.matches == []
    assert "fpcalc binary is missing" in result.warnings[0]
