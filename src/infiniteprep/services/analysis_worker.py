"""Entry point of one analysis task inside a worker process.

The pool sends this function to a worker process. The spawn start method pickles it by
name, so the module path and the function name stay stable.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from infiniteprep.domain.models import AnalysisResult
from infiniteprep.domain.ports import AudioAnalyzer

# The pool sends the factory to the worker, which builds its own analyzer there. A class
# pickles by name. A lambda or a local function does not.
AnalyzerFactory = Callable[[], AudioAnalyzer]


def analyze_track(path: str, file_id: str, analyzer_factory: AnalyzerFactory) -> AnalysisResult:
    """Analyze one file and return the result to the parent process.

    The worker holds no database connection. The parent process stores the result.
    """
    analyzer = analyzer_factory()
    return analyzer.analyze(Path(path), file_id)
