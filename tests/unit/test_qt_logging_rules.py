"""Behavioral tests for the Qt logging rule that silences FFmpeg output."""

from __future__ import annotations

from infiniteprep.qt_logging import (
    QT_LOGGING_RULES_VAR,
    SILENCE_FFMPEG_RULE,
    silence_ffmpeg_logs,
)

USER_RULE = "qt.network.ssl.warning=false"


def test_silence_sets_the_rule_when_none_exist() -> None:
    """With no rules set, the FFmpeg rule becomes the only rule."""
    environ: dict[str, str] = {}

    silence_ffmpeg_logs(environ)

    assert environ[QT_LOGGING_RULES_VAR] == SILENCE_FFMPEG_RULE


def test_silence_keeps_rules_already_set() -> None:
    """Rules set before launch stay first, and the FFmpeg rule follows them."""
    environ = {QT_LOGGING_RULES_VAR: USER_RULE}

    silence_ffmpeg_logs(environ)

    assert environ[QT_LOGGING_RULES_VAR] == f"{USER_RULE};{SILENCE_FFMPEG_RULE}"


def test_silence_twice_adds_the_rule_once() -> None:
    """A second call leaves the rules unchanged."""
    environ = {QT_LOGGING_RULES_VAR: f"{USER_RULE};"}

    first = silence_ffmpeg_logs(environ)
    second = silence_ffmpeg_logs(environ)

    assert first == second == f"{USER_RULE};{SILENCE_FFMPEG_RULE}"
