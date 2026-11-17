"""Bundle size report and limit for the packaged builds (D354).

build-packages runs this on each built bundle after the packages exist:

    python packaging/bundle_size.py check --os <windows|macos|linux> \
        --bundle <folder> [--folders-root <folder>] \
        [--package <name>=<file> ...] [--report-dir <folder>]

The check reports the unpacked size of the bundle, the size of every package,
the largest folders in the bundle, and the limit for the operating system. It
fails when the bundle grows past that limit.

The limit comes from packaging/bundle_size_limits.json. Each record holds the
measured unpacked size of the bundle on main in bytes. The limit is that size
plus 50 percent. A record of null means the size was not measured yet, so the
check reports the sizes and passes without a limit comparison.

The unpacked size is the sum of the byte sizes of the regular files below the
bundle root. A symbolic link is skipped and never followed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import date
from pathlib import Path
from typing import Final, NamedTuple

LIMITS_FILE: Final[Path] = Path(__file__).resolve().parent / "bundle_size_limits.json"
OPERATING_SYSTEMS: Final[tuple[str, ...]] = ("windows", "macos", "linux")
# The limit is the measured size plus 50 percent (D354). The fraction is
# computed with integers so that no rounding error changes the limit.
HEADROOM_NUMERATOR: Final[int] = 3
HEADROOM_DENOMINATOR: Final[int] = 2
LARGEST_FOLDER_COUNT: Final[int] = 10
NOTE_KEY: Final[str] = "note"
RECORD_FIELDS: Final[frozenset[str]] = frozenset({"measured_bytes", "measured_at"})
BYTES_PER_MEBIBYTE: Final[int] = 1024 * 1024
CHECK_FAILED: Final[int] = 1
INPUT_FAILED: Final[int] = 2


class FolderSize(NamedTuple):
    """One folder of the bundle and the total size of the files below it."""

    name: str
    bytes: int


class PackageSize(NamedTuple):
    """One built package and the size of the file on disk."""

    name: str
    path: str
    bytes: int


class SizeReport(NamedTuple):
    """The measured sizes of one bundle and the packages built from it."""

    operating_system: str
    bundle_path: str
    bundle_bytes: int
    measured_bytes: int | None
    limit_bytes: int | None
    headroom_bytes: int | None
    over_limit: bool
    folders: tuple[FolderSize, ...]
    packages: tuple[PackageSize, ...]


def directory_bytes(root: Path) -> int:
    """Return the sum of the regular file sizes below root.

    A symbolic link is skipped and never followed. A link to a folder must not
    pull a second copy of that folder into the total.
    """
    total = 0
    pending = [root]
    while pending:
        current = pending.pop()
        with os.scandir(current) as entries:
            for entry in entries:
                if entry.is_symlink():
                    continue
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
    return total


def largest_folders(root: Path, count: int = LARGEST_FOLDER_COUNT) -> tuple[FolderSize, ...]:
    """Return the largest immediate child folders of root, largest first.

    Each folder holds the total size of the regular files below it. The list
    holds at most count folders.
    """
    folders: list[FolderSize] = []
    with os.scandir(root) as entries:
        for entry in entries:
            if entry.is_symlink():
                continue
            if entry.is_dir(follow_symlinks=False):
                folders.append(FolderSize(entry.name, directory_bytes(Path(entry.path))))
    folders.sort(key=lambda folder: (-folder.bytes, folder.name))
    return tuple(folders[:count])


def derive_limit(measured_bytes: int) -> int:
    """Return the limit for a measured size: that size plus 50 percent, rounded up."""
    product = measured_bytes * HEADROOM_NUMERATOR
    return -(-product // HEADROOM_DENOMINATOR)


def load_limits(path: Path) -> dict[str, int | None]:
    """Return the measured size of each operating system from the limits file.

    An operating system without a record, and a record of null, both mean that
    the size was not measured yet. An unknown operating system and an unknown
    field raise ValueError, so that a typo cannot disable the limit.
    """
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"{path} must hold a JSON object.")
    measured: dict[str, int | None] = {}
    for key, value in document.items():
        if key == NOTE_KEY:
            continue
        if key not in OPERATING_SYSTEMS:
            raise ValueError(f"{path} names the unknown operating system {key!r}.")
        measured[key] = _read_measurement(path, key, value)
    return measured


def _read_measurement(path: Path, key: str, value: object) -> int | None:
    """Return the measured size of one record, or None when it was not measured."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ValueError(f"{path} must hold an object or null for {key!r}.")
    unknown = sorted(set(value) - RECORD_FIELDS)
    if unknown:
        raise ValueError(f"{path} holds the unknown field {unknown[0]!r} for {key!r}.")
    _check_measurement_date(path, key, value.get("measured_at"))
    size = value.get("measured_bytes")
    if size is None:
        return None
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        raise ValueError(f"{path} must hold a byte count of zero or more for {key!r}.")
    return size


