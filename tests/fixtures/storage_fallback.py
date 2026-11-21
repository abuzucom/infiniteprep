"""Shared helpers for the storage location and fallback prompt tests (D83a1a, D280 to D292)."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from infiniteprep import storage_paths
from infiniteprep.storage_paths import FALLBACK_LIST_NAME, FallbackAnswer

# Each per-user folder maps to its own subfolder of the temp tree.
USER_DIR_FUNCTIONS = {
    "user_data_dir": "data",
    "user_config_dir": "config",
    "user_cache_dir": "cache",
}


class RecordingConfirm:
    """Return a fixed answer and record each folder it was asked about."""

    def __init__(self, answer: FallbackAnswer) -> None:
        """Store the answer and start with no recorded folders."""
        self.answer = answer
        self.asked: list[Path] = []

    def __call__(self, folder: Path) -> FallbackAnswer:
        """Record the folder and return the fixed answer."""
        self.asked.append(folder)
        return self.answer


def build_fixed_folder(folder: Path) -> Callable[..., str]:
    """Return a platformdirs stand-in that ignores its arguments and names one folder."""

    def return_folder(*_arguments: object) -> str:
        """Return the fixed folder as a string, as platformdirs does."""
        return str(folder)

    return return_folder


def redirect_user_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point the per-user folders at a temp tree and return its root."""
    root = tmp_path / "user"
    for function_name, subfolder in USER_DIR_FUNCTIONS.items():
        monkeypatch.setattr(
            storage_paths.platformdirs, function_name, build_fixed_folder(root / subfolder)
        )
    return root


def read_fallback_list(user_dirs: Path) -> list[str]:
    """Return the remembered folders, or an empty list when the file is absent."""
    path = user_dirs / "config" / FALLBACK_LIST_NAME
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def build_never_asked_confirm() -> RecordingConfirm:
    """Return a prompt that would quit, so a test fails loudly if it is shown."""
    return RecordingConfirm(FallbackAnswer(use_per_user=False, remember=False))
