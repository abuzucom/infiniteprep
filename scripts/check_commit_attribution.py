#!/usr/bin/env python3
"""Enforce verified commit identities and approved co-author trailers."""
import argparse
import base64
import binascii
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

try:
    from scripts._commit_trailers import (
        COAUTHOR_PATTERN, TRAILER_PATTERN, terminal_paragraph, terminal_trailers)
    from scripts.trusted_git import run_git
    from scripts.trusted_gh import run_gh
except ModuleNotFoundError:
    from _commit_trailers import (
        COAUTHOR_PATTERN, TRAILER_PATTERN, terminal_paragraph, terminal_trailers)
    from trusted_git import run_git
    from trusted_gh import run_gh


APPROVED_HUMAN_COAUTHORS = frozenset()
NOREPLY_PATTERN = re.compile(
    r"\A(?P<account_id>[0-9]+)\+(?P<login>[A-Za-z0-9-]+)"
    r"@users\.noreply\.github\.com\Z",
    re.IGNORECASE,
)
OBJECT_ID_PATTERN = re.compile(r"\A[0-9a-fA-F]{40,64}\Z")
# A bare vendor or tool name ("Claude", "Codex") names no model. Every
# released model name carries a version digit.
VERSION_DIGIT_PATTERN = re.compile(r"[0-9]")
NON_PRINTABLE_PATTERN = re.compile(r"[^\x20-\x7e]")
MAX_REPORTED_MODEL = 80
MAX_COMMITS = 200
MAX_MESSAGE_BYTES = 256 * 1024
SHORT_SHA_LENGTH = 12
# GitHub commits web merges and squashes as this account and signs every
# one. The id is GitHub's fixed web-flow account id.
GITHUB_WEB_COMMITTER_EMAIL = "noreply@github.com"
GITHUB_WEB_COMMITTER_LOGIN = "web-flow"
GITHUB_WEB_COMMITTER_ID = 19864447
# GitHub's current web-flow signing key: RSA 4096, fingerprint
# 968479A1AFF927E37D1A566BB5690EEEBB952194, created 2024-01-16. The checker
# verifies each web-flow signature against this pinned public key. A key ID
# inside a signature is signer-supplied and proves nothing on its own.
GITHUB_WEB_FLOW_KEY_ID = "B5690EEEBB952194"
GITHUB_WEB_FLOW_EXPONENT = 65537
GITHUB_WEB_FLOW_MODULUS = int(
    "B237EE2F88540CE904282ABD00D96D2B19D3286D3270399B28D903E97B06F206"
    "CB8BDEB356E4E987AD6C170213CA36A508C8ECDDABC3284B1A134EEA8B92CA1A"
    "862C442FF5F69F284E07147D1A4633FE823D4779D870B45A150A1226855F3ADE"
    "B5A2990B5336D601E8696E04FC10A967F09B0436AD3ED270E55908B487643DA0"
    "69AA960573B24C26559F0B7B2BF8320EE137AB30C99EE6A197E49290F9F94ED9"
    "D9756EAC2CE44927E0E336F694C44B268B3678201CEF8F440AC5F4B7B3817ED1"
    "7E62A8232178B15EC9B646B52ABD4E3B3A43AD65E021BC061E9DB0B7A6BF4585"
    "AE4DC6805411AFB5D4C2BD5BA39C461C2FC55094ACC511E854D26720BFFD5A57"
    "4D3B6B4653AAA54E7018C6CFDF3A67F607AA3970A5F2B17BFB58003C6FE8F901"
    "504C1B1512BBB08B8256A20890B90C8EC0022515EFFB76F6B15991A9BFFC96DB"
    "E2F782E2FBC5A1CA1551AC6B28658AD410E576BE3F589280A4970F245E09B5A8"
    "B5FA807A6A4FC92236E4B1E1DD271723A3607474AB481685A23C007E0C5B3D31"
    "10E9A748B0A1C24287EB9CBACD6B750E7860C688E38AD1A615DE38840C6028B9"
    "AB6435FDAAF39128B23D5FF65682A801F760B243EDD7129DED620A12B9A914C1"
    "C8CBC8C9E483D0D6CD9D5305386A57C92C96B4A6F695DA6EF00581409B09B3D1"
    "66601716006040DA9A7E2316829FAAC120722C51515137C39564AB04B01916CB",
    16,
)
GITHUB_WEB_FLOW_MODULUS_BYTES = (GITHUB_WEB_FLOW_MODULUS.bit_length() + 7) // 8
BINARY_SIGNATURE_TYPE = 0x00
# OpenPGP public-key algorithm ids 1 (RSA) and 3 (RSA sign-only).
RSA_ALGORITHM_IDS = frozenset({1, 3})
# OpenPGP hash algorithm id -> (hashlib name, PKCS#1 v1.5 DigestInfo prefix).
DIGEST_INFO = {
    8: ("sha256", bytes.fromhex("3031300d060960864801650304020105000420")),
    9: ("sha384", bytes.fromhex("3041300d060960864801650304020205000430")),
    10: ("sha512", bytes.fromhex("3051300d060960864801650304020305000440")),
}
V4_HASH_TRAILER = b"\x04\xff"
TRAILER_LENGTH_BYTES = 4
UNHASHED_LENGTH_BYTES = 2
LEFT_DIGEST_BYTES = 2
MPI_LENGTH_BYTES = 2
MIN_PKCS1_PADDING_BYTES = 8
MAX_PAYLOAD_CHARACTERS = 1024 * 1024
# GitHub reports this reason for a signature that verifies cleanly.
GITHUB_VALID_REASON = "valid"
ARMOR_HEADER = "-----BEGIN PGP SIGNATURE-----"
MAX_SIGNATURE_CHARACTERS = 16384
SIGNATURE_PACKET_TAG = 2
SIGNATURE_VERSION = 4
ISSUER_KEY_ID_SUBPACKET = 16
ISSUER_FINGERPRINT_SUBPACKET = 33
KEY_ID_BYTES = 8
V4_FINGERPRINT_SUBPACKET_BYTES = 21
NEW_FORMAT_FLAG = 0x40
PACKET_FLAG = 0x80
ONE_BYTE_LENGTH_LIMIT = 192
TWO_BYTE_LENGTH_LIMIT = 224
SUBPACKET_TWO_BYTE_LIMIT = 255
FOUR_BYTE_LENGTH_MARKER = 255
OLD_FORMAT_LENGTH_BYTES = {0: 1, 1: 2, 2: 4}
HASHED_AREA_OFFSET = 4
GIT_RANGE_TIMEOUT_SECONDS = 60
GIT_COMMIT_TIMEOUT_SECONDS = 30
GITHUB_API_TIMEOUT_SECONDS = 30
# Object ID, then the message.
COMMIT_METADATA_FIELD_COUNT = 2