def _check_measurement_date(path: Path, key: str, measured_at: object) -> None:
    """Raise ValueError when the measurement date is not a real calendar date."""
    if measured_at is None:
        return
    if not isinstance(measured_at, str):
        raise ValueError(f"{path} must hold the measurement date for {key!r} as a string.")
    try:
        date.fromisoformat(measured_at)
    except ValueError as error:
        raise ValueError(
            f"{path} must hold the measurement date for {key!r} as YYYY-MM-DD."
        ) from error


def build_report(
    operating_system: str,
    bundle: Path,
    folders_root: Path,
    packages: Sequence[PackageSize],
    measured_bytes: int | None,
) -> SizeReport:
    """Return the size report of one bundle and the packages built from it."""
    bundle_bytes = directory_bytes(bundle)
    limit_bytes = None if measured_bytes is None else derive_limit(measured_bytes)
    headroom_bytes = None if limit_bytes is None else limit_bytes - bundle_bytes
    over_limit = limit_bytes is not None and bundle_bytes > limit_bytes
    return SizeReport(
        operating_system=operating_system,
        bundle_path=bundle.as_posix(),
        bundle_bytes=bundle_bytes,
        measured_bytes=measured_bytes,
        limit_bytes=limit_bytes,
        headroom_bytes=headroom_bytes,
        over_limit=over_limit,
        folders=largest_folders(folders_root),
        packages=tuple(packages),
    )


def report_as_json(report: SizeReport) -> dict[str, object]:
    """Return the JSON document of one report."""
    return {
        "operating_system": report.operating_system,
        "bundle_path": report.bundle_path,
        "bundle_bytes": report.bundle_bytes,
        "measured_bytes": report.measured_bytes,
        "limit_bytes": report.limit_bytes,
        "headroom_bytes": report.headroom_bytes,
        "over_limit": report.over_limit,
        "limit_recorded": report.measured_bytes is not None,
        "largest_folders": [
            {"name": folder.name, "bytes": folder.bytes} for folder in report.folders
        ],
        "packages": [
            {"name": package.name, "path": package.path, "bytes": package.bytes}
            for package in report.packages
        ],
    }


def format_size(value: int) -> str:
    """Return a byte count as a mebibyte value and the exact byte count."""
    return f"{value / BYTES_PER_MEBIBYTE:.1f} MiB ({value:,} bytes)"


def render_markdown(report: SizeReport) -> str:
    """Return the Markdown report of one bundle."""
    lines = [f"## Bundle size: {report.operating_system}", ""]
    lines.append(f"- Unpacked bundle: {format_size(report.bundle_bytes)}")
    if report.measured_bytes is None:
        lines.append("- Measured size on main: not recorded yet")
        lines.append("- Limit: not recorded yet")
        result = "no limit recorded"
    else:
        lines.append(f"- Measured size on main: {report.measured_bytes:,} bytes")
        lines.append(f"- Limit: {format_size(report.limit_bytes or 0)}")
        if report.over_limit:
            lines.append(f"- Excess: {format_size(-(report.headroom_bytes or 0))}")
        else:
            lines.append(f"- Headroom: {format_size(report.headroom_bytes or 0)}")
        result = "over the limit" if report.over_limit else "within the limit"
    lines.append(f"- Result: {result}")
    lines.append("")
    lines.append("### Largest folders")
    lines.append("")
    lines.append("| Folder | Size |")
    lines.append("| --- | --- |")
    if report.folders:
        for folder in report.folders:
            lines.append(f"| `{folder.name}` | {format_size(folder.bytes)} |")
    else:
        lines.append(f"| none | {format_size(0)} |")
    lines.append("")
    lines.append("### Packages")
    lines.append("")
    lines.append("| Package | Size |")
    lines.append("| --- | --- |")
    if report.packages:
        for package in report.packages:
            lines.append(f"| `{package.name}` | {format_size(package.bytes)} |")
    else:
        lines.append(f"| none | {format_size(0)} |")
    return "\n".join(lines) + "\n"


