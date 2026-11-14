"""The bundles leave out Pillow. Only the build uses Pillow (D393).

scipy imports pytest inside PytestTester. The pytest hook pulls in pygments, the pygments
hook collects every formatter, and pygments.formatters.img imports PIL inside a
try/except ImportError block. With Pillow in the build environment, that chain ships
Pillow and its native libraries unless the spec excludes PIL.
"""

from __future__ import annotations

import ast
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC_PATH = ROOT / "infiniteprep.spec"
APP_SOURCE_DIR = ROOT / "src" / "infiniteprep"
PILLOW_PACKAGE = "PIL"


def read_list_constants(tree: ast.Module) -> dict[str, list[str]]:
    """Return each top-level NAME = [literal strings] assignment in the spec."""
    constants: dict[str, list[str]] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or not isinstance(node.value, ast.List):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                constants[target.id] = ast.literal_eval(node.value)
    return constants


def evaluate_list_expression(node: ast.expr, constants: dict[str, list[str]]) -> list[str]:
    """Evaluate a list literal, a list constant name, or a sum of those."""
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left = evaluate_list_expression(node.left, constants)
        return left + evaluate_list_expression(node.right, constants)
    if isinstance(node, ast.Name):
        return constants[node.id]
    return ast.literal_eval(node)


def find_analysis_excludes(tree: ast.Module) -> ast.expr:
    """Return the expression passed as excludes= to the spec's Analysis call."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        if node.func.id != "Analysis":
            continue
        for keyword in node.keywords:
            if keyword.arg == "excludes":
                return keyword.value
    raise AssertionError("infiniteprep.spec has no Analysis(excludes=...) argument")


def read_spec_excludes() -> list[str]:
    """Return the module names the spec passes to Analysis as excludes."""
    tree = ast.parse(SPEC_PATH.read_text(encoding="utf-8"), filename=str(SPEC_PATH))
    return evaluate_list_expression(find_analysis_excludes(tree), read_list_constants(tree))


def imported_top_level_packages(path: Path) -> set[str]:
    """Return the top-level package names that one Python file imports."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    packages: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            packages.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            packages.add(node.module.split(".")[0])
    return packages


class SpecExcludesPillowTest(unittest.TestCase):
    """PyInstaller never collects Pillow into a bundle."""

    def test_analysis_excludes_pil(self) -> None:
        """The evaluated Analysis excludes list names PIL."""
        self.assertIn(PILLOW_PACKAGE, read_spec_excludes())

    def test_existing_exclusions_stay(self) -> None:
        """readline (D302), setuptools, and pkg_resources (D313) stay excluded."""
        excludes = read_spec_excludes()
        for name in ("readline", "setuptools", "pkg_resources"):
            self.assertIn(name, excludes)

    def test_app_code_never_imports_pil(self) -> None:
        """No module under src/infiniteprep imports PIL. The exclusion removes nothing used."""
        importers = sorted(
            str(path.relative_to(ROOT))
            for path in APP_SOURCE_DIR.rglob("*.py")
            if PILLOW_PACKAGE in imported_top_level_packages(path)
        )
        self.assertEqual(importers, [])


if __name__ == "__main__":
    unittest.main()
