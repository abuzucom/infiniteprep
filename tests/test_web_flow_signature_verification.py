#!/usr/bin/env python3
"""GitHub's pinned web-flow key verifies only GitHub's own signatures.

Each case alters one field of a real web-flow signature packet. Any change to
a signed field, the signature value, or the payload fails verification.
"""
import base64
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_commit_attribution as attribution

FIXTURES = ROOT / "tests" / "fixtures" / "web_flow"
GITHUB_SIGNATURE = (FIXTURES / "github_signature.asc").read_text(encoding="ascii")
OTHER_SIGNATURE = (FIXTURES / "other_signature.asc").read_text(encoding="ascii")
GITHUB_PAYLOAD = json.loads((FIXTURES / "github_payload.json").read_text(encoding="ascii"))["payload"]
SIGNATURE_TYPE_INDEX = 1
PUBLIC_KEY_ALGORITHM_INDEX = 2
HASH_ALGORITHM_INDEX = 3
TEXT_SIGNATURE_TYPE = 0x01
DSA_ALGORITHM = 17
SHA1_ALGORITHM = 2
SHA512_ALGORITHM = 10


def packet_bytes(armored: str) -> bytes:
    """Return the binary packet inside one armored signature."""
    lines = armored.strip().splitlines()
    return base64.b64decode("".join(line for line in lines[1:] if line and not line.startswith(("=", "-----"))))


def armor(packet: bytes) -> str:
    """Return the armored form of one binary packet."""
    encoded = base64.b64encode(packet).decode("ascii")
    return "\n".join(["-----BEGIN PGP SIGNATURE-----", "", encoded, "-----END PGP SIGNATURE-----", ""])


def split_packet(packet: bytes) -> tuple:
    """Return the packet header and body."""
    _, start, _ = attribution._packet_length(packet)
    return packet[:start], packet[start:]


def with_body_byte(index: int, value: int) -> str:
    """Return the GitHub signature with one body byte replaced."""
    header, body = split_packet(packet_bytes(GITHUB_SIGNATURE))
    return armor(header + body[:index] + bytes([value]) + body[index + 1:])


def with_signature_value(value: int) -> str:
    """Return the GitHub signature with its RSA value replaced."""
    header, body = split_packet(packet_bytes(GITHUB_SIGNATURE))
    size = attribution.GITHUB_WEB_FLOW_MODULUS_BYTES
    body = body[:-size] + value.to_bytes(size, "big")
    return armor(header + body)


class VerifyWebFlowSignatureTest(unittest.TestCase):
    """Only an unaltered GitHub signature over its payload verifies."""

    def test_github_signature_verifies(self):
        self.assertTrue(attribution.verify_web_flow_signature(GITHUB_PAYLOAD, GITHUB_SIGNATURE))

    def test_altered_inputs_fail(self):
        header, body = split_packet(packet_bytes(GITHUB_SIGNATURE))
        cases = {
            "other key": (GITHUB_PAYLOAD, OTHER_SIGNATURE),
            "payload changed": (GITHUB_PAYLOAD.replace("tree", "tref", 1), GITHUB_SIGNATURE),
            "payload not text": (GITHUB_PAYLOAD.encode("ascii"), GITHUB_SIGNATURE),
            "payload missing": (None, GITHUB_SIGNATURE),
            "payload oversized": ("x" * (attribution.MAX_PAYLOAD_CHARACTERS + 1), GITHUB_SIGNATURE),
            "signature missing": (GITHUB_PAYLOAD, None),
            "text signature type": (GITHUB_PAYLOAD, with_body_byte(SIGNATURE_TYPE_INDEX, TEXT_SIGNATURE_TYPE)),
            "non-RSA algorithm": (GITHUB_PAYLOAD, with_body_byte(PUBLIC_KEY_ALGORITHM_INDEX, DSA_ALGORITHM)),
            "unsupported hash": (GITHUB_PAYLOAD, with_body_byte(HASH_ALGORITHM_INDEX, SHA1_ALGORITHM)),
            "different hash": (GITHUB_PAYLOAD, with_body_byte(HASH_ALGORITHM_INDEX, SHA512_ALGORITHM)),
            "zero signature": (GITHUB_PAYLOAD, with_signature_value(0)),
            "signature off by one": (GITHUB_PAYLOAD, with_signature_value(
                int.from_bytes(body[-attribution.GITHUB_WEB_FLOW_MODULUS_BYTES:], "big") ^ 1)),
            "truncated value": (GITHUB_PAYLOAD, armor(header + body[:-1])),
            "trailing data": (GITHUB_PAYLOAD, armor(header + body + b"\x00")),
            "no unhashed length": (GITHUB_PAYLOAD, armor(header + body[:attribution._hashed_end(body) + 1])),
            "no value header": (GITHUB_PAYLOAD, armor(header + body[:attribution._hashed_end(body) + 2 + 2])),
        }
        for name, (payload, signature) in cases.items():
            with self.subTest(case=name):
                self.assertFalse(attribution.verify_web_flow_signature(payload, signature))

    def test_value_above_modulus_fails(self):
        signature = with_signature_value(attribution.GITHUB_WEB_FLOW_MODULUS)
        self.assertFalse(attribution.verify_web_flow_signature(GITHUB_PAYLOAD, signature))


class Pkcs1EncodingTest(unittest.TestCase):
    """The encoding rejects a digest too long for the minimum padding."""

    def test_oversized_digest_info(self):
        oversized = bytes(attribution.GITHUB_WEB_FLOW_MODULUS_BYTES)
        self.assertEqual(attribution._pkcs1_encoding(oversized), b"")


if __name__ == "__main__":
    unittest.main()
