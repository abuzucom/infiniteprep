"""Mechanical AST and style invariant checks for src/infiniteprep (Sections 11, 14, 15)."""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final

import pytest

# Code points below 128 are ASCII.
ASCII_LIMIT: Final[int] = 128
SRC_ROOT: Final[Path] = Path(__file__).resolve().parents[2] / "src" / "infiniteprep"
MAX_FUNCTION_LINES: Final[int] = 60
MAX_LOCAL_VARIABLES: Final[int] = 10
MAX_NESTING_LEVEL: Final[int] = 3
# The AST walks stop with an error past this depth instead of exhausting memory.
MAX_AST_DEPTH: Final[int] = 100
MAX_LINE_LENGTH: Final[int] = 100
MIN_INNER_LOOP_DEPTH: Final[int] = 2
FORBIDDEN_COMMENT_MARKERS: Final[tuple[str, ...]] = ("TO" + "DO", "FIX" + "ME")
FORBIDDEN_CALL_NAMES: Final[frozenset[str]] = frozenset({"eval", "exec"})
FORBIDDEN_HASH_ATTRS: Final[frozenset[str]] = frozenset({"md5", "sha1"})
NESTING_TYPES: Final[tuple[type[ast.AST], ...]] = (
    ast.If,
    ast.For,
    ast.AsyncFor,
    ast.While,
    ast.Try,
    ast.With,
    ast.AsyncWith,
)
LOOP_TYPES: Final[tuple[type[ast.AST], ...]] = (ast.For, ast.AsyncFor, ast.While)
SCOPE_TYPES: Final[tuple[type[ast.AST], ...]] = (
    ast.FunctionDef,
    ast.AsyncFunctionDef,
    ast.ClassDef,
)
# The USB-5 files also keep every line within MAX_LINE_LENGTH characters.
USB_WORKTREE_PATHS: Final[tuple[Path, ...]] = (
    SRC_ROOT / "services" / "usb" / "_shared.py",
    SRC_ROOT / "services" / "usb" / "manifest.py",
    SRC_ROOT / "services" / "usb" / "recovery.py",
    SRC_ROOT / "services" / "usb" / "__init__.py",
    SRC_ROOT / "config" / "settings.py",
    SRC_ROOT / "ui" / "settings" / "storage.py",
    SRC_ROOT / "ui" / "usb_recovery_dialog.py",
)


def collect_source_files() -> list[Path]:
    """Return all Python source files under src/infiniteprep."""
    return sorted(SRC_ROOT.rglob("*.py"))


