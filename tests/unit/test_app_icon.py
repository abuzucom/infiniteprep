"""Behavioral tests for the application window icon (D393)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtGui import QIcon

from infiniteprep.ui import app_icon
from infiniteprep.ui.app_icon import APP_ICON_PATH, apply_application_icon, load_application_icon

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def test_the_icon_file_ships_beside_its_module() -> None:
    """The package holds the logo as a PNG file."""
    assert APP_ICON_PATH.is_file()
    assert APP_ICON_PATH.read_bytes().startswith(PNG_SIGNATURE)


def test_the_loaded_icon_holds_the_logo(qapp: Any) -> None:
    """Qt decodes the bundled PNG into a non-empty icon."""
    icon = load_application_icon()
    assert isinstance(icon, QIcon)
    assert not icon.isNull()


def test_the_application_carries_the_logo_icon(qapp: Any) -> None:
    """Every window without its own icon inherits the application icon."""
    qapp.setWindowIcon(QIcon())
    apply_application_icon(qapp)
    assert not qapp.windowIcon().isNull()


def test_a_missing_icon_file_logs_a_warning(
    qapp: Any,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A packaging layout without the PNG names the missing file in the log."""
    missing = tmp_path / "app-icon.png"
    monkeypatch.setattr(app_icon, "APP_ICON_PATH", missing)
    with caplog.at_level(logging.WARNING, logger=app_icon.__name__):
        apply_application_icon(qapp)
    assert str(missing) in caplog.text