def write_reports(report_dir: Path, report: SizeReport, markdown: str) -> None:
    """Write the Markdown and the JSON report of one bundle."""
    report_dir.mkdir(parents=True, exist_ok=True)
    stem = f"bundle-size-{report.operating_system}"
    (report_dir / f"{stem}.md").write_text(markdown, encoding="utf-8", newline="\n")
    document = json.dumps(report_as_json(report), indent=2, sort_keys=True)
    (report_dir / f"{stem}.json").write_text(document + "\n", encoding="utf-8", newline="\n")


def read_package(value: str) -> PackageSize:
    """Return one package record with the size of the file on disk."""
    name, separator, path = value.partition("=")
    if not separator or not name or not path:
        raise ValueError(f"Package {value!r} must use the NAME=PATH form.")
    package_path = Path(path)
    if not package_path.is_file():
        raise ValueError(f"Package {name!r} has no file at {path}.")
    return PackageSize(name, package_path.as_posix(), package_path.stat().st_size)


def main(argv: Sequence[str] | None = None) -> int:
    """Run the size check from the command line and return 0 on a pass, 1 on a failure."""
    parser = argparse.ArgumentParser(description="Report the bundle and package sizes.")
    commands = parser.add_subparsers(dest="command", required=True)
    check = commands.add_parser("check", help="Report the sizes and fail past the limit.")
    check.add_argument("--os", dest="operating_system", choices=OPERATING_SYSTEMS, required=True)
    check.add_argument("--bundle", type=Path, required=True, help="The built bundle folder.")
    check.add_argument(
        "--folders-root",
        type=Path,
        help="The folder that holds the bundle parts. Defaults to the bundle folder.",
    )
    check.add_argument(
        "--package",
        dest="packages",
        action="append",
        default=[],
        help="One built package as NAME=PATH. Repeat for every package.",
    )
    check.add_argument("--limits", type=Path, default=LIMITS_FILE, help="The limits file.")
    check.add_argument("--report-dir", type=Path, help="The folder that receives the reports.")
    args = parser.parse_args(argv)

    bundle = args.bundle
    if not bundle.is_dir():
        parser.error(f"{bundle} is not a folder. Pass the built bundle folder.")
    folders_root = args.folders_root or bundle
    if not folders_root.is_dir():
        parser.error(
            f"{folders_root} is not a folder. Pass the folder that holds the bundle parts."
        )
    try:
        packages = tuple(read_package(value) for value in args.packages)
    except ValueError as error:
        parser.error(str(error))
    try:
        measured_bytes = load_limits(args.limits).get(args.operating_system)
    except (OSError, ValueError) as error:
        print(f"bundle_size: {error}", file=sys.stderr)
        return INPUT_FAILED

    report = build_report(args.operating_system, bundle, folders_root, packages, measured_bytes)
    markdown = render_markdown(report)
    if args.report_dir:
        write_reports(args.report_dir, report, markdown)
    print(markdown, end="")
    if report.over_limit:
        print(
            f"bundle_size: the {report.operating_system} bundle is {report.bundle_bytes:,} bytes,"
            f" past the limit of {report.limit_bytes:,} bytes.",
            file=sys.stderr,
        )
        print(
            f"bundle_size: the limit is the measured size of {report.measured_bytes:,} bytes plus"
            " 50 percent. Reduce the bundle, or raise the limit for"
            f" {report.operating_system} in {LIMITS_FILE.name} in a deliberate change.",
            file=sys.stderr,
        )
        return CHECK_FAILED
    if report.measured_bytes is None:
        print(
            f"bundle_size: no measured size is recorded for {report.operating_system}."
            f" The check reported the sizes without a limit comparison.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
