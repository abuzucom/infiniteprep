"""Behavioral tests for the smoke check's multimedia backend check (D313)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from PySide6.QtMultimedia import QMediaPlayer

from infiniteprep import __version__
from infiniteprep.smoke_check import (
    BACKEND_ERROR_MESSAGE,
    SMOKE_CHECK_BACKEND_ERROR,
    run_smoke_check,
)


class UnavailablePlayer:
    """A player whose backend failed to load, as when the FFmpeg plugin is missing.

    It stands in only for QMediaPlayer, at the Qt boundary. The check under test runs
    unchanged.
    """

    deleted: bool = False

    def isAvailable(self) -> bool:  # Qt's own method name.
        """Report that no multimedia backend loaded."""
        return False

    def deleteLater(self) -> None:  # Qt's own method name.
        """Record the scheduled deletion."""
        self.deleted = True


def test_real_backend_is_available_and_the_version_is_written(qapp: Any, tmp_path: Path) -> None:
    """With the installed PySide6, a real QMediaPlayer has a backend, and the check passes."""
    player = QMediaPlayer()
    assert player.isAvailable()
    player.deleteLater()
    result_file = tmp_path / "smoke-version.txt"

    exit_code = run_smoke_check(result_file)

    assert exit_code == 0
    assert result_file.read_text(encoding="utf-8") == __version__


def test_missing_backend_fails_before_any_file_is_written(
    qapp: Any, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Without a backend the check returns the backend error, writes nothing, and says why."""
    players: list[UnavailablePlayer] = []

    def make_player() -> UnavailablePlayer:
        player = UnavailablePlayer()
        players.append(player)
        return player

    result_file = tmp_path / "smoke-version.txt"

    exit_code = run_smoke_check(result_file, player_factory=make_player)

    assert exit_code == SMOKE_CHECK_BACKEND_ERROR
    assert not result_file.exists()
    assert BACKEND_ERROR_MESSAGE in capsys.readouterr().err
    assert [player.deleted for player in players] == [True]