def trailer_violations(commits: list[dict]) -> list[str]:
    """Return violations for unapproved co-author and assisted-by trailers."""
    violations = []
    for commit in commits:
        sha = commit["sha"][:SHORT_SHA_LENGTH]
        body = commit.get("body", "")
        trailer_lines = terminal_paragraph(body)
        has_trailer = any(TRAILER_PATTERN.fullmatch(line) for line in trailer_lines)
        if has_trailer:
            for line in trailer_lines:
                if not line.startswith((" ", "\t")) and not TRAILER_PATTERN.fullmatch(line):
                    violations.append(
                        f"{sha}: non-trailer text in terminal trailer paragraph '{line}'"
                    )
        for key, value in terminal_trailers(body):
            norm_key = key.lower().replace(" ", "-")
            if norm_key == "co-authored-by":
                match = COAUTHOR_PATTERN.fullmatch(value.strip())
                if not match:
                    violations.append(f"{sha}: malformed co-author trailer")
                    continue
                name = match.group("name").strip()
                email = match.group("email")
                if not email and name:
                    continue
                if email and (name, email) in APPROVED_HUMAN_COAUTHORS:
                    continue
                violations.append(f"{sha}: unapproved co-author trailer for '{name}'")
            elif norm_key == "assisted-by":
                stripped = value.strip()
                if not stripped:
                    violations.append(f"{sha}: malformed assisted-by trailer")
                    continue
                match = COAUTHOR_PATTERN.fullmatch(stripped)
                if not match or not match.group("name").strip():
                    violations.append(f"{sha}: malformed assisted-by trailer")
                    continue
                if match.group("email"):
                    violations.append(
                        f"{sha}: assisted-by trailer must not include an email"
                    )
    return violations


