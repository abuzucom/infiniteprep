#!/usr/bin/env python3
"""Cover how the action version checker fetches upstream tags and changelogs."""

import importlib.util
import subprocess
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKER_PATH = REPO_ROOT / "scripts" / "check_action_versions.py"
PIN_SHA = "a" * 40
TAG_OBJECT_SHA = "b" * 40
OTHER_SHA = "c" * 40
CLAIMED_VERSION = "7.0.1"
RATE_LIMIT_ERROR = "HTTP 403: API rate limit exceeded"
UNREADABLE_TEXT = "could not read"
NO_RELEASE_TEXT = "pins no release"
PAGE_SIZE_ARGUMENT = "per_page=100"


def load_checker():
    """Import the action version checker by path."""
    spec = importlib.util.spec_from_file_location("check_action_versions", CHECKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def completed(stdout: str = "", returncode: int = 0, stderr: str = "") -> SimpleNamespace:
    """Return a stand-in for one finished gh call."""
    return SimpleNamespace(returncode=returncode, stdout=stdout, stderr=stderr)


class FakeGh:
    """Answer gh api calls from a table of endpoint fragments and record each call."""

    def __init__(self, answers: dict[str, object]) -> None:
        """Store answers keyed by a fragment of the endpoint path."""
        self.answers = answers
        self.endpoints: list[str] = []

    def run(self, _root: Path, arguments: list[str], *, timeout: float | None = None):
        """Return the answer for the first matching fragment, or raise a stored error."""
        endpoint = arguments[1]
        self.endpoints.append(endpoint)
        for fragment, answer in self.answers.items():
            if fragment in endpoint:
                if isinstance(answer, BaseException):
                    raise answer
                return answer
        return completed(returncode=1, stderr="HTTP 404: Not Found")


class ActionVersionFetchTest(unittest.TestCase):
    """The checker reads each claimed tag directly and names a failed fetch as such."""

    def setUp(self) -> None:
        self.checker = load_checker()

    def pin(self, **overrides):
        """Return one action pin with the given fields replaced."""
        fields = {
            "path": "ci.yml",
            "owner": "actions",
            "repo": "checkout",
            "sha": PIN_SHA,
            "workflow": "",
            "comment": "",
        }
        fields.update(overrides)
        return self.checker.Pin(**fields)

    def check(self, pins, fake: FakeGh) -> list[str]:
        """Resolve the pins through the fake gh and return the violations."""
        self.checker.run_gh = fake.run
        sources = self.checker.resolve_pin_sources(pins)
        return self.checker.find_violations(
            pins, sources.action_tags, sources.reusable_releases, unreadable=sources.unreadable
        )

    def test_a_failed_tag_fetch_names_the_fetch_failure(self) -> None:
        """A refused tag listing reports the fetch, never a missing release."""
        fake = FakeGh({"/tags": completed(returncode=1, stderr=RATE_LIMIT_ERROR)})

        violations = self.check([self.pin()], fake)

        self.assertEqual(len(violations), 1)
        self.assertIn(UNREADABLE_TEXT, violations[0])
        self.assertIn(RATE_LIMIT_ERROR, violations[0])
        self.assertNotIn(NO_RELEASE_TEXT, violations[0])

    def test_a_timed_out_tag_fetch_names_the_fetch_failure(self) -> None:
        """A gh call past its time limit becomes a report, not a crash."""
        timeout = subprocess.TimeoutExpired(cmd="gh", timeout=1)
        fake = FakeGh({"/tags": timeout})

        violations = self.check([self.pin()], fake)

        self.assertEqual(len(violations), 1)
        self.assertIn(UNREADABLE_TEXT, violations[0])

    def test_the_claimed_tag_is_read_without_listing_every_tag(self) -> None:
        """A pin that names its release costs one ref lookup and no tag listing."""
        ref = completed(stdout=f"commit {PIN_SHA}\n")
        fake = FakeGh({f"git/ref/tags/v{CLAIMED_VERSION}": ref})

        violations = self.check([self.pin(comment=f"v{CLAIMED_VERSION}")], fake)

        self.assertEqual(violations, [])
        self.assertEqual(len(fake.endpoints), 1)

    def test_an_annotated_claimed_tag_resolves_to_its_commit(self) -> None:
        """An annotated tag points at a tag object, which then names the commit."""
        fake = FakeGh(
            {
                f"git/ref/tags/v{CLAIMED_VERSION}": completed(stdout=f"tag {TAG_OBJECT_SHA}\n"),
                f"git/tags/{TAG_OBJECT_SHA}": completed(stdout=f"{PIN_SHA}\n"),
            }
        )

        violations = self.check([self.pin(comment=f"v{CLAIMED_VERSION}")], fake)

        self.assertEqual(violations, [])

    def test_a_claimed_tag_on_another_commit_falls_back_to_the_listing(self) -> None:
        """A claim that names a different commit still gets the full tag check."""
        fake = FakeGh(
            {
                f"git/ref/tags/v{CLAIMED_VERSION}": completed(stdout=f"commit {OTHER_SHA}\n"),
                "/tags?": completed(stdout=f"v7.0.0 {PIN_SHA}\n"),
            }
        )

        violations = self.check([self.pin(comment=f"v{CLAIMED_VERSION}")], fake)

        self.assertEqual(len(violations), 1)
        self.assertIn("7.0.0", violations[0])

    def test_the_tag_listing_asks_for_full_pages(self) -> None:
        """A pin without a claim lists the tags 100 at a time."""
        fake = FakeGh({"/tags?": completed(stdout=f"v7.0.1 {PIN_SHA}\n")})

        violations = self.check([self.pin()], fake)

        self.assertEqual(violations, [])
        self.assertTrue(any(PAGE_SIZE_ARGUMENT in endpoint for endpoint in fake.endpoints))

    def test_a_failed_changelog_fetch_names_the_fetch_failure(self) -> None:
        """A refused changelog read reports the fetch, never a missing release."""
        fake = FakeGh({"contents/CHANGELOG.md": completed(returncode=1, stderr=RATE_LIMIT_ERROR)})
        pin = self.pin(owner="abuzucom", repo="agents", workflow="/.github/workflows/x.yml")

        violations = self.check([pin], fake)

        self.assertEqual(len(violations), 1)
        self.assertIn(UNREADABLE_TEXT, violations[0])
        self.assertIn(RATE_LIMIT_ERROR, violations[0])

    def test_tag_lines_keep_only_full_commit_revisions(self) -> None:
        """A malformed listing line never becomes a release."""
        text = f"v1.0.0 {PIN_SHA}\nv2.0.0 short\nbroken\n"

        self.assertEqual(self.checker.parse_tag_lines(text), {PIN_SHA: ["v1.0.0"]})


if __name__ == "__main__":
    unittest.main()
