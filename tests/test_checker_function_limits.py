#!/usr/bin/env python3
"""Pin the AGENTS.md size limits for the checker functions issue 122 V-54 names."""
import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MAX_FUNCTION_LINES = 60
MAX_LOCAL_NAMES = 10
# AGENTS.md keeps nesting under 4 levels. The function body is level 0.
MAX_NESTING_DEPTH = 3
COMPOUND_STATEMENTS = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try,
                       ast.With, ast.AsyncWith, ast.Match)
LOOP_STATEMENTS = (ast.For, ast.AsyncFor, ast.While)
SCOPE_NODES = (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp,
               ast.Lambda)
TARGET_FUNCTIONS = (
    ("scripts/check_hook_coverage.py", "run_test_shards"),
    ("scripts/prose_policy.py", "find_violations"),
    ("scripts/check_conflict_markers.py", "_check_all"),
    ("scripts/check_conflict_markers.py", "_parse_tree_entries"),
    ("scripts/check_dockerfile_root.py", "_skip_heredocs"),
)


def load_function(relative_path: str, name: str) -> ast.FunctionDef:
    """Return the module-level function definition named `name`."""
    tree = ast.parse((ROOT / relative_path).read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{relative_path} defines no function {name}")


def parameter_names(function: ast.FunctionDef) -> set[str]:
    """Return every parameter name the function declares."""
    arguments = function.args
    names = [argument.arg for argument in
             arguments.posonlyargs + arguments.args + arguments.kwonlyargs]
    names += [extra.arg for extra in (arguments.vararg, arguments.kwarg) if extra]
    return set(names)


def local_names(function: ast.FunctionDef) -> set[str]:
    """Return names the function binds, excluding parameters.

    Comprehensions, lambdas, and nested functions each open a separate
    scope. Names inside such a scope stay out of the count. A nested
    function name counts.
    """
    names = set()
    pending = list(ast.iter_child_nodes(function))
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.add(node.name)
            continue
        if isinstance(node, SCOPE_NODES):
            continue
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            names.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
        pending.extend(ast.iter_child_nodes(node))
    return names.difference(parameter_names(function))


def nesting_depth(statements: list, depth: int = 0) -> int:
    """Return the deepest compound-statement level below `statements`."""
    deepest = depth
    pending = [(statement, depth) for statement in statements]
    while pending:
        statement, level = pending.pop()
        if not isinstance(statement, COMPOUND_STATEMENTS):
            continue
        deepest = max(deepest, level + 1)
        for child in ast.iter_child_nodes(statement):
            if isinstance(child, ast.stmt):
                pending.append((child, level + 1))
            elif isinstance(child, (ast.ExceptHandler, ast.match_case)):
                pending.extend((grandchild, level + 1) for grandchild in child.body)
    return deepest


def breaks_out_of_nested_loop(function: ast.FunctionDef) -> bool:
    """Return whether a break sits in a loop nested inside another loop."""
    for outer in ast.walk(function):
        if not isinstance(outer, LOOP_STATEMENTS):
            continue
        for inner in ast.walk(outer):
            if inner is outer or not isinstance(inner, LOOP_STATEMENTS):
                continue
            if any(isinstance(node, ast.Break) for node in ast.walk(inner)):
                return True
    return False


class CheckerFunctionLimitTest(unittest.TestCase):
    """Each named function meets the AGENTS.md code quality limits."""

    def test_functions_stay_within_line_limit(self):
        for relative_path, name in TARGET_FUNCTIONS:
            with self.subTest(function=name):
                function = load_function(relative_path, name)
                length = len(range(function.lineno, function.end_lineno + 1))
                self.assertLessEqual(length, MAX_FUNCTION_LINES)

    def test_local_name_limit(self):
        for relative_path, name in TARGET_FUNCTIONS:
            with self.subTest(function=name):
                names = local_names(load_function(relative_path, name))
                self.assertLessEqual(len(names), MAX_LOCAL_NAMES, sorted(names))

    def test_functions_stay_under_nesting_limit(self):
        for relative_path, name in TARGET_FUNCTIONS:
            with self.subTest(function=name):
                function = load_function(relative_path, name)
                self.assertLessEqual(nesting_depth(function.body), MAX_NESTING_DEPTH)

    def test_nested_loops_exit_by_return(self):
        for relative_path, name in TARGET_FUNCTIONS:
            with self.subTest(function=name):
                function = load_function(relative_path, name)
                self.assertFalse(breaks_out_of_nested_loop(function))


if __name__ == "__main__":
    unittest.main()
