#!/usr/bin/env python3
"""Delete untracked paths inside this session's own zone folders.

Usage: python scripts/delete_zone_path.py --session <id> <path> [<path> ...]

The command execution gate asks before this helper runs. The helper repeats
every path check at delete time through directory descriptors. Each path
component opens relative to its parent and follows no link. A swap of any
component to a link after the gate check refuses the delete. A final link
gets removed itself and its target stays.

Deletion runs one operand at a time and is not atomic. Each deleted
operand prints a line on stdout. A failure names how many operands the
helper deleted first.

The checks guard against a swapped link and a planted Git tree. A
concurrent writer that runs as the same user inside the session zone falls
outside that boundary. That writer can delete the files itself.

Exit codes:
- 0: every operand got deleted.
- 1: the walk or the delete refused an operand.
- 2: the input, the tracked state, or the platform refused the call.
"""
import functools
import inspect
import os
import shutil
import stat
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
EXIT_DELETED = 0
EXIT_WALK_REFUSED = 1
EXIT_INPUT_REFUSED = 2
SESSION_OPTION = "--session"
MINIMUM_ARGUMENTS = 3
# A zone name, the session id, and at least one name below the session folder.
MINIMUM_COMPONENTS = 3
CURRENT_DIRECTORY = "."
DESCRIPTOR_FUNCTIONS = (os.open, os.stat, os.unlink, os.rmdir)
# shutil.rmtree accepts dir_fd from Python 3.11 on.
RMTREE_DIR_FD_PARAMETER = "dir_fd"
USAGE = "usage: delete_zone_path.py --session <id> <path> [<path> ...]"


@functools.lru_cache(maxsize=1)
def _zone_modules() -> tuple:
    """Import and return the shared zone check and Git probe modules from hooks/."""
    sys.path.insert(0, str(REPOSITORY_ROOT / "hooks"))
    import _git_probe
    import _zones
    return _zones, _git_probe


def safe_deletion_supported() -> bool:
    """Return whether the platform can delete through descriptors without following links."""
    return (hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY")
            and all(function in os.supports_dir_fd for function in DESCRIPTOR_FUNCTIONS)
            and os.stat in os.supports_follow_symlinks
            and bool(getattr(shutil.rmtree, "avoids_symlink_attacks", False))
            and _rmtree_accepts_dir_fd())


def _rmtree_accepts_dir_fd() -> bool:
    """Return whether shutil.rmtree takes a dir_fd argument on this Python."""
    try:
        parameters = inspect.signature(shutil.rmtree).parameters
    except (TypeError, ValueError) as error:
        print(f"delete_zone_path: rmtree signature unreadable ({type(error).__name__}).",
              file=sys.stderr)
        return False
    return RMTREE_DIR_FD_PARAMETER in parameters


def _directory_flags() -> int:
    """Return the open flags for one directory component that must not be a link."""
    return os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY


def _components(operand: str, session_id: str):
    """Return the path components of a zone operand, or None for any other operand."""
    zones, _probe = _zone_modules()
    if not zones.literal_operand(operand):
        return None
    parts = operand.split("/")
    if len(parts) < MINIMUM_COMPONENTS or parts[0] not in zones.ZONES:
        return None
    if parts[1] != session_id or any(part in ("", CURRENT_DIRECTORY) for part in parts):
        return None
    return parts


def _open_parent(root_descriptor: int, parts: list, descriptors: list) -> int:
    """Open every component above the last one through descriptors and return the parent.

    Each opened descriptor joins `descriptors`. The caller closes them.
    """
    current = root_descriptor
    for name in parts[:-1]:
        current = os.open(name, _directory_flags(), dir_fd=current)
        descriptors.append(current)
    return current


def _scan_descriptor(directory: int, pending: list, seen: int, limit: int, git_entry: str):
    """Scan one open directory, queue child directory descriptors, and return the entry count.

    Returns None when the directory holds a Git entry or the walk passes its bound.
    """
    with os.scandir(directory) as entries:
        for entry in entries:
            seen += 1
            if entry.name == git_entry or seen > limit:
                return None
            if entry.is_dir(follow_symlinks=False):
                pending.append(os.open(entry.name, _directory_flags(), dir_fd=directory))
    return seen


