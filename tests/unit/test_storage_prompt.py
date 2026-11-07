"""Behavioral tests for the read-only portable folder prompt (D258)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from PySide6.QtCore import Qt

from infiniteprep.storage_paths import FallbackAnswer
from infiniteprep.ui.storage_prompt import QUIT_LABEL, build_fallback_prompt

FOLDER = Path("<b>Music</b>") / "InfinitePrep"


def _quit_button(prompt: Any) -> Any:
    """Return the Quit button of the prompt."""
    [button] = [b for b in prompt.box.buttons() if b.text() == QUIT_LABEL]
    return button


def test_prompt_shows_the_folder_as_plain_text(qtbot: Any) -> None:
    """The folder path shows literally, and Quit is the default button."""
    prompt = build_fallback_prompt(FOLDER)
    qtbot.addWidget(prompt.box)

    assert prompt.box.textFormat() == Qt.TextFormat.PlainText
    assert str(FOLDER) in prompt.box.text()
    assert prompt.box.defaultButton() is _quit_button(prompt)


def test_use_profile_with_remember_checked(qtbot: Any) -> None:
    """Clicking Use my user profile with Remember checked asks to remember."""
    prompt = build_fallback_prompt(FOLDER)
    qtbot.addWidget(prompt.box)
    prompt.remember_checkbox.setChecked(True)

    prompt.use_per_user_button.click()

    assert prompt.answer() == FallbackAnswer(use_per_user=True, remember=True)


def test_use_profile_without_remember(qtbot: Any) -> None:
    """Clicking Use my user profile alone falls back for this run only."""
    prompt = build_fallback_prompt(FOLDER)
    qtbot.addWidget(prompt.box)

    prompt.use_per_user_button.click()

    assert prompt.answer() == FallbackAnswer(use_per_user=True, remember=False)


def test_quit_never_remembers(qtbot: Any) -> None:
    """Quit ignores the Remember checkbox."""
    prompt = build_fallback_prompt(FOLDER)
    qtbot.addWidget(prompt.box)
    prompt.remember_checkbox.setChecked(True)

    _quit_button(prompt).click()

    assert prompt.answer() == FallbackAnswer(use_per_user=False, remember=False)
