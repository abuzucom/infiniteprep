"""Trusted origin owners for test runners that execute repository code.

A test runner imports repository code such as conftest.py, a Makefile, or
package scripts. The command gate allows those runners without a prompt
only when the origin owner appears in trusted-owners.txt beside this
module. Each adopter keeps its own list. A missing or unusable list trusts
nobody, so every runner keeps its consent prompt.

This module uses only the standard library. It imports no gate module, so
no import cycle can form.
"""
import functools
import os
import re

TRUSTED_OWNERS_LIST = "trusted-owners.txt"
TRUSTED_OWNERS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), TRUSTED_OWNERS_LIST)
# A real list holds a few owner names. The cap bounds the read of a repository file.
MAX_TRUSTED_OWNERS_BYTES = 4096
COMMENT_PREFIX = "#"
# GitHub owner names hold 1 to 39 letters, digits, or hyphens and start with a letter or digit.
OWNER_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{0,38}")
READ_FLAGS = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)


def _parse_owners(text: str) -> frozenset:
    """Return lowercase owner names, or raise ValueError on any malformed line."""
    owners = set()
    for line in text.splitlines():
        entry = line.strip()
        if not entry or entry.startswith(COMMENT_PREFIX):
            continue
        if not OWNER_PATTERN.fullmatch(entry):
            raise ValueError("an owner line holds something other than one owner name")
        owners.add(entry.lower())
    return frozenset(owners)


def read_trusted_owners(path: str) -> frozenset:
    """Return the owners listed in one file, or an empty set when the file is unusable."""
    try:
        descriptor = os.open(path, READ_FLAGS)
        with os.fdopen(descriptor, "rb") as handle:
            content = handle.read(MAX_TRUSTED_OWNERS_BYTES + 1)
        if len(content) > MAX_TRUSTED_OWNERS_BYTES:
            return frozenset()
        return _parse_owners(content.decode("ascii"))
    except (OSError, UnicodeDecodeError, ValueError):
        # An unusable list trusts nobody. Each runner then keeps its consent prompt.
        return frozenset()


@functools.lru_cache(maxsize=1)
def load_trusted_owners() -> frozenset:
    """Return the owners in the list beside this module."""
    return read_trusted_owners(TRUSTED_OWNERS_PATH)


def owner_in(owners: frozenset, owner: str) -> bool:
    """Return whether a nonempty owner appears in a set of lowercase owners."""
    return bool(owner) and owner.lower() in owners


def owner_trusted(owner: str) -> bool:
    """Return whether the trusted owner list names this origin owner."""
    return owner_in(load_trusted_owners(), owner)
