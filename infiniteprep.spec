# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for packaging InfinitePrep as a standalone desktop app."""

import re
import sys
from pathlib import Path

project_root = Path.cwd()
src_dir = project_root / "src"

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
    excludes=[],
    noarchive=False,
)
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
