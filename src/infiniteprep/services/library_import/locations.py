"""The known locations of the DJ libraries the app reads (D333).

Every entry names the file to read, the folders that hold it on each platform,
and the vendor page that states the location. The access date sits beside the
page. A location with no vendor page is marked observed and names the version
it was seen on.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

ACCESS_DATE: Final[str] = "2026-11-23"


class FormatId(StrEnum):
    """The source formats the locator knows."""

    TRAKTOR = "traktor"
    SERATO = "serato"
    MIXXX = "mixxx"
    REKORDBOX = "rekordbox"
    REKORDBOX_USB = "rekordbox_usb"
    # A file the DJ named by hand. The importer of the format decides how to read it.
    MANUAL = "manual"


class Platform(StrEnum):
    """The platforms a known location covers."""

    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"
    ANY = "any"


# The name each format carries in the user interface.
FORMAT_DISPLAY_NAMES: Final[dict[FormatId, str]] = {
    FormatId.TRAKTOR: "Traktor",
    FormatId.SERATO: "Serato",
    FormatId.MIXXX: "Mixxx",
    FormatId.REKORDBOX: "rekordbox",
    FormatId.REKORDBOX_USB: "rekordbox USB export",
    FormatId.MANUAL: "Chosen file",
}


def format_display_name(format_id: FormatId) -> str:
    """Return the name the user interface shows for one format."""
    return FORMAT_DISPLAY_NAMES.get(format_id, str(format_id))


@dataclass(frozen=True)
class KnownLocation:
    """One file to read, the folders that hold it, and the page that states it.

    file_name names the file the reader opens. folders names the folders that
    hold the file, relative to the user profile folder, or relative to the root
    of the device for a device location. citation_url names the vendor page.
    observed marks a location with no vendor page, and observed_version names
    the version it was seen on.

    versioned_parent marks a location whose folder holds one child folder per
    version, such as the Traktor Root Directory. Every other location names a
    folder that holds the file itself.

    on_device marks a location that sits on a mounted device rather than in
    the user profile folder. The framework looks for those on the drives the
    USB inspection layer reports.
    """

    format_id: FormatId
    file_name: str
    folders: tuple[tuple[Platform, tuple[str, ...]], ...]
    citation_url: str
    citation_note: str
    observed: bool = False
    observed_version: str = ""
    versioned_parent: bool = False
    on_device: bool = False


TRAKTOR_CITATION: Final[str] = (
    "https://support.native-instruments.com/support/solutions/articles/"
    "69000879391-how-to-transfer-traktor-collections-playlists-and-settings-"
    "to-another-computer"
)
TRAKTOR_INSTALL_CITATION: Final[str] = (
    "https://support.native-instruments.com/support/solutions/articles/"
    "69000879348-native-access-what-files-and-folders-does-traktor-pro-3-install-"
    "on-my-system-"
)
SERATO_FOLDER_CITATION: Final[str] = (
    "https://support.serato.com/hc/en-us/articles/204022904-What-is-in-the-Serato-folder"
)
SERATO_BACKUP_CITATION: Final[str] = (
    "https://support.serato.com/hc/en-us/articles/"
    "202305054-Backing-up-and-moving-your-library-to-a-new-computer"
)
MIXXX_SETTINGS_CITATION: Final[str] = (
    "https://mixxx.org/manual/2.5/en/chapters/appendix/settings_directory/"
)
REKORDBOX_FOLDER_CITATION: Final[str] = (
    "https://support.alphatheta.com/en-US/articles/8443065790489"
)
REKORDBOX_USB_CITATION: Final[str] = "https://support.alphatheta.com/en-US/articles/21050011890201"
REKORDBOX_USB_GUIDE_CITATION: Final[str] = (
    "https://cdn.rekordbox.com/files/20251021171528/USB_export_guide_en_251007.pdf"
)

KNOWN_LOCATIONS: Final[tuple[KnownLocation, ...]] = (
    KnownLocation(
        format_id=FormatId.TRAKTOR,
        file_name="collection.nml",
        folders=(
            (Platform.WINDOWS, ("Documents/Native Instruments",)),
            (Platform.MACOS, ("Documents/Native Instruments",)),
        ),
        citation_url=TRAKTOR_CITATION,
        citation_note=(
            "The Root Directory holds collection.nml. The folder name carries the version, "
            "so each update leaves a folder behind. Read with "
            f"{TRAKTOR_INSTALL_CITATION}, accessed {ACCESS_DATE}."
        ),
        versioned_parent=True,
    ),
    KnownLocation(
        format_id=FormatId.SERATO,
        file_name="database V2",
        folders=(
            (Platform.WINDOWS, ("Music/_Serato_",)),
            (Platform.MACOS, ("Music/_Serato_",)),
        ),
        citation_url=SERATO_FOLDER_CITATION,
        citation_note=(
            "The _Serato_ folder in the Music folder holds the database file. Serato also "
            "creates a _Serato_ folder at the root of every external drive it read music "
            f"from. Read with {SERATO_BACKUP_CITATION}, accessed {ACCESS_DATE}."
        ),
    ),
    KnownLocation(
        format_id=FormatId.MIXXX,
        file_name="mixxxdb.sqlite",
        folders=(
            (Platform.WINDOWS, ("AppData/Local/Mixxx",)),
            (
                Platform.MACOS,
                ("Library/Containers/org.mixxx.mixxx/Data/Library/Application Support/Mixxx",),
            ),
            (Platform.MACOS, ("Library/Application Support/Mixxx",)),
            (Platform.LINUX, (".mixxx",)),
            (Platform.LINUX, (".var/app/org.mixxx.Mixxx/.mixxx",)),
        ),
        citation_url=MIXXX_SETTINGS_CITATION,
        citation_note=(
            "The settings directory holds mixxxdb.sqlite, which is the library database. "
            f"Read on {ACCESS_DATE}."
        ),
    ),
    KnownLocation(
        format_id=FormatId.REKORDBOX,
        file_name="master.db",
        folders=(
            (Platform.WINDOWS, ("AppData/Roaming/Pioneer/rekordbox",)),
            (Platform.MACOS, ("Library/Pioneer/rekordbox",)),
        ),
        citation_url=REKORDBOX_FOLDER_CITATION,
        citation_note=(
            "The rekordbox folder holds master.db and rekordbox.xml. The page sits in the "
            f"rekordbox 5 section, and the path holds the master database on a fresh "
            f"ver. 7 install. Read on {ACCESS_DATE}."
        ),
    ),
    KnownLocation(
        format_id=FormatId.REKORDBOX,
        file_name="rekordbox.xml",
        folders=(
            (Platform.WINDOWS, ("AppData/Roaming/Pioneer/rekordbox",)),
            (Platform.MACOS, ("Library/Pioneer/rekordbox",)),
        ),
        citation_url=REKORDBOX_FOLDER_CITATION,
        citation_note=(
            "The rekordbox folder holds the imported library XML beside master.db. Read on "
            f"{ACCESS_DATE}."
        ),
    ),
    KnownLocation(
        format_id=FormatId.REKORDBOX,
        file_name="rekordbox.xml",
        folders=((Platform.WINDOWS, ("AppData/Roaming/rekordbox/rekordbox",)),),
        citation_url=REKORDBOX_FOLDER_CITATION,
        citation_note=(
            "A fresh ver. 7 install writes the imported library XML into a separate "
            "rekordbox folder. No vendor page states this folder, so the location is "
            f"observed on ver. 7. Read on {ACCESS_DATE}."
        ),
        observed=True,
        observed_version="7",
    ),
    KnownLocation(
        format_id=FormatId.REKORDBOX_USB,
        file_name="export.pdb",
        folders=((Platform.ANY, ("PIONEER/rekordbox",)), (Platform.ANY, (".PIONEER/rekordbox",))),
        citation_url=REKORDBOX_USB_CITATION,
        citation_note=(
            "A hidden folder at the root of the device holds the library files. The folder is "
            "named PIONEER on FAT and .PIONEER on HFS. The device library sits in a rekordbox "
            "folder inside it. "
            f"Read with {REKORDBOX_USB_GUIDE_CITATION}, accessed {ACCESS_DATE}."
        ),
        on_device=True,
    ),
    KnownLocation(
        format_id=FormatId.REKORDBOX_USB,
        file_name="exportLibrary.db",
        folders=((Platform.ANY, ("PIONEER/rekordbox",)), (Platform.ANY, (".PIONEER/rekordbox",))),
        citation_url=REKORDBOX_USB_CITATION,
        citation_note=(
            "The same hidden folder holds the OneLibrary database beside the device library. "
            f"Read with {REKORDBOX_USB_GUIDE_CITATION}, accessed {ACCESS_DATE}."
        ),
        on_device=True,
    ),
)


def locations_for(
    platform: Platform, format_id: FormatId | None = None
) -> tuple[KnownLocation, ...]:
    """Return the known locations of one platform, optionally of one format."""
    selected = []
    for location in KNOWN_LOCATIONS:
        if format_id is not None and location.format_id is not format_id:
            continue
        if not any(item[0] in (platform, Platform.ANY) for item in location.folders):
            continue
        selected.append(location)
    return tuple(selected)


def folders_for(location: KnownLocation, platform: Platform) -> tuple[str, ...]:
    """Return the folders of one location that belong to one platform.

    A folder declared for another platform never holds a candidate on this
    computer. A folder declared for every platform holds one on all of them.
    """
    folders: list[str] = []
    for entry_platform, entry_folders in location.folders:
        if entry_platform in (platform, Platform.ANY):
            folders.extend(entry_folders)
    return tuple(folders)
