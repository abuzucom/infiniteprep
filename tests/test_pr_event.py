#!/usr/bin/env python3
"""One pull request event loader serves the PR prose and reference checkers (S15)."""
import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import _pr_event
import check_external_pr_refs
import check_pull_request_message

VALID_EVENT = {
    "pull_request": {"title": "fix: a thing", "body": None, "user": {"login": "octocat"}},
    "repository": {"owner": {"login": "example-owner"}},
}


class EventFile:
    """Write one event payload to a temporary JSON file."""

    def __init__(self, test_case: unittest.TestCase, payload) -> None:
        temporary = tempfile.TemporaryDirectory()
        test_case.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "event.json"
        text = payload if isinstance(payload, str) else json.dumps(payload)
        self.path.write_text(text, encoding="utf-8")


class LoaderTest(unittest.TestCase):
    """The shared loader validates shape and size before returning prose."""

    def test_valid_event(self):
        event = EventFile(self, VALID_EVENT)
        self.assertEqual(_pr_event.load_pull_request(event.path), ("fix: a thing", "", "octocat"))
        self.assertEqual(check_external_pr_refs._load_event(event.path),
                         ("fix: a thing", "", "octocat", "example-owner"))

    def test_invalid_events(self):
        cases = (
            ("[]", "must contain an object"),
            ({"repository": {}}, "lacks pull request data"),
            ({"pull_request": {"title": 1, "user": {"login": "a"}}}, "title must contain text"),
            ({"pull_request": {"title": "t", "body": 3, "user": {"login": "a"}}}, "body must contain"),
            ({"pull_request": {"title": "t", "user": {}}}, "author must contain a login"),
            ({"pull_request": {"title": "t" * (_pr_event.MAX_TITLE_LENGTH + 1),
                               "user": {"login": "a"}}}, "exceeds the size limit"),
        )
        for payload, message in cases:
            with self.subTest(message=message):
                event = EventFile(self, payload)
                with self.assertRaisesRegex(ValueError, message):
                    _pr_event.load_pull_request(event.path)

    def test_oversized_file_is_rejected(self):
        event = EventFile(self, " " * (_pr_event.MAX_EVENT_BYTES + 1))
        with self.assertRaisesRegex(ValueError, "event payload exceeds the size limit"):
            _pr_event.load_pull_request(event.path)

    def test_reference_checker_requires_the_owner(self):
        payload = {"pull_request": VALID_EVENT["pull_request"], "repository": {"owner": {}}}
        event = EventFile(self, payload)
        with self.assertRaisesRegex(ValueError, "repository owner login"):
            check_external_pr_refs._load_event(event.path)


class SharedLoaderTest(unittest.TestCase):
    """Both checkers take the loader and limits from scripts/_pr_event.py."""

    def test_checkers_share_the_module(self):
        shared = Path(inspect.getsourcefile(_pr_event.load_pull_request)).resolve()
        self.assertEqual(
            Path(inspect.getsourcefile(check_pull_request_message._load_event)).resolve(), shared)
        for name in ("MAX_EVENT_BYTES", "MAX_TITLE_LENGTH", "MAX_BODY_LENGTH"):
            with self.subTest(name=name):
                self.assertEqual(getattr(check_external_pr_refs, name), getattr(_pr_event, name))
                self.assertEqual(getattr(check_pull_request_message, name), getattr(_pr_event, name))
        self.assertEqual(check_external_pr_refs.DEPENDABOT_LOGIN, _pr_event.DEPENDABOT_LOGIN)


if __name__ == "__main__":
    unittest.main()
