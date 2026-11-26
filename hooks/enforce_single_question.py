#!/usr/bin/env python3
"""Deny an AskUserQuestion call that is not one question with one recommendation.

AGENTS.md requires one recommendation with its reason for each open choice,
asked once. Claude Code registers this hook on PreToolUse for
AskUserQuestion. A deny makes the agent write the question again. The hook
checks shape only. It requires one question with non-empty text and two to
four options with non-empty labels. Exactly one label ends with
`(Recommended)`, and that option comes first. The client validates the
other fields against its own schema.

Other clients expose no structured question tool to repository hooks. The
`interactive-decisions` skill carries the rule there.
"""
import sys

import _gate_core as core

GATE = "enforce_single_question.py"
QUESTION_TOOL = "AskUserQuestion"
REQUIRED_QUESTIONS = 1
REQUIRED_RECOMMENDATIONS = 1
MIN_OPTIONS = 2
MAX_OPTIONS = 4
RECOMMENDED_SUFFIX = "(Recommended)"
MALFORMED_REASON = "the gate cannot read the question call or check its shape"
COUNT_REASON = ("ask one question per call. Put each open choice in its own "
                "AskUserQuestion call")
OPTIONS_REASON = f"give each question {MIN_OPTIONS} to {MAX_OPTIONS} options"
RECOMMENDATION_REASON = (f"mark one option label with a trailing {RECOMMENDED_SUFFIX} "
                         "and put that option first")


def _filled(value) -> bool:
    """Return whether a value is a string with visible text."""
    return isinstance(value, str) and bool(value.strip())


def _recommendation_first(labels: list) -> bool:
    """Return whether exactly one label carries the suffix and that label comes first."""
    marked = [label.rstrip().endswith(RECOMMENDED_SUFFIX) for label in labels]
    return marked[0] and sum(marked) == REQUIRED_RECOMMENDATIONS


def question_decision(payload: dict) -> tuple:
    """Return (decision, reason) for one PreToolUse payload."""
    if payload.get("tool_name") != QUESTION_TOOL:
        return "", ""
    tool_input = payload.get("tool_input")
    questions = tool_input.get("questions") if isinstance(tool_input, dict) else None
    if not isinstance(questions, list) or not questions:
        return "deny", MALFORMED_REASON
    if len(questions) != REQUIRED_QUESTIONS:
        return "deny", COUNT_REASON
    entry = questions[0]
    if not isinstance(entry, dict) or not _filled(entry.get("question")):
        return "deny", MALFORMED_REASON
    options = entry.get("options")
    if not isinstance(options, list) or not all(isinstance(item, dict) for item in options):
        return "deny", MALFORMED_REASON
    if not MIN_OPTIONS <= len(options) <= MAX_OPTIONS:
        return "deny", OPTIONS_REASON
    labels = [item.get("label") for item in options]
    if not all(_filled(label) for label in labels):
        return "deny", MALFORMED_REASON
    if not _recommendation_first(labels):
        return "deny", RECOMMENDATION_REASON
    return "", ""


def main() -> int:
    """Read one PreToolUse payload and emit a deny or nothing."""
    try:
        payload = core.read_payload()
        if payload is None:
            return core.emit(GATE, "deny", MALFORMED_REASON)
        decision, reason = question_decision(payload)
        if decision:
            return core.emit(GATE, decision, reason)
        return 0
    except Exception:
        # Claude Code treats a non-2 exit as a non-blocking error. An
        # unhandled exception would let the call through unchecked.
        return core.emit(GATE, "deny", "the gate raised an unexpected error. The gate "
                                       "cannot clear this call")


if __name__ == "__main__":
    sys.exit(main())
