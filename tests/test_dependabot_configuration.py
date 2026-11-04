"""Check the Dependabot configuration against the repository it maintains."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any, Final

import yaml

ROOT: Final[Path] = Path(__file__).resolve().parent.parent
CONFIG: Final[Path] = ROOT / ".github" / "dependabot.yml"
# The ecosystems that hold a manifest or a workflow file in this repository.
EXPECTED_ECOSYSTEMS: Final[set[str]] = {"pip", "github-actions"}
# The directories that hold a manifest or a workflow file in this repository.
EXPECTED_DIRECTORIES: Final[set[str]] = {"/"}
# The branch prefix that the dependabot bot exemption in
# scripts/trusted_bot_identities.py recognizes.
DEPENDABOT_BRANCH_PREFIX: Final[str] = "dependabot/"


def load_config() -> dict[str, Any]:
    """Return the parsed Dependabot configuration, failing when the file is absent."""
    if not CONFIG.is_file():
        raise AssertionError(f"missing Dependabot configuration: {CONFIG.relative_to(ROOT)}")
    document = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise AssertionError("Dependabot configuration must be a mapping")
    return document


def updates() -> list[dict[str, Any]]:
    """Return every update entry the configuration declares."""
    entries = load_config().get("updates")
    if not isinstance(entries, list):
        raise AssertionError("Dependabot configuration must declare a list of updates")
    for entry in entries:
        if not isinstance(entry, dict):
            raise AssertionError("every Dependabot update entry must be a mapping")
    return list(entries)


class DependabotConfigurationTest(unittest.TestCase):
    """The Dependabot configuration covers every ecosystem and keeps the bot branch prefix."""

    def test_configuration_file_exists(self) -> None:
        self.assertTrue(CONFIG.is_file(), ".github/dependabot.yml is missing")

    def test_configuration_declares_version_two(self) -> None:
        self.assertEqual(load_config().get("version"), 2)

    def test_configuration_covers_every_ecosystem(self) -> None:
        declared = {entry.get("package-ecosystem") for entry in updates()}
        self.assertEqual(declared, EXPECTED_ECOSYSTEMS)

    def test_every_ecosystem_scans_the_repository_root(self) -> None:
        for entry in updates():
            self.assertEqual(entry.get("directory"), "/", str(entry.get("package-ecosystem")))

    def test_every_ecosystem_declares_a_schedule(self) -> None:
        for entry in updates():
            schedule = entry.get("schedule")
            self.assertIsInstance(schedule, dict, str(entry.get("package-ecosystem")))
            self.assertIn(schedule.get("interval"), {"daily", "weekly", "monthly"})

    def test_every_ecosystem_bounds_the_open_pull_requests(self) -> None:
        for entry in updates():
            limit = entry.get("open-pull-requests-limit")
            self.assertIsInstance(limit, int, str(entry.get("package-ecosystem")))
            self.assertGreater(limit, 0, str(entry.get("package-ecosystem")))

    def test_every_group_declares_a_pattern(self) -> None:
        for entry in updates():
            groups = entry.get("groups")
            self.assertIsInstance(groups, dict, str(entry.get("package-ecosystem")))
            for name, group in groups.items():
                self.assertIsInstance(group, dict, name)
                self.assertIn("*", group.get("patterns", []), name)

    def test_no_group_collects_a_major_update(self) -> None:
        # A major bump arrives alone so that a breaking release cannot block
        # the safe updates that share the same ecosystem.
        for entry in updates():
            groups = entry.get("groups", {})
            for name, group in groups.items():
                self.assertNotIn("major", group.get("update-types", []), name)

    def test_commit_messages_use_the_repository_subject_format(self) -> None:
        for entry in updates():
            message = entry.get("commit-message")
            self.assertIsInstance(message, dict, str(entry.get("package-ecosystem")))
            prefix = message.get("prefix")
            self.assertIsInstance(prefix, str, str(entry.get("package-ecosystem")))
            self.assertRegex(prefix, r"^(feat|fix|chore|docs|test|ci):?$")

    def test_branch_names_keep_the_dependabot_prefix(self) -> None:
        # The bot exemption in scripts/trusted_bot_identities.py recognizes
        # only the dependabot branch prefix.
        for entry in updates():
            naming = entry.get("pull-request-branch-name")
            if naming is None:
                continue
            self.assertEqual(naming.get("prefix", "dependabot"), "dependabot")


if __name__ == "__main__":
    unittest.main()
