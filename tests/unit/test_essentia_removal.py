"""Behavioral tests for the Essentia adapter removal and stored settings that still name it."""

from __future__ import annotations

import importlib.util
import json
from dataclasses import fields
from pathlib import Path

from infiniteprep.config.settings import AppSettings, SettingsManager

ESSENTIA_SETTING_KEYS = ("prefer_essentia_cli", "essentia_cli_path")


def test_essentia_adapter_module_is_gone() -> None:
    """The source tree no longer holds the Essentia subprocess adapter."""
    assert importlib.util.find_spec("infiniteprep.adapters.analyzers.essentia_ext") is None


def test_app_settings_has_no_essentia_fields() -> None:
    """AppSettings no longer declares the two Essentia fields."""
    names = {field.name for field in fields(AppSettings)}
    assert names.isdisjoint(ESSENTIA_SETTING_KEYS)


def test_old_settings_file_with_essentia_fields_loads(tmp_path: Path) -> None:
    """A settings file saved before the removal loads, and its other values survive."""
    path = tmp_path / "settings.json"
    stored = {
        "prefer_essentia_cli": True,
        "essentia_cli_path": "/opt/essentia/essentia_streaming_extractor_music",
        "theme": "light_blue.xml",
    }
    path.write_text(json.dumps(stored), encoding="utf-8")

    settings = SettingsManager(path).load()

    assert settings.theme == "light_blue.xml"


def test_saving_old_settings_drops_essentia_fields(tmp_path: Path) -> None:
    """The next save after loading an old settings file writes no Essentia keys."""
    path = tmp_path / "settings.json"
    path.write_text(json.dumps({"prefer_essentia_cli": True}), encoding="utf-8")
    manager = SettingsManager(path)

    manager.save(manager.load())

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert set(saved).isdisjoint(ESSENTIA_SETTING_KEYS)
