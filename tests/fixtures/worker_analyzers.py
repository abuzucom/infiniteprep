"""Analyzers that the analysis pool runs inside a worker process.

The classes live outside the test modules on purpose. The test tooling removes the
loaded modules whose name starts with ``test_`` from ``sys.modules``. A class in such a
module then pickles into a different object, and the worker never receives it.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from infiniteprep.domain.models import AnalysisResult

# The analyzer stops the worker process on the file with this name.
CRASHING_FILE_NAME = "b_crash.wav"
# The exit code of the stopped worker process.
CRASH_EXIT_CODE = 9
# The slow analyzer sleeps for this many seconds. A cancel then reaches the worker
# before the result arrives.
SLOW_SLEEP_SECONDS = 20.0
# The counter file records one line per analysis call.
COUNTER_ENVIRONMENT_VARIABLE = "INFINITEPREP_ANALYSIS_COUNTER"


def record_call(audio_reference: Path) -> None:
    """Write one line to the counter file, so a test can count the analyses."""
    counter_path = os.environ.get(COUNTER_ENVIRONMENT_VARIABLE)
    if not counter_path:
        return
    with open(counter_path, "a", encoding="utf-8") as handle:
        handle.write(f"{Path(audio_reference).name}\n")


def _result(file_id: str, analyzer_name: str) -> AnalysisResult:
    """Return one analysis result for a file."""
    return AnalysisResult(
        id=file_id or "result",
        file_id=file_id,
        analyzer_name=analyzer_name,
        analyzer_version="1.0.0",
        configuration={},
        bpm=128.0,
        key_tonic=None,
        key_mode=None,
        analysis_strength=1.0,
    )


class CrashingAnalyzer:
    """Analyzer that stops the worker process on one named file."""

    analyzer_name = "crashing"
    analyzer_version = "1.0.0"

    def is_available(self) -> tuple[bool, str | None]:
        """Return the availability of the analyzer."""
        return True, None

    def analyze(self, audio_reference: Path, file_id: str = "") -> AnalysisResult:
        """Stop the worker process on the named file, and analyze the other files."""
        record_call(audio_reference)
        if Path(audio_reference).name == CRASHING_FILE_NAME:
            os._exit(CRASH_EXIT_CODE)
        return _result(file_id, self.analyzer_name)


class SlowAnalyzer:
    """Analyzer that keeps the worker busy, so a cancel reaches it in time."""

    analyzer_name = "slow"
    analyzer_version = "1.0.0"

    def is_available(self) -> tuple[bool, str | None]:
        """Return the availability of the analyzer."""
        return True, None

    def analyze(self, audio_reference: Path, file_id: str = "") -> AnalysisResult:
        """Sleep before the result, so the worker runs when the cancel arrives."""
        time.sleep(SLOW_SLEEP_SECONDS)
        return _result(file_id, self.analyzer_name)


class FailingSlowAnalyzer:
    """Analyzer that fails at once on a missing file and keeps the worker busy otherwise."""

    analyzer_name = "failing_slow"
    analyzer_version = "1.0.0"

    def is_available(self) -> tuple[bool, str | None]:
        """Return the availability of the analyzer."""
        return True, None

    def analyze(self, audio_reference: Path, file_id: str = "") -> AnalysisResult:
        """Fail at once on a missing file. Sleep on every other file."""
        path = Path(audio_reference)
        if not path.exists():
            raise FileNotFoundError(f"No such file: {path}")
        time.sleep(SLOW_SLEEP_SECONDS)
        return _result(file_id, self.analyzer_name)
