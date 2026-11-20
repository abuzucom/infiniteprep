"""Behavioral checks for the helper functions in infiniteprep.spec (D83a3c, D304)."""

from __future__ import annotations

import ast
import re
import unittest
from collections.abc import Callable
from pathlib import Path
from types import CodeType, FunctionType
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = ROOT / "infiniteprep.spec"
VERSION = "1.2.0"
INIT_TEXT = f'"""Package."""\n\n__version__ = "{VERSION}"\n'
SOURCE_PATH = "/build/lib/libexample.so.1"
LINKED_NAMES = (("libc.so.6", "/lib/libc.so.6"), ("libm.so.6", "/lib/libm.so.6"))
PYINSTALLER_NAMES = frozenset({"a", "pyz", "exe", "coll"})


def parse_spec() -> ast.Module:
    """Return the syntax tree of infiniteprep.spec."""
    return ast.parse(SPEC_PATH.read_text(encoding="utf-8"), filename=str(SPEC_PATH))


def load_spec_function(name: str, namespace: dict[str, Any]) -> Callable[..., Any]:
    """Return one top-level spec function, bound to the given globals.

    The spec as a whole needs PyInstaller. The function body runs here as written.
    The namespace supplies the module globals the body reads.
    """
    nodes = [node for node in parse_spec().body if isinstance(node, ast.FunctionDef)]
    matching = [node for node in nodes if node.name == name]
    if not matching:
        raise AssertionError(f"infiniteprep.spec defines no function named {name!r}.")
    module_code = compile(ast.Module(body=matching, type_ignores=[]), str(SPEC_PATH), "exec")
    for constant in module_code.co_consts:
        if isinstance(constant, CodeType) and constant.co_name == name:
            return FunctionType(constant, namespace, name)
    raise AssertionError(f"The compiled spec holds no code object for {name!r}.")


class RecordingImports:
    """A stand-in for bindepend that records each get_imports call."""

    def __init__(self) -> None:
        """Start with no recorded calls."""
        self.calls: list[str] = []

    def get_imports(self, source: str) -> list[tuple[str, str]]:
        """Record the source and return the fixed link list."""
        self.calls.append(source)
        return list(LINKED_NAMES)


class AppVersionTest(unittest.TestCase):
    """The spec reads the app version through a guarded helper."""

    def setUp(self) -> None:
        """Load read_app_version with the re module it uses."""
        self.read_app_version = load_spec_function("read_app_version", {"re": re})

    def test_version_line_gives_the_version(self) -> None:
        """A package init with a __version__ line yields that version."""
        self.assertEqual(self.read_app_version(INIT_TEXT), VERSION)

    def test_missing_version_line_stops_the_build_with_a_message(self) -> None:
        """A package init without the line raises SystemExit that names the file."""
        with self.assertRaises(SystemExit) as raised:
            self.read_app_version('"""Package."""\n')
        self.assertIn("__init__.py", str(raised.exception))


class LinkedLibraryNamesTest(unittest.TestCase):
    """The spec reads the links of each binary once per build."""

    def test_each_source_reads_its_links_once(self) -> None:
        """A second call for the same binary reuses the first result."""
        imports = RecordingImports()
        linked_library_names = load_spec_function(
            "linked_library_names", {"bindepend": imports, "LINKED_NAMES_CACHE": {}}
        )
        first = linked_library_names(SOURCE_PATH)
        second = linked_library_names(SOURCE_PATH)
        self.assertEqual(first, frozenset(name for name, _path in LINKED_NAMES))
        self.assertEqual(second, first)
        self.assertEqual(imports.calls, [SOURCE_PATH])


class SpecShapeTest(unittest.TestCase):
    """The spec declares return types and keeps the PyInstaller object names."""

    def test_every_spec_function_has_a_return_hint(self) -> None:
        """No top-level function in the spec lacks a return annotation."""
        unhinted = [
            node.name
            for node in parse_spec().body
            if isinstance(node, ast.FunctionDef) and node.returns is None
        ]
        self.assertEqual(unhinted, [])

    def test_the_build_objects_keep_their_names(self) -> None:
        """a, pyz, exe, and coll stay assigned at the top level."""
        assigned = {
            target.id
            for node in parse_spec().body
            if isinstance(node, ast.Assign)
            for target in node.targets
            if isinstance(target, ast.Name)
        }
        self.assertLessEqual(PYINSTALLER_NAMES, assigned)


if __name__ == "__main__":
    unittest.main()
