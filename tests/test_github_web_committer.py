#!/usr/bin/env python3
"""A GitHub-signed web-flow committer passes strict attribution; nothing else does.

GitHub commits a web merge or squash as `GitHub <noreply@github.com>`. Its
API resolves that identity to the web-flow account. A squash keeps the merged commits'
agent trailers, so the strict numbered-noreply rule applies to it. The
committer exemption needs every web-flow marker plus a signature that
verifies against GitHub's pinned web-flow key. The author keeps the strict
rule.
"""
import base64
import copy
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_commit_attribution as attribution

FIXTURES = ROOT / "tests" / "fixtures" / "web_flow"
# A real web-flow signature from this repository's history, and one made by
# a throwaway key. Both are public signature data, never keys.
GITHUB_SIGNATURE = (FIXTURES / "github_signature.asc").read_text(encoding="ascii")
OTHER_SIGNATURE = (FIXTURES / "other_signature.asc").read_text(encoding="ascii")
# The payload GitHub reports for the commit that GITHUB_SIGNATURE signs.
GITHUB_PAYLOAD = json.loads((FIXTURES / "github_payload.json").read_text(encoding="ascii"))["payload"]
GITHUB_KEY_ID = bytes.fromhex("B5690EEEBB952194")
OTHER_KEY_ID = bytes.fromhex("405A1684C1B51779")
AUTHOR_ID = 1234567
AUTHOR_EMAIL = f"{AUTHOR_ID}+octocat@users.noreply.github.com"
WEB_FLOW = {"login": "web-flow", "id": 19864447}


def web_commit() -> dict:
    """Return GitHub API metadata for a signed web squash commit."""
    return {
        "sha": "a" * 40,
        "author": {"login": "octocat", "id": AUTHOR_ID},
        "committer": dict(WEB_FLOW),
        "commit": {
            "author": {"email": AUTHOR_EMAIL},
            "committer": {"email": "noreply@github.com"},
            "verification": {"verified": True, "reason": "valid",
                             "signature": GITHUB_SIGNATURE, "payload": GITHUB_PAYLOAD},
        },
    }


def committer_violation(document: dict) -> str | None:
    """Return the strict committer verdict for one commit document."""
    return attribution._identity_violation(
        "committer", document, document.get("committer"), require_noreply=True)


class WebFlowCommitterTest(unittest.TestCase):
    """Only a GitHub-signed web-flow commit skips the committer noreply rule."""

    def test_signed_web_flow_committer_passes(self):
        self.assertIsNone(committer_violation(web_commit()))

    def test_author_stays_strict(self):
        document = web_commit()
        document["commit"]["author"]["email"] = "noreply@github.com"
        violation = attribution._identity_violation(
            "author", document, document["author"], require_noreply=True)
        self.assertIn("numbered GitHub noreply", violation)

    def test_each_marker_is_required(self):
        mutations = {
            "email": lambda doc: doc["commit"]["committer"].update(email="ci@example.com"),
            "login": lambda doc: doc["committer"].update(login="web-flow-bot"),
            "account id": lambda doc: doc["committer"].update(id=42),
            "unverified": lambda doc: doc["commit"]["verification"].update(verified=False),
            "no verification": lambda doc: doc["commit"].pop("verification"),
            "reason not valid": lambda doc: doc["commit"]["verification"].update(
                reason="unknown_key"),
            "no reason": lambda doc: doc["commit"]["verification"].pop("reason"),
            "other signer": lambda doc: doc["commit"]["verification"].update(
                signature=OTHER_SIGNATURE),
            "no signature": lambda doc: doc["commit"]["verification"].pop("signature"),
            "forged issuer": lambda doc: doc["commit"]["verification"].update(
                signature=forged_issuer_signature()),
            "payload changed": lambda doc: doc["commit"]["verification"].update(
                payload=GITHUB_PAYLOAD + " "),
            "no payload": lambda doc: doc["commit"]["verification"].pop("payload"),
            "malformed signature": lambda doc: doc["commit"]["verification"].update(
                signature="-----BEGIN PGP SIGNATURE-----\n\nnot base64!\n"),
        }
        for name, mutate in mutations.items():
            with self.subTest(marker=name):
                document = copy.deepcopy(web_commit())
                mutate(document)
                self.assertTrue(committer_violation(document))


def forged_issuer_signature() -> str:
    """Return the throwaway-key signature relabeled with GitHub's key ID."""
    lines = OTHER_SIGNATURE.strip().splitlines()
    body = "".join(line for line in lines[1:] if line and not line.startswith(("=", "-----")))
    packet = base64.b64decode(body).replace(OTHER_KEY_ID, GITHUB_KEY_ID)
    encoded = base64.b64encode(packet).decode("ascii")
    return "\n".join(["-----BEGIN PGP SIGNATURE-----", "", encoded, "-----END PGP SIGNATURE-----", ""])


class SignatureIssuerTest(unittest.TestCase):
    """The issuer key ID comes from the signature's hashed subpackets."""

    def test_github_signature_issuer(self):
        self.assertEqual(attribution.signature_issuer(GITHUB_SIGNATURE), "B5690EEEBB952194")

    def test_other_signature_issuer(self):
        self.assertEqual(attribution.signature_issuer(OTHER_SIGNATURE), "405A1684C1B51779")

    def test_forged_issuer_reads_as_github(self):
        self.assertEqual(attribution.signature_issuer(forged_issuer_signature()), "B5690EEEBB952194")

    def test_unreadable_signature_has_no_issuer(self):
        for value in ("", "plain text", None, "-----BEGIN PGP SIGNATURE-----\n\nAAAA\n"):
            with self.subTest(value=value):
                self.assertEqual(attribution.signature_issuer(value), "")


if __name__ == "__main__":
    unittest.main()
