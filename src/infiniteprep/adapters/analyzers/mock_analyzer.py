"""Mock audio analyzer adapter returning deterministic BPM and canonical key results."""

from __future__ import annotations

from pathlib import Path

from infiniteprep.domain.enums import KeyMode, PitchClass
from infiniteprep.domain.models import AnalysisResult


class MockAudioAnalyzerAdapter:
    """Offline deterministic AudioAnalyzer for unit and UI workflow tests."""

    analyzer_name: str = "mock_analyzer"
    analyzer_version: str = "0.1.0"

    def is_available(self) -> tuple[bool, str | None]:
        """Return availability status for mock audio analyzer."""
        return True, None

    def analyze(
        self, file_id_or_path: str | Path, maybe_path: Path | str | None = None
    ) -> AnalysisResult:
        """Analyze audio reference and return a deterministic 124 BPM F# minor result."""
        if isinstance(file_id_or_path, Path):
            audio_path = file_id_or_path
            file_id = str(maybe_path or "mock-file")
        else:
            file_id = str(file_id_or_path)
            audio_path = Path(maybe_path) if maybe_path is not None else Path(file_id_or_path)

        return AnalysisResult(
            id=f"ana-mock-{file_id}",
            file_id=file_id,
            analyzer_name=self.analyzer_name,
            analyzer_version=self.analyzer_version,
            configuration={"source_name": audio_path.name},
            bpm=124.0,
            key_tonic=PitchClass.F_SHARP,
            key_mode=KeyMode.MINOR,
            analysis_strength=0.91,
            warnings=[],
        )
