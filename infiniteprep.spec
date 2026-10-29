# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller specification for packaging InfinitePrep as a standalone desktop app."""

from pathlib import Path

project_root = Path.cwd()
src_dir = project_root / "src"

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
        "mutagen",
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
