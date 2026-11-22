"""Behavioral tests for the bundled icons, their notices, and the icon loader."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtGui import QColor

from infiniteprep.ui.icon_loader import (
    ICON_SETS,
    ICONS_DIR,
    LUCIDE_ICONS,
    TABLER_ICONS,
    IconSet,
    colored_svg,
    icon_file_path,
    render_icon_image,
    themed_icon,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
TABLER_ICON_COUNT = 21
LUCIDE_ICON_COUNT = 33
# SHA-256 digests of each file at Tabler Icons v3.50.0
# (commit 6904efd245391ad396df17654738f52f6da8c40e, icons/outline/<name>.svg) and
# Lucide 1.70.0 (commit b826f0c527e1498f5e46f41ca0277ccab144bf31, icons/<name>.svg).
PINNED_DIGESTS: dict[str, str] = {
    "tabler/adjustments.svg": "46ee742d7cdec46bacd05374a8322f6dae1ccca41b4e09e3c52456ef70af014d",
    "tabler/arrow-down.svg": "34efc2d5737ffa3f29f8056e6d861ea8218d8f40858539a959a9482811f6bd05",
    "tabler/arrow-up.svg": "c0a58436629c59996826b7980b0cbdfff51bfa289eefb2cceee494a964c5105e",
    "tabler/arrows-exchange.svg": (
        "67be2e2afc04e53c70a4b81f58b18c232edd5b6124eff81a285fcd513f857016"
    ),
    "tabler/circle-dashed-check.svg": (
        "0992feda8a53ab09ffc0ca89b0e764c5c1dd13914da12812309bc8c413eff19c"
    ),
    "tabler/file-music.svg": "aa0221d9651fedd31262a2ad31ea0fcd4360f90b6f78ed33dd7fd2fc9305359f",
    "tabler/fingerprint.svg": "cd1ff19081e56af42bd183a98eec4ba68d7a7affbfc37775b10604824489a96b",
    "tabler/history.svg": "5f8a5a975a8136a4755380c18db1fe533acf7dc4e34250ceae0272c5d2cb7312",
    "tabler/lock-open.svg": "f1b1ef8aa7cdaad103e0062298ba5bf26c698b9f8a5c142cddeff19ab20b271c",
    "tabler/lock.svg": "407856048124fdd63239f75a3640ca8f20a9acbc1f49cac9de0bcdb1cb42ec5f",
    "tabler/logout.svg": "fa2752dd6456bfb77486e42908c262ae1e05eb1e52693ff6c453ee39ddeee003",
    "tabler/metronome.svg": "ab2defa9358b2bed47d73531e0aded772b759a9c921b0306362b9aacfd9f6fd7",
    "tabler/music.svg": "afd2fe98e580e1d4e9bf45d88165894b0e683e9141f63c312585d778b73d02c5",
    "tabler/pin.svg": "f21a39f28e474725a175d6c132a1657ecc0b55c92d44ec8b272f0d06f94b4769",
    "tabler/refresh.svg": "9dd8ab8cac268489501cbb0a106d93cc3f367add493d20c47ec1e1d9804c760a",
    "tabler/shield-x.svg": "8177514d4e6ad942294b189edcccec6f4fc872689975398fe3b1d5ac0a1dc1f0",
    "tabler/tag-plus.svg": "c1af1c1aa6a5fb0fe240a2181548efb74c2a0fefe66e557b282f7b5341af302f",
    "tabler/usb.svg": "530e5bca19f9050586cf6d504cdc0fc4c2495ac1d2c8fa303b1d141fd8d2db71",
    "tabler/vinyl.svg": "5b468a8f22205aa0d196501e5842f2d0618f6a0da951fdab8567b24cee2df8da",
    "tabler/zoom-in.svg": "b361e358c8d0035b2b822b2b497f83b60a00c31a6300b502838292c706070c4e",
    "tabler/zoom-question.svg": "942be19b7890e16b58be50f6bebf27964f90b042b42d2fc575ab63c4ed80152e",
    "lucide/audio-waveform.svg": (
        "d4b6f62b0d72b7ad223f9abeb51e64875e4533123d7898d07acfa74154eb14b3"
    ),
    "lucide/ban.svg": "2eba67f86d70bbd5d22e4211d44d7dda86c3397410f714765a12c3cd3ca0ebe3",
    "lucide/check-check.svg": "2b08c34edf3177ef80d523ad4ec3ebbd9efb92c581a48b6765044464e7e373f9",
    "lucide/check.svg": "7f33acc9a77a61659531044525fc008edebe215bf4dcf1c789c8674ad3277db0",
    "lucide/circle-check.svg": "9711e045a5999f8a3b0782eb88e09b04e0559bc7b379dcc0a4ae645223e10345",
    "lucide/circle-dashed.svg": "9b964be491a821be8ffd714baf3b94ca2d70af789bddcfc1bda6bde250d89187",
    "lucide/circle-off.svg": "0d79af6d7b882eeb2d84c4fad7c965a4d20a043f7d2eeb42b16cd74ef0381c54",
    "lucide/circle-play.svg": "10b5deb7c8fa5587897a1eb3d7ffc3c6c2f7b1b7f9a5b5080050aa529e9eb9ae",
    "lucide/circle-x.svg": "bcd8788901e6f29e1b231a81ba5e707d083d06cb4848a28f29407fab4f8e0b64",
    "lucide/clock-fading.svg": "97dc20d9d11a0801b17994d8b941b5b2039e55555ec0cbd471ab54667d841c28",
    "lucide/cloud-download.svg": (
        "c50569ad4c39d6ecf911debd62d37e805e610f71d696495d353be4cbf6bd8b1f"
    ),
    "lucide/copy.svg": "ea80e566c7a12628a447cb53179b19aea4f60b9143e2becd0d0f20bf260e5718",
    "lucide/database-backup.svg": (
        "7c3390be2ace227565d806d568579e1a5a54847be026a1d25117aea2df51971a"
    ),
    "lucide/database.svg": "471eb14bc8d144896a82dfc4aa9daf350f769f0896f4bef33e50aabdbb51130d",
    "lucide/folder-input.svg": "422afd22efd4b7474ee50fcdc42c37cdee1c170a81a2eb339df7ecd0d0dc3578",
    "lucide/folder.svg": "f0d92b94b797a8ab7d4c4ae33c3236c47f64068351b4e14bdb5014ee42898a39",
    "lucide/hard-drive.svg": "324363c40f54e4ca843de0ad1d023b063b70a26a83bb2e0a9e9f4aad9af37378",
    "lucide/loader.svg": "54b6eeae749a70379df86e6360670a838d0f98f00d5a887d50bd76ca62cbf0d4",
    "lucide/microscope.svg": "72fab65c4fdd0a7d784485dc7aaa8bf8c4fba9a4dc9ae38a6538e58bdd8b5f42",
    "lucide/notebook-pen.svg": "56705c35e839977bd6f133d7ca4e9c93a6eea563c89e4be125efd0a32e1fa041",
    "lucide/pause.svg": "f122ec4ea7f5693a0b1baafa9c708b53980dc87b4aacb297c6e6f71c1a4c115c",
    "lucide/play.svg": "d7c34786135922a92b6896f6c2384ceeb0346afbf6041dc79982011411409833",
    "lucide/replace.svg": "abaaab3d3f74242f09968e4bbbf6450281c0dea23989d9e13cfc0aba22b6fca7",
    "lucide/save.svg": "ee9d56a7fec4b20dd6689546d41f68219ea8cbd67f99bd17a1bdaff5be6edb53",
    "lucide/scan-eye.svg": "343741486dfe4188a80628e3b8ae7a71f6ac63e0de5fbf474674b053c8f80721",
    "lucide/settings.svg": "0ae27fd0f81999229e3127ac96c5b32edfea448e291d509e76212b917551d66b",
    "lucide/square-arrow-out-up-right.svg": (
        "7973e8a9d4a70bf090ee9fe2c52f53614aa58d71f62bf7f6eca39ba3b62fd2c1"
    ),
    "lucide/square.svg": "bd979354f0ab184b95cecf03eedefe40c2dc65830ac6d7e60017b2b25a354acb",
    "lucide/tags.svg": "61ffd6d11beb2073bfed08a318d2804492545e11356995c7fc9bc6d20ca1873a",
    "lucide/triangle-exclamation-point.svg": (
        "4866f38b8560d410f21e3226413e0b77997b6dfbb6931fadfe0a0d5aef9ffeb4"
    ),
    "lucide/turntable.svg": "b86832c1e14f3ff8598938e7e343de00e766f9456a2fe9ff572c13cdc6877653",
    "lucide/undo-2.svg": "efad2edbbd2be6038cc23fd64df2d012fad5cc6460dfbf227cfa3d990e9e897f",
    "lucide/x.svg": "4a9cdab38fbb96162e7dace28e33f4ca0e49d8963a6162abc3d4691b7d675117",
}
ICON_COLOR = "#38bdf8"


def test_icon_count() -> None:
    """21 Tabler and 33 Lucide icons ship, and each name belongs to one set only."""
    assert len(TABLER_ICONS) == TABLER_ICON_COUNT
    assert len(LUCIDE_ICONS) == LUCIDE_ICON_COUNT
    assert len(ICON_SETS) == TABLER_ICON_COUNT + LUCIDE_ICON_COUNT
    expected = {f"{ICON_SETS[name].value}/{name}.svg" for name in ICON_SETS}
    assert expected == set(PINNED_DIGESTS)


@pytest.mark.parametrize("relative_path", sorted(PINNED_DIGESTS))
def test_icon_digests(relative_path: str) -> None:
    """Every icon file holds the bytes of its file at the pinned upstream commit."""
    digest = hashlib.sha256((ICONS_DIR / relative_path).read_bytes()).hexdigest()
    assert digest == PINNED_DIGESTS[relative_path]


def test_no_extra_svgs() -> None:
    """The icon folders hold the listed SVG files and nothing else of that type."""
    shipped = {
        f"{icon_set.value}/{path.name}"
        for icon_set in IconSet
        for path in (ICONS_DIR / icon_set.value).glob("*.svg")
    }
    assert shipped == set(PINNED_DIGESTS)


@pytest.mark.parametrize("name", sorted(ICON_SETS))
def test_every_icon_renders(name: str, qtbot: Any) -> None:
    """Each icon renders at the requested size, and the result is not empty."""
    image = render_icon_image(name, ICON_COLOR, 20)

    assert image.width() == 20
    assert image.height() == 20
    assert any(image.pixelColor(x, y).alpha() > 0 for x in range(image.width()) for y in range(20))


def test_render_color(qtbot: Any) -> None:
    """The opaque pixels of a render carry the requested color."""
    image = render_icon_image("square", ICON_COLOR, 48)
    target = QColor(ICON_COLOR)

    opaque = [
        image.pixelColor(x, y)
        for x in range(image.width())
        for y in range(image.height())
        if image.pixelColor(x, y).alpha() == 255
    ]
    assert opaque
    assert all(
        (pixel.red(), pixel.green(), pixel.blue()) == (target.red(), target.green(), target.blue())
        for pixel in opaque
    )


def test_render_pixel_ratio(qtbot: Any) -> None:
    """A 2x render holds twice the pixels and reports the same logical size."""
    image = render_icon_image("folder", ICON_COLOR, 16, device_pixel_ratio=2.0)

    assert image.width() == 32
    assert image.devicePixelRatio() == 2.0


def test_themed_icon_sizes(qtbot: Any) -> None:
    """A QIcon from the loader offers a pixmap at each size it was built for."""
    icon = themed_icon("play", ICON_COLOR, sizes=(16, 24))

    assert not icon.isNull()
    assert {size.width() for size in icon.availableSizes()} == {16, 24}


def test_color_normalized() -> None:
    """The loader writes #rrggbb in place of currentColor and rejects other text."""
    svg = colored_svg("x", "#38BDF8")

    assert b"currentColor" not in svg
    assert b"#38bdf8" in svg
    with pytest.raises(ValueError, match="not a color"):
        colored_svg("x", '"/><script')


