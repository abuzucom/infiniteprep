"""Mechanical AST and style invariant checks for src/infiniteprep (Sections 11, 14, 15)."""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Final

SRC_ROOT: Final[Path] = Path(__file__).resolve().parents[2] / "src" / "infiniteprep"
MAX_FUNCTION_LINES: Final[int] = 60
MAX_LOCAL_VARIABLES: Final[int] = 10
MAX_NESTING_LEVEL: Final[int] = 3
FORBIDDEN_COMMENT_MARKERS: Final[tuple[str, ...]] = ("TODO", "FIXME")
FORBIDDEN_CALL_NAMES: Final[frozenset[str]] = frozenset({"eval", "exec"})
FORBIDDEN_HASH_ATTRS: Final[frozenset[str]] = frozenset({"md5", "sha1"})


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
            for target in node.targets:
                if isinstance(target, ast.Name):
                    local_names.add(target.id)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            local_names.add(node.target.id)
    return len(local_names)


def compute_max_nesting(node: ast.AST, current_depth: int = 0) -> int:
    """Compute maximum control-flow block nesting depth inside a function."""
    nesting_types = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.With, ast.AsyncWith)
    max_seen = current_depth
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        next_depth = current_depth + 1 if isinstance(child, nesting_types) else current_depth
        child_max = compute_max_nesting(child, next_depth)
        max_seen = max(max_seen, child_max)
    return max_seen


def find_inner_loop_breaks(node: ast.AST, loop_depth: int = 0) -> list[int]:
    """Return line numbers of any break statements inside nested inner loops (depth >= 2)."""
    violations: list[int] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(child, ast.Break) and loop_depth >= 2:
            violations.append(getattr(child, "lineno", 0))
        next_loop_depth = (
            loop_depth + 1 if isinstance(child, (ast.For, ast.AsyncFor, ast.While)) else loop_depth
        )
        violations.extend(find_inner_loop_breaks(child, next_loop_depth))
    return violations


def test_all_source_files_are_ascii_only_and_free_of_todo_markers() -> None:
    """Verify every file in src/infiniteprep is strict ASCII and has no TODO/FIXME markers."""
    files = collect_source_files()
    assert len(files) > 0, "Expected at least one source file in src/infiniteprep"
    for file_path in files:
        raw_text = file_path.read_text(encoding="utf-8")
        for idx, ch in enumerate(raw_text):
            assert ord(ch) < 128, (
                f"Non-ASCII character {ch!r} at offset {idx} in {file_path.name}; "
                "replace with ASCII text."
            )
        for line_num, line in enumerate(raw_text.splitlines(), start=1):
            if "#" in line:
                comment_part = line.partition("#")[2].upper()
                for marker in FORBIDDEN_COMMENT_MARKERS:
                    assert marker not in comment_part, (
                        f"Forbidden marker {marker} on line {line_num} of {file_path.name}"
                    )


def test_functions_respect_length_locals_nesting_and_docstrings() -> None:
    """Verify function length <= 60, locals <= 10, nesting <= 3, and return annotations."""
    for file_path in collect_source_files():
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            start_line = node.lineno
            end_line = getattr(node, "end_lineno", start_line) or start_line
            length = end_line - start_line + 1
            assert length <= MAX_FUNCTION_LINES, (
                f"Function {node.name} in {file_path.name} is {length} lines "
                f"(max {MAX_FUNCTION_LINES})."
            )
            local_count = count_local_assignments(node)
            assert local_count <= MAX_LOCAL_VARIABLES, (
                f"Function {node.name} in {file_path.name} has {local_count} locals "
                f"(max {MAX_LOCAL_VARIABLES})."
            )
            depth = compute_max_nesting(node)
            assert depth <= MAX_NESTING_LEVEL, (
                f"Function {node.name} in {file_path.name} has nesting depth {depth} "
                f"(max {MAX_NESTING_LEVEL})."
            )
            inner_breaks = find_inner_loop_breaks(node)
            assert not inner_breaks, (
                f"Function {node.name} in {file_path.name} uses break in inner loop: {inner_breaks}"
            )
            assert node.returns is not None, (
                f"Function {node.name} in {file_path.name} is missing a return type annotation."
            )


def test_no_assignments_in_conditionals_or_empty_catch_blocks() -> None:
    """Verify no walrus assignments in conditionals and no empty except blocks."""
    for file_path in collect_source_files():
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.If, ast.While)):
                for sub in ast.walk(node.test):
                    assert not isinstance(sub, ast.NamedExpr), (
                        f"Assignment inside conditional in {file_path.name} line {node.lineno}"
                    )
            if isinstance(node, ast.ExceptHandler):
                assert len(node.body) > 0, f"Empty except handler in {file_path.name}"
                only_pass = len(node.body) == 1 and isinstance(node.body[0], ast.Pass)
                assert not only_pass, (
                    f"Silent pass in except block in {file_path.name} line {node.lineno}"
                )


def test_no_weak_hashing_or_unsafe_execution_calls() -> None:
    """Verify no md5/sha1, eval/exec, or subprocess shell=True calls exist in src/."""
    for file_path in collect_source_files():
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        for node in ast.walk(tree):
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
            for kw in node.keywords:
                if kw.arg == "shell" and isinstance(kw.value, ast.Constant):
                    assert kw.value.value is not True, (
                        f"Forbidden shell=True in {file_path.name} line {node.lineno}"
                    )
