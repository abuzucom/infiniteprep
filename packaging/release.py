"""Release helpers for the tag release in build-packages (D83a4b, D314 to D318).

Three subcommands, each run from the repository root:

    python packaging/release.py check-tag v0.48.0
    python packaging/release.py checksums <asset-folder>
    python packaging/release.py notes 0.48.0 <output-file>

check-tag fails unless the tag equals "v" plus the version in
src/infiniteprep/__init__.py, pyproject.toml, and the top CHANGELOG entry (D317).
checksums writes SHA256SUMS.txt in the sha256sum format, sorted by file name.
notes writes the CHANGELOG section of one version and a fixed download block (D318).
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Final

ROOT: Final[Path] = Path(__file__).resolve().parent.parent
TAG_PREFIX: Final[str] = "v"
CHECKSUM_FILE: Final[str] = "SHA256SUMS.txt"
READ_CHUNK_BYTES: Final[int] = 1024 * 1024
CHECK_FAILED: Final[int] = 1
VERSION_LINE: Final[re.Pattern[str]] = re.compile(r'^__version__ = "([^"]+)"$', re.MULTILINE)
# A CHANGELOG release heading, such as "## [0.47.0] (2026-11-08)".
RELEASE_HEADING: Final[re.Pattern[str]] = re.compile(r"^## \[([^\]]+)\]", re.MULTILINE)
DOWNLOAD_NOTES: Final[str] = """\
## Downloads

- Check each download against `SHA256SUMS.txt`.
- macOS: the app carries an ad-hoc signature only. macOS blocks its first launch.
  Open System Settings, then Privacy & Security, and choose `Open Anyway` for
  InfinitePrep.
- Each package ships `NOTICE`. It lists the third-party licenses and the written
  offer for the source code of the LGPL components.
"""


def read_versions(root: Path) -> dict[str, str]:
    """Return the version each source file states, keyed by its relative path."""
    init_text = (root / "src" / "infiniteprep" / "__init__.py").read_text(encoding="utf-8")
    init_match = VERSION_LINE.search(init_text)
    with (root / "pyproject.toml").open("rb") as handle:
        pyproject = tomllib.load(handle)
    changelog_match = RELEASE_HEADING.search((root / "CHANGELOG.md").read_text(encoding="utf-8"))
    return {
        "src/infiniteprep/__init__.py": init_match.group(1) if init_match else "",
        "pyproject.toml": str(pyproject.get("project", {}).get("version", "")),
        "CHANGELOG.md": changelog_match.group(1) if changelog_match else "",
    }


def tag_problems(tag: str, root: Path) -> list[str]:
    """Return one line per file whose version does not match the tag."""
    if not tag.startswith(TAG_PREFIX):
        return [f"Tag {tag!r} does not start with {TAG_PREFIX!r}."]
    expected = tag.removeprefix(TAG_PREFIX)
    return [
        f"{source} states version {found!r}, but the tag names {expected!r}."
        for source, found in read_versions(root).items()
        if found != expected
    ]


def file_sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of a file, read in chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(READ_CHUNK_BYTES), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_checksums(folder: Path) -> Path:
    """Write SHA256SUMS.txt for every other file directly in the folder and return its path."""
    assets = sorted(
        path for path in folder.iterdir() if path.is_file() and path.name != CHECKSUM_FILE
    )
    lines = [f"{file_sha256(path)}  {path.name}\n" for path in assets]
    output = folder / CHECKSUM_FILE
    output.write_text("".join(lines), encoding="utf-8", newline="\n")
    return output


def changelog_section(version: str, changelog: str) -> str:
    """Return the body of one version's CHANGELOG section, without its heading.

    A version with no section raises ValueError.
    """
    headings = list(RELEASE_HEADING.finditer(changelog))
    for index, heading in enumerate(headings):
        if heading.group(1) != version:
            continue
        body_start = changelog.index("\n", heading.start()) + 1
        body_end = headings[index + 1].start() if index + 1 < len(headings) else len(changelog)
        return changelog[body_start:body_end].strip() + "\n"
    raise ValueError(f"CHANGELOG.md has no section for version {version}. Add one first.")


def release_notes(version: str, root: Path) -> str:
    """Return the release notes: the CHANGELOG section, then the download block."""
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    return f"{changelog_section(version, changelog)}\n{DOWNLOAD_NOTES}"


def main(argv: Sequence[str] | None = None) -> int:
    """Run one subcommand and return 0 on success, 1 on a failed tag check."""
    parser = argparse.ArgumentParser(description="Release helpers for build-packages.")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-tag", help="Check a tag against the version files.")
    check.add_argument("tag")
    sums = commands.add_parser("checksums", help="Write SHA256SUMS.txt for a folder.")
    sums.add_argument("folder", type=Path)
    notes = commands.add_parser("notes", help="Write the release notes of a version.")
    notes.add_argument("version")
    notes.add_argument("output", type=Path)
    args = parser.parse_args(argv)
    if args.command == "check-tag":
        problems = tag_problems(args.tag, ROOT)
        for problem in problems:
            print(f"release: {problem}")
        if problems:
            print("release: align the tag and the version files, then push a new tag.")
            return CHECK_FAILED
        print(f"release: tag {args.tag} matches every version file.")
        return 0
    if args.command == "checksums":
        if not args.folder.is_dir():
            parser.error(f"{args.folder} is not a folder. Pass the folder of release assets.")
        print(f"release: wrote {write_checksums(args.folder)}")
        return 0
    args.output.write_text(release_notes(args.version, ROOT), encoding="utf-8", newline="\n")
    print(f"release: wrote {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