def test_bad_name_or_size(qtbot: Any) -> None:
    """An unknown icon name and a size below one logical pixel raise ValueError."""
    with pytest.raises(ValueError, match="No bundled icon"):
        icon_file_path("sparkles")
    with pytest.raises(ValueError, match="start at 1"):
        render_icon_image("x", ICON_COLOR, 0)


@pytest.mark.parametrize(
    ("icon_set", "copyright_line"),
    [
        (IconSet.TABLER, "Copyright (c) 2020-2026 Pawe"),
        (IconSet.LUCIDE, "Copyright (c) 2026 Lucide Icons and Contributors"),
    ],
)
def test_icon_licenses(icon_set: IconSet, copyright_line: str) -> None:
    """Each icon folder carries its set's license text."""
    license_text = (ICONS_DIR / icon_set.value / "LICENSE").read_text(encoding="utf-8")
    assert copyright_line in license_text


def test_feather_icons_listed() -> None:
    """The Lucide license lists every bundled Feather-derived icon under its Feather name."""
    license_text = (ICONS_DIR / "lucide" / "LICENSE").read_text(encoding="utf-8")
    feather_names = ("check", "database", "loader", "square", "x", "x-circle", "alert-triangle")

    assert "Copyright (c) 2013-present Cole Bemis" in license_text
    feather_list = license_text.split("derived from the Feather project:")[1].split("The MIT")[0]
    listed = {name.strip() for name in feather_list.split(",")}
    assert set(feather_names) <= listed