def count_local_assignments(func_node: ast.FunctionDef | ast.AsyncFunctionDef) -> int:
    """Count distinct local variable names assigned directly inside a function."""
    local_names: set[str] = set()
    for node in ast.walk(func_node):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node is not func_node:
            continue
        if isinstance(node, ast.Assign):
            local_names.update(target.id for target in node.targets if isinstance(target, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            local_names.add(node.target.id)
    return len(local_names)


def check_tree_depth(node: ast.AST, tree_depth: int, source_name: str) -> None:
    """Raise ValueError when a walk reaches past MAX_AST_DEPTH levels below its start."""
    if tree_depth > MAX_AST_DEPTH:
        line = getattr(node, "lineno", "unknown")
        raise ValueError(
            f"{source_name or 'source'} line {line} nests deeper than {MAX_AST_DEPTH} AST "
            "levels. Split the construct into smaller parts."
        )


def list_walk_children(node: ast.AST) -> list[ast.AST]:
    """Return the children of node that stay inside the same function scope."""
    return [child for child in ast.iter_child_nodes(node) if not isinstance(child, SCOPE_TYPES)]


def compute_max_nesting(node: ast.AST, current_depth: int = 0, source_name: str = "") -> int:
    """Compute maximum control-flow block nesting depth inside a function.

    The walk runs on an explicit stack. A tree deeper than MAX_AST_DEPTH raises
    ValueError that names source_name and the line.
    """
    max_seen = current_depth
    pending: list[tuple[ast.AST, int, int]] = [(node, current_depth, 0)]
    while pending:
        parent, nesting, tree_depth = pending.pop()
        max_seen = max(max_seen, nesting)
        for child in list_walk_children(parent):
            check_tree_depth(child, tree_depth + 1, source_name)
            child_nesting = nesting + 1 if isinstance(child, NESTING_TYPES) else nesting
            pending.append((child, child_nesting, tree_depth + 1))
    return max_seen


def find_inner_loop_breaks(node: ast.AST, loop_depth: int = 0, source_name: str = "") -> list[int]:
    """Return sorted line numbers of break statements inside nested inner loops (depth >= 2).

    The walk runs on an explicit stack. A tree deeper than MAX_AST_DEPTH raises
    ValueError that names source_name and the line.
    """
    violations: list[int] = []
    pending: list[tuple[ast.AST, int, int]] = [(node, loop_depth, 0)]
    while pending:
        parent, loops, tree_depth = pending.pop()
        for child in list_walk_children(parent):
            check_tree_depth(child, tree_depth + 1, source_name)
            if isinstance(child, ast.Break) and loops >= MIN_INNER_LOOP_DEPTH:
                violations.append(child.lineno)
            child_loops = loops + 1 if isinstance(child, LOOP_TYPES) else loops
            pending.append((child, child_loops, tree_depth + 1))
    return sorted(violations)


def find_comment_markers(text: str) -> list[tuple[int, str]]:
    """Return the line number and marker of each forbidden marker inside a comment."""
    found: list[tuple[int, str]] = []
    for line_num, line in enumerate(text.splitlines(), start=1):
        comment_part = line.partition("#")[2].upper()
        found.extend(
            (line_num, marker) for marker in FORBIDDEN_COMMENT_MARKERS if marker in comment_part
        )
    return found


def find_conditional_walrus_lines(tree: ast.Module) -> list[int]:
    """Return the line of each if or while whose test holds an assignment expression."""
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, (ast.If, ast.While))
        and any(isinstance(sub, ast.NamedExpr) for sub in ast.walk(node.test))
    ]


def is_shell_true_call(node: ast.Call) -> bool:
    """Return True when a call passes the literal shell=True keyword."""
    return any(
        keyword.arg == "shell"
        and isinstance(keyword.value, ast.Constant)
        and keyword.value.value is True
        for keyword in node.keywords
    )


@dataclass(frozen=True)
class ParsedSource:
    """One source file under src/infiniteprep with its text and syntax tree."""

    path: Path
    text: str
    tree: ast.Module


@pytest.fixture(scope="module")
def parsed_sources() -> list[ParsedSource]:
    """Read and parse every source file once for all invariant tests in this module."""
    sources = []
    for file_path in collect_source_files():
        text = file_path.read_text(encoding="utf-8")
        sources.append(ParsedSource(file_path, text, ast.parse(text, filename=str(file_path))))
    return sources


def first_non_ascii(text: str) -> tuple[int, str]:
    """Return the offset and character of the first non-ASCII character in text."""
    return next((idx, ch) for idx, ch in enumerate(text) if ord(ch) >= ASCII_LIMIT)


def test_all_source_files_are_ascii_only_and_free_of_todo_markers(
    parsed_sources: list[ParsedSource],
) -> None:
    """Verify every file in src/infiniteprep is strict ASCII and has no deferred-work markers."""
    assert len(parsed_sources) > 0, "Expected at least one source file in src/infiniteprep"
    for source in parsed_sources:
        # isascii() checks the whole text at C speed. Only a failing file is scanned
        # again to report where the first non-ASCII character sits.
        if not source.text.isascii():
            idx, ch = first_non_ascii(source.text)
            pytest.fail(
                f"Non-ASCII character {ch!r} at offset {idx} in {source.path.name}; "
                "replace with ASCII text."
            )
        assert find_comment_markers(source.text) == [], (
            f"Forbidden comment markers in {source.path.name}"
        )


