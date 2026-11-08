"""Bundle content check for the packaged builds (D231, D300, D301).

build-packages runs this on each built PyInstaller folder before any package is made:

    python packaging/check_bundle.py dist/InfinitePrep <windows|macos|linux>

The check fails when any of these holds:

- A file matches no pattern in packaging/bundle-allowlist/<os>.txt.
- A file is a Qt tool, belongs to a GPL-3-only Qt module family, or is GNU Readline.
  A denied file fails even when an allowlist pattern matches it.
- LICENSE, NOTICE, or ICU-LICENSE.txt is missing from the bundle root.

Allowlist patterns match paths relative to the bundle root, with forward slashes.
"*" matches within one path part, "?" matches one character within a part, and
"**/" matches any number of whole parts, including none. Lines starting with "#"
are comments.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Final

ALLOWLIST_DIR: Final[Path] = Path(__file__).resolve().parent / "bundle-allowlist"
OPERATING_SYSTEMS: Final[tuple[str, ...]] = ("windows", "macos", "linux")
REQUIRED_NOTICES: Final[tuple[str, ...]] = ("LICENSE", "NOTICE", "ICU-LICENSE.txt")
COMMENT_PREFIX: Final[str] = "#"
CHECK_FAILED: Final[int] = 1
# Qt tool programs carry GPL-3 with the Qt GPL exception, and the app never runs
# them (D228). Matched against the lowercase file name without ".exe", and against
# macOS ".app" folder names.
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
EXECUTABLE_SUFFIX: Final[str] = ".exe"
APP_SUFFIX: Final[str] = ".app"
# The ten GPL-3-only Qt module families (D231). Each marker matches as a substring
# of the lowercase path. The markers cover Linux, macOS, and Windows file names.
GPL3_MODULE_MARKERS: Final[tuple[str, ...]] = (
    "qt6charts",
    "qtcharts",
    "qt6datavisualization",
    "qtdatavisualization",
    "qt6graphs",
    "qtgraphs",
    "virtualkeyboard",
    "qt6quick3d",
    "qtquick3d",
    "qt6lottie",
    "qtlottie",
    "qt6quicktimeline",
    "qtquicktimeline",
    "qt6networkauth",
    "qtnetworkauth",
    "qt6httpserver",
    "qthttpserver",
)
# GNU Readline is GPL-3 (D302): the library and Python's readline module.
READLINE_PREFIXES: Final[tuple[str, ...]] = ("libreadline", "readline.")


@dataclass(frozen=True)
class BundleReport:
    """The problems one bundle check found, each list sorted."""

    unlisted: list[str]
    denied: list[str]
    missing_notices: list[str]

    @property
    def passed(self) -> bool:
        """Return True when the check found no problem."""
        return not (self.unlisted or self.denied or self.missing_notices)


def compile_pattern(pattern: str) -> re.Pattern[str]:
    """Translate one allowlist pattern into an anchored regular expression.

    The translation uses no nested quantifier. Matching cannot backtrack heavily.
    """
    parts: list[str] = []
    index = 0
    while index < len(pattern):
        if pattern.startswith("**/", index):
            parts.append("(?:.*/)?")
            index += len("**/")
        elif pattern.startswith("**", index):
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
    if path.name.removesuffix(EXECUTABLE_SUFFIX) in QT_TOOL_NAMES:
        return True
    return any(part.removesuffix(APP_SUFFIX) in QT_TOOL_NAMES for part in _app_folders(path))


def _app_folders(path: PurePosixPath) -> list[str]:
    """Return the folder names in the path that end in ".app"."""
    return [part for part in path.parts[:-1] if part.endswith(APP_SUFFIX)]


def bundle_files(bundle: Path) -> list[str]:
    """Return every file below the bundle as a sorted list of relative POSIX paths."""
    files = (path for path in bundle.rglob("*") if path.is_file())
    return sorted(path.relative_to(bundle).as_posix() for path in files)


def check_files(files: Sequence[str], patterns: Sequence[re.Pattern[str]]) -> BundleReport:
    """Check relative file paths against the allowlist, the denylist, and the notices."""
    present = set(files)
    unlisted = [name for name in files if not any(p.fullmatch(name) for p in patterns)]
    denied = [name for name in files if is_denied(name)]
    missing = [notice for notice in REQUIRED_NOTICES if notice not in present]
    return BundleReport(sorted(unlisted), sorted(denied), missing)


def check_bundle(bundle: Path, operating_system: str) -> BundleReport:
    """Check a built bundle folder against the allowlist of one operating system."""
    allowlist = ALLOWLIST_DIR / f"{operating_system}.txt"
    patterns = read_allowlist(allowlist.read_text(encoding="utf-8").splitlines())
    return check_files(bundle_files(bundle), patterns)


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
            f" packaging/bundle-allowlist/{args.operating_system}.txt or the spec."
        )
        return CHECK_FAILED
    print(f"check_bundle: {args.operating_system} bundle passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
