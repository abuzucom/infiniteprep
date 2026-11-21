"""Behavioral tests for the packaged theme payload and its dependency pins."""

from __future__ import annotations

import importlib.util
import tomllib
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC_PATH = REPO_ROOT / "infiniteprep.spec"
HOOK_PATH = REPO_ROOT / "packaging" / "pyinstaller_hooks" / "hook-qt_material.py"
ALLOWLIST_DIR = REPO_ROOT / "packaging" / "bundle-allowlist"
APPROVED_THEME_PACKAGES = {"Jinja2": "3.1.6", "MarkupSafe": "3.0.4", "qt-material": "2.17"}
THEME_FILES = ("dark_teal.xml", "light_teal.xml")


def load_hook_module() -> Any:
    """Return the PyInstaller hook module for the theme package."""
    module_spec = importlib.util.spec_from_file_location("hook_qt_material", HOOK_PATH)
    assert module_spec is not None and module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def load_bundle_filters() -> Any:
    """Return the bundle filter module that the spec loads by path."""
    filters_path = REPO_ROOT / "packaging" / "bundle_filters.py"
    module_spec = importlib.util.spec_from_file_location("bundle_filters", filters_path)
    assert module_spec is not None and module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    return module


def read_allowlist(operating_system: str) -> list[str]:
    """Return the entries of one bundle allowlist."""
    text = (ALLOWLIST_DIR / f"{operating_system}.txt").read_text(encoding="utf-8")
    return [line.strip() for line in text.splitlines() if line.strip()]


def test_the_spec_collects_the_theme_package() -> None:
    """The spec names the theme package and points PyInstaller at the hook folder."""
    spec_text = SPEC_PATH.read_text(encoding="utf-8")

    assert '"qt_material"' in spec_text
    assert 'hookspath=[str(project_root / "packaging" / "pyinstaller_hooks")]' in spec_text


def test_the_hook_collects_the_theme_files_the_template_and_the_icons() -> None:
    """The hook keeps the theme files, the style sheet template, and the icon sources."""
    hook = load_hook_module()
    destinations = {destination for _source, destination in hook.datas}

    assert "qt_material/themes" in destinations
    assert "qt_material" in destinations
    assert "qt_material/resources/source" in destinations

    sources = [Path(source).name for source, _destination in hook.datas]
    for theme_file in THEME_FILES:
        assert theme_file in sources
    assert any(name.endswith(".template") for name in sources)


def test_the_hook_collects_the_roboto_fonts_and_their_license_text() -> None:
    """The hook keeps the Roboto fonts and the Apache license text beside them."""
    hook = load_hook_module()
    fonts = [
        (source, destination)
        for source, destination in hook.datas
        if destination == "qt_material/fonts/roboto"
    ]

    font_files = [Path(source).name for source, _destination in fonts]
    assert "Roboto-Regular.ttf" in font_files
    assert "Apache-2.0.txt" in font_files
    assert all(name.endswith(".ttf") or name == "Apache-2.0.txt" for name in font_files)


@pytest.mark.parametrize(
    ("operating_system", "expected"),
    [
        (
            "windows",
            [
                "_internal/PySide6/QtUiTools.pyd",
                "_internal/PySide6/Qt6UiTools.dll",
                "_internal/PySide6/Qt6OpenGLWidgets.dll",
                "_internal/markupsafe-*.dist-info/**",
                "_internal/markupsafe/_speedups.cp312-win_amd64.pyd",
            ],
        ),
        (
            "linux",
            [
                "_internal/PySide6/QtUiTools.abi3.so",
                "_internal/PySide6/Qt/lib/libQt6UiTools.so.6",
                "_internal/libQt6UiTools.so.6",
                "_internal/PySide6/Qt/lib/libQt6OpenGLWidgets.so.6",
                "_internal/libQt6OpenGLWidgets.so.6",
                "_internal/markupsafe-*.dist-info/**",
                "_internal/markupsafe/_speedups.cpython-312-x86_64-linux-gnu.so",
            ],
        ),
        (
            "macos",
            [
                "_internal/PySide6/QtUiTools.abi3.so",
                "_internal/PySide6/Qt/lib/QtUiTools.framework/QtUiTools",
                "_internal/QtUiTools",
                "_internal/PySide6/Qt/lib/QtOpenGLWidgets.framework/QtOpenGLWidgets",
                "_internal/QtOpenGLWidgets",
                "_internal/markupsafe-*.dist-info/**",
                "_internal/markupsafe/_speedups.cpython-312-darwin.so",
            ],
        ),
    ],
)
def test_each_bundle_allowlist_names_the_theme_payload(
    operating_system: str, expected: list[str]
) -> None:
    """Every bundle allowlist names the theme files and the libraries they need."""
    entries = read_allowlist(operating_system)

    for entry in expected:
        assert entry in entries
    for theme_file in THEME_FILES:
        assert f"_internal/qt_material/themes/{theme_file}" in entries
    assert "_internal/qt_material/material.qss.template" in entries
    assert "_internal/qt_material/resources/source/*.svg" in entries
    assert "_internal/qt_material/fonts/roboto/*.ttf" in entries
    assert "_internal/qt_material/fonts/roboto/Apache-2.0.txt" in entries


def test_the_theme_packages_stay_pinned_to_the_approved_versions() -> None:
    """The theme engine and its template dependencies keep their approved pins."""
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    pinned = (name.partition("==") for name in pyproject["project"]["dependencies"])
    declared = {package: version for package, separator, version in pinned if separator}
    lock_text = (REPO_ROOT / "requirements-lock.txt").read_text(encoding="utf-8")

    for package, version in APPROVED_THEME_PACKAGES.items():
        assert declared.get(package) == version
        assert f"{package}=={version}" in lock_text


def test_the_unused_opengl_bindings_leave_every_bundle() -> None:
    """The OpenGL bindings stay out of every bundle, and their libraries stay in."""
    prefixes = load_bundle_filters().ORPHAN_QT_FILE_PREFIXES

    assert "qtopengl.pyd" in prefixes["win32"]
    assert "qtopenglwidgets.pyd" in prefixes["win32"]
    assert "qtopengl.abi3.so" in prefixes["linux"]
    assert "qtopenglwidgets.abi3.so" in prefixes["linux"]
    assert "qtopengl.abi3.so" in prefixes["darwin"]
    assert "qtopenglwidgets.abi3.so" in prefixes["darwin"]

    for operating_system in ("windows", "linux", "macos"):
        entries = read_allowlist(operating_system)
        assert not any("opengl.pyd" in entry for entry in entries)
        assert not any("opengl.abi3.so" in entry for entry in entries)


def test_the_notice_names_the_bundled_theme_package_versions() -> None:
    """NOTICE names the pinned versions of the packages the theme bundle ships."""
    notice = (REPO_ROOT / "NOTICE").read_text(encoding="utf-8")
    lock_text = (REPO_ROOT / "requirements-lock.txt").read_text(encoding="utf-8")

    for package in ("qt-material", "Jinja2", "MarkupSafe"):
        version = next(
            line.split("==")[1].rstrip("\\").strip()
            for line in lock_text.splitlines()
            if line.startswith(f"{package}==")
        )
        assert f"{package} {version}" in notice


def test_the_hook_stops_the_build_when_the_fonts_are_missing(tmp_path: Path) -> None:
    """A theme package without the Roboto fonts stops the build."""
    hook = load_hook_module()

    with pytest.raises(SystemExit):
        hook.collect_fonts(tmp_path)
