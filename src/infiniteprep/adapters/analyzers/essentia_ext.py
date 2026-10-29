"""Optional external Essentia CLI extractor adapter invoked in an isolated subprocess."""

from __future__ import annotations

import json
import subprocess
import tempfile
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from infiniteprep.domain.enums import parse_key_string
from infiniteprep.domain.models import AnalysisResult


class EssentiaCliAnalyzer:
    """Runs external essentia_streaming_extractor_music binary via subprocess (shell=False)."""

    analyzer_name: str = "essentia_cli"
    analyzer_version: str = "2.1"

    def __init__(
        self,
        cli_path: Path | None = None,
        runner: Callable[[list[str]], None] | None = None,
    ) -> None:
        """Initialize EssentiaCliAnalyzer with an explicit binary path and optional test runner."""
        self.cli_path = cli_path
        self.runner = runner

    def is_available(self) -> tuple[bool, str | None]:
        """Return True if the external Essentia CLI binary exists and has expected name."""
        if self.cli_path and self.cli_path.is_file():
            if self.cli_path.name.lower().startswith("essentia") or self.runner is not None:
                return True, None
            return False, "Configured Essentia CLI binary name must start with 'essentia'."
        return (
            False,
            "External Essentia CLI binary not configured; using built-in LocalDspAnalyzer.",
        )

    def analyze(self, audio_reference: Path, file_id: str = "") -> AnalysisResult:
        """Execute Essentia extractor into a temporary JSON file and parse BPM and key."""
        ready, reason = self.is_available()
        if not ready or self.cli_path is None:
            return AnalysisResult(
                id=str(uuid.uuid4()),
                file_id=file_id,
                analyzer_name=self.analyzer_name,
                analyzer_version=self.analyzer_version,
                configuration={},
                bpm=None,
                key_tonic=None,
                key_mode=None,
                analysis_strength=0.0,
                warnings=[reason or "Essentia CLI unavailable."],
            )

        with tempfile.TemporaryDirectory() as tmp_dir:
            out_json = Path(tmp_dir) / "essentia_out.json"
            cmd = [str(self.cli_path), str(audio_reference), str(out_json)]
            if self.runner is not None:
                self.runner(cmd)
            else:
                subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=60,
                    check=True,
                    shell=False,
                )
            payload = json.loads(out_json.read_text(encoding="utf-8"))
        return self._parse_essentia_json(payload, file_id)

    def _parse_essentia_json(self, payload: dict[str, Any], file_id: str) -> AnalysisResult:
        """Parse BPM and canonical key from Essentia JSON output."""
        rhythm = payload.get("rhythm", {})
        tonal = payload.get("tonal", {})
        key_block = tonal.get("key_temperley") or tonal.get("key_edma") or {}
        raw_bpm = rhythm.get("bpm")
        bpm = round(float(raw_bpm), 1) if raw_bpm is not None else None

        raw_key_str = f"{key_block.get('key', '')} {key_block.get('scale', '')}".strip()
        parsed_key = parse_key_string(raw_key_str)
        tonic = parsed_key[0] if parsed_key else None
        mode = parsed_key[1] if parsed_key else None
        strength = float(key_block["strength"]) if "strength" in key_block else None

        return AnalysisResult(
            id=str(uuid.uuid4()),
            file_id=file_id,
            analyzer_name=self.analyzer_name,
            analyzer_version=self.analyzer_version,
            configuration={"cli_path": str(self.cli_path)},
            bpm=bpm,
            key_tonic=tonic,
            key_mode=mode,
            analysis_strength=strength,
            warnings=[],
        )
