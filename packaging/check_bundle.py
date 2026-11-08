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
Any other "**" is an error.
Lines starting with "#" are comments.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath
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


def compile_pattern(pattern: str) -> re.Pattern[str]:
    """Translate one allowlist pattern into an anchored regular expression.

    The translation puts no quantifier on a group that holds one. Matching cannot
    backtrack heavily. A "**" that does not start a path part raises ValueError. So do
    a bare "**" that does not end the pattern and a pattern that ends in "/".
    """
    if pattern.endswith(PATH_SEPARATOR):
        raise ValueError(f"{pattern!r} ends in '/' and matches no file. Use a trailing '/**'.")
    parts: list[str] = []
    index = 0
    while index < len(pattern):
        at_part_start = index == 0 or pattern[index - 1] == PATH_SEPARATOR
        if pattern.startswith("**", index) and not at_part_start:
            raise ValueError(f"'**' must start a path part in {pattern!r}.")
        if pattern.startswith("**/", index):
            # An alternation instead of an optional group keeps quantifiers unnested.
            parts.append("(?:.*/|)")
            index += len("**/")
        elif pattern.startswith("**", index):
            if index + len("**") != len(pattern):
                raise ValueError(f"A bare '**' must end the pattern in {pattern!r}.")
            parts.append(".*")
            index += len("**")
        elif pattern[index] == "*":
            parts.append("[^/]*")
            index += 1
        elif pattern[index] == "?":
            parts.append("[^/]")
            index += 1
        else:
            parts.append(re.escape(pattern[index]))
            index += 1
    return re.compile("".join(parts))


def read_allowlist(lines: Iterable[str]) -> list[re.Pattern[str]]:
    """Return the compiled patterns of an allowlist, skipping blanks and comments."""
    patterns: list[re.Pattern[str]] = []
    for line in lines:
        entry = line.strip()
        if entry and not entry.startswith(COMMENT_PREFIX):
            patterns.append(compile_pattern(entry))
    return patterns


def is_denied(relative_path: str) -> bool:
    """Return True for a Qt tool, a GPL-3-only Qt module file, or GNU Readline."""
    path = PurePosixPath(relative_path.lower())
    if any(marker in path.as_posix() for marker in GPL3_MODULE_MARKERS):
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


def bundle_files(bundle: Path) -> list[str]:
    """Return every file below the bundle as a sorted list of relative POSIX paths.

    A symlink counts as a file unless it points to a folder. A dangling link would
    otherwise escape the check and still ship through cp -a and tar.
    """
    files = (path for path in bundle.rglob("*") if _is_listed_entry(path))
    return sorted(path.relative_to(bundle).as_posix() for path in files)


def _is_listed_entry(path: Path) -> bool:
    """Return True for a regular file or a symlink that does not point to a folder."""
    if path.is_symlink():
        return not path.is_dir()
    return path.is_file()


def check_files(files: Sequence[str], patterns: Sequence[re.Pattern[str]]) -> BundleReport:
    """Check relative file paths against the allowlist, the denylist, and the notices."""
    present = set(files)
    unlisted = [name for name in files if not any(p.fullmatch(name) for p in patterns)]
    denied = [name for name in files if is_denied(name)]
    missing = [notice for notice in REQUIRED_NOTICES if notice not in present]
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
    patterns = read_allowlist(allowlist.read_text(encoding="utf-8").splitlines())
    report = check_files(bundle_files(bundle), patterns)
    # The inventory keeps dangling links for the allowlist and denylist. A notice
    # must also resolve to a file, or the bundle ships no license text.
    missing = [notice for notice in REQUIRED_NOTICES if not (bundle / notice).is_file()]
    return report._replace(missing_notices=missing)


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
