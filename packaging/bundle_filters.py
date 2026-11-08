"""Filters for the PyInstaller collection lists (D302 to D304).

infiniteprep.spec calls these after Analysis. Each function takes PyInstaller TOC
entries, the (destination, source, typecode) tuples, and returns the entries to keep
plus the destinations it dropped, so the build log can name every removed file.

- Qt Virtual Keyboard is GPL-3-only and the app never uses it, so it leaves every
  bundle.
- On Linux, unused Qt plugins leave the bundle. Then any top-level library that no
  kept binary links against leaves too, until nothing more changes.
- Qt libraries that no kept binary loads leave each bundle (D313). Research run
  42468442490 mapped the links on Linux and macOS.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import PurePosixPath
from typing import Final

TocEntry = tuple[str, str, str]
NeededNames = Callable[[str], set[str]]

# Matched against the lowercase destination path of each collected file.
GPL3_QT_MARKERS: Final[tuple[str, ...]] = ("virtualkeyboard",)
# GNU Readline is GPL-3. Python's readline module is excluded in the spec, and this
# marker removes the library itself.
GPL3_LIBRARY_PREFIXES: Final[tuple[str, ...]] = ("libreadline",)
LINUX_UNUSED_PLUGIN_PATHS: Final[tuple[str, ...]] = (
    "plugins/platformthemes/libqgtk3.so",
    "plugins/networkinformation/",
    "plugins/egldeviceintegrations/",
    "plugins/generic/",
    "plugins/platforms/libqeglfs.so",
    "plugins/platforms/libqlinuxfb.so",
    "plugins/platforms/libqvnc.so",
    "plugins/platforms/libqvkkhrdisplay.so",
    "plugins/platforms/libqminimalegl.so",
    "plugins/wayland-decoration-client/libadwaita.so",
)
# The bootloader opens libpython itself, and glibc opens libgcc_s at run time, so no
# kept binary names them as a link dependency.
PROTECTED_LIBRARY_PREFIXES: Final[tuple[str, ...]] = ("libpython", "libgcc_s")
SYMLINK_TYPECODE: Final[str] = "SYMLINK"
SHARED_LIBRARY_MARKER: Final[str] = ".so"
# The orphan lists come from research run 42468442490 on the research-linuxdeploy
# workflow. It printed the links of every bundled binary with readelf -d on Linux and
# otool -L on macOS. To rebuild the lists after a PySide6 update, run that map again
# and keep only Qt files that no other bundled binary links. The build log prints a
# "dropped" line for each match. A missing line means a name stopped matching. A
# kept binary that links a dropped file stops the build (find_loaded_orphans).
#
# File-name prefixes of orphaned Qt files per platform (D313). Only the PDF image
# plugin loads QtPdf, and the app never opens PDF files as images. On Linux the
# EGLFS libraries only load each other, and the app never imports the PySide6
# QtConcurrent and QtDBus bindings. The Qt libraries under those bindings stay. On
# Windows, dropping QML and Quick is a trial: the smoke check fails when the
# FFmpeg playback plugin cannot load without them.
ORPHAN_QT_FILE_PREFIXES: Final[dict[str, tuple[str, ...]]] = {
    "linux": (
        "libqt6pdf.so",
        "libqpdf.so",
        "libqt6eglfsdeviceintegration.so",
        "libqt6eglfskmssupport.so",
        "qtconcurrent.abi3.so",
        "qtdbus.abi3.so",
    ),
    "darwin": ("libqpdf.dylib",),
    "win32": (
        "qt6pdf.dll",
        "qpdf.dll",
        "qt6qml.dll",
        "qt6qmlmeta.dll",
        "qt6qmlmodels.dll",
        "qt6qmlworkerscript.dll",
        "qt6quick.dll",
    ),
}
# macOS ships Qt as frameworks. Nothing loads QML and Quick there, because the macOS
# FFmpeg plugin does not link them.
MACOS_ORPHAN_FRAMEWORKS: Final[tuple[str, ...]] = (
    "qtpdf",
    "qtqml",
    "qtqmlmeta",
    "qtqmlmodels",
    "qtqmlworkerscript",
    "qtquick",
)
FRAMEWORK_SUFFIX: Final[str] = ".framework"
DARWIN_PLATFORM: Final[str] = "darwin"
LINUX_PLATFORM: Final[str] = "linux"


def _normalized(destination: str) -> str:
    """Return the destination with forward slashes, in lowercase."""
    return destination.replace("\\", "/").lower()


def is_gpl3_component(destination: str) -> bool:
    """Return True for a GPL-3 file that must never ship (D302)."""
    path = _normalized(destination)
    if any(marker in path for marker in GPL3_QT_MARKERS):
        return True
    return PurePosixPath(path).name.startswith(GPL3_LIBRARY_PREFIXES)


def is_unused_linux_plugin(destination: str) -> bool:
    """Return True for a Linux Qt plugin the app does not use (D303)."""
    path = _normalized(destination)
    return any(marker in path for marker in LINUX_UNUSED_PLUGIN_PATHS)


def _platform_key(platform: str) -> str:
    """Return the ORPHAN_QT_FILE_PREFIXES key for a sys.platform value."""
    if platform.startswith(LINUX_PLATFORM):
        return LINUX_PLATFORM
    return platform


def is_orphaned_qt_component(destination: str, platform: str) -> bool:
    """Return True for a Qt file that no kept binary loads on this platform (D313).

    platform takes a sys.platform value, such as "linux", "darwin", or "win32".
    """
    key = _platform_key(platform)
    path = PurePosixPath(_normalized(destination))
    if path.name.startswith(ORPHAN_QT_FILE_PREFIXES.get(key, ())):
        return True
    if key != DARWIN_PLATFORM:
        return False
    framework_parts = {f"{name}{FRAMEWORK_SUFFIX}" for name in MACOS_ORPHAN_FRAMEWORKS}
    if framework_parts.intersection(path.parts):
        return True
    # PyInstaller also places a top-level symlink named after each framework binary.
    return path.name in MACOS_ORPHAN_FRAMEWORKS


def drop_entries(
    entries: Iterable[TocEntry], should_drop: Callable[[str], bool]
) -> tuple[list[TocEntry], list[str]]:
    """Split entries into the kept ones and the destinations removed."""
    kept: list[TocEntry] = []
    dropped: list[str] = []
    for entry in entries:
        if should_drop(entry[0]):
            dropped.append(entry[0])
        else:
            kept.append(entry)
    return kept, dropped


def find_loaded_orphans(
    kept: Iterable[TocEntry], dropped_destinations: Iterable[str], needed_names: NeededNames
) -> list[str]:
    """Return one line per kept binary that still links a dropped orphan, sorted.

    The orphan lists are fixed names, so a later PySide6 or PyInstaller release could
    add a binary that needs one of them. The spec calls this on every OS and stops the
    build when the result is not empty. Names compare by file name, in lowercase,
    because Windows names ignore case and macOS links name a path to the binary.
    """
    dropped_names = {_file_name(destination) for destination in dropped_destinations}
    conflicts: list[str] = []
    for destination, source, typecode in kept:
        if typecode == SYMLINK_TYPECODE:
            continue
        for name in needed_names(source):
            if _file_name(name) in dropped_names:
                conflicts.append(f"{destination} loads {name}")
    return sorted(conflicts)


def _file_name(path: str) -> str:
    """Return the last component of a path with either separator, in lowercase."""
    return PurePosixPath(_normalized(path)).name


def is_prunable_library(destination: str) -> bool:
    """Return True for a top-level Linux .so library that only a link dependency brought in.

    The spec prunes on Linux only, so Windows .dll and macOS .dylib names never match.
    """
    path = PurePosixPath(destination.replace("\\", "/"))
    if len(path.parts) != 1 or not path.name.startswith("lib"):
        return False
    if SHARED_LIBRARY_MARKER not in path.name:
        return False
    return not path.name.startswith(PROTECTED_LIBRARY_PREFIXES)


def prune_unneeded_libraries(
    entries: Iterable[TocEntry], needed_names: NeededNames
) -> tuple[list[TocEntry], list[str]]:
    """Drop top-level libraries no kept binary links against, until nothing changes.

    needed_names returns the library names a binary links against, given its source
    path. Symlink entries hold a link target instead of a file, so they add no names.
    """
    kept = list(entries)
    dropped: list[str] = []
    cache: dict[str, set[str]] = {}
    while True:
        needed = _all_needed_names(kept, needed_names, cache)
        unused = {
            entry[0]
            for entry in kept
            if is_prunable_library(entry[0]) and PurePosixPath(entry[0]).name not in needed
        }
        if not unused:
            return kept, dropped
        dropped.extend(sorted(unused))
        kept = [entry for entry in kept if entry[0] not in unused]


def _all_needed_names(
    entries: list[TocEntry], needed_names: NeededNames, cache: dict[str, set[str]]
) -> set[str]:
    """Return every library name the entries link against."""
    names: set[str] = set()
    for _destination, source, typecode in entries:
        if typecode == SYMLINK_TYPECODE:
            continue
        if source not in cache:
            cache[source] = needed_names(source)
        names |= cache[source]
    return names
