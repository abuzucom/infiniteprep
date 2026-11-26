#!/usr/bin/env python3
"""The signature issuer parser covers every packet and subpacket length form.

Synthetic v4 signature packets exercise each branch of the parser. A valid
form yields the issuer key ID. A malformed or truncated form yields no issuer.
"""
import base64
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_commit_attribution as attribution

KEY_ID = bytes.fromhex("B5690EEEBB952194")
KEY_ID_HEX = "B5690EEEBB952194"
FINGERPRINT = bytes([4]) + bytes(range(12)) + KEY_ID
ISSUER_TYPE = 16
FINGERPRINT_TYPE = 33
NOTATION_TYPE = 20
SIGNATURE_TAG = 2
OTHER_TAG = 11
OLD_FORMAT_BASE = 0x80
NEW_FORMAT_BASE = 0xC0
SUBPACKET_TWO_BYTE_SIZE = 300
SUBPACKET_FIVE_BYTE_SIZE = 20
PACKET_TWO_BYTE_FILLER = 250
PACKET_FIVE_BYTE_FILLER = 9000
SIGNATURE_TRAILER = bytes([0, 0, 0xAB, 0xCD])


def subpacket(kind: int, data: bytes, form: str = "one") -> bytes:
    """Return one subpacket with the requested length encoding."""
    length = len(data) + 1
    if form == "one":
        header = bytes([length])
    elif form == "two":
        offset = length - 192
        header = bytes([(offset >> 8) + 192, offset & 0xFF])
    else:
        header = bytes([0xFF]) + length.to_bytes(4, "big")
    return header + bytes([kind]) + data


def signature_body(hashed: bytes, version: int = 4) -> bytes:
    """Return a signature packet body with the given hashed subpacket area."""
    return bytes([version, 0, 1, 8]) + len(hashed).to_bytes(2, "big") + hashed + SIGNATURE_TRAILER


def new_packet(body: bytes, tag: int = SIGNATURE_TAG) -> bytes:
    """Return a new-format packet using the shortest length encoding."""
    length = len(body)
    if length < 192:
        header = bytes([length])
    elif length < 8384:
        offset = length - 192
        header = bytes([(offset >> 8) + 192, offset & 0xFF])
    else:
        header = bytes([0xFF]) + length.to_bytes(4, "big")
    return bytes([NEW_FORMAT_BASE | tag]) + header + body


def old_packet(body: bytes, length_type: int, tag: int = SIGNATURE_TAG) -> bytes:
    """Return an old-format packet with a one, two, or four byte length."""
    size = {0: 1, 1: 2, 2: 4}[length_type]
    return bytes([OLD_FORMAT_BASE | (tag << 2) | length_type]) + len(body).to_bytes(size, "big") + body


def armor(packet: bytes) -> str:
    """Return the ASCII-armored form of one binary packet."""
    encoded = base64.b64encode(packet).decode("ascii")
    lines = [encoded[i:i + 64] for i in range(0, len(encoded), 64)]
    return "\n".join(["-----BEGIN PGP SIGNATURE-----", "", *lines, "=AAAA", "-----END PGP SIGNATURE-----", ""])


ISSUER = subpacket(ISSUER_TYPE, KEY_ID)


