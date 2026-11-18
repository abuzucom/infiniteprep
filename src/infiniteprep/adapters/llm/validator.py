"""Closed-world output validator for optional LLM candidate arbitration (Section 7.D)."""

from __future__ import annotations

from typing import Any, Final

# Candidate IDs and metadata values stay far below this length. Longer model text is invented.
MAX_LLM_FIELD_CHARS: Final[int] = 1000
# Error messages quote at most this much model text, so a message stays short and readable.
MAX_QUOTED_MODEL_CHARS: Final[int] = 80


def normalize_candidate_value(value: object) -> str:
    """Return the comparison form of a candidate value: trimmed and case folded."""
    return str(value).strip().casefold()


def _quote(model_text: object) -> str:
    """Return a short printable excerpt of untrusted model text for an error message."""
    text = "".join(char for char in str(model_text)[:MAX_QUOTED_MODEL_CHARS] if char.isprintable())
    return repr(text)


def _field_error(name: str, value: object) -> str | None:
    """Return an error when a model field holds the wrong type or too much text, else None."""
    if value is None:
        return None
    if not isinstance(value, str):
        return f"LLM field {name} is not a string; discarded LLM output."
    if len(value) > MAX_LLM_FIELD_CHARS:
        return f"LLM field {name} exceeds {MAX_LLM_FIELD_CHARS} characters; discarded LLM output."
    return None


class ClosedWorldValidator:
    """Validates that LLM outputs reference only candidate IDs and values in evidence."""

    def validate_response(
        self,
        llm_output: dict[str, Any],
        allowed_candidates: list[dict[str, Any]],
    ) -> tuple[bool, str | None]:
        """Verify that the selected ID and the proposed value belong to one allowed candidate.

        The ID and the value must name the same candidate. A value alone must equal the value
        of some candidate. A value for a candidate that carries no value counts as invented.
        """
        if not isinstance(llm_output, dict):
            return False, "LLM response is not a JSON object; discarding output."
        selected_id = llm_output.get("selected_candidate_id")
        proposed_value = llm_output.get("proposed_value")
        fields = (("selected_candidate_id", selected_id), ("proposed_value", proposed_value))
        for name, value in fields:
            error = _field_error(name, value)
            if error is not None:
                return False, error
        if selected_id is None and proposed_value is None:
            return True, None
        if selected_id is not None:
            return self._validate_selected(selected_id, proposed_value, allowed_candidates)
        return self._validate_value_only(proposed_value, allowed_candidates)

    def _validate_selected(
        self,
        selected_id: str,
        proposed_value: str | None,
        allowed_candidates: list[dict[str, Any]],
    ) -> tuple[bool, str | None]:
        """Check that selected_id names a candidate and proposed_value matches that candidate."""
        candidate = find_candidate(selected_id, allowed_candidates)
        if candidate is None:
            return (
                False,
                f"Hallucinated candidate ID {_quote(selected_id)} not in allowed_candidates; "
                "discarded LLM output.",
            )
        if proposed_value is None:
            return True, None
        expected = candidate.get("value")
        if expected is None or normalize_candidate_value(expected) != normalize_candidate_value(
            proposed_value
        ):
            return (
                False,
                f"Value {_quote(proposed_value)} does not belong to candidate "
                f"{_quote(selected_id)}; discarded LLM output.",
            )
        return True, None

    def _validate_value_only(
        self, proposed_value: str, allowed_candidates: list[dict[str, Any]]
    ) -> tuple[bool, str | None]:
        """Check that a value proposed without an ID equals the value of some candidate."""
        allowed_values = {
            normalize_candidate_value(candidate.get("value"))
            for candidate in allowed_candidates
            if candidate.get("value") is not None
        }
        if normalize_candidate_value(proposed_value) not in allowed_values:
            return (
                False,
                f"Hallucinated value {_quote(proposed_value)} not in allowed_candidates; "
                "discarded LLM output.",
            )
        return True, None


def find_candidate(
    candidate_id: object, allowed_candidates: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Return the allowed candidate whose ID equals candidate_id, or None."""
    for candidate in allowed_candidates:
        identifier = candidate.get("id")
        if identifier is not None and str(identifier) == str(candidate_id):
            return candidate
    return None
