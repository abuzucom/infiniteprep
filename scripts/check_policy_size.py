#!/usr/bin/env python3
"""Validate the canonical policy size, the assembled size, and required rules.

With no flags the checker fails when AGENTS.md or the assembled policy
exceeds its limit. --json prints a size report for CI and exits 0 after it
prints the report. --render-markdown turns such a report into the pull
request comment body. The renderer treats the report as untrusted data.
"""
import argparse
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import ModuleType


MAX_POLICY_BYTES = 32 * 1024
REQUIRED_TEXT = (
    "## Non-negotiable",
    "## Critical rules",
    "## Code quality",
    "## Style",
    "Change policy safely",
    "Version every change",
    "xAI",
    "Grok",
)
REPORT_SCHEMA = 1
AGENTS_TARGET = "AGENTS.md"
ASSEMBLED_TARGET = "assembled policy"
TARGET_LABELS = {AGENTS_TARGET: "`AGENTS.md`", ASSEMBLED_TARGET: "Assembled policy"}
REPORT_TARGET_NAMES = (AGENTS_TARGET, ASSEMBLED_TARGET)
REPORT_FIELDS = frozenset(("name", "bytes", "limit"))
FILE_FIELDS = frozenset(("path", "bytes"))
MAX_RANKED_FILES = 10
# Bounds keep a hostile report from producing absurd comment text.
MAX_REPORT_FILE_BYTES = 4096
MAX_REPORTED_BYTES = 16 * 1024 * 1024
# A valid report nests three levels: the report object, a list, and an entry
# object. json.loads recurses once per level, and its depth limit depends on
# the interpreter. The renderer rejects deeper input before it parses.
MAX_REPORT_NESTING_DEPTH = 3
JSON_OPENERS = frozenset("[{")
JSON_CLOSERS = frozenset("]}")
PERCENT = 100
COMMENT_MARKER = "<!-- policy-size-report -->"
# Low headroom tempts an agent to cut policy text on its own. The directive
# appears in every failure and every report so the decision stays human.
AGENT_DIRECTIVE = (
    "AGENT DIRECTIVE: Agents must not reduce, trim, condense, reword, move,\n"
    "or remove policy text to meet this limit.\n"
    "Agents must not make an independent decision about policy reduction.\n"
    "Any reduction requires an itemized proposal under the AGENTS.md\n"
    "\"Change policy safely\" rule and explicit active-human approval.\n"
    "Stop work. Report these numbers to the active human. Wait for a decision."
)


