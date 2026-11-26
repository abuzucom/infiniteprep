"""Per-session deletion zones under .scratch and .worktrees.

Each session owns `.scratch/<session id>/` and `.worktrees/<session id>/`.
A delete through scripts/delete_zone_path.py whose every operand sits
strictly below one of those folders routes to consent. The helper repeats
the path checks through descriptors at delete time. Every other delete keeps its gate verdict. The check
fails closed: any doubt about an operand, the session id, or tracked
state leaves the delete unrecognized.

A tree that holds a `.git` entry never qualifies. A linked worktree keeps
its own index, and the parent repository lists none of its files. Removing
a worktree goes through `git worktree remove`, which refuses a dirty tree.

This module imports only the standard library and `_git_probe`.
"""
import os
import re

import _git_probe

ZONES = (".scratch", ".worktrees")
SESSION_ID_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,128}")
GLOB_CHARACTERS = frozenset("*?[]{}")
PARENT_DIRECTORY = ".."
GIT_ENTRY = ".git"
# The helper rejects these components too. Both must accept the same paths.
REJECTED_COMPONENTS = frozenset({PARENT_DIRECTORY, GIT_ENTRY, ".", ""})
# A larger tree refuses. The bound keeps the walk inside the hook time limit.
MAX_WALK_ENTRIES = 100_000


def valid_session_id(session_id) -> bool:
    """Return whether a session id can name a zone folder."""
    return isinstance(session_id, str) and bool(SESSION_ID_PATTERN.fullmatch(session_id))


def literal_operand(operand: str) -> bool:
    """Return whether an operand is a plain relative path with no expansion or escape."""
    return (bool(operand) and not operand.startswith(("-", "/", "\\", "~"))
            and not GLOB_CHARACTERS.intersection(operand)
            and not operand.endswith(("/", "\\"))
            and not REJECTED_COMPONENTS.intersection(operand.replace("\\", "/").split("/"))
            and not os.path.isabs(operand))


def _zone_roots(project_dir: str, session_id: str) -> tuple:
    """Return the resolved session folder of each zone."""
    base = os.path.realpath(project_dir)
    return tuple(os.path.join(base, zone, session_id) for zone in ZONES)


def _strictly_below(path: str, roots: tuple) -> bool:
    """Return whether a resolved path sits inside one zone root and is not the root."""
    return any(path.startswith(root + os.sep) for root in roots)


def _scan_directory(path: str, pending: list, seen: int):
    """Scan one directory, queue its child directories, and return the new entry count.

    Returns None when the directory holds a Git entry or the walk passes its bound.
    """
    with os.scandir(path) as entries:
        for entry in entries:
            seen += 1
            if entry.name == GIT_ENTRY or seen > MAX_WALK_ENTRIES:
                return None
            if entry.is_dir(follow_symlinks=False):
                pending.append(entry.path)
    return seen


def holds_git_entry(path: str) -> bool:
    """Return whether a path is, or holds below it, a `.git` entry.

    The walk follows no link. Any error or a tree past the bound counts as a
    Git entry, so the delete stays unrecognized.
    """
    if os.path.basename(path) == GIT_ENTRY:
        return True
    try:
        if os.path.islink(path) or not os.path.isdir(path):
            return False
        pending = [path]
        seen = 0
        while pending:
            seen = _scan_directory(pending.pop(), pending, seen)
            if seen is None:
                return True
    except OSError:
        return True
    return False


def zone_delete_allowed(operands: list, project_dir: str, session_id) -> bool:
    """Return whether every operand names an untracked path inside this session's zones."""
    if not valid_session_id(session_id) or not operands:
        return False
    if not all(literal_operand(operand) for operand in operands):
        return False
    base = os.path.realpath(project_dir)
    roots = _zone_roots(project_dir, session_id)
    resolved = [os.path.realpath(os.path.join(base, operand)) for operand in operands]
    if not all(_strictly_below(path, roots) for path in resolved):
        return False
    if any(holds_git_entry(path) for path in resolved):
        return False
    relative = [os.path.relpath(path, base).replace(os.sep, "/") for path in resolved]
    tracked = _git_probe.tracked_paths(base, relative)
    return tracked is not None and not tracked
