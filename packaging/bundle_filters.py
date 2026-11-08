"""Filters for the PyInstaller collection lists (D302 to D304).

infiniteprep.spec calls these after Analysis. Each function takes PyInstaller TOC
entries, the (destination, source, typecode) tuples, and returns the entries to keep
plus the destinations it dropped, so the build log can name every removed file.

- Qt Virtual Keyboard is GPL-3-only and the app never uses it, so it leaves every
  bundle.
- On Linux, unused Qt plugins leave the bundle. Then any top-level library that no
  kept binary links against leaves too, until nothing more changes.
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