def _load_sync() -> ModuleType:
    """Load sync.py by fixed sibling path, never through sys.path."""
    path = Path(__file__).resolve().parent / "sync.py"
    if path.is_symlink() or not path.is_file():
        raise ImportError("trusted sibling sync.py is missing or not a regular file")
    # A ".py" location always yields a spec with a source loader.
    spec = importlib.util.spec_from_file_location("_policy_size_sync", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def find_violations(path: Path) -> list[str]:
    violations = []
    try:
        data = path.read_bytes()
    except OSError as error:
        return [f"{path}: cannot read policy: {error}"]
    if len(data) > MAX_POLICY_BYTES:
        violations.append(
            f"{path}: {len(data)} bytes exceeds {MAX_POLICY_BYTES} bytes"
        )
    if b"\r\n" in data:
        violations.append(f"{path}: policy must use LF line endings")
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError:
        return violations + [f"{path}: policy is not ASCII"]
    violations.extend(
        f"{path}: missing required text: {required}"
        for required in REQUIRED_TEXT
        if required not in text
    )
    return violations


def measure_assembled_files(root: Path) -> tuple[list[tuple[str, int]], int]:
    """Return (name, bytes) for each assembled policy file and the limit.

    Raises OSError or ValueError when the policy cannot be assembled.
    """
    sync = _load_sync()
    # sync.py compares absolute paths. A relative root such as "." fails.
    parts = sync.read_assembled_policy_parts(Path(os.path.abspath(root)))
    return [(name, len(content)) for name, content in parts], sync.MAX_POLICY_BYTES


def measure_assembled_bytes(root: Path) -> tuple[int, int]:
    """Return the assembled policy size and its limit for root.

    Raises OSError or ValueError when the policy cannot be assembled.
    """
    sizes, limit = measure_assembled_files(root)
    return sum(size for _, size in sizes), limit


def make_largest_first_key(item: tuple[str, int]) -> tuple[int, str]:
    """Return a sort key that orders (path, bytes) pairs largest first.

    Equal sizes fall back to the path in ascending order, so the ranking is
    deterministic. The report builder and the report validator share it.
    """
    path, size = item
    return -size, path


def rank_largest_files(sizes: list[tuple[str, int]]) -> list[dict]:
    """Return up to MAX_RANKED_FILES entries, largest first, ties by path."""
    ordered = sorted(sizes, key=make_largest_first_key)
    return [{"path": name, "bytes": size}
            for name, size in ordered[:MAX_RANKED_FILES]]


def format_largest_files(root: Path) -> list[str]:
    """Return plain-text lines ranking the assembled policy files."""
    try:
        sizes, _ = measure_assembled_files(root)
    except (ImportError, OSError, ValueError):
        return []
    return ["Largest files in the assembled policy:"] + [
        f"  {rank}. {entry['bytes']} bytes  {entry['path']}"
        for rank, entry in enumerate(rank_largest_files(sizes), start=1)
    ]


def find_combined_violations(root: Path) -> list[str]:
    """Return violations for the assembled policy under root."""
    try:
        size, limit = measure_assembled_bytes(root)
    except (ImportError, OSError, ValueError) as error:
        return [f"{ASSEMBLED_TARGET}: cannot assemble: {error}"]
    if size <= limit:
        return []
    return [
        f"{ASSEMBLED_TARGET}: {size} bytes exceeds {limit} bytes"
        f" by {size - limit}"
    ]


def measure_policy(path: Path) -> dict:
    """Return the size report for AGENTS.md and the assembled policy.

    Raises OSError, ImportError, or ValueError when a size is unavailable.
    """
    agents_size = path.stat().st_size
    sizes, assembled_limit = measure_assembled_files(path.parent)
    assembled_size = sum(size for _, size in sizes)
    return {"schema": REPORT_SCHEMA, "targets": [
        {"name": AGENTS_TARGET, "bytes": agents_size, "limit": MAX_POLICY_BYTES},
        {"name": ASSEMBLED_TARGET, "bytes": assembled_size,
         "limit": assembled_limit},
    ], "files": rank_largest_files(sizes)}


def _validate_count(value: object, minimum: int) -> int:
    """Return value when it is a bounded integer, else raise ValueError."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("size fields must be integers")
    if value < minimum or value > MAX_REPORTED_BYTES:
        raise ValueError("size field is out of range")
    return value


def validate_report(report: object) -> list[tuple[str, int, int]]:
    """Return (name, bytes, limit) rows from an untrusted size report."""
    if not isinstance(report, dict) or report.get("schema") != REPORT_SCHEMA:
        raise ValueError("unknown report schema")
    targets = report.get("targets")
    if not isinstance(targets, list) or len(targets) != len(REPORT_TARGET_NAMES):
        raise ValueError("report must list exactly the known targets")
    # A report cannot raise a limit. An inflated limit would show an
    # over-limit policy as within limits.
    expected_limits = {
        AGENTS_TARGET: MAX_POLICY_BYTES,
        ASSEMBLED_TARGET: _load_sync().MAX_POLICY_BYTES,
    }
    rows = []
    for expected_name, target in zip(REPORT_TARGET_NAMES, targets):
        if not isinstance(target, dict) or set(target) != REPORT_FIELDS:
            raise ValueError("report target has unexpected fields")
        if target["name"] != expected_name:
            raise ValueError("report target name is not recognized")
        size = _validate_count(target["bytes"], 0)
        limit = _validate_count(target["limit"], 1)
        if limit != expected_limits[expected_name]:
            raise ValueError("report target limit differs from the checker limit")
        rows.append((expected_name, size, limit))
    return rows


def _list_known_policy_files() -> frozenset:
    """Return every file name that the assembled policy can contain."""
    sync = _load_sync()
    return frozenset((sync.SOURCE,) + sync.ADOPTER_POLICY_FILES
                     + sync.SOURCE_ONLY_POLICY_FILES)


def validate_files(report: dict) -> list[tuple[str, int]]:
    """Return (path, bytes) rows from the optional untrusted file ranking."""
    files = report.get("files", [])
    if not isinstance(files, list) or len(files) > MAX_RANKED_FILES:
        raise ValueError("file ranking must be a list of at most 10 entries")
    known = _list_known_policy_files()
    rows = []
    for entry in files:
        if not isinstance(entry, dict) or set(entry) != FILE_FIELDS:
            raise ValueError("file ranking entry has unexpected fields")
        if entry["path"] not in known:
            raise ValueError("file ranking names an unknown policy file")
        rows.append((entry["path"], _validate_count(entry["bytes"], 0)))
    if len({path for path, _ in rows}) != len(rows):
        raise ValueError("file ranking repeats a policy file")
    if rows != sorted(rows, key=make_largest_first_key):
        raise ValueError("file ranking is not ordered largest first")
    return rows


def _render_file_rows(files: list[tuple[str, int]], total: int) -> list[str]:
    """Return the Markdown table ranking the assembled policy files."""
    lines = [
        "### Largest files in the assembled policy",
        "",
        "| Rank | File | Bytes | Share |",
        "|---:|---|---:|---:|",
    ]
    for rank, (path, size) in enumerate(files, start=1):
        share = size * PERCENT / total if total else 0.0
        lines.append(f"| {rank} | `{path}` | {size:,} | {share:.1f}% |")
    lines.append("")
    return lines


def render_markdown(rows: list[tuple[str, int, int]],
                    files: list[tuple[str, int]] | None = None) -> str:
    """Return the pull request comment body for validated rows.

    validate_report rejects a limit below 1. This function checks again
    because a caller can pass rows that skipped validation, and each limit
    divides the byte count for the percent column.
    """
    for name, _size, limit in rows:
        if limit <= 0:
            raise ValueError(f"{name} limit must be positive, got {limit}")
    lines = [
        COMMENT_MARKER,
        "## Policy size report",
        "",
        "| Target | Bytes | Limit | Headroom | Used |",
        "|---|---:|---:|---:|---:|",
    ]
    over_limit = []
    for name, size, limit in rows:
        used = size * PERCENT / limit
        lines.append(
            f"| {TARGET_LABELS[name]} | {size:,} | {limit:,} |"
            f" {limit - size:,} | {used:.1f}% |"
        )
        if size > limit:
            over_limit.append(TARGET_LABELS[name])
    status = f"Over limit: {', '.join(over_limit)}." if over_limit else "Within limits."
    lines.extend(["", f"**Status:** {status}", ""])
    if files:
        assembled_total = rows[1][1]
        lines.extend(_render_file_rows(files, assembled_total))
    lines.extend(["```text", AGENT_DIRECTIVE, "```", ""])
    return "\n".join(lines)


def _print_report_json(path: Path) -> int:
    """Print the size report as JSON."""
    try:
        report = measure_policy(path)
    except (ImportError, OSError, ValueError) as error:
        print(f"error: cannot measure policy size: {error}", file=sys.stderr)
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


def check_report_nesting(text: str) -> None:
    """Raise ValueError when JSON text nests deeper than a valid report.

    One iterative pass tracks string state, so brackets inside strings and
    escaped quotes do not count.
    """
    depth = 0
    in_string = False
    escaped = False
    for character in text:
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in JSON_OPENERS:
            depth += 1
            if depth > MAX_REPORT_NESTING_DEPTH:
                raise ValueError(
                    f"report nesting exceeds {MAX_REPORT_NESTING_DEPTH} levels")
        elif character in JSON_CLOSERS:
            depth -= 1


def _print_rendered_report(report_path: Path) -> int:
    """Print the comment body for an untrusted report file."""
    try:
        with report_path.open("rb") as handle:
            data = handle.read(MAX_REPORT_FILE_BYTES + 1)
        if len(data) > MAX_REPORT_FILE_BYTES:
            raise ValueError("report file exceeds the size limit")
        text = data.decode("ascii")
        check_report_nesting(text)
        report = json.loads(text)
        rows = validate_report(report)
        files = validate_files(report)
    except (ImportError, OSError, ValueError) as error:
        print(f"error: invalid size report: {error}", file=sys.stderr)
        return 1
    print(render_markdown(rows, files), end="")
    return 0


def _check_limits(path: Path) -> int:
    """Print every violation and the agent directive on failure."""
    combined_violations = find_combined_violations(path.parent)
    violations = find_violations(path) + combined_violations
    for violation in violations:
        print(violation, file=sys.stderr)
    if not violations:
        return 0
    over_limit = any(" exceeds " in item for item in combined_violations)
    if over_limit:
        for line in format_largest_files(path.parent):
            print(line, file=sys.stderr)
    print(AGENT_DIRECTIVE, file=sys.stderr)
    return 1


def main(argv: list[str] | None = None) -> int:
    """Run one checker mode and return its process exit code.

    The default mode checks both size limits. --json prints the size report.
    --render-markdown prints the pull request comment for a report file.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("path", nargs="?", type=Path, default=Path("AGENTS.md"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--json", action="store_true",
                      help="print the size report as JSON")
    mode.add_argument("--render-markdown", type=Path, metavar="REPORT",
                      help="print the pull request comment for a JSON report")
    args = parser.parse_args(argv)
    if args.render_markdown is not None:
        return _print_rendered_report(args.render_markdown)
    if args.json:
        return _print_report_json(args.path)
    return _check_limits(args.path)


if __name__ == "__main__":
    raise SystemExit(main())