def test_notice_entries() -> None:
    """NOTICE carries the Tabler, Lucide, and Feather entries."""
    notice = (REPO_ROOT / "NOTICE").read_text(encoding="utf-8")

    assert "Tabler Icons v3.50.0 (21 icons): MIT." in notice
    assert "Copyright (c) 2020-2026 Pawel Kuna." in notice
    assert "Lucide 1.70.0 (33 icons): ISC." in notice
    assert "Copyright (c) 2026 Lucide Icons and Contributors." in notice
    assert "Copyright (c) 2013-present Cole Bemis." in notice


def test_bundles_ship_icons() -> None:
    """The spec collects both icon folders, and every allowlist names them."""
    spec_text = (REPO_ROOT / "infiniteprep.spec").read_text(encoding="utf-8")
    for icon_set in IconSet:
        assert f'ICONS_SOURCE / "{icon_set.value}" / "*.svg"' in spec_text
        assert f'ICONS_SOURCE / "{icon_set.value}" / "LICENSE"' in spec_text
        for operating_system in ("linux", "macos", "windows"):
            allowlist = REPO_ROOT / "packaging" / "bundle-allowlist" / f"{operating_system}.txt"
            entries = allowlist.read_text(encoding="utf-8").splitlines()
            assert f"_internal/infiniteprep/ui/icons/{icon_set.value}/*.svg" in entries
            assert f"_internal/infiniteprep/ui/icons/{icon_set.value}/LICENSE" in entries
