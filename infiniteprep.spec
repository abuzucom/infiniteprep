# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for packaging InfinitePrep as a standalone desktop app."""

import re
import sys
from pathlib import Path

project_root = Path.cwd()
src_dir = project_root / "src"
sys.path.insert(0, str(project_root / "packaging"))

# The packaging folder joins sys.path above, so this import must follow it.
from bundle_filters import (
    drop_entries,
    is_gpl3_component,
    is_unused_linux_plugin,
    prune_unneeded_libraries,
)

# macOS bundle settings (D83a3c). The identifier stays fixed once released (D297).
MACOS_BUNDLE_IDENTIFIER = "dev.infiniteprep.app"
MACOS_MINIMUM_VERSION = "14.4"
version_source = (src_dir / "infiniteprep" / "__init__.py").read_text(encoding="utf-8")
APP_VERSION = re.search(r'^__version__ = "([^"]+)"$', version_source, re.MULTILINE).group(1)

a = Analysis(
    [str(src_dir / "infiniteprep" / "main.py")],
    pathex=[str(src_dir)],
    binaries=[],
    datas=[
        (str(src_dir / "infiniteprep" / "db" / "schema.sql"), "infiniteprep/db"),
    ],
    hiddenimports=[
        "PySide6.QtCore",
        "PySide6.QtGui",
        "PySide6.QtWidgets",
        "PySide6.QtMultimedia",
        "scipy.signal",
        "soundfile",
        "keyring",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Python's readline module links GNU Readline (GPL-3) on Linux (D302).
    excludes=["readline"],
    noarchive=False,
)


def report_dropped(reason, dropped):
    """Print each removed destination, so the build log names every change."""
    for destination in sorted(dropped):
        print(f"infiniteprep.spec: dropped {destination} ({reason})")


# Qt Virtual Keyboard and GNU Readline are GPL-3-only and unused (D302).
a.binaries, dropped = drop_entries(a.binaries, is_gpl3_component)
report_dropped("GPL-3", dropped)
a.datas, dropped = drop_entries(a.datas, is_gpl3_component)
report_dropped("GPL-3", dropped)

if sys.platform.startswith("linux"):
    from PyInstaller.depend import bindepend

    # Unused Linux Qt plugins go first, then the libraries only they needed (D303, D304).
    a.binaries, dropped = drop_entries(a.binaries, is_unused_linux_plugin)
    report_dropped("unused Linux Qt plugin", dropped)

    def linked_library_names(source):
        """Return the names of the libraries a collected binary links against."""
        return {name for name, _path in bindepend.get_imports(source)}

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
        bundle_identifier=MACOS_BUNDLE_IDENTIFIER,
        version=APP_VERSION,
        info_plist={
            "CFBundleShortVersionString": APP_VERSION,
            "CFBundleVersion": APP_VERSION,
            "LSMinimumSystemVersion": MACOS_MINIMUM_VERSION,
            "NSHighResolutionCapable": True,
        },
    )
