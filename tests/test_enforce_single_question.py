#!/usr/bin/env python3
"""The question hook allows one question with two to four options and one recommendation."""
import contextlib
import importlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
HOOK_PATH = REPOSITORY_ROOT / "hooks" / "enforce_single_question.py"
HOOK_TIMEOUT_SECONDS = 15
POLICY_DENIAL_EXIT_CODE = 2


def option(label: str) -> dict:
    """Return one option entry with a description."""
    return {"label": label, "description": f"{label} details"}


def question(*labels: str) -> dict:
    """Return one question entry with the given option labels."""
    return {"question": "Which path?", "header": "Path", "multiSelect": False,
            "options": [option(label) for label in labels]}


def run_hook(raw: str):
    """Run the hook on raw stdin and return (exit code, parsed stdout, stderr)."""
    result = subprocess.run([sys.executable, str(HOOK_PATH)], input=raw, text=True,
                            capture_output=True, timeout=HOOK_TIMEOUT_SECONDS, check=False,
                            cwd=REPOSITORY_ROOT)
    parsed = json.loads(result.stdout) if result.stdout.strip() else {}
    return result.returncode, parsed, result.stderr


def payload(questions) -> str:
    """Return a PreToolUse payload for one AskUserQuestion call."""
    return json.dumps({"hook_event_name": "PreToolUse", "tool_name": "AskUserQuestion",
                       "cwd": str(REPOSITORY_ROOT), "tool_input": {"questions": questions}})


def decision(parsed: dict) -> str:
    """Return the permission decision, or an empty string for no decision."""
    return parsed.get("hookSpecificOutput", {}).get("permissionDecision", "")


class SingleQuestionTest(unittest.TestCase):
    """Each AskUserQuestion call holds exactly one well-formed question."""

    def test_one_recommended_question_passes(self) -> None:
        code, parsed, _ = run_hook(payload([question("Fix it (Recommended)", "Leave it")]))
        self.assertEqual(code, 0)
        self.assertEqual(decision(parsed), "")

    def test_two_questions_deny(self) -> None:
        both = [question("A (Recommended)", "B"), question("C (Recommended)", "D")]
        code, parsed, _ = run_hook(payload(both))
        self.assertEqual(code, POLICY_DENIAL_EXIT_CODE)
        self.assertEqual(decision(parsed), "deny")
        self.assertIn("one question", parsed["hookSpecificOutput"]["permissionDecisionReason"])

    def test_option_count_bounds_deny(self) -> None:
        for labels in (("Only (Recommended)",), ("A (Recommended)", "B", "C", "D", "E")):
            with self.subTest(count=len(labels)):
                code, parsed, _ = run_hook(payload([question(*labels)]))
                self.assertEqual(code, POLICY_DENIAL_EXIT_CODE)
                self.assertEqual(decision(parsed), "deny")

    def test_missing_recommendation_denies(self) -> None:
        code, parsed, _ = run_hook(payload([question("A", "B")]))
        self.assertEqual(code, POLICY_DENIAL_EXIT_CODE)
        self.assertIn("(Recommended)", parsed["hookSpecificOutput"]["permissionDecisionReason"])

    def test_two_recommendations_deny(self) -> None:
        code, _parsed, _ = run_hook(payload([question("A (Recommended)", "B (Recommended)")]))
        self.assertEqual(code, POLICY_DENIAL_EXIT_CODE)

    def test_late_recommendation_denies(self) -> None:
        code, _parsed, _ = run_hook(payload([question("A", "B (Recommended)")]))
        self.assertEqual(code, POLICY_DENIAL_EXIT_CODE)

    def test_empty_question_text_denies(self) -> None:
        for text in ("", "   ", 7):
            with self.subTest(text=text):
                entry = question("A (Recommended)", "B")
                entry["question"] = text
                code, _parsed, _ = run_hook(payload([entry]))
                self.assertEqual(code, POLICY_DENIAL_EXIT_CODE)

    def test_bad_options_deny(self) -> None:
        for options in ("x", [1, 2], None):
            with self.subTest(options=options):
                entry = question("A (Recommended)", "B")
                entry["options"] = options
                code, _parsed, _ = run_hook(payload([entry]))
                self.assertEqual(code, POLICY_DENIAL_EXIT_CODE)

    def test_bad_label_denies(self) -> None:
        for label in ("", "  ", None, 3):
            with self.subTest(label=label):
                entry = question("A (Recommended)", "B")
                entry["options"][1]["label"] = label
                code, _parsed, _ = run_hook(payload([entry]))
                self.assertEqual(code, POLICY_DENIAL_EXIT_CODE)

    def test_malformed_input_denies(self) -> None:
        for raw in ("not json", payload("text"), payload([{"options": "x"}]),
                    payload([{"options": [1, 2]}]), payload([])):
            with self.subTest(raw=raw[:40]):
                code, parsed, _ = run_hook(raw)
                self.assertEqual(code, POLICY_DENIAL_EXIT_CODE)
                self.assertEqual(decision(parsed), "deny")

    def test_other_tool_passes(self) -> None:
        raw = json.dumps({"hook_event_name": "PreToolUse", "tool_name": "Read",
                          "tool_input": {"file_path": "x"}})
        code, parsed, _ = run_hook(raw)
        self.assertEqual(code, 0)
        self.assertEqual(parsed, {})

    def test_reason_omits_option_text(self) -> None:
        labels = ("A\nInjected line", "B\x1b[31m")
        code, parsed, _ = run_hook(payload([question(*labels)]))
        self.assertEqual(code, POLICY_DENIAL_EXIT_CODE)
        reason = parsed["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertNotIn("Injected", reason)
        self.assertNotIn("\x1b", reason)



class UnexpectedErrorTest(unittest.TestCase):
    """An unexpected error inside the decision still denies the call."""

    def test_unexpected_error_denies(self) -> None:
        sys.path.insert(0, str(REPOSITORY_ROOT / "hooks"))
        module = importlib.import_module("enforce_single_question")
        stdin = io.StringIO(json.dumps({"tool_name": "AskUserQuestion", "tool_input": {}}))
        output = io.StringIO()
        with patch.object(module, "question_decision", side_effect=RuntimeError("boom")), \
                patch.object(module.sys, "stdin", stdin), contextlib.redirect_stdout(output), \
                contextlib.redirect_stderr(io.StringIO()):
            code = module.main()
        self.assertEqual(code, POLICY_DENIAL_EXIT_CODE)
        self.assertIn("unexpected error", output.getvalue())


if __name__ == "__main__":
    unittest.main()