class ValidFormTest(unittest.TestCase):
    """Each valid length encoding yields the issuer key ID."""

    def assert_issuer(self, packet: bytes):
        self.assertEqual(attribution.signature_issuer(armor(packet)), KEY_ID_HEX)

    def test_new_format_lengths(self):
        cases = {
            "one byte": b"",
            "two byte": subpacket(NOTATION_TYPE, bytes(PACKET_TWO_BYTE_FILLER), "two"),
            "five byte": subpacket(NOTATION_TYPE, bytes(PACKET_FIVE_BYTE_FILLER), "two"),
        }
        for name, filler in cases.items():
            with self.subTest(length=name):
                self.assert_issuer(new_packet(signature_body(filler + ISSUER)))

    def test_old_format_lengths(self):
        for length_type in (0, 1, 2):
            with self.subTest(length_type=length_type):
                self.assert_issuer(old_packet(signature_body(ISSUER), length_type))

    def test_subpacket_lengths(self):
        cases = {
            "two byte": subpacket(NOTATION_TYPE, bytes(SUBPACKET_TWO_BYTE_SIZE), "two"),
            "five byte": subpacket(NOTATION_TYPE, bytes(SUBPACKET_FIVE_BYTE_SIZE), "five"),
        }
        for name, filler in cases.items():
            with self.subTest(length=name):
                self.assert_issuer(new_packet(signature_body(filler + ISSUER)))

    def test_fingerprint_subpacket(self):
        hashed = subpacket(FINGERPRINT_TYPE, FINGERPRINT)
        self.assert_issuer(new_packet(signature_body(hashed)))

    def test_critical_bit_is_ignored(self):
        hashed = subpacket(ISSUER_TYPE | 0x80, KEY_ID)
        self.assert_issuer(new_packet(signature_body(hashed)))


class MalformedFormTest(unittest.TestCase):
    """Each malformed or truncated form yields no issuer."""

    def test_malformed_packets(self):
        good = new_packet(signature_body(ISSUER))
        cases = {
            "truncated body": good[:-3],
            "one byte only": good[:1],
            "missing packet flag": bytes([0x02]) + good[1:],
            "indeterminate old length": bytes([OLD_FORMAT_BASE | (SIGNATURE_TAG << 2) | 3]) + good[2:],
            "short old length field": old_packet(signature_body(ISSUER), 2)[:3],
            "partial new length": bytes([NEW_FORMAT_BASE | SIGNATURE_TAG, 0xE0]) + good[2:],
            "truncated two byte length": bytes([NEW_FORMAT_BASE | SIGNATURE_TAG, 0xC0]),
            "truncated five byte length": bytes([NEW_FORMAT_BASE | SIGNATURE_TAG, 0xFF, 0, 0]),
            "other packet tag": new_packet(signature_body(ISSUER), OTHER_TAG),
            "version 3": new_packet(signature_body(ISSUER, version=3)),
            "body too short": new_packet(bytes([4, 0, 1])),
            "hashed length overrun": new_packet(bytes([4, 0, 1, 8, 0xFF, 0xFF]) + ISSUER),
        }
        for name, packet in cases.items():
            with self.subTest(case=name):
                self.assertEqual(attribution.signature_issuer(armor(packet)), "")

    def test_malformed_subpackets(self):
        cases = {
            "length overrun": bytes([40, ISSUER_TYPE]) + KEY_ID,
            "zero length": bytes([0]) + ISSUER,
            "truncated two byte length": bytes([0xC0]),
            "truncated five byte length": bytes([0xFF, 0, 0]),
            "short issuer": subpacket(ISSUER_TYPE, KEY_ID[:7]),
            "short fingerprint": subpacket(FINGERPRINT_TYPE, FINGERPRINT[:20]),
        }
        for name, hashed in cases.items():
            with self.subTest(case=name):
                packet = new_packet(signature_body(hashed))
                self.assertEqual(attribution.signature_issuer(armor(packet)), "")

    def test_unhashed_issuer_is_ignored(self):
        body = bytes([4, 0, 1, 8, 0, 0]) + len(ISSUER).to_bytes(2, "big") + ISSUER + bytes([0xAB, 0xCD])
        self.assertEqual(attribution.signature_issuer(armor(new_packet(body))), "")

    def test_oversized_armor(self):
        packet = new_packet(signature_body(ISSUER))
        oversized = armor(packet) + " " * attribution.MAX_SIGNATURE_CHARACTERS
        self.assertEqual(attribution.signature_issuer(oversized), "")


if __name__ == "__main__":
    unittest.main()
