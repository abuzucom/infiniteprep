#!/usr/bin/env python3
"""Test that reviewed workflows cannot reference the whole secrets context."""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import check_compliance_tree

NOTICE_PATH = ".github/workflows/stop-requested-notice.yml"
# A well-formed sha256 that the structural workflow checks never compare.
PLACEHOLDER_DIGEST = "0" * 64
NOTICE = """\
name: stop-requested-notice
"on":
  pull_request_target:
    types: [labeled]
permissions: {}
jobs:
  notice:
    runs-on: ubuntu-latest
    steps:
      - run: echo ${{ secrets.MODEL_KEY }}
"""
WHOLE_MESSAGE = (
    f"{NOTICE_PATH}: reviewed pull_request_target references the whole secrets context")
WHOLE_FORMS = (
    "${{ toJSON(secrets) }}",
    "${{ fromJSON(toJSON(secrets)) }}",
    "${{ secrets }}",
    "${{ secrets || 'none' }}",
)


class WholeSecretsTest(unittest.TestCase):
    """Only named secrets.<NAME> references pass the reviewed allowlist."""

    persist = check_compliance_tree._load_trusted("check_persist_credentials")

    def setUp(self) -> None:
        document = {"version": 1, "pull_request_target": {
            NOTICE_PATH: {"secrets": ["MODEL_KEY"], "sha256": PLACEHOLDER_DIGEST}}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / check_compliance_tree.EXEMPTIONS_FILE).write_text(
                json.dumps(document), encoding="ascii")
            self.exemptions = check_compliance_tree.load_exemptions(root)

    def scan(self, text: str) -> list[str]:
        """Return workflow violations for the notice path."""
        return check_compliance_tree._workflow_violations(
            text, NOTICE_PATH, self.persist, self.exemptions)

    def test_named_secret_passes(self) -> None:
        self.assertEqual(self.scan(NOTICE), [])

    def test_whole_context_in_run_fails(self) -> None:
        for form in WHOLE_FORMS:
            with self.subTest(form=form):
                text = NOTICE.replace("${{ secrets.MODEL_KEY }}", form)
                self.assertIn(WHOLE_MESSAGE, self.scan(text))

    def test_whole_context_in_with_fails(self) -> None:
        text = NOTICE.replace(
            "      - run: echo ${{ secrets.MODEL_KEY }}\n",
            "      - uses: ./.github/actions/local\n        with:\n"
            "          payload: ${{ toJSON(secrets) }}\n")
        self.assertIn(WHOLE_MESSAGE, self.scan(text))

    def test_secrets_key_outside_expression_passes(self) -> None:
        text = NOTICE.replace(
            "    runs-on: ubuntu-latest\n",
            "    runs-on: ubuntu-latest\n    env:\n      NOTE: secrets stay named\n")
        self.assertEqual(self.scan(text), [])


if __name__ == "__main__":
    unittest.main()
