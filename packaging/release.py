"""Release helpers for the tag release in build-packages (D83a4b, D314 to D318, D328).

Four subcommands, each run from the repository root:

    python packaging/release.py check-tag v0.48.0
    python packaging/release.py check-assets <asset-folder> 0.48.0
    python packaging/release.py checksums <asset-folder>
    python packaging/release.py notes 0.48.0 <output-file>

check-tag fails unless the tag equals "v" plus the version in
src/infiniteprep/__init__.py, pyproject.toml, and the top CHANGELOG entry (D317).
check-assets fails unless the folder holds exactly the six packages of the
version (D319). With --require-bundles, the folder must also hold the six
attestation bundles (D328).
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
CHECK_FAILED: Final[int] = 1
INIT_FILE: Final[str] = "src/infiniteprep/__init__.py"
PYPROJECT_FILE: Final[str] = "pyproject.toml"
CHANGELOG_FILE: Final[str] = "CHANGELOG.md"
ASSET_PREFIX: Final[str] = "InfinitePrep"
# The six packages that build-packages makes, after "InfinitePrep-<version>-".
ASSET_SUFFIXES: Final[tuple[str, ...]] = (
    "windows-x64-portable.zip",
    "windows-x64-setup.exe",
    "linux-x86_64.AppImage",
    "linux-x86_64.tar.gz",
    "linux-x86_64-portable.tar.gz",
    "macos-arm64.dmg",
)
BUNDLE_SUFFIX: Final[str] = ".sigstore.json"
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


def _read_text(path: Path) -> str:
    """Return the text of a file, or an empty string when the file is absent."""
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


def read_versions(root: Path) -> dict[str, str]:
    """Return the version each source file states, keyed by its relative path.

    A missing file or an unparsable pyproject.toml states an empty version.
    """
    init_match = VERSION_LINE.search(_read_text(root / INIT_FILE))
    try:
        pyproject = tomllib.loads(_read_text(root / PYPROJECT_FILE))
    except tomllib.TOMLDecodeError:
        pyproject = {}
    changelog_match = RELEASE_HEADING.search(_read_text(root / CHANGELOG_FILE))
    return {
        INIT_FILE: init_match.group(1) if init_match else "",
        PYPROJECT_FILE: str(pyproject.get("project", {}).get("version", "")),
        CHANGELOG_FILE: changelog_match.group(1) if changelog_match else "",
    }


def tag_problems(tag: str, root: Path) -> list[str]:
    """Return one line per missing file, empty version, or version that differs from the tag."""
    if not tag.startswith(TAG_PREFIX):
        return [f"Tag {tag!r} does not start with {TAG_PREFIX!r}."]
    expected = tag.removeprefix(TAG_PREFIX)
    problems = [] if expected else [f"Tag {tag!r} names no version."]
    for source, found in read_versions(root).items():
        if not (root / source).is_file():
            problems.append(f"{source} is missing from the checkout.")
        elif not found:
            problems.append(f"{source} states no version.")
        elif found != expected:
            problems.append(f"{source} states version {found!r}, but the tag names {expected!r}.")
    return problems


def file_sha256(path: Path) -> str:
    """Return the SHA-256 hex digest of a file."""
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def expected_packages(version: str) -> set[str]:
    """Return the six package file names that a build of this version makes."""
    return {f"{ASSET_PREFIX}-{version}-{suffix}" for suffix in ASSET_SUFFIXES}


def expected_bundles(version: str) -> set[str]:
    """Return the six Sigstore bundle file names that attest this version's packages (D328)."""
    return {f"{name}{BUNDLE_SUFFIX}" for name in expected_packages(version)}


def expected_assets(version: str, *, include_bundles: bool = True) -> set[str]:
    """Return the expected asset names of this version (D319, D328)."""
    packages = expected_packages(version)
    return packages | expected_bundles(version) if include_bundles else packages


def _asset_kind(name: str) -> str:
    """Return 'Bundle' for a Sigstore bundle and 'Package' for a release package."""
    return "Bundle" if name.endswith(BUNDLE_SUFFIX) else "Package"


def asset_problems(folder: Path, version: str, *, require_bundles: bool = False) -> list[str]:
    """Return one line per expected asset that is missing and per extra entry.

    Only require_bundles makes the Sigstore bundles expected. Without it, a
    bundle counts as an extra entry.
    """
    present = {path.name for path in folder.iterdir()}
    expected = expected_assets(version, include_bundles=require_bundles)
    missing = [f"{_asset_kind(name)} {name} is missing." for name in sorted(expected - present)]
    extra = [
        f"{name} is not a release asset of version {version}."
        for name in sorted(present - expected)
    ]
    return missing + extra


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
        line_end = changelog.find("\n", heading.start())
        body_start = len(changelog) if line_end == -1 else line_end + 1
        body_end = headings[index + 1].start() if index + 1 < len(headings) else len(changelog)
        return changelog[body_start:body_end].strip() + "\n"
    raise ValueError(f"CHANGELOG.md has no section for version {version}. Add one first.")


def release_notes(version: str, root: Path) -> str:
    """Return the release notes: the CHANGELOG section, then the download block."""
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    return f"{changelog_section(version, changelog)}\n{DOWNLOAD_NOTES}"


def _report_assets(folder: Path, version: str, *, require_bundles: bool = False) -> int:
    """Print each asset problem and return 0 when the folder holds the expected assets."""
    problems = asset_problems(folder, version, require_bundles=require_bundles)
    for problem in problems:
        print(f"release: {problem}")
    if problems:
        print("release: rebuild the packages for this tag, then rerun the release job.")
        return CHECK_FAILED
    # A folder without problems holds exactly the expected names.
    count = len(expected_assets(version, include_bundles=require_bundles))
    print(f"release: {folder} holds the {count} release assets of version {version}.")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Run one subcommand and return 0 on success, 1 on a failed tag or asset check."""
    parser = argparse.ArgumentParser(description="Release helpers for build-packages.")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check-tag", help="Check a tag against the version files.")
    check.add_argument("tag")
    sums = commands.add_parser("checksums", help="Write SHA256SUMS.txt for a folder.")
    sums.add_argument("folder", type=Path)
    notes = commands.add_parser("notes", help="Write the release notes of a version.")
    notes.add_argument("version")
    notes.add_argument("output", type=Path)
    assets = commands.add_parser("check-assets", help="Check the package names in a folder.")
    assets.add_argument("folder", type=Path)
    assets.add_argument("version")
    assets.add_argument(
        "--require-bundles",
        action="store_true",
        help="Require the six Sigstore bundle files alongside the packages (D328).",
    )
    args = parser.parse_args(argv)
    if args.command == "check-assets":
        if not args.folder.is_dir():
            parser.error(f"{args.folder} is not a folder. Pass the folder of release assets.")
        return _report_assets(args.folder, args.version, require_bundles=args.require_bundles)
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
    if args.command == "notes":
        args.output.write_text(release_notes(args.version, ROOT), encoding="utf-8", newline="\n")
        print(f"release: wrote {args.output}")
        return 0
    parser.error(f"Subcommand {args.command} has no handler. Add one to main().")


if __name__ == "__main__":
    sys.exit(main())
