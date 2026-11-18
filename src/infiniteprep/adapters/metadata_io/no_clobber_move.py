"""Move a file to a new name without replacing a file that already holds that name."""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def move_to_new_path(source: Path, destination: Path) -> None:
    """Move source to destination, or raise FileExistsError when destination exists.

    An existence check followed by a rename leaves a gap. A POSIX rename replaces a file that
    appears in that gap. Exclusive creation reserves the name first. The move then replaces
    only the empty placeholder this call created.
    """
    try:
        with destination.open("xb"):
            pass
    except FileExistsError as exc:
        raise FileExistsError(
            f"Refusing to overwrite existing file {destination}; move or rename it and retry."
        ) from exc
    try:
        os.replace(source, destination)
    except BaseException:
        _remove_placeholder(destination)
        raise


def _remove_placeholder(placeholder: Path) -> None:
    """Remove the empty reserved file after a failed move without hiding the move error."""
    try:
        # Another program could fill the name in the meantime. Only an empty file is ours.
        if placeholder.stat().st_size == 0:
            placeholder.unlink()
    except OSError as exc:
        logger.warning("Could not remove placeholder %s (%s); delete it by hand.", placeholder, exc)
