"""Closed-world output validator for optional LLM candidate arbitration (Section 7.D)."""

from __future__ import annotations

from typing import Any


class ClosedWorldValidator:
    """Validates that LLM outputs reference only candidate IDs and values in evidence."""

    def validate_response(
        self,
        llm_output: dict[str, Any],
        allowed_candidates: list[dict[str, Any]],
    ) -> tuple[bool, str | None]:
        """Verify selected_candidate_id and proposed_value exist in allowed_candidates."""
        if not isinstance(llm_output, dict):
            return False, "LLM response is not a JSON object; discarding output."

        selected_id = llm_output.get("selected_candidate_id")
        proposed_val = llm_output.get("proposed_value")
        if selected_id is None and proposed_val is None:
            return True, None

        by_id = {str(c.get("id")): c for c in allowed_candidates if c.get("id") is not None}
        if selected_id is not None and str(selected_id) not in by_id:
            return (
                False,
                f"Hallucinated candidate ID '{selected_id}' not in allowed_candidates; "
                "discarded LLM output.",
            )
        if proposed_val is None:
            return True, None
        # The value must belong to the selected candidate. Two separate sets accepted an
        # ID from one candidate paired with the value of another.
        scope = [by_id[str(selected_id)]] if selected_id is not None else allowed_candidates
        allowed_vals = {
            _normalize_value(c.get("value")) for c in scope if c.get("value") is not None
        }
        if _normalize_value(proposed_val) not in allowed_vals:
            return (
                False,
                f"Value '{proposed_val}' does not belong to the selected candidate; "
                "discarded LLM output.",
            )
        return True, None


def _normalize_value(value: Any) -> str:
    """Return a candidate value folded for comparison."""
    return str(value).strip().lower()
