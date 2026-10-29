"""Mock audio fingerprint adapter for deterministic offline development and testing."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from infiniteprep.adapters.metadata_io.mutagen_adapter import compute_sha256
from infiniteprep.domain.enums import EntityType
from infiniteprep.domain.models import CandidateMatch, ReleaseDetails
from infiniteprep.domain.ports import FingerprintMatchItem, FingerprintResult


@dataclass
class MockFingerprintLookupResponse:
    """Convenience container holding fingerprint string and CandidateMatch list."""

    fingerprint: str
    candidates: list[CandidateMatch]
    result: FingerprintResult


class MockFingerprintAdapter:
    """Offline mock FingerprintProvider returning deterministic candidate recordings."""

    provider_name: str = "acoustid_mock"
    is_cloud_opt_in: bool = False

    def is_available(self) -> tuple[bool, str | None]:
        """Return availability status for mock fingerprint provider."""
        return True, None

    def identify(self, audio_reference: Path, file_id: str | None = None) -> FingerprintResult:
        """Compute deterministic mock fingerprint and return a FingerprintResult."""
        digest = compute_sha256(audio_reference)[:16]
        fp_str = f"MOCK_FP_{digest}"
        stem = audio_reference.stem
        item = FingerprintMatchItem(
            fingerprint_id=f"acid-{digest[:8]}",
            confidence=0.92,
            recording_ids=[f"mb-rec-{digest[:8]}"],
            artist="Deep Dish",
            title=stem,
            mix_name="Original 12-Inch Mix",
            duration=480.0,
            release_group_ids=[f"mb-rg-{digest[:8]}"],
            raw_evidence={"source": "mock_fp", "file": audio_reference.name},
        )
        return FingerprintResult(
            provider=self.provider_name,
            raw_fingerprint=fp_str,
            duration=480.0,
            matches=[item],
            warnings=[],
        )

    def fingerprint_and_lookup(
        self, audio_reference: Path, file_id: str = "mock-file"
    ) -> MockFingerprintLookupResponse:
        """Run identify() and convert matches into CandidateMatch domain entities."""
        fp_res = self.identify(audio_reference, file_id=file_id)
        candidates: list[CandidateMatch] = []
        for idx, match in enumerate(fp_res.matches):
            rec_id = match.recording_ids[0] if match.recording_ids else f"mock-rec-{idx}"
            candidates.append(
                CandidateMatch(
                    id=f"cand-fp-{rec_id}",
                    file_id=file_id,
                    provider=self.provider_name,
                    provider_entity_id=rec_id,
                    entity_type=EntityType.RECORDING,
                    artist=match.artist or "Unknown Artist",
                    title=match.title or audio_reference.stem,
                    mix_name=match.mix_name,
                    duration=match.duration,
                    release_details=ReleaseDetails(
                        release_title=match.title or audio_reference.stem,
                        label="Tribal America",
                        catalog_number="TRIUK014",
                        year="1994",
                    ),
                    match_evidence=match.raw_evidence,
                    match_score=match.confidence,
                )
            )
        return MockFingerprintLookupResponse(
            fingerprint=fp_res.raw_fingerprint or "MOCK_FP_EMPTY",
            candidates=candidates,
            result=fp_res,
        )
