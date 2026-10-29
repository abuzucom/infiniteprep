"""Mock closed-world reasoning adapter for deterministic offline arbitration tests."""

from __future__ import annotations

from typing import Any


class MockReasoningAdapter:
    """Offline mock ReasoningAdapter selecting the first allowed candidate in evidence."""

    adapter_name: str = "mock_llm"

    def is_available(self) -> tuple[bool, str | None]:
        """Return availability status for mock reasoning adapter."""
        return True, None

    def arbitrate(self, evidence_packet: dict[str, Any]) -> dict[str, Any]:
        """Select the first candidate in allowed_candidates without inventing facts."""
        allowed = evidence_packet.get("allowed_candidates", [])
        if not allowed:
            return {
                "selected_candidate_id": None,
                "proposed_value": None,
                "rationale": "No allowed candidates supplied in evidence packet.",
            }
        first = allowed[0]
        cand_id = first.get("id") if isinstance(first, dict) else str(first)
        val = first.get("value") if isinstance(first, dict) else str(first)
        return {
            "selected_candidate_id": cand_id,
            "proposed_value": val,
            "rationale": "Deterministic mock arbitration selected top allowed candidate.",
        }
