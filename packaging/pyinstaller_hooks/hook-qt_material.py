"""PyInstaller hook for the qt-material theme package.

The app reads the theme files, the style sheet template, and the icon sources at
run time, so the packaged app keeps them next to the package. The Roboto fonts
and the dock theme form stay out of the bundle. The app never opens the dock
theme, and the fonts carry a license whose text the package does not ship.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

PACKAGE_NAME: str = "qt_material"
THEME_FILES: tuple[str, ...] = ("dark_teal.xml", "light_teal.xml")


def package_dir() -> Path:
    """Return the folder of the qt_material package."""
    spec = importlib.util.find_spec(PACKAGE_NAME)
    if spec is None or spec.origin is None:
        raise SystemExit(f"The {PACKAGE_NAME} package is missing. Install the pinned version.")
    return Path(spec.origin).parent


def required_file(path: Path) -> str:
    """Return the path as a string, or stop the build when the file is missing."""
    if not path.is_file():
        raise SystemExit(f"The {PACKAGE_NAME} package is missing {path.name}.")
    return str(path)


def collect_theme_data() -> list[tuple[str, str]]:
    """Return the data entries for the themes, the template, and the icon sources."""
    root = package_dir()
    entries = [
        (required_file(root / "themes" / name), "qt_material/themes") for name in THEME_FILES
    ]
    template = next(root.glob("material*.template"), None)
    if template is None:
        raise SystemExit(f"The {PACKAGE_NAME} package is missing its style sheet template.")
    entries.append((required_file(template), "qt_material"))
    icon_sources = sorted((root / "resources" / "source").glob("*.svg"))
    entries.extend((str(icon), "qt_material/resources/source") for icon in icon_sources)
    return entries


datas = collect_theme_data()
