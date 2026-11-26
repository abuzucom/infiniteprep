#!/usr/bin/env python3
"""Test which github context expression forms count as event data."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_compliance_tree

WHOLE_CONTEXT_FORMS = (
    "${{ github }}",
    "${{github}}",
    "${{ github || 'fallback' }}",
    "${{ toJSON(github) }}",
    "${{ format('{0}', github) }}",
    "${{ github['event'] }}",
)
EVENT_FIELD_FORMS = (
    "${{ github.event.pull_request.title }}",
    "${{ github.head_ref }}",
)
SAFE_FORMS = (
    "${{ github.sha }}",
    "${{ github.event_name }}",
    "${{ github.repository }}",
    "${{ secrets.GITHUB_TOKEN }}",
)


class ContextFormTest(unittest.TestCase):
    """The whole github context and its event fields count as event data."""

    def matches(self, value: str) -> bool:
        """Return whether the checker treats the value as event data."""
        return check_compliance_tree._has_event_expression(value)

    def test_whole_context_forms_match(self) -> None:
        for value in WHOLE_CONTEXT_FORMS:
            with self.subTest(value=value):
                self.assertTrue(self.matches(value))

    def test_event_field_forms_match(self) -> None:
        for value in EVENT_FIELD_FORMS:
            with self.subTest(value=value):
                self.assertTrue(self.matches(value))

    def test_safe_property_forms_pass(self) -> None:
        for value in SAFE_FORMS:
            with self.subTest(value=value):
                self.assertFalse(self.matches(value))


if __name__ == "__main__":
    unittest.main()