def _git_log(repository: str, base: str, head: str) -> list[dict]:
    """Load bounded commit metadata from one validated range."""
    revision = f"{base}..{head}"
    result = run_git(
        repository,
        ["rev-list", "--reverse", "--end-of-options", revision],
        check=True,
        runner=subprocess.run,
        timeout=GIT_RANGE_TIMEOUT_SECONDS,
    )
    shas = result.stdout.splitlines()
    if len(shas) > MAX_COMMITS or any(not OBJECT_ID_PATTERN.fullmatch(sha) for sha in shas):
        raise ValueError("commit range exceeds the supported limit")
    commits = []
    for sha in shas:
        metadata = run_git(
            repository,
            ["show", "--no-ext-diff", "--no-patch", f"--format=%H%x00%B",
             "--end-of-options", sha],
            check=True,
            runner=subprocess.run,
            timeout=GIT_COMMIT_TIMEOUT_SECONDS,
        ).stdout
        fields = metadata.split("\x00", 1)
        if len(fields) != COMMIT_METADATA_FIELD_COUNT or fields[0] != sha:
            raise ValueError("git returned malformed commit metadata")
        if len(fields[1].encode("utf-8")) > MAX_MESSAGE_BYTES:
            raise ValueError("commit message exceeds the supported limit")
        commits.append({"sha": sha, "body": fields[1]})
    return commits