def test_functions_respect_length_locals_nesting_and_docstrings(
    parsed_sources: list[ParsedSource],
) -> None:
    """Verify function length <= 60, locals <= 10, nesting <= 3, and return annotations."""
    for source in parsed_sources:
        file_path = source.path
        for node in ast.walk(source.tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            length = node.end_lineno - node.lineno + 1
            assert length <= MAX_FUNCTION_LINES, (
                f"Function {node.name} in {file_path.name} is {length} lines "
                f"(max {MAX_FUNCTION_LINES})."
            )
            local_count = count_local_assignments(node)
            assert local_count <= MAX_LOCAL_VARIABLES, (
                f"Function {node.name} in {file_path.name} has {local_count} locals "
                f"(max {MAX_LOCAL_VARIABLES})."
            )
            depth = compute_max_nesting(node, source_name=file_path.name)
            assert depth <= MAX_NESTING_LEVEL, (
                f"Function {node.name} in {file_path.name} has nesting depth {depth} "
                f"(max {MAX_NESTING_LEVEL})."
            )
            inner_breaks = find_inner_loop_breaks(node, source_name=file_path.name)
            assert not inner_breaks, (
                f"Function {node.name} in {file_path.name} uses break in inner loop: {inner_breaks}"
            )
            assert node.returns is not None, (
                f"Function {node.name} in {file_path.name} is missing a return type annotation."
            )


def test_no_assignments_in_conditionals_or_empty_catch_blocks(
    parsed_sources: list[ParsedSource],
) -> None:
    """Verify no walrus assignments in conditionals and no empty except blocks."""
    for source in parsed_sources:
        file_path = source.path
        walrus_lines = find_conditional_walrus_lines(source.tree)
        assert walrus_lines == [], (
            f"Assignment inside conditional in {file_path.name} lines {walrus_lines}"
        )
        for node in ast.walk(source.tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            only_pass = len(node.body) == 1 and isinstance(node.body[0], ast.Pass)
            assert not only_pass, (
                f"Silent pass in except block in {file_path.name} line {node.lineno}"
            )


def test_no_weak_hashing_or_unsafe_execution_calls(parsed_sources: list[ParsedSource]) -> None:
    """Verify no md5/sha1, eval/exec, or subprocess shell=True calls exist in src/."""
    for source in parsed_sources:
        file_path = source.path
        for node in ast.walk(source.tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Name):
                assert node.func.id not in FORBIDDEN_CALL_NAMES, (
                    f"Forbidden call {node.func.id} in {file_path.name} line {node.lineno}"
                )
            elif isinstance(node.func, ast.Attribute):
                assert node.func.attr not in FORBIDDEN_HASH_ATTRS, (
                    f"Weak hash {node.func.attr} in {file_path.name} line {node.lineno}"
                )
            assert not is_shell_true_call(node), (
                f"Forbidden shell=True in {file_path.name} line {node.lineno}"
            )


@pytest.mark.parametrize(
    "file_path", USB_WORKTREE_PATHS, ids=[path.name for path in USB_WORKTREE_PATHS]
)
def test_usb_worktree_files_keep_lines_within_the_limit(file_path: Path) -> None:
    """Verify every line of the USB-5 source files fits within MAX_LINE_LENGTH characters."""
    long_lines = [
        line_num
        for line_num, line in enumerate(file_path.read_text(encoding="utf-8").splitlines(), 1)
        if len(line) > MAX_LINE_LENGTH
    ]
    assert long_lines == [], f"Lines over {MAX_LINE_LENGTH} chars in {file_path.name}: {long_lines}"


def test_ast_walks_stop_past_the_depth_cap() -> None:
    """A tree nested past MAX_AST_DEPTH raises ValueError that names the source."""
    deep_tree = ast.parse("x = 1")
    deep_tree.body[0].value = _wrap_in_unary_ops(deep_tree.body[0].value, MAX_AST_DEPTH + 1)
    for walk in (compute_max_nesting, find_inner_loop_breaks):
        with pytest.raises(ValueError, match=re.escape("deep.py line")):
            walk(deep_tree, source_name="deep.py")


def _wrap_in_unary_ops(expression: ast.expr, levels: int) -> ast.expr:
    """Return expression wrapped in levels of unary minus, one AST level each."""
    wrapped = expression
    for _ in range(levels):
        wrapped = ast.UnaryOp(op=ast.USub(), operand=wrapped, lineno=1, col_offset=0)
    return wrapped
