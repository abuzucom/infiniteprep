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

        allowed_ids = {str(c.get("id")) for c in allowed_candidates if c.get("id") is not None}
        allowed_vals = {
            str(c.get("value")).strip().lower()
            for c in allowed_candidates
            if c.get("value") is not None
        }

        if selected_id is not None and str(selected_id) not in allowed_ids:
            return (
                False,
                f"Hallucinated candidate ID '{selected_id}' not in allowed_candidates; "
                "discarded LLM output.",
            )
        if (
            proposed_val is not None
            and allowed_vals
            and str(proposed_val).strip().lower() not in allowed_vals
        ):
            return (
                False,
                f"Hallucinated value '{proposed_val}' not in allowed_candidates; "
                "discarded LLM output.",
            )
        return True, None
