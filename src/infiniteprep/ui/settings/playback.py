"""Playback settings tab. It holds no settings yet."""

from __future__ import annotations

from infiniteprep.ui.settings.base import PlaceholderTab


class PlaybackTab(PlaceholderTab):
    """Settings tab for audio playback."""

    TITLE = "Playback"
    # The tab holds no setting, so it stays hidden until one joins it.
    SHOWN = False


TAB_CLASS = PlaybackTab
