"""Guard that every package imported under src/ stays inside the approved dependencies."""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from pathlib import Path
from typing import Final

REPOSITORY_ROOT: Final[Path] = Path(__file__).resolve().parents[2]
SOURCE_ROOT: Final[Path] = REPOSITORY_ROOT / "src"
ALLOWLIST_PATH: Final[Path] = REPOSITORY_ROOT / "scripts" / "approved_runtime_dependencies.txt"
FIRST_PARTY_PACKAGE: Final[str] = "infiniteprep"
PACKAGE_NAME_PATTERN: Final[re.Pattern[str]] = re.compile(r"[A-Za-z0-9._-]+")
COMMENT_PREFIX: Final[str] = "#"


def normalize_package_name(name: str) -> str:
    """Return the import name of a distribution name."""
    return name.replace("-", "_").replace(".", "_").lower()


def requirement_import_name(requirement: str) -> str:
    """Return the import name of one requirement specifier from pyproject.toml."""
    match = PACKAGE_NAME_PATTERN.match(requirement.strip())
    if match is None:
        raise ValueError(f"Cannot read a package name from requirement {requirement!r}")
    return normalize_package_name(match.group(0))


def declared_runtime_packages() -> frozenset[str]:
    """Return the import names of the runtime dependencies declared in pyproject.toml."""
    document = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = document["project"]["dependencies"]
    return frozenset(requirement_import_name(item) for item in requirements)


def approved_runtime_packages() -> frozenset[str]:
    """Return the import names listed in the checked-in runtime allowlist."""
    lines = ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines()
    entries = [
        line.strip()
        for line in lines
        if line.strip() and not line.strip().startswith(COMMENT_PREFIX)
    ]
    return frozenset(normalize_package_name(entry) for entry in entries)


def allowed_packages() -> frozenset[str]:
    """Return every package name the source tree may import."""
    standard_library = {normalize_package_name(name) for name in sys.stdlib_module_names}
    return standard_library | approved_runtime_packages() | {FIRST_PARTY_PACKAGE}


def imported_roots(source: Path) -> set[str]:
    """Return the top-level package names that one source file imports."""
    tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_approved_allowlist_matches_the_declared_dependencies() -> None:
    """Verify the checked-in allowlist equals the runtime dependencies in pyproject.toml."""
    approved = approved_runtime_packages()
    declared = declared_runtime_packages()
    assert approved == declared, (
        f"{ALLOWLIST_PATH.name} must list the same runtime dependencies as pyproject.toml. "
        f"Approved but not declared: {sorted(approved - declared)}. "
        f"Declared but not approved: {sorted(declared - approved)}"
    )


def test_source_imports_stay_inside_the_approved_dependencies() -> None:
    """Verify every package imported under src/ is standard library, first party, or approved."""
    allowed = allowed_packages()
    offenders: list[str] = []
    for source in sorted(SOURCE_ROOT.rglob("*.py")):
        for root in sorted(imported_roots(source)):
            if normalize_package_name(root) not in allowed:
                offenders.append(f"{source.relative_to(SOURCE_ROOT)}: {root}")
    assert offenders == [], (
        "These packages are imported under src/ but are not standard library, "
        f"first party, or approved runtime dependencies: {offenders}"
    )
