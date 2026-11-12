"""A failed temporary-file removal keeps the original settings write error."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from infiniteprep.config.settings import AppSettings, SettingsManager

ORIGINAL_TEMPLATE = "{artist} - {title}"
REPLACEMENT_TEMPLATE = "{title}"


def test_cleanup_failure_does_not_mask_the_write_error(tmp_path: Path, monkeypatch: Any) -> None:
    """The caller sees the write failure even when temporary-file removal also fails."""
    manager = SettingsManager(tmp_path / "settings.json")
    manager.save(AppSettings(rename_template=ORIGINAL_TEMPLATE))

    def fail_replace(_source: Any, _target: Any) -> None:
        raise RuntimeError("simulated write failure")

    def fail_unlink(_self: Path, missing_ok: bool = False) -> None:
        raise PermissionError("simulated cleanup failure")

    monkeypatch.setattr("infiniteprep.config.settings.os.replace", fail_replace)
    monkeypatch.setattr(Path, "unlink", fail_unlink)

    with pytest.raises(RuntimeError, match="simulated write failure"):
        manager.save(AppSettings(rename_template=REPLACEMENT_TEMPLATE))

    monkeypatch.undo()
    assert manager.load().rename_template == ORIGINAL_TEMPLATE
