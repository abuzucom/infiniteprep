#!/usr/bin/env python3
"""Exercise the divisor checks in the size report renderer."""
import unittest

from scripts import check_policy_size

AGENTS_LIMIT = 32 * 1024
ASSEMBLED_LIMIT = 64 * 1024
SAMPLE_BYTES = 100


class RenderDivisorTests(unittest.TestCase):
    def test_zero_limit_value_error(self):
        for rows in (
            [("AGENTS.md", SAMPLE_BYTES, 0),
             ("assembled policy", SAMPLE_BYTES, ASSEMBLED_LIMIT)],
            [("AGENTS.md", SAMPLE_BYTES, AGENTS_LIMIT),
             ("assembled policy", SAMPLE_BYTES, 0)],
        ):
            with self.subTest(rows=rows):
                with self.assertRaisesRegex(ValueError, "limit must be positive"):
                    check_policy_size.render_markdown(rows)

    def test_negative_limit_raises_value_error(self):
        rows = [("AGENTS.md", SAMPLE_BYTES, -1),
                ("assembled policy", SAMPLE_BYTES, ASSEMBLED_LIMIT)]
        with self.assertRaisesRegex(ValueError, "limit must be positive"):
            check_policy_size.render_markdown(rows)

    def test_zero_total_shares(self):
        rows = [("AGENTS.md", 0, AGENTS_LIMIT), ("assembled policy", 0, ASSEMBLED_LIMIT)]
        body = check_policy_size.render_markdown(rows, [("AGENTS.md", 0)])
        self.assertIn("| 1 | `AGENTS.md` | 0 | 0.0% |", body)


if __name__ == "__main__":
    unittest.main()
