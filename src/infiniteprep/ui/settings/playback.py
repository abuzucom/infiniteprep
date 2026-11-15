"""Playback settings tab. It holds no settings yet."""

from __future__ import annotations

from infiniteprep.ui.settings.base import PlaceholderTab


class PlaybackTab(PlaceholderTab):
    """Settings tab for audio playback."""

    TITLE = "Playback"


TAB_CLASS = PlaybackTab
