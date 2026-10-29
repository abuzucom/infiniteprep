#!/usr/bin/env python3
"""Tests that run_git accepts only bounded filter-driver config overrides."""
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import trusted_git


def _recording_runner(calls: list):
    """Return a runner that records each Git invocation and succeeds."""
    def runner(command, **kwargs):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")
    return runner


class ConfigOverrideValidationTest(unittest.TestCase):
    """run_git rejects overrides outside the filter-driver surface."""

    def _assert_rejected(self, overrides) -> None:
        calls = []
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                trusted_git.run_git(
                    Path(temporary), ["status"],
                    runner=_recording_runner(calls),
                    config_overrides=overrides)
        self.assertEqual(calls, [], "Git ran with a rejected override")

    def test_rejects_key_outside_filter_section(self):
        self._assert_rejected((("core.pager", "sh -c true"),))

    def test_rejects_credential_helper_key(self):
        self._assert_rejected((("credential.helper", "!true"),))

    def test_rejects_unknown_filter_variable(self):
        self._assert_rejected((("filter.probe.command", ""),))

    def test_rejects_key_without_subsection(self):
        self._assert_rejected((("filter.clean", ""),))

    def test_rejects_empty_subsection(self):
        self._assert_rejected((("filter..clean", ""),))

    def test_rejects_newline_in_subsection(self):
        self._assert_rejected((("filter.a\nb.clean", ""),))

    def test_rejects_nul_in_subsection(self):
        self._assert_rejected((("filter.a\0b.clean", ""),))

    def test_rejects_newline_in_value(self):
        self._assert_rejected((("filter.probe.required", "false\ntrue"),))

    def test_rejects_nul_in_value(self):
        self._assert_rejected((("filter.probe.clean", "\0"),))

    def test_rejects_non_string_pair(self):
        self._assert_rejected((("filter.probe.clean", None),))

    def test_rejects_too_many_overrides(self):
        limit = trusted_git.MAX_CONFIG_OVERRIDES
        overrides = tuple((f"filter.d{index}.clean", "")
                          for index in range(limit + 1))
        self._assert_rejected(overrides)

    def test_accepts_override_limit(self):
        calls = []
        limit = trusted_git.MAX_CONFIG_OVERRIDES
        overrides = tuple((f"filter.d{index}.clean", "")
                          for index in range(limit))
        with tempfile.TemporaryDirectory() as temporary:
            trusted_git.run_git(
                Path(temporary), ["status"],
                runner=_recording_runner(calls),
                config_overrides=overrides)
        self.assertEqual(len(calls), 1)

    def test_accepts_dotted_subsection_with_equals(self):
        calls = []
        with tempfile.TemporaryDirectory() as temporary:
            trusted_git.run_git(
                Path(temporary), ["status"],
                runner=_recording_runner(calls),
                config_overrides=(("filter.a.b=c.clean", ""),
                                  ("filter.a.b=c.required", "false")))
        self.assertEqual(len(calls), 1)


class ConfigCountValidationTest(unittest.TestCase):
    """The helper rejects a malformed existing GIT_CONFIG_COUNT."""

    def _assert_count_rejected(self, count: str) -> None:
        environment = {"GIT_CONFIG_COUNT": count}
        with self.assertRaises(ValueError):
            trusted_git._append_config_overrides(
                environment, (("filter.probe.clean", ""),))
        self.assertEqual(environment, {"GIT_CONFIG_COUNT": count})

    def test_rejects_negative_count(self):
        self._assert_count_rejected("-1")

    def test_rejects_non_numeric_count(self):
        self._assert_count_rejected("one")

    def test_rejects_count_with_missing_slot(self):
        self._assert_count_rejected("1")

    def test_rejects_count_above_context_limit(self):
        count = trusted_git.MAX_GIT_CONTEXT_PAIRS + 1
        environment = {"GIT_CONFIG_COUNT": str(count)}
        for index in range(count):
            environment[f"GIT_CONFIG_KEY_{index}"] = "user.name"
            environment[f"GIT_CONFIG_VALUE_{index}"] = "Fixture"
        expected = dict(environment)
        with self.assertRaises(ValueError):
            trusted_git._append_config_overrides(
                environment, (("filter.probe.clean", ""),))
        self.assertEqual(environment, expected)


if __name__ == "__main__":
    unittest.main()
