"""Analyzer that stops the worker process, to test a crashed analysis worker.

The class lives outside the test modules on purpose. The test tooling removes the
loaded modules whose name starts with ``test_`` from ``sys.modules``. A class in such a
module then pickles into a different object, and the worker never receives it.
"""

from __future__ import annotations

import os
from pathlib import Path

from infiniteprep.domain.models import AnalysisResult

# The analyzer stops the worker process on the file with this name.
CRASHING_FILE_NAME = "b_crash.wav"
# The exit code of the stopped worker process.
CRASH_EXIT_CODE = 9


class CrashingAnalyzer:
    """Analyzer that stops the worker process on one named file."""

    analyzer_name = "crashing"
    analyzer_version = "1.0.0"

    def is_available(self) -> tuple[bool, str | None]:
        """Return the availability of the analyzer."""
        return True, None

    def analyze(self, audio_reference: Path, file_id: str = "") -> AnalysisResult:
        """Stop the worker process on the named file, and analyze the other files."""
        if Path(audio_reference).name == CRASHING_FILE_NAME:
            os._exit(CRASH_EXIT_CODE)
        return AnalysisResult(
            id=file_id or "result",
            file_id=file_id,
            analyzer_name=self.analyzer_name,
            analyzer_version=self.analyzer_version,
            configuration={},
            bpm=128.0,
            key_tonic=None,
            key_mode=None,
            analysis_strength=1.0,
        )
