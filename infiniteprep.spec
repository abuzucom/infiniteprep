# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for packaging InfinitePrep as a standalone desktop app."""

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

from PyInstaller.depend import bindepend

project_root = Path.cwd()
src_dir = project_root / "src"


def load_bundle_filters() -> ModuleType:
    """Load packaging/bundle_filters.py from its exact path.

    sys.path stays unchanged, so no other file named bundle_filters can take its place.
    """
    filters_path = project_root / "packaging" / "bundle_filters.py"
    module_spec = importlib.util.spec_from_file_location("bundle_filters", filters_path)
    if module_spec is None or module_spec.loader is None:
        raise SystemExit(f"Cannot load {filters_path}. Restore packaging/bundle_filters.py.")
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


bundle_filters = load_bundle_filters()
drop_entries = bundle_filters.drop_entries
find_loaded_orphans = bundle_filters.find_loaded_orphans
is_gpl3_component = bundle_filters.is_gpl3_component
is_orphaned_qt_component = bundle_filters.is_orphaned_qt_component
is_unused_linux_plugin = bundle_filters.is_unused_linux_plugin
prune_unneeded_libraries = bundle_filters.prune_unneeded_libraries

# macOS bundle settings (D83a3c). The identifier stays fixed once released (D297).
MACOS_BUNDLE_IDENTIFIER = "dev.infiniteprep.app"
MACOS_MINIMUM_VERSION = "14.4"


def read_app_version(init_text: str) -> str:
    """Return the __version__ value of the package init text.

    A missing version line stops the build with a message instead of an AttributeError.
    """
    match = re.search(r'^__version__ = "([^"]+)"$', init_text, re.MULTILINE)
    if match is None:
        raise SystemExit(
            "src/infiniteprep/__init__.py has no __version__ line. Restore the version line."
        )
    return match.group(1)


APP_VERSION = read_app_version(
    (src_dir / "infiniteprep" / "__init__.py").read_text(encoding="utf-8")
)
# The app never imports setuptools or pkg_resources (D313). Excluding them also keeps
# PyInstaller from adding the pkg_resources runtime hook.
UNUSED_PYTHON_PACKAGES = ["setuptools", "pkg_resources"]
# The logo serves as the window icon and the packaged app icon (D393). PyInstaller
# converts the PNG to .ico and .icns with Pillow.
APP_ICON_SOURCE = src_dir / "infiniteprep" / "ui" / "app-icon.png"
THEMES_SOURCE = src_dir / "infiniteprep" / "ui" / "themes"
ICONS_SOURCE = src_dir / "infiniteprep" / "ui" / "icons"
# Pillow serves only the build-time icon conversion, and the app never imports it (D393).
# The exclusion keeps Pillow out of every bundle even if another optional import names it.
BUILD_ONLY_PACKAGES = ["PIL"]
# scipy imports pytest inside PytestTester. The pytest hook then collects all of pytest
# and pygments. The app never runs the scipy test runner and never imports either package.
TEST_ONLY_PACKAGES = ["pytest", "_pytest", "pygments"]

a = Analysis(
    [str(src_dir / "infiniteprep" / "main.py")],
    pathex=[str(src_dir)],
    binaries=[],
    datas=[
        (str(src_dir / "infiniteprep" / "db" / "schema.sql"), "infiniteprep/db"),
        (str(APP_ICON_SOURCE), "infiniteprep/ui"),
        # The theme files that ui/theme.py hands to qt-material by path.
        (str(THEMES_SOURCE / "infiniteprep_dark.xml"), "infiniteprep/ui/themes"),
        (str(THEMES_SOURCE / "infiniteprep_light.xml"), "infiniteprep/ui/themes"),
        # The icons and the license file of each icon set.
        (str(ICONS_SOURCE / "tabler" / "*.svg"), "infiniteprep/ui/icons/tabler"),
        (str(ICONS_SOURCE / "tabler" / "LICENSE"), "infiniteprep/ui/icons/tabler"),
        (str(ICONS_SOURCE / "lucide" / "*.svg"), "infiniteprep/ui/icons/lucide"),
        (str(ICONS_SOURCE / "lucide" / "LICENSE"), "infiniteprep/ui/icons/lucide"),
    ],
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PySide6.QtMultimedia",
        "scipy.signal",
        "soundfile",
        "keyring",
        # The theme package is imported by name at run time, so PyInstaller needs the
        # hidden import. The hook in packaging/pyinstaller_hooks collects its data files.
        "qt_material",
    ],
    hookspath=[str(project_root / "packaging" / "pyinstaller_hooks")],
    hooksconfig={},
    runtime_hooks=[],
    # Python's readline module links GNU Readline (GPL-3) on Linux (D302). The exclusion
    # applies on every OS, because no InfinitePrep code uses the module.
    excludes=["readline"] + UNUSED_PYTHON_PACKAGES + BUILD_ONLY_PACKAGES + TEST_ONLY_PACKAGES,
    noarchive=False,
)


