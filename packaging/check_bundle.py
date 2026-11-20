"""Bundle content check for the packaged builds (D231, D300, D301).

build-packages runs this on each built PyInstaller folder before any package is made:

    python packaging/check_bundle.py dist/InfinitePrep <windows|macos|linux>

The check fails when any of these holds:

- A file matches no pattern in packaging/bundle-allowlist/<os>.txt.
- A file is a Qt tool, belongs to a GPL-3-only Qt module family, or is GNU Readline.
  A denied file fails even when an allowlist pattern matches it.
- LICENSE, NOTICE, or ICU-LICENSE.txt is missing from the bundle root.

Allowlist patterns match paths relative to the bundle root, with forward slashes.
"*" matches within one path part, and "?" matches one character within a part.
"**/" at the start of a part matches any number of whole parts, including none. A
trailing "/**" matches every file below that folder. "**" alone matches every file.
Any other "**" is an error. Two adjacent "**" parts and two "*" joined only by "?" are
errors too.
Lines starting with "#" are comments.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections.abc import Collection, Iterable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Final, NamedTuple

ALLOWLIST_DIR: Final[Path] = Path(__file__).resolve().parent / "bundle-allowlist"
OPERATING_SYSTEMS: Final[tuple[str, ...]] = ("windows", "macos", "linux")
REQUIRED_NOTICES: Final[tuple[str, ...]] = ("LICENSE", "NOTICE", "ICU-LICENSE.txt")
COMMENT_PREFIX: Final[str] = "#"
CHECK_FAILED: Final[int] = 1
# Qt tool programs carry GPL-3 with the Qt GPL exception. The app never runs them
# (D228). Each lowercase path part is compared after one launcher suffix and the
# "pyside6-" wrapper prefix come off. Data files such as "rcc.dat" keep their suffix
# and stay allowed.
QT_TOOL_NAMES: Final[frozenset[str]] = frozenset(
    {
        "assistant",
        "balsam",
        "balsamui",
        "designer",
        "linguist",
        "lrelease",
        "lupdate",
        "qml",
        "qmlcachegen",
        "qmlformat",
        "qmlimportscanner",
        "qmllint",
        "qmlls",
        "qmlscene",
        "qmltyperegistrar",
        "qsb",
        "rcc",
        "svgtoqml",
        "uic",
    }
)
LAUNCHER_SUFFIXES: Final[tuple[str, ...]] = (".exe", ".bat", ".cmd", ".sh", ".py", ".app")
WRAPPER_PREFIX: Final[str] = "pyside6-"
PATH_SEPARATOR: Final[str] = "/"
# Allowlist token kinds. Each wildcard kind is also its pattern text.
DEEP_PREFIX: Final[str] = "**/"
DEEP_WILDCARD: Final[str] = "**"
PART_WILDCARD: Final[str] = "*"
CHARACTER_WILDCARD: Final[str] = "?"
LITERAL_KIND: Final[str] = "literal"
# The ten GPL-3-only Qt module families (D231). Each marker matches as a substring
# of the lowercase path. The markers cover Linux, macOS, and Windows file names.
# Module stems also catch QML plugins such as libqquick3dplugin and
# libdatavisualizationqmlplugin. Qt Lottie Animation builds its library as Bodymovin.
GPL3_MODULE_MARKERS: Final[tuple[str, ...]] = (
    "qt6charts",
    "qtcharts",
    "chartsqml",
    "datavisualization",
    "qt6graphs",
    "qtgraphs",
    "graphsplugin",
    "virtualkeyboard",
    "quick3d",
    "lottie",
    "bodymovin",
    "quicktimeline",
    "networkauth",
    "httpserver",
)
# GNU Readline is GPL-3 (D302): the library and Python's readline module.
READLINE_PREFIXES: Final[tuple[str, ...]] = ("libreadline", "readline.")


class BundleReport(NamedTuple):
    """The problems one bundle check found, each list sorted.

    A NamedTuple needs no entry in sys.modules. The tests load this file by path. A
    dataclass fails under that load.
    """

    unlisted: list[str]
    denied: list[str]
    missing_notices: list[str]

    @property
    def passed(self) -> bool:
        """Return True when the check found no problem."""
        return not (self.unlisted or self.denied or self.missing_notices)


def _read_token(pattern: str, index: int) -> tuple[str, str, int]:
    """Return the kind, the regular expression, and the width of the token at index.

    A "**" that does not start a path part raises ValueError. So does a bare "**"
    that does not end the pattern.
    """
    at_part_start = index == 0 or pattern[index - 1] == PATH_SEPARATOR
    if pattern.startswith(DEEP_WILDCARD, index) and not at_part_start:
        raise ValueError(f"'**' must start a path part in {pattern!r}.")
    if pattern.startswith(DEEP_PREFIX, index):
        # An alternation instead of an optional group keeps quantifiers unnested.
        return DEEP_PREFIX, "(?:.*/|)", len(DEEP_PREFIX)
    if pattern.startswith(DEEP_WILDCARD, index):
        if index + len(DEEP_WILDCARD) != len(pattern):
            raise ValueError(f"A bare '**' must end the pattern in {pattern!r}.")
        return DEEP_WILDCARD, ".*", len(DEEP_WILDCARD)
    if pattern[index] == PART_WILDCARD:
        return PART_WILDCARD, "[^/]*", 1
    if pattern[index] == CHARACTER_WILDCARD:
        return CHARACTER_WILDCARD, "[^/]", 1
    return LITERAL_KIND, re.escape(pattern[index]), 1


def _reject_overlap(pattern: str, previous_kind: str, kind: str) -> None:
    """Raise ValueError when a token overlaps the last token that was not a '?'.

    Two adjacent '**' parts, or two '*' joined only by '?', give adjacent quantifiers
    over the same characters. Those shapes backtrack polynomially on a near miss.
    """
    deep_after_deep = previous_kind == DEEP_PREFIX and kind in (DEEP_PREFIX, DEEP_WILDCARD)
    star_after_star = previous_kind == PART_WILDCARD and kind == PART_WILDCARD
    if deep_after_deep or star_after_star:
        raise ValueError(
            f"{pattern!r} joins two overlapping wildcards. Merge them into one wildcard."
        )


def compile_pattern(pattern: str) -> re.Pattern[str]:
    """Translate one allowlist pattern into an anchored regular expression.

    The translation puts no quantifier on a group that holds one. It also rejects
    adjacent overlapping wildcards. Matching therefore cannot backtrack heavily. A
    pattern that ends in "/" raises ValueError, as do the shapes _read_token and
    _reject_overlap refuse.
    """
    if pattern.endswith(PATH_SEPARATOR):
        raise ValueError(f"{pattern!r} ends in '/' and matches no file. Use a trailing '/**'.")
    parts: list[str] = []
    index = 0
    # A '?' consumes exactly one character. The overlap check therefore skips it.
    previous_kind = ""
    while index < len(pattern):
        kind, expression, width = _read_token(pattern, index)
        _reject_overlap(pattern, previous_kind, kind)
        parts.append(expression)
        index += width
        if kind != CHARACTER_WILDCARD:
            previous_kind = kind
    return re.compile("".join(parts))


def _read_entries(lines: Iterable[str]) -> list[str]:
    """Return the stripped allowlist entries, skipping blanks and comments."""
    entries = (line.strip() for line in lines)
    return [entry for entry in entries if entry and not entry.startswith(COMMENT_PREFIX)]


def read_allowlist(lines: Iterable[str]) -> list[re.Pattern[str]]:
    """Return the compiled patterns of an allowlist, skipping blanks and comments."""
    return [compile_pattern(entry) for entry in _read_entries(lines)]


class AllowlistIndex(NamedTuple):
    """An allowlist split for hash lookups (D300).

    An entry without a wildcard joins exact_paths. A wildcard entry whose first path
    part is literal joins the bucket of that part. Every other wildcard entry stays
    unanchored and gets tried against every file.
    """

    exact_paths: frozenset[str]
    patterns_by_first_part: Mapping[str, tuple[re.Pattern[str], ...]]
    unanchored_patterns: tuple[re.Pattern[str], ...]

    def matches(self, relative_path: str) -> bool:
        """Return True when an allowlist entry matches the relative path."""
        if relative_path in self.exact_paths:
            return True
        first_part = relative_path.split(PATH_SEPARATOR, 1)[0]
        bucket = self.patterns_by_first_part.get(first_part, ())
        return _matches_any(bucket, relative_path) or _matches_any(
            self.unanchored_patterns, relative_path
        )


def _has_wildcard(text: str) -> bool:
    """Return True when the text holds a '*' or a '?'."""
    return PART_WILDCARD in text or CHARACTER_WILDCARD in text


def index_allowlist(lines: Iterable[str]) -> AllowlistIndex:
    """Return the hash index of an allowlist, skipping blanks and comments.

    Every entry compiles first, so an invalid entry raises ValueError as in
    read_allowlist.
    """
    exact_paths: set[str] = set()
    buckets: dict[str, list[re.Pattern[str]]] = {}
    unanchored: list[re.Pattern[str]] = []
    for entry in _read_entries(lines):
        compiled = compile_pattern(entry)
        first_part, separator, _rest = entry.partition(PATH_SEPARATOR)
        if not _has_wildcard(entry):
            exact_paths.add(entry)
        elif separator and not _has_wildcard(first_part):
            buckets.setdefault(first_part, []).append(compiled)
        else:
            unanchored.append(compiled)
    by_first_part = MappingProxyType({part: tuple(found) for part, found in buckets.items()})
    return AllowlistIndex(frozenset(exact_paths), by_first_part, tuple(unanchored))


def is_denied(relative_path: str) -> bool:
    """Return True for a Qt tool, a GPL-3-only Qt module file, or GNU Readline."""
    path = PurePosixPath(relative_path.lower())
    lowered = path.as_posix()
    if any(marker in lowered for marker in GPL3_MODULE_MARKERS):
        return True
    if path.name.startswith(READLINE_PREFIXES):
        return True
    # Every part counts, so a file inside a folder named after a tool also fails.
    return any(_tool_name(part) in QT_TOOL_NAMES for part in path.parts)


def _tool_name(part: str) -> str:
    """Return a path part without one launcher suffix and without the wrapper prefix."""
    for suffix in LAUNCHER_SUFFIXES:
        if part.endswith(suffix):
            part = part.removesuffix(suffix)
            break
    return part.removeprefix(WRAPPER_PREFIX)


class BundleInventory(NamedTuple):
    """The entries of one bundle walk, as relative POSIX paths."""

    listed: list[str]
    regular_files: frozenset[str]


def inventory_bundle(bundle: Path) -> BundleInventory:
    """Walk the bundle once with os.scandir and return its listed entries and regular files.

    A symlink counts as a listed entry unless it points to a folder. A dangling link
    would otherwise escape the check and still ship through cp -a and tar. The walk
    never descends into a folder link. A regular file is any entry that resolves to a
    file, links included.
    """
    listed: list[str] = []
    regular: set[str] = set()
    folders: list[tuple[str, str]] = [(os.fspath(bundle), "")]
    while folders:
        folder, prefix = folders.pop()
        with os.scandir(folder) as entries:
            for entry in entries:
                _record_entry(entry, f"{prefix}{entry.name}", folders, listed, regular)
    return BundleInventory(sorted(listed), frozenset(regular))


def _record_entry(
    entry: os.DirEntry[str],
    relative_path: str,
    folders: list[tuple[str, str]],
    listed: list[str],
    regular: set[str],
) -> None:
    """Record one scandir entry. A real folder joins the walk instead of the inventory."""
    if entry.is_symlink():
        if not entry.is_dir():
            listed.append(relative_path)
        if entry.is_file():
            regular.add(relative_path)
        return
    if entry.is_dir(follow_symlinks=False):
        folders.append((entry.path, f"{relative_path}{PATH_SEPARATOR}"))
        return
    if entry.is_file(follow_symlinks=False):
        listed.append(relative_path)
        regular.add(relative_path)


def bundle_files(bundle: Path) -> list[str]:
    """Return every listed entry below the bundle as a sorted list of relative POSIX paths."""
    return inventory_bundle(bundle).listed


def _matches_any(patterns: Iterable[re.Pattern[str]], relative_path: str) -> bool:
    """Return True when any pattern matches the whole relative path."""
    return any(pattern.fullmatch(relative_path) for pattern in patterns)


def check_files(
    files: Sequence[str],
    patterns: Sequence[re.Pattern[str]],
    *,
    regular_files: Collection[str] | None = None,
    allowlist_index: AllowlistIndex | None = None,
) -> BundleReport:
    """Check relative file paths against the allowlist, the denylist, and the notices.

    An allowlist_index replaces the linear scan over patterns. A notice counts as
    present only when it is in regular_files. Without regular_files, every listed
    file counts.
    """
    if allowlist_index is None:
        unlisted = [name for name in files if not _matches_any(patterns, name)]
    else:
        unlisted = [name for name in files if not allowlist_index.matches(name)]
    denied = [name for name in files if is_denied(name)]
    notice_sources = set(files) if regular_files is None else regular_files
    missing = [notice for notice in REQUIRED_NOTICES if notice not in notice_sources]
    return BundleReport(sorted(unlisted), sorted(denied), missing)


def check_bundle(bundle: Path, operating_system: str) -> BundleReport:
    """Check a built bundle folder against the allowlist of one operating system.

    An operating system outside OPERATING_SYSTEMS raises ValueError.
    """
    if operating_system not in OPERATING_SYSTEMS:
        raise ValueError(
            f"Unknown operating system {operating_system!r}. Pass one of {OPERATING_SYSTEMS}."
        )
    allowlist = ALLOWLIST_DIR / f"{operating_system}.txt"
    index = index_allowlist(allowlist.read_text(encoding="utf-8").splitlines())
    inventory = inventory_bundle(bundle)
    # The listed entries keep dangling links for the allowlist and denylist. A notice
    # must also resolve to a file, or the bundle ships no license text.
    return check_files(
        inventory.listed,
        (),
        regular_files=inventory.regular_files,
        allowlist_index=index,
    )


def print_report(report: BundleReport) -> None:
    """Print one line per problem. The build log then names every file."""
    for name in report.unlisted:
        print(f"check_bundle: not in the allowlist: {name}")
    for name in report.denied:
        print(f"check_bundle: denied file: {name}")
    for name in report.missing_notices:
        print(f"check_bundle: missing notice at the bundle root: {name}")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the check from the command line and return 0 on a pass, 1 on a failure."""
    parser = argparse.ArgumentParser(description="Check a built bundle's files.")
    parser.add_argument("bundle", type=Path, help="The built bundle folder.")
    parser.add_argument("operating_system", choices=OPERATING_SYSTEMS)
    args = parser.parse_args(argv)
    if not args.bundle.is_dir():
        parser.error(f"{args.bundle} is not a folder. Pass the built bundle folder.")
    report = check_bundle(args.bundle, args.operating_system)
    print_report(report)
    if not report.passed:
        print(
            "check_bundle: the bundle check failed. Review each file above. Update"
            f" packaging/bundle-allowlist/{args.operating_system}.txt or infiniteprep.spec."
        )
        return CHECK_FAILED
    print(f"check_bundle: {args.operating_system} bundle passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