def _repository_name(explicit: str) -> str:
    """Return the validated GitHub repository name for API requests."""
    value = explicit or os.environ.get("GITHUB_REPOSITORY", "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value):
        raise ValueError("GitHub repository is missing or malformed")
    return value


def _commit_api(repository: str, sha: str, repo_root: str) -> dict:
    """Load one commit from GitHub through the trusted CLI wrapper."""
    endpoint = f"repos/{repository}/commits/{sha}"
    result = run_gh(repo_root, ["api", endpoint], timeout=GITHUB_API_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise RuntimeError("GitHub commit identity lookup failed")
    document = json.loads(result.stdout)
    if document.get("sha") != sha:
        raise ValueError("GitHub returned an unexpected commit")
    return document


def _dearmor(armored) -> bytes:
    """Return the binary body of one ASCII-armored PGP signature, or b""."""
    if not isinstance(armored, str) or len(armored) > MAX_SIGNATURE_CHARACTERS:
        return b""
    lines = armored.strip().splitlines()
    if not lines or lines[0].strip() != ARMOR_HEADER:
        return b""
    body = []
    for line in lines[1:]:
        stripped = line.strip()
        if stripped.startswith(("-----END", "=")):
            break
        if stripped and ":" not in stripped:
            body.append(stripped)
    try:
        return base64.b64decode("".join(body), validate=True)
    except (binascii.Error, ValueError):
        return b""


def _packet_length(data: bytes) -> tuple:
    """Return (tag, body start, body length) for the first packet, or (0, 0, 0)."""
    if len(data) < 2 or not data[0] & PACKET_FLAG:
        return 0, 0, 0
    if data[0] & NEW_FORMAT_FLAG:
        first = data[1]
        if first < ONE_BYTE_LENGTH_LIMIT:
            return data[0] & 0x3F, 2, first
        if first < TWO_BYTE_LENGTH_LIMIT and len(data) > 2:
            return data[0] & 0x3F, 3, ((first - ONE_BYTE_LENGTH_LIMIT) << 8) + data[2] + ONE_BYTE_LENGTH_LIMIT
        if first == FOUR_BYTE_LENGTH_MARKER and len(data) >= 6:
            return data[0] & 0x3F, 6, int.from_bytes(data[2:6], "big")
        return 0, 0, 0
    size = OLD_FORMAT_LENGTH_BYTES.get(data[0] & 3)
    if size is None or len(data) < 1 + size:
        return 0, 0, 0
    return (data[0] >> 2) & 0x0F, 1 + size, int.from_bytes(data[1:1 + size], "big")


def _signature_body(data: bytes) -> bytes:
    """Return the body of the one v4 signature packet in `data`, or b"".

    Any data after the packet fails, so the armor carries exactly one packet.
    """
    tag, start, length = _packet_length(data)
    body = data[start:start + length]
    if tag != SIGNATURE_PACKET_TAG or start + length != len(data) or len(body) < HASHED_AREA_OFFSET + 2:
        return b""
    if body[0] != SIGNATURE_VERSION:
        return b""
    return body


def _hashed_end(body: bytes) -> int:
    """Return the offset just past the hashed subpacket area, or 0."""
    if not body:
        return 0
    hashed_length = int.from_bytes(body[HASHED_AREA_OFFSET:HASHED_AREA_OFFSET + 2], "big")
    end = HASHED_AREA_OFFSET + 2 + hashed_length
    return end if end <= len(body) else 0


def _hashed_area(data: bytes) -> bytes:
    """Return the hashed subpacket area of a v4 signature packet, or b""."""
    body = _signature_body(data)
    end = _hashed_end(body)
    return body[HASHED_AREA_OFFSET + 2:end] if end else b""


def _signature_value(body: bytes, end: int) -> int:
    """Return the RSA signature integer that ends the packet, or 0."""
    unhashed = body[end:end + UNHASHED_LENGTH_BYTES]
    if len(unhashed) != UNHASHED_LENGTH_BYTES:
        return 0
    mpi_start = end + UNHASHED_LENGTH_BYTES + int.from_bytes(unhashed, "big") + LEFT_DIGEST_BYTES
    header = body[mpi_start:mpi_start + MPI_LENGTH_BYTES]
    if len(header) != MPI_LENGTH_BYTES:
        return 0
    size = (int.from_bytes(header, "big") + 7) // 8
    value_start = mpi_start + MPI_LENGTH_BYTES
    if size == 0 or value_start + size != len(body):
        return 0
    return int.from_bytes(body[value_start:], "big")


def _pkcs1_encoding(digest_info: bytes) -> bytes:
    """Return the EMSA-PKCS1-v1_5 encoding of one DigestInfo, or b""."""
    padding = GITHUB_WEB_FLOW_MODULUS_BYTES - len(digest_info) - 3
    if padding < MIN_PKCS1_PADDING_BYTES:
        return b""
    return b"\x00\x01" + b"\xff" * padding + b"\x00" + digest_info


def verify_web_flow_signature(payload, armored) -> bool:
    """Return whether GitHub's pinned web-flow key signed `payload`.

    The check follows OpenPGP v4 RSA verification (RFC 4880 5.2.4). It
    hashes the payload, the signed packet fields, and the v4 trailer. It
    then compares the PKCS#1 v1.5 encoding with the decrypted signature.
    """
    if not isinstance(payload, str) or len(payload) > MAX_PAYLOAD_CHARACTERS:
        return False
    body = _signature_body(_dearmor(armored))
    end = _hashed_end(body)
    if not end or body[1] != BINARY_SIGNATURE_TYPE or body[2] not in RSA_ALGORITHM_IDS:
        return False
    algorithm = DIGEST_INFO.get(body[3])
    signature = _signature_value(body, end)
    if algorithm is None or not 0 < signature < GITHUB_WEB_FLOW_MODULUS:
        return False
    hasher = hashlib.new(algorithm[0])
    hasher.update(payload.encode("utf-8"))
    hasher.update(body[:end])
    hasher.update(V4_HASH_TRAILER + end.to_bytes(TRAILER_LENGTH_BYTES, "big"))
    expected = _pkcs1_encoding(algorithm[1] + hasher.digest())
    recovered = pow(signature, GITHUB_WEB_FLOW_EXPONENT, GITHUB_WEB_FLOW_MODULUS)
    return bool(expected) and recovered.to_bytes(GITHUB_WEB_FLOW_MODULUS_BYTES, "big") == expected


def _subpackets(area: bytes) -> list:
    """Return (type, data) pairs from one subpacket area, stopping at bad data."""
    found = []
    index = 0
    while index < len(area):
        first = area[index]
        if first < ONE_BYTE_LENGTH_LIMIT:
            header, length = 1, first
        elif first < SUBPACKET_TWO_BYTE_LIMIT and index + 1 < len(area):
            header = 2
            length = ((first - ONE_BYTE_LENGTH_LIMIT) << 8) + area[index + 1] + ONE_BYTE_LENGTH_LIMIT
        elif first == FOUR_BYTE_LENGTH_MARKER and index + 4 < len(area):
            header, length = 5, int.from_bytes(area[index + 1:index + 5], "big")
        else:
            return found
        start = index + header
        if length < 1 or start + length > len(area):
            return found
        found.append((area[start] & 0x7F, area[start + 1:start + length]))
        index = start + length
    return found


def signature_issuer(armored) -> str:
    """Return the issuer key ID from a signature's hashed subpackets, or "".

    Only the hashed area counts, because the signature covers it.
    """
    for kind, data in _subpackets(_hashed_area(_dearmor(armored))):
        if kind == ISSUER_KEY_ID_SUBPACKET and len(data) == KEY_ID_BYTES:
            return data.hex().upper()
        if (kind == ISSUER_FINGERPRINT_SUBPACKET
                and len(data) == V4_FINGERPRINT_SUBPACKET_BYTES):
            return data[-KEY_ID_BYTES:].hex().upper()
    return ""


def _is_github_web_commit(commit: dict, identity: dict) -> bool:
    """Return whether GitHub itself committed and signed `commit`.

    A web merge or squash names GitHub as committer and keeps the merged
    commits' trailers. The author still carries the contributor identity.
    Every marker must match. GitHub must report the signature valid. The
    signature must also verify against GitHub's pinned web-flow public key
    over the payload GitHub reports. A commit signed with any other key
    still fails, even when GitHub verifies it.
    """
    details = commit.get("commit", {})
    raw = details.get("committer", {}).get("email", "")
    verification = details.get("verification")
    return (raw == GITHUB_WEB_COMMITTER_EMAIL
            and identity.get("login") == GITHUB_WEB_COMMITTER_LOGIN
            and identity.get("id") == GITHUB_WEB_COMMITTER_ID
            and isinstance(verification, dict)
            and verification.get("verified") is True
            and verification.get("reason") == GITHUB_VALID_REASON
            and signature_issuer(verification.get("signature")) == GITHUB_WEB_FLOW_KEY_ID
            and verify_web_flow_signature(verification.get("payload"), verification.get("signature")))


def _identity_violation(role: str, commit: dict, identity: dict | None,
                        require_noreply: bool = False) -> str | None:
    """Return a bounded identity violation for one commit field."""
    sha = commit["sha"][:SHORT_SHA_LENGTH]
    if not isinstance(identity, dict) or not identity.get("login"):
        return f"{sha}: GitHub could not resolve the {role} identity"
    if role == "committer" and _is_github_web_commit(commit, identity):
        return None
    raw = commit.get("commit", {}).get(role, {}).get("email", "")
    login = identity["login"]
    match = NOREPLY_PATTERN.fullmatch(raw)
    if require_noreply and not match:
        return f"{sha}: {role} email must use a numbered GitHub noreply address"
    if not match:
        return None
    if match.group("login").lower() != login.lower():
        return f"{sha}: {role} noreply login does not match GitHub identity"
    account_id = identity.get("id")
    if not isinstance(account_id, int) or match.group("account_id") != str(account_id):
        return f"{sha}: {role} noreply account ID does not match GitHub identity"
    return None


def has_agent_label(body: str) -> bool:
    """Return whether a message contains a name-only agent label."""
    for key, value in terminal_trailers(body):
        norm_key = key.lower().replace(" ", "-")
        if norm_key not in ("co-authored-by", "assisted-by"):
            continue
        match = COAUTHOR_PATTERN.fullmatch(value.strip())
        if match and not match.group("email") and match.group("name").strip():
            return True
    return False


def _reported_value(value: str) -> str:
    """Return a bounded printable copy of untrusted trailer text."""
    return NON_PRINTABLE_PATTERN.sub("", value)[:MAX_REPORTED_MODEL]


def _disclosure_labels(body: str) -> tuple[list[str], bool]:
    """Return name-only Assisted-by models and whether an agent co-author exists."""
    models = []
    agent_label = False
    for key, value in terminal_trailers(body):
        norm_key = key.lower().replace(" ", "-")
        match = COAUTHOR_PATTERN.fullmatch(value.strip())
        if not match or match.group("email") or not match.group("name").strip():
            continue
        if norm_key == "assisted-by":
            models.append(match.group("name").strip())
        elif norm_key == "co-authored-by":
            agent_label = True
    return models, agent_label


def model_disclosure_violations(commits: list[dict]) -> list[str]:
    """Require agent-labeled commits to disclose a versioned model.

    A commit with no name-only agent label is outside Rule 14 disclosure,
    so human commits without trailers pass.
    """
    violations = []
    for commit in commits:
        sha = commit["sha"][:SHORT_SHA_LENGTH]
        models, agent_label = _disclosure_labels(commit.get("body", ""))
        for model in models:
            if not VERSION_DIGIT_PATTERN.search(model):
                violations.append(
                    f"{sha}: assisted-by trailer must name a versioned model, "
                    f"not '{_reported_value(model)}'"
                )
        if agent_label and not models:
            violations.append(
                f"{sha}: agent co-author label requires an Assisted-by model disclosure"
            )
    return violations


def github_identity_violations(commits: list[dict], repository: str,
                               repo_root: str) -> list[str]:
    """Validate each commit author and committer through GitHub metadata."""
    violations = []
    for commit in commits:
        document = _commit_api(repository, commit["sha"], repo_root)
        require_noreply = has_agent_label(commit.get("body", ""))
        for role in ("author", "committer"):
            violation = _identity_violation(
                role, document, document.get(role), require_noreply
            )
            if violation:
                violations.append(violation)
    return violations


def main() -> int:
    """Run strict trailer and GitHub identity checks."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="")
    parser.add_argument("--head", default="")
    parser.add_argument("--repo", default=os.getcwd())
    parser.add_argument("--repository", default="")
    parser.add_argument("--message-file", action="store_true")
    parser.add_argument("message_path", nargs="?")
    args = parser.parse_args()
    try:
        if args.message_file:
            if args.base or args.head or not args.message_path:
                parser.error("--message-file requires one message path")
            message_path = Path(args.message_path).resolve(strict=True)
            body = message_path.read_text(encoding="utf-8")
            if len(body.encode("utf-8")) > MAX_MESSAGE_BYTES:
                raise ValueError("commit message exceeds the supported limit")
            commits = [{"sha": "message", "body": body}]
        else:
            if not args.base or not args.head or args.message_path:
                parser.error("--base and --head require no positional path")
            commits = _git_log(args.repo, args.base, args.head)
        violations = trailer_violations(commits)
        violations.extend(model_disclosure_violations(commits))
        if not args.message_file:
            violations.extend(
                github_identity_violations(
                    commits, _repository_name(args.repository), args.repo
                )
            )
    except (OSError, RuntimeError, subprocess.SubprocessError, ValueError,
            json.JSONDecodeError) as error:
        print(f"error: strict commit attribution check failed: {error}", file=sys.stderr)
        return 1
    for violation in violations:
        print(violation, file=sys.stderr)
    if violations:
        return 1
    print(f"verified commit attribution for {len(commits)} commit(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
