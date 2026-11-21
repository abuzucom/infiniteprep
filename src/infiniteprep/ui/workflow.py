"""Workflow stages, library filters, and window breakpoints of the UI-R shell (#342).

The workflow bar and the sidebar show five stages: Import, Analyze, Review, Apply,
and Export. Import counts every track. Each review status belongs to one of the
other four stages, so each track counts in exactly one of them (owner, 2026-11-21).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from enum import StrEnum
from typing import Final

from PySide6.QtCore import QRect

from infiniteprep.domain.enums import ReviewStatus


class WorkflowStage(StrEnum):
    """The five workflow stages in their working order."""

    IMPORT = "import"
    ANALYZE = "analyze"
    REVIEW = "review"
    APPLY = "apply"
    EXPORT = "export"


STAGE_ORDER: Final[tuple[WorkflowStage, ...]] = tuple(WorkflowStage)
# The statuses each stage counts. Import counts every track instead.
STAGE_STATUSES: Final[dict[WorkflowStage, frozenset[ReviewStatus]]] = {
    WorkflowStage.ANALYZE: frozenset(
        {ReviewStatus.UNPROCESSED, ReviewStatus.PROCESSING, ReviewStatus.ERROR}
    ),
    WorkflowStage.REVIEW: frozenset(
        {
            ReviewStatus.PROPOSED,
            ReviewStatus.READY_FOR_REVIEW,
            ReviewStatus.CONFLICTING_CANDIDATES,
            ReviewStatus.PARTIALLY_IDENTIFIED,
            ReviewStatus.PARTIALLY_APPROVED,
            ReviewStatus.NO_MATCH,
        }
    ),
    WorkflowStage.APPLY: frozenset({ReviewStatus.APPROVED, ReviewStatus.READY_TO_WRITE}),
    WorkflowStage.EXPORT: frozenset({ReviewStatus.APPLIED, ReviewStatus.WRITTEN}),
}
STATUS_STAGES: Final[dict[str, WorkflowStage]] = {
    status.value: stage for stage, statuses in STAGE_STATUSES.items() for status in statuses
}
# The stages before Export that can hold waiting work, in working order.
WORK_STAGES: Final[tuple[WorkflowStage, ...]] = (
    WorkflowStage.ANALYZE,
    WorkflowStage.REVIEW,
    WorkflowStage.APPLY,
)


def stage_for_status(status: str) -> WorkflowStage | None:
    """Return the stage that counts a review status, or None for an unknown value."""
    return STATUS_STAGES.get(status)


def count_stages(statuses: Iterable[str]) -> dict[WorkflowStage, int]:
    """Return the track count of each stage for the review statuses of a library."""
    status_list = list(statuses)
    tallies = Counter(stage_for_status(status) for status in status_list)
    counts = {stage: tallies.get(stage, 0) for stage in STAGE_ORDER}
    counts[WorkflowStage.IMPORT] = len(status_list)
    return counts


def find_current_stage(counts: dict[WorkflowStage, int]) -> WorkflowStage:
    """Return the first stage with waiting work.

    An empty library waits on Import. Export is current once no track waits on
    Analyze, Review, or Apply.
    """
    if counts.get(WorkflowStage.IMPORT, 0) == 0:
        return WorkflowStage.IMPORT
    for stage in WORK_STAGES:
        if counts.get(stage, 0) > 0:
            return stage
    return WorkflowStage.EXPORT


class LibraryFilter(StrEnum):
    """The sidebar library filters. They replace the review status dropdown."""

    ALL = "all"
    NEEDS_REVIEW = "needs_review"
    HAS_WARNINGS = "has_warnings"
    READY_TO_WRITE = "ready_to_write"


# Filters that select tracks by review status. Has Warnings reads the warnings instead.
FILTER_STATUSES: Final[dict[LibraryFilter, frozenset[ReviewStatus]]] = {
    LibraryFilter.NEEDS_REVIEW: STAGE_STATUSES[WorkflowStage.REVIEW],
    LibraryFilter.READY_TO_WRITE: STAGE_STATUSES[WorkflowStage.APPLY],
}


def matches_library_filter(chosen: LibraryFilter, status: str, has_warnings: bool) -> bool:
    """Return True when a track with this status and warning state passes the filter."""
    if chosen is LibraryFilter.HAS_WARNINGS:
        return has_warnings
    statuses = FILTER_STATUSES.get(chosen)
    if statuses is None:
        return True
    return status in statuses


class LayoutMode(StrEnum):
    """The three window layouts of proposal H, by window width."""

    WIDE = "wide"
    MEDIUM = "medium"
    NARROW = "narrow"


WIDE_MIN_WIDTH: Final[int] = 1600
MEDIUM_MIN_WIDTH: Final[int] = 1280
MINIMUM_WINDOW_WIDTH: Final[int] = 960
MINIMUM_WINDOW_HEIGHT: Final[int] = 600
SIDEBAR_WIDTH: Final[int] = 200
DETAIL_PANEL_WIDTH: Final[int] = 480
# A restored window stays where it was only while this much of its top strip shows.
GRAB_STRIP_HEIGHT: Final[int] = 32
MIN_GRAB_WIDTH: Final[int] = 120


def find_layout_mode(window_width: int) -> LayoutMode:
    """Return the layout for a window width in logical pixels."""
    if window_width >= WIDE_MIN_WIDTH:
        return LayoutMode.WIDE
    if window_width >= MEDIUM_MIN_WIDTH:
        return LayoutMode.MEDIUM
    return LayoutMode.NARROW


def shows_grab_strip(window: QRect, screen: QRect) -> bool:
    """Return True when a screen shows enough of the window's top strip to drag it.

    The strip stands in for the title bar just above the window's client area. The
    whole strip height and at least 120 px of its width, or the whole width of a
    narrower window, must sit on the screen.
    """
    strip = QRect(window.x(), window.y(), window.width(), GRAB_STRIP_HEIGHT)
    visible = strip.intersected(screen)
    needed_width = min(MIN_GRAB_WIDTH, window.width())
    return visible.height() == GRAB_STRIP_HEIGHT and visible.width() >= needed_width


def fit_window_on_screens(window: QRect, screens: list[QRect]) -> QRect:
    """Return the window geometry moved onto a screen when no screen shows its top.

    A window whose top strip a screen shows keeps its geometry. Otherwise it moves to
    the center of the first screen, shrunk to fit that screen. A monitor that is gone
    at launch, or a corner that barely touches a screen, therefore never leaves the
    window out of reach.
    """
    if not screens or any(shows_grab_strip(window, screen) for screen in screens):
        return QRect(window)
    target = screens[0]
    width = min(window.width(), target.width())
    height = min(window.height(), target.height())
    moved = QRect(0, 0, width, height)
    moved.moveCenter(target.center())
    return moved
