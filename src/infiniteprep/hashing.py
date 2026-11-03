"""SHA-256 hashing for file content, content addresses, and backup verification."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Final

HASH_CHUNK_BYTES: Final[int] = 65536


def compute_sha256(file_path: Path) -> str:
    """Compute SHA-256 hex digest of an entire file."""
    hasher = hashlib.sha256()
    with file_path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
            hasher.update(chunk)
    return hasher.hexdigest()