def _tree_holds_git(parent: int, name: str) -> bool:
    """Return whether the named directory holds a `.git` entry anywhere below it."""
    zones, _probe = _zone_modules()
    status = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if not stat.S_ISDIR(status.st_mode):
        return False
    pending = [os.open(name, _directory_flags(), dir_fd=parent)]
    seen = 0
    try:
        while pending:
            directory = pending.pop()
            try:
                seen = _scan_descriptor(directory, pending, seen, zones.MAX_WALK_ENTRIES,
                                        zones.GIT_ENTRY)
            finally:
                os.close(directory)
            if seen is None:
                return True
    finally:
        for directory in pending:
            os.close(directory)
    return False


def _git_tree_verdict(root_descriptor: int, parts: list):
    """Return an exit code that refuses the operand, or None when no Git tree sits below it."""
    descriptors = []
    try:
        parent = _open_parent(root_descriptor, parts, descriptors)
        if _tree_holds_git(parent, parts[-1]):
            print("delete_zone_path: the target holds a .git entry. Remove a worktree with "
                  "git worktree remove. Nothing got deleted.", file=sys.stderr)
            return EXIT_INPUT_REFUSED
        return None
    except OSError as error:
        print(f"delete_zone_path: the Git tree check failed ({type(error).__name__}). "
              "Check that the path exists and holds no link.", file=sys.stderr)
        return EXIT_WALK_REFUSED
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def _delete_one(root_descriptor: int, parts: list) -> bool:
    """Walk to the parent of the last component through descriptors and delete it."""
    descriptors = []
    try:
        current = _open_parent(root_descriptor, parts, descriptors)
        last = parts[-1]
        status = os.stat(last, dir_fd=current, follow_symlinks=False)
        if stat.S_ISDIR(status.st_mode):
            shutil.rmtree(last, dir_fd=current)
        else:
            os.unlink(last, dir_fd=current)
        return True
    except OSError as error:
        print(f"delete_zone_path: walk or delete failed ({type(error).__name__}). "
              "Check that the path exists and holds no link.", file=sys.stderr)
        return False
    finally:
        for descriptor in descriptors:
            os.close(descriptor)


def _validated_components(root: str, session_id: str, operands: list):
    """Return the component lists for every operand, or None when any check fails."""
    if not safe_deletion_supported():
        print("delete_zone_path: this platform lacks descriptor deletion. Nothing got deleted.",
              file=sys.stderr)
        return None
    zones, probe = _zone_modules()
    if not zones.valid_session_id(session_id) or not operands:
        return None
    components = [_components(operand, session_id) for operand in operands]
    if any(parts is None for parts in components):
        return None
    tracked = probe.tracked_paths(root, operands)
    if tracked is None or tracked:
        return None
    return components


def delete_zone_paths(root: str, session_id: str, operands: list) -> int:
    """Delete each operand below the session's own zone folders and return an exit code."""
    components = _validated_components(root, session_id, operands)
    if components is None:
        print("delete_zone_path: the operands or session id failed the zone check. "
              "Nothing got deleted.", file=sys.stderr)
        return EXIT_INPUT_REFUSED
    try:
        root_descriptor = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    except OSError as error:
        print(f"delete_zone_path: the repository root failed to open ({type(error).__name__}).",
              file=sys.stderr)
        return EXIT_WALK_REFUSED
    try:
        return _delete_all(root_descriptor, components)
    finally:
        os.close(root_descriptor)


def _delete_all(root_descriptor: int, components: list) -> int:
    """Refuse every operand that holds a Git tree, then delete each operand in order."""
    for parts in components:
        verdict = _git_tree_verdict(root_descriptor, parts)
        if verdict is not None:
            return verdict
    for index, parts in enumerate(components, 1):
        if not _delete_one(root_descriptor, parts):
            print(f"delete_zone_path: operand {index} refused. The helper deleted "
                  f"{index - 1} of {len(components)} operands first.", file=sys.stderr)
            return EXIT_WALK_REFUSED
        print(f"delete_zone_path: deleted operand {index}")
    return EXIT_DELETED


def main(argv=None) -> int:
    """Parse the session option and operands, then delete inside the repository zones."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) < MINIMUM_ARGUMENTS or arguments[0] != SESSION_OPTION:
        print(USAGE, file=sys.stderr)
        return EXIT_INPUT_REFUSED
    return delete_zone_paths(str(REPOSITORY_ROOT), arguments[1], arguments[2:])


if __name__ == "__main__":
    sys.exit(main())
