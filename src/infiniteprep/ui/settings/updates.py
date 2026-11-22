"""Updates settings tab. It holds no settings yet."""

from __future__ import annotations

from infiniteprep.ui.settings.base import PlaceholderTab


class UpdatesTab(PlaceholderTab):
    """Settings tab for application updates."""

    TITLE = "Updates"
    ICON = "cloud-download"


TAB_CLASS = UpdatesTab
