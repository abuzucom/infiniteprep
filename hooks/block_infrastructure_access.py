#!/usr/bin/env python3
"""Deny agent file access to infrastructure credentials and configuration."""
import os
import sys


sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

POLICY_DENIAL_EXIT_CODE = 2

try:
    import _gate_core as core
except ImportError as error:
    print(f"shared gate core import failed ({error}). Stop. Report this "
          "denial to the active human. Do not retry a variant or propose "
          "a gate change.", file=sys.stderr)
    sys.exit(POLICY_DENIAL_EXIT_CODE)


GATE = "block_infrastructure_access.py"
FILE_TOOLS = frozenset({"Edit", "MultiEdit", "NotebookEdit", "Read", "Write"})
SEARCH_TOOLS = frozenset({"Glob", "Grep"})
PATH_KEYS = ("file_path", "notebook_path", "path")
BROAD_PATTERNS = frozenset({"", "*", "**", "**/*"})
PROTECTED_REASON = "infrastructure credentials and configuration are protected from agent access"


def _path(tool_input: dict) -> str:
    """Return the first supported path field."""
    for key in PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return ""



def _file_access_denied(tool_input: dict, project_dir: str) -> bool:
    """Return whether one direct file operation reaches protected content."""
    path = _path(tool_input)
    return bool(path and core.is_protected_infrastructure_path(
        path, project_dir, core.write_content(tool_input, core.MAX_INFRASTRUCTURE_FILE_BYTES)))


def _search_denied(tool_name: str, tool_input: dict, project_dir: str) -> str:
    """Return why one search may traverse protected files, or an empty string.

    A scan that hits its file limit or an unreadable directory denies with
    a reason naming that outcome, so the agent sees a recovery step.
    """
    root = _path(tool_input) or project_dir
    include = tool_input.get("include", tool_input.get("glob", ""))
    pattern = tool_input.get("pattern", "")
    for candidate in (root, include, pattern):
        if isinstance(candidate, str) and core.is_protected_infrastructure_path(
                candidate, project_dir):
            return PROTECTED_REASON
    resolved_root = root if os.path.isabs(root) else os.path.join(project_dir, root)
    scan_include = ""
    if isinstance(include, str) and include not in BROAD_PATTERNS:
        scan_include = include
    elif (isinstance(pattern, str) and pattern not in BROAD_PATTERNS
          and tool_name == "Glob"):
        scan_include = pattern
    status = core.infrastructure_scan_status(resolved_root, scan_include)
    return core.infrastructure_scan_reason(status)


def main() -> int:
    """Read one file-tool payload and deny protected infrastructure access."""
    core.reset_invocation_caches()
    payload = core.read_payload()
    if payload is None:
        return core.emit(GATE, "deny", "the hook payload cannot be inspected")
    tool_name = payload.get("tool_name")
    if tool_name not in FILE_TOOLS | SEARCH_TOOLS:
        return 0
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return core.emit(GATE, "deny", "the file tool input cannot be inspected")
    project_dir = core.project_dir(payload)
    if tool_name in FILE_TOOLS:
        reason = PROTECTED_REASON if _file_access_denied(tool_input, project_dir) else ""
    else:
        reason = _search_denied(tool_name, tool_input, project_dir)
    if not reason:
        return 0
    return core.emit(GATE, "deny", reason)


if __name__ == "__main__":
    sys.exit(main())
