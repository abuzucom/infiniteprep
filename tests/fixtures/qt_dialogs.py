"""QMessageBox replacement that records dialogs for UI action tests."""

from __future__ import annotations

from typing import Any

import pytest
from PySide6.QtWidgets import QMessageBox


class DialogRecorder:
    """Replaces QMessageBox static calls, records each call, and returns a fixed answer."""

    def __init__(self, answer: QMessageBox.StandardButton) -> None:
        """Store the answer that question() returns."""
        self.answer = answer
        self.calls: list[tuple[str, str]] = []

    def install(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Patch question, information, and warning on QMessageBox."""
        for kind in ("question", "information", "warning"):
            monkeypatch.setattr(QMessageBox, kind, self._make_handler(kind))

    def _make_handler(self, kind: str) -> Any:
        """Return a replacement that records the dialog kind and message text."""

        def handler(_parent: Any, _title: str, text: str, *_args: Any) -> Any:
            self.calls.append((kind, text))
            return self.answer if kind == "question" else QMessageBox.StandardButton.Ok

        return handler

    def texts(self, kind: str) -> list[str]:
        """Return the message texts recorded for one dialog kind."""
        return [text for recorded_kind, text in self.calls if recorded_kind == kind]
