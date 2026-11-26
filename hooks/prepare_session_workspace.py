#!/usr/bin/env python3
"""Create the session's zone folders at session start.

Each session owns `.scratch/<session id>/` and `.worktrees/<session id>/`.
The command gate routes deletes inside those folders to consent. Every
path component opens through its parent descriptor without following a
link. A planted link then cannot redirect the folders outside the
repository.

The hook never blocks a session. A missing or invalid session id or a
platform without `dir_fd` support creates nothing. A link or another
failure stops creation at that component. Folders created before it stay
in place. Each case exits 0 with a note on stderr.
"""
import os
import sys

import _gate_core as core
import _zones

PRIVATE_MODE = 0o700
DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
NO_FOLLOW_FLAGS = DIRECTORY_FLAGS | getattr(os, "O_NOFOLLOW", 0)
NOT_CREATED_NOTE = "prepare_session_workspace: zone folders not created"


def _safe_creation_supported() -> bool:
    """Return whether the platform can open each component without following links."""
    return (hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY")
            and os.mkdir in os.supports_dir_fd and os.open in os.supports_dir_fd)


def _open_child(parent: int, name: str) -> int:
    """Create a private child directory if absent and open it without following a link."""
    try:
        os.mkdir(name, PRIVATE_MODE, dir_fd=parent)
    except FileExistsError:
        # An existing directory is the idempotent case. The open rejects anything else.
        return os.open(name, NO_FOLLOW_FLAGS, dir_fd=parent)
    return os.open(name, NO_FOLLOW_FLAGS, dir_fd=parent)


def _create_zone(root: int, zone: str, session_id: str) -> None:
    """Create one zone folder and its session folder below the repository root."""
    zone_descriptor = _open_child(root, zone)
    try:
        os.close(_open_child(zone_descriptor, session_id))
    finally:
        os.close(zone_descriptor)


def prepare(project_dir: str, session_id) -> bool:
    """Create both zone folders for a valid session id and return whether it succeeded."""
    if not _zones.valid_session_id(session_id) or not _safe_creation_supported():
        return False
    root = os.open(project_dir, DIRECTORY_FLAGS)
    try:
        for zone in _zones.ZONES:
            _create_zone(root, zone, session_id)
    finally:
        os.close(root)
    return True


def main() -> int:
    """Read the SessionStart payload, create the folders, and always exit 0."""
    payload = core.read_payload(empty_is_session_start=True)
    if not payload:
        print(f"{NOT_CREATED_NOTE} (no payload)", file=sys.stderr)
        return 0
    try:
        created = prepare(core.project_dir(payload), payload.get("session_id"))
    except OSError as error:
        print(f"{NOT_CREATED_NOTE} ({type(error).__name__})", file=sys.stderr)
        return 0
    if not created:
        print(f"{NOT_CREATED_NOTE} (no valid session id or no safe creation support)",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
