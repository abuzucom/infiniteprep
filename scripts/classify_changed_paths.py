#!/usr/bin/env python3
"""Classify the changed paths of a commit range for CI job selection.

The class tells a workflow which jobs a change needs. A range that changes
only prose or only the landing site needs no cross-platform test run. Any
doubt selects the full class, and the full class runs every job.
"""
import argparse
import os
import re
import subprocess
import sys
import tomllib
from pathlib import PurePosixPath

FULL = "full"
DOCS = "docs"
WEBSITE = "website"
LIGHT = "light"
LIGHT_CLASSES = frozenset({DOCS, WEBSITE, LIGHT})

CHANGELOG_FILE = "CHANGELOG.md"
ROOT_DOCS_FILES = frozenset({"README.md"})
DOCS_DIRECTORY = "docs"
DOCS_SUFFIX = ".md"
# The policy loader appends this file to every policy copy. A change to it
# needs the synchronization and hook tests.
EXCLUDED_DOCS_FILES = frozenset({"docs/project-orientation.md"})
WEBSITE_PREFIX = "website/"
PARENT_SEGMENT = ".."

# Every pull request bumps the version in these two files. Without the
# version-only check, no pull request could leave the full class.
PYPROJECT_FILE = "pyproject.toml"
PACKAGE_INIT_FILE = "src/infiniteprep/__init__.py"
VERSION_FILES = frozenset({PYPROJECT_FILE, PACKAGE_INIT_FILE})
PACKAGE_VERSION_LINE = re.compile(r'\A__version__ = "[0-9A-Za-z.+-]{1,64}"\Z')

REVISION = re.compile(r"\A[0-9a-f]{40}\Z")
# GitHub sends this value as the previous commit of a new branch.
NULL_REVISION = "0" * 40
GIT_TIMEOUT_SECONDS = 60


def is_docs_path(path: str) -> bool:
    """Return whether the path belongs to the docs-only set."""
    if path in ROOT_DOCS_FILES:
        return True
    if path.casefold() in EXCLUDED_DOCS_FILES:
        return False
    parts = PurePosixPath(path).parts
    return len(parts) == 2 and parts[0] == DOCS_DIRECTORY and path.endswith(DOCS_SUFFIX)


def is_website_path(path: str) -> bool:
    """Return whether the path stays inside the landing site folder."""
    return path.startswith(WEBSITE_PREFIX) and PARENT_SEGMENT not in PurePosixPath(path).parts


def classify_paths(paths: list[str], version_only_files: frozenset[str] = frozenset()) -> str:
    """Return the change class for the changed paths.

    version_only_files names the version files whose change passed
    is_version_only_edit. An unverified version file selects the full class.
    """
    if not paths:
        return FULL
    has_docs = False
    has_website = False
    for path in paths:
        if path == CHANGELOG_FILE or path in version_only_files:
            continue
        if is_docs_path(path):
            has_docs = True
        elif is_website_path(path):
            has_website = True
        else:
            return FULL
    if has_docs and has_website:
        return LIGHT
    return WEBSITE if has_website else DOCS


def _is_project_version_edit(old_text: str, new_text: str) -> bool:
    """Return whether two pyproject.toml states differ only in project.version."""
    try:
        documents = (tomllib.loads(old_text), tomllib.loads(new_text))
    except tomllib.TOMLDecodeError:
        return False
    for document in documents:
        project = document.get("project")
        if not isinstance(project, dict):
            return False
        project.pop("version", None)
    return documents[0] == documents[1]


def _is_package_version_edit(old_text: str, new_text: str) -> bool:
    """Return whether two package states differ only in the version line."""
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()
    if len(old_lines) != len(new_lines):
        return False
    for old_line, new_line in zip(old_lines, new_lines):
        if old_line == new_line:
            continue
        if not PACKAGE_VERSION_LINE.fullmatch(old_line):
            return False
        if not PACKAGE_VERSION_LINE.fullmatch(new_line):
            return False
    return True


def is_version_only_edit(path: str, old_text: str, new_text: str) -> bool:
    """Return whether a version file changed nothing except its version."""
    if path == PYPROJECT_FILE:
        return _is_project_version_edit(old_text, new_text)
    if path == PACKAGE_INIT_FILE:
        return _is_package_version_edit(old_text, new_text)
    return False


def validate_revision(value: str) -> str:
    """Return the revision when it is one full commit hash."""
    if not REVISION.fullmatch(value) or value == NULL_REVISION:
        raise ValueError("revision must be a full lowercase commit hash")
    return value


def run_git(repository: str, arguments: list[str]) -> str:
    """Run one Git command with fixed arguments and return its output."""
    result = subprocess.run(
        ["git", "-C", repository, *arguments],
        capture_output=True, check=True, timeout=GIT_TIMEOUT_SECONDS,
    )
    return result.stdout.decode("utf-8")


def find_version_only_files(repository: str, base: str, head: str, paths: list[str]) -> frozenset[str]:
    """Return the changed version files that changed only their version."""
    verified = set()
    for path in sorted(VERSION_FILES.intersection(paths)):
        old_text = run_git(repository, ["show", f"{base}:{path}"])
        new_text = run_git(repository, ["show", f"{head}:{path}"])
        if is_version_only_edit(path, old_text, new_text):
            verified.add(path)
    return frozenset(verified)


def classify_range(repository: str, base: str, head: str) -> str:
    """Return the change class for the commits that head adds to base."""
    validate_revision(base)
    validate_revision(head)
    merge_base = validate_revision(run_git(repository, ["merge-base", base, head]).strip())
    # Without rename detection, a rename lists the old and the new path.
    listing = run_git(
        repository, ["diff", "--name-only", "--no-renames", "-z", merge_base, head, "--"])
    paths = [path for path in listing.split("\0") if path]
    version_only_files = find_version_only_files(repository, merge_base, head, paths)
    return classify_paths(paths, version_only_files)


def write_outputs(change_class: str, output_path: str | None) -> None:
    """Print the workflow outputs and append them to the output file."""
    light = "true" if change_class in LIGHT_CLASSES else "false"
    text = f"change_class={change_class}\nlight={light}\n"
    sys.stdout.write(text)
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(text)


def main(argv: list[str], output_path: str | None) -> int:
    """Classify one commit range and write the workflow outputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", default=".")
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    try:
        change_class = classify_range(args.repository, args.base, args.head)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        # A wrong light class would skip tests. The full class only costs time.
        print(
            f"warning: cannot classify the range ({type(error).__name__}). "
            "Full CI runs. Check the base and head revisions and the fetch depth.",
            file=sys.stderr,
        )
        change_class = FULL
    write_outputs(change_class, output_path)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:], os.environ.get("GITHUB_OUTPUT")))
