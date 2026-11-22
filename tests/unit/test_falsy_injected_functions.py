"""Tests that injected functions win over the defaults even when they are falsy.

An empty CallRecorder is a list and therefore falsy. A truthiness check would replace it
with the real network, download, or system command default.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from infiniteprep.adapters.catalog.discogs import DiscogsAdapter
from infiniteprep.adapters.catalog.itunes import ItunesSearchAdapter
from infiniteprep.adapters.catalog.musicbrainz import MusicBrainzAdapter
from infiniteprep.adapters.fingerprint.chromaprint_acoustid import ChromaprintAcoustIDAdapter
from infiniteprep.adapters.fingerprint.fpcalc_bootstrap import FpcalcBootstrapService
from infiniteprep.adapters.llm.ollama_openai import OllamaOpenAIReasoningAdapter
from infiniteprep.services.usb import targets
from infiniteprep.services.usb.linux import LinuxDriveInspector
from infiniteprep.services.usb.linux_probe import LinuxPreflightProbe
from infiniteprep.services.usb.macos import MacOSDriveInspector
from infiniteprep.services.usb.macos_probe import MacOSPreflightProbe
from infiniteprep.services.usb.windows import WindowsDriveInspector
from tests.fixtures.call_recorder import CallRecorder
from tests.fixtures.memory_repository import build_memory_repository


def _build_acoustid(recorder: CallRecorder, tmp_path: Path) -> object:
    """Return an AcoustID adapter whose fpcalc service stays inside tmp_path."""
    return ChromaprintAcoustIDAdapter(
        build_memory_repository(),
        bootstrap_service=FpcalcBootstrapService(install_dir=tmp_path),
        http_get_json=recorder,
    )


BUILDERS: dict[str, tuple[Callable[[CallRecorder, Path], object], str]] = {
    "discogs": (
        lambda recorder, _tmp: DiscogsAdapter(build_memory_repository(), http_get_json=recorder),
        "http_get_json",
    ),
    "itunes": (
        lambda recorder, _tmp: ItunesSearchAdapter(build_memory_repository(), recorder),
        "http_get_json",
    ),
    "musicbrainz": (
        lambda recorder, _tmp: MusicBrainzAdapter(
            build_memory_repository(), http_get_json=recorder
        ),
        "http_get_json",
    ),
    "acoustid": (_build_acoustid, "http_get_json"),
    "ollama": (
        lambda recorder, _tmp: OllamaOpenAIReasoningAdapter(http_post_json=recorder),
        "http_post_json",
    ),
    "fpcalc": (
        lambda recorder, tmp: FpcalcBootstrapService(install_dir=tmp, downloader=recorder),
        "downloader",
    ),
    "linux_inspector": (
        lambda recorder, _tmp: LinuxDriveInspector(command_runner=recorder),
        "_command_runner",
    ),
    "linux_probe": (
        lambda recorder, _tmp: LinuxPreflightProbe(command_runner=recorder),
        "_command_runner",
    ),
    "macos_probe": (
        lambda recorder, _tmp: MacOSPreflightProbe(info_reader=recorder),
        "_info_reader",
    ),
    "windows_inspector": (
        lambda recorder, _tmp: WindowsDriveInspector(command_runner=recorder),
        "_command_runner",
    ),
}


@pytest.mark.parametrize("name", sorted(BUILDERS))
def test_empty_recorder_replaces_default(name: str, tmp_path: Path) -> None:
    """The object keeps the injected recorder, not its real default."""
    build, attribute = BUILDERS[name]
    recorder = CallRecorder()

    built = build(recorder, tmp_path)

    assert getattr(built, attribute) is recorder


@pytest.mark.skipif(sys.platform != "darwin", reason="The macOS inspector runs on macOS only.")
def test_empty_recorder_replaces_macos_runner() -> None:
    """The macOS inspector keeps the injected command runner."""
    recorder = CallRecorder()

    inspector = MacOSDriveInspector(command_runner=recorder)

    assert inspector._command_runner is recorder


class _FalsyInspector:
    """A drive inspector that reports no drives and counts as empty."""

    def __init__(self) -> None:
        """Start with no recorded listings."""
        self.listings = 0

    def __len__(self) -> int:
        """Report zero length, so the inspector is falsy."""
        return 0

    def inspect_all(self) -> tuple[Any, ...]:
        """Count the listing and report no drives."""
        self.listings += 1
        return ()


def test_falsy_inspector_lists_the_drives(monkeypatch: pytest.MonkeyPatch) -> None:
    """A falsy inspector still lists the drives when only the probe uses its default."""
    default_inspector = _FalsyInspector()
    monkeypatch.setattr(
        targets, "build_platform_tools", lambda _platform: (default_inspector, object())
    )
    inspector = _FalsyInspector()

    candidates = targets.load_removable_targets(inspector=inspector)

    assert candidates == ()
    assert inspector.listings == 1
    assert default_inspector.listings == 0
