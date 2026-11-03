"""Check the CodeQL configuration against the repository it analyzes."""

from __future__ import annotations

import unittest
from pathlib import Path
from typing import Any, Final

import yaml

from scripts.trusted_git import run_git

ROOT: Final[Path] = Path(__file__).resolve().parent.parent
CONFIG: Final[Path] = ROOT / ".github" / "codeql" / "config.yml"
DOCUMENT: Final[Path] = ROOT / "docs" / "code-scanning.md"
ADVANCED_WORKFLOW: Final[Path] = ROOT / ".github" / "workflows" / "codeql.yml"
EXTENDED_SUITE: Final[str] = "security-extended"
# The property value must name a remote address. A local path breaks every
# analysis of a ref that predates the configuration file.
PROPERTY_VALUE: Final[str] = "remote=abuzucom/infiniteprep@main:.github/codeql/config.yml"
GIT_TIMEOUT_SECONDS: Final[int] = 60


def load_config() -> dict[str, Any]:
    """Return the parsed CodeQL configuration, failing when the file is absent."""
    if not CONFIG.is_file():
        raise AssertionError(f"missing CodeQL configuration: {CONFIG.relative_to(ROOT)}")
    document = yaml.safe_load(CONFIG.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise AssertionError("CodeQL configuration must be a mapping")
    return document


def declared_paths() -> list[str]:
    """Return the analyzed directories the configuration declares."""
    paths = load_config().get("paths")
    if not isinstance(paths, list):
        raise AssertionError("CodeQL configuration must declare a list of paths")
    for entry in paths:
        if not isinstance(entry, str):
            raise AssertionError("every CodeQL path must be text")
    return list(paths)


def tracked_files() -> list[Path]:
    """Return every tracked repository file, which is the tree CodeQL analyzes."""
    result = run_git(ROOT, ["ls-files", "-z"], timeout=GIT_TIMEOUT_SECONDS)
    if result.returncode != 0:
        raise AssertionError(f"git ls-files failed: {result.stderr.strip()}")
    return [Path(name) for name in result.stdout.split("\0") if name]


def source_files(suffix: str) -> list[Path]:
    """Return every tracked repository file with the suffix."""
    return [path for path in tracked_files() if path.name.endswith(suffix)]


class CodeQLConfigurationTest(unittest.TestCase):
    """The CodeQL configuration names every analyzed directory and the extended suite."""

    def test_configuration_file_exists(self) -> None:
        self.assertTrue(CONFIG.is_file(), ".github/codeql/config.yml is missing")

    def test_configuration_declares_a_name(self) -> None:
        name = load_config().get("name")
        self.assertIsInstance(name, str)
        self.assertTrue(str(name).strip(), "the CodeQL configuration needs a name")

    def test_configuration_adds_the_extended_query_suite(self) -> None:
        queries = load_config().get("queries")
        self.assertIsInstance(queries, list)
        suites = [entry.get("uses") for entry in queries if isinstance(entry, dict)]
        self.assertIn(EXTENDED_SUITE, suites)

    def test_every_queried_suite_is_a_known_codeql_suite(self) -> None:
        known = {"security", "security-extended", "security-and-quality"}
        queries = load_config().get("queries")
        self.assertIsInstance(queries, list)
        for entry in queries:
            self.assertIsInstance(entry, dict)
            self.assertIn(entry.get("uses"), known)

    def test_every_analyzed_directory_is_declared(self) -> None:
        declared = set(declared_paths())
        required = {path.parts[0] for path in source_files(".py") if len(path.parts) > 1}
        self.assertEqual(
            required - declared,
            set(),
            "a directory holds Python source but the CodeQL configuration omits it",
        )

    def test_workflow_directory_is_declared(self) -> None:
        self.assertIn(".github", declared_paths())

    def test_no_python_source_sits_at_the_repository_root(self) -> None:
        root_level = [path for path in source_files(".py") if len(path.parts) == 1]
        self.assertEqual(
            [str(path) for path in root_level],
            [],
            "a root level Python file cannot be named by a CodeQL path entry",
        )

    def test_declared_paths_are_plain_relative_directories(self) -> None:
        for entry in declared_paths():
            self.assertFalse(entry.startswith("/"), entry)
            self.assertNotIn("..", Path(entry).parts, entry)
            self.assertNotIn("*", entry, entry)
            self.assertTrue(Path(entry).is_dir(), entry)

    def test_declared_paths_are_unique(self) -> None:
        paths = declared_paths()
        self.assertEqual(len(paths), len(set(paths)))

    def test_default_setup_keeps_ownership_of_the_analysis(self) -> None:
        self.assertFalse(
            ADVANCED_WORKFLOW.exists(),
            "an advanced CodeQL workflow competes with the default setup",
        )

    def test_document_records_the_remote_property_value(self) -> None:
        self.assertIn(
            PROPERTY_VALUE,
            DOCUMENT.read_text(encoding="utf-8"),
            "the document must name the property value that the repository needs",
        )

    def test_documented_property_value_names_an_existing_file(self) -> None:
        path = PROPERTY_VALUE.rsplit(":", 1)[-1]
        self.assertTrue((ROOT / path).is_file(), path)


if __name__ == "__main__":
    unittest.main()
