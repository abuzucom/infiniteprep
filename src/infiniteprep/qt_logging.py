"""Qt logging rules the app applies before QApplication starts."""

from __future__ import annotations

import os
from collections.abc import MutableMapping
from typing import Final

QT_LOGGING_RULES_VAR: Final[str] = "QT_LOGGING_RULES"
# Qt reads QT_LOGGING_RULES as rules separated by semicolons.
QT_RULE_SEPARATOR: Final[str] = ";"
# Qt Multimedia's FFmpeg backend logs every decoder message to the console.
SILENCE_FFMPEG_RULE: Final[str] = "qt.multimedia.ffmpeg*=false"


def add_qt_logging_rule(environ: MutableMapping[str, str], rule: str) -> str:
    """Add rule to QT_LOGGING_RULES in environ and return the new value.

    Rules already set stay in place and come first. A rule already present is not added
    again.
    """
    current = environ.get(QT_LOGGING_RULES_VAR, "")
    rules = [part.strip() for part in current.split(QT_RULE_SEPARATOR) if part.strip()]
    if rule not in rules:
        rules.append(rule)
    updated = QT_RULE_SEPARATOR.join(rules)
    environ[QT_LOGGING_RULES_VAR] = updated
    return updated


def silence_ffmpeg_logs(environ: MutableMapping[str, str] | None = None) -> str:
    """Turn off Qt Multimedia FFmpeg logging. Call before QApplication is created."""
    target = os.environ if environ is None else environ
    return add_qt_logging_rule(target, SILENCE_FFMPEG_RULE)
