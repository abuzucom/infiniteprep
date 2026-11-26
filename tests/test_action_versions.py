#!/usr/bin/env python3
"""Cover the action version checker without contacting GitHub."""

import importlib.util
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKER_PATH = REPO_ROOT / "scripts" / "check_action_versions.py"
# The review callers and the agents workflows are protected upstream copies.
OWNED_WORKFLOWS = (
    "ci.yml",
    "os-tests.yml",
    "security-devops.yml",
    "dependency-review.yml",
    "build-packages.yml",
    "deploy-pages.yml",
)
USES_KEY = "uses:"
LOCAL_REFERENCE_PREFIX = "./"


def is_external_uses_line(line: str) -> bool:
    """Return whether one workflow line references an action outside this repository."""
    stripped = line.strip().removeprefix("- ").strip()
    if not stripped.startswith(USES_KEY):
        return False
    reference = stripped.removeprefix(USES_KEY).strip()
    return bool(reference) and not reference.startswith(LOCAL_REFERENCE_PREFIX)


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

    def test_pin_names_release_commit_carries(self):
        pins = [self.pin(comment="# v7.0.1")]
        tags = {"actions/checkout": {"a" * 40: ["v7.0.1", "v7"]}}
        self.assertEqual(self.checker.find_violations(pins, tags, {}), [])

    def test_comment_naming_other_release_fails(self):
        pins = [self.pin(comment="# v9.9.9")]
        tags = {"actions/checkout": {"a" * 40: ["v7.0.1", "v7"]}}
        violations = self.checker.find_violations(pins, tags, {})
        self.assertEqual(len(violations), 1)
        self.assertIn("9.9.9", violations[0])
        self.assertIn("7.0.1", violations[0])

    def test_pin_without_release_fails(self):
        pins = [self.pin()]
        self.assertEqual(len(self.checker.find_violations(pins, {}, {})), 1)

    def test_pin_without_comment_passes(self):
        pins = [self.pin()]
        tags = {"actions/checkout": {"a" * 40: ["v7.0.1"]}}
        self.assertEqual(self.checker.find_violations(pins, tags, {}), [])

    def test_moving_major_tag_not_release(self):
        pins = [self.pin(comment="# v7")]
        tags = {"actions/checkout": {"a" * 40: ["v7"]}}
        self.assertEqual(len(self.checker.find_violations(pins, tags, {})), 1)

    def test_reusable_workflow_uses_changelog(self):
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

    def test_reusable_workflow_bad_version_fails(self):
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

    def test_reusable_workflow_without_release_fails(self):
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

    def test_action_reference_parses_without_workflow(self):
        parsed = self.checker.parse_reference("actions/checkout@" + "a" * 40)
        self.assertEqual(parsed, ("actions", "checkout", ""))

    def test_subfolder_action_parses_to_repo(self):
        """An action in a repository subfolder pins the tags of that repository."""
        parsed = self.checker.parse_reference("github/codeql-action/upload-sarif@" + "c" * 40)
        self.assertEqual(parsed, ("github", "codeql-action", ""))

    def test_empty_part_reference_not_pin(self):
        """A malformed owner or repository part never becomes a pin."""
        self.assertIsNone(self.checker.parse_reference("github//upload-sarif@" + "c" * 40))

    def test_local_reference_not_pin(self):
        self.assertIsNone(self.checker.parse_reference("./.github/workflows/agents-compliance.yml"))

    def test_short_revision_not_pin(self):
        self.assertIsNone(self.checker.parse_reference("actions/checkout@v4"))

    def test_owned_workflow_pins_name_release(self):
        """Every external action line in an owned workflow is a pin with a version comment.

        The raw line count catches a reference the parser skips. The claimed version
        lets check_action_versions.py compare the pin with its upstream release.
        """
        workflows = REPO_ROOT / ".github" / "workflows"
        for name in OWNED_WORKFLOWS:
            path = workflows / name
            lines = path.read_text(encoding="utf-8").splitlines()
            external_lines = [line for line in lines if is_external_uses_line(line)]
            pins = self.checker.collect_pins(path)
            with self.subTest(workflow=name):
                self.assertTrue(external_lines)
                self.assertEqual(len(pins), len(external_lines))
            for pin in pins:
                with self.subTest(workflow=name, repository=pin.repository):
                    missing = f"{name}: {pin.repository} names no version"
                    self.assertTrue(pin.claimed_version, missing)


if __name__ == "__main__":
    unittest.main()
