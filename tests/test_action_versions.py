#!/usr/bin/env python3
"""Cover the action version checker without contacting GitHub."""

import importlib.util
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKER_PATH = REPO_ROOT / "scripts" / "check_action_versions.py"


def load_checker():
    """Import the action version checker by path."""
    spec = importlib.util.spec_from_file_location("check_action_versions", CHECKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ActionVersionCheckerTest(unittest.TestCase):
    """The checker compares a pin against the release that its commit names."""

    def setUp(self):
        self.checker = load_checker()

    def pin(self, **overrides):
        """Return one action pin with the given fields replaced."""
        fields = {
            "path": "ci.yml",
            "owner": "actions",
            "repo": "checkout",
            "sha": "a" * 40,
            "workflow": "",
            "comment": "",
        }
        fields.update(overrides)
        return self.checker.Pin(**fields)

    def test_a_pin_names_the_release_its_commit_carries(self):
        pins = [self.pin(comment="# v7.0.1")]
        tags = {"actions/checkout": {"a" * 40: ["v7.0.1", "v7"]}}
        self.assertEqual(self.checker.find_violations(pins, tags, {}), [])

    def test_a_comment_that_names_another_release_fails(self):
        pins = [self.pin(comment="# v9.9.9")]
        tags = {"actions/checkout": {"a" * 40: ["v7.0.1", "v7"]}}
        violations = self.checker.find_violations(pins, tags, {})
        self.assertEqual(len(violations), 1)
        self.assertIn("9.9.9", violations[0])
        self.assertIn("7.0.1", violations[0])

    def test_a_pin_without_a_release_fails(self):
        pins = [self.pin()]
        self.assertEqual(len(self.checker.find_violations(pins, {}, {})), 1)

    def test_a_pin_without_a_comment_passes(self):
        pins = [self.pin()]
        tags = {"actions/checkout": {"a" * 40: ["v7.0.1"]}}
        self.assertEqual(self.checker.find_violations(pins, tags, {}), [])

    def test_a_moving_major_tag_does_not_count_as_a_release(self):
        pins = [self.pin(comment="# v7")]
        tags = {"actions/checkout": {"a" * 40: ["v7"]}}
        self.assertEqual(len(self.checker.find_violations(pins, tags, {})), 1)

    def test_a_reusable_workflow_uses_the_upstream_changelog(self):
        pins = [
            self.pin(
                owner="abuzucom",
                repo="foucault",
                sha="b" * 40,
                workflow="/.github/workflows/security-review.yml",
                comment="Foucault 3.6.1.",
            )
        ]
        releases = {("abuzucom/foucault", "b" * 40): "3.6.1"}
        self.assertEqual(self.checker.find_violations(pins, {}, releases), [])

    def test_a_reusable_workflow_with_another_version_fails(self):
        pins = [
            self.pin(
                owner="abuzucom",
                repo="foucault",
                sha="b" * 40,
                workflow="/.github/workflows/security-review.yml",
                comment="Foucault 3.5.0.",
            )
        ]
        releases = {("abuzucom/foucault", "b" * 40): "3.6.1"}
        violations = self.checker.find_violations(pins, {}, releases)
        self.assertEqual(len(violations), 1)
        self.assertIn("3.5.0", violations[0])

    def test_a_reusable_workflow_without_a_release_fails(self):
        pins = [
            self.pin(
                owner="abuzucom",
                repo="foucault",
                sha="b" * 40,
                workflow="/.github/workflows/security-review.yml",
                comment="Foucault 3.6.1.",
            )
        ]
        self.assertEqual(len(self.checker.find_violations(pins, {}, {})), 1)

    def test_a_reusable_workflow_reference_parses(self):
        parsed = self.checker.parse_reference(
            "abuzucom/foucault/.github/workflows/security-review.yml@" + "b" * 40
        )
        self.assertEqual(
            parsed,
            ("abuzucom", "foucault", "/.github/workflows/security-review.yml"),
        )

    def test_an_action_reference_parses_without_a_workflow(self):
        parsed = self.checker.parse_reference("actions/checkout@" + "a" * 40)
        self.assertEqual(parsed, ("actions", "checkout", ""))

    def test_a_local_reference_is_not_a_pin(self):
        self.assertIsNone(self.checker.parse_reference("./.github/workflows/agents-compliance.yml"))

    def test_a_short_revision_is_not_a_pin(self):
        self.assertIsNone(self.checker.parse_reference("actions/checkout@v4"))

    def test_every_workflow_pin_names_a_release(self):
        pins = self.checker.collect_repository_pins(REPO_ROOT / ".github" / "workflows")
        self.assertTrue(pins)
        for pin in pins:
            with self.subTest(pin=pin.path, repository=pin.repository):
                self.assertRegex(pin.sha, r"^[0-9a-f]{40}$")
                if pin.claimed_version:
                    self.assertRegex(pin.claimed_version, r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