def report_dropped(reason: str, dropped: list[str]) -> None:
    """Print each removed destination, so the build log names every change."""
    for destination in sorted(dropped):
        print(f"infiniteprep.spec: dropped {destination} ({reason})")


# Qt Virtual Keyboard and GNU Readline are GPL-3-only and unused (D302).
a.binaries, dropped = drop_entries(a.binaries, is_gpl3_component)
report_dropped("GPL-3", dropped)
a.datas, dropped = drop_entries(a.datas, is_gpl3_component)
report_dropped("GPL-3", dropped)


def is_orphan_on_this_platform(destination: str) -> bool:
    """Return True for a Qt file that no kept binary loads on the build platform."""
    return is_orphaned_qt_component(destination, sys.platform)


# The orphan check and the Linux prune both read the links of the same binaries.
# The cache lets each binary's links come from one bindepend read per build.
LINKED_NAMES_CACHE: dict[str, frozenset[str]] = {}


def linked_library_names(source: str) -> frozenset[str]:
    """Return the names of the libraries a collected binary links against."""
    cached = LINKED_NAMES_CACHE.get(source)
    if cached is None:
        cached = frozenset(name for name, _path in bindepend.get_imports(source))
        LINKED_NAMES_CACHE[source] = cached
    return cached


# Qt files that nothing loads go before the Linux prune, so it sees the final set (D313).
a.binaries, orphan_binaries = drop_entries(a.binaries, is_orphan_on_this_platform)
report_dropped("no Qt file loads it", orphan_binaries)
a.datas, dropped = drop_entries(a.datas, is_orphan_on_this_platform)
report_dropped("no Qt file loads it", dropped)

if sys.platform.startswith("linux"):
    # Unused Linux Qt plugins go first, then the libraries only they needed (D303, D304).
    a.binaries, dropped = drop_entries(a.binaries, is_unused_linux_plugin)
    report_dropped("unused Linux Qt plugin", dropped)

# The orphan lists are fixed names. A kept binary that still links one stops the build.
# The check runs after the Linux plugin trim, because the dropped EGLFS plugins link
# the EGLFS libraries.
loaded_orphans = find_loaded_orphans(a.binaries, orphan_binaries, linked_library_names)
if loaded_orphans:
    for conflict in loaded_orphans:
        print(f"infiniteprep.spec: {conflict}")
    raise SystemExit(
        "A kept binary links a dropped Qt file. Remove that file from"
        " ORPHAN_QT_FILE_PREFIXES or MACOS_ORPHAN_FRAMEWORKS in packaging/bundle_filters.py."
    )

if sys.platform.startswith("linux"):
    a.binaries, dropped = prune_unneeded_libraries(a.binaries, linked_library_names)
    report_dropped("no kept binary links it", dropped)
    for destination, _source, _typecode in sorted(a.binaries):
        if "/" not in destination.replace("\\", "/") and destination.startswith("lib"):
            print(f"infiniteprep.spec: kept library {destination}")

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="InfinitePrep",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=str(APP_ICON_SOURCE),
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="InfinitePrep",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="InfinitePrep.app",
        icon=str(APP_ICON_SOURCE),
        bundle_identifier=MACOS_BUNDLE_IDENTIFIER,
        version=APP_VERSION,
        info_plist={
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "LSMinimumSystemVersion": MACOS_MINIMUM_VERSION,
            "NSHighResolutionCapable": True,
        },
    )
