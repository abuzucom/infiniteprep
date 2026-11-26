"""Focused coverage for filesystem races, junction handling, and track edges."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import IO, cast

import pytest

from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.domain.enums import AudioFormat
from infiniteprep.services.library_import import filesystem, relocation
from infiniteprep.services.library_import.filesystem import (
    open_inside_root,
    resolve_inside_root,
)
from infiniteprep.services.library_import.relocation import (
    MissingReference,
    find_relocated_files,
)
from infiniteprep.services.library_import.track_files import read_opened_file_facts


class NoneFormatAdapter(MetadataTagAdapter):
    """Report that the audio format cannot be determined."""

    def detect_format(self, file_path: Path) -> AudioFormat | None:
        """Return no detected audio format."""
        return None


def test_is_link_or_junction_detects(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A path reported as a junction is refused as a link."""
    junction = tmp_path / "junction"
    junction.mkdir()
    monkeypatch.setattr(
        Path,
        "is_junction",
        lambda path: path == junction,
        raising=False,
    )

    assert filesystem.is_link_or_junction(junction) is True


def test_is_link_or_case_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A directory entry with a reparse tag is refused as a link."""
    path = tmp_path / "reparse"
    path.mkdir()
    monkeypatch.setattr(Path, "is_symlink", lambda path: False)
    monkeypatch.setattr(Path, "is_junction", lambda path: False, raising=False)
    monkeypatch.setattr(
        filesystem.os,
        "lstat",
        lambda path: SimpleNamespace(st_reparse_tag=123),
    )

    assert filesystem.is_link_or_junction(path) is True


def test_is_link_or_junction_survives(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed directory entry stat does not report a link."""
    path = tmp_path / "missing"
    monkeypatch.setattr(Path, "is_symlink", lambda path: False)
    monkeypatch.setattr(Path, "is_junction", lambda path: False, raising=False)

    def fail_lstat(path: Path):
        """Fail the directory entry stat."""
        raise OSError("the directory entry cannot be read")

    monkeypatch.setattr(filesystem.os, "lstat", fail_lstat)

    assert filesystem.is_link_or_junction(path) is False


def test_resolve_inside_root_survives_a(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A path that cannot resolve does not pass the root check."""
    root = tmp_path / "root"
    root.mkdir()
    candidate = root / "track.wav"

    def fail_resolve(path: Path, *args: object, **kwargs: object) -> Path:
        raise OSError("the path cannot be resolved")

    monkeypatch.setattr(Path, "resolve", fail_resolve)

    assert resolve_inside_root(candidate, root) is None


def test_open_inside_root_survives_a(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A candidate that cannot resolve never reaches the safe open."""
    root = tmp_path / "root"
    root.mkdir()
    candidate = root / "track.wav"

    def fail_resolve(path: Path, *args: object, **kwargs: object) -> Path:
        raise OSError("the path cannot be resolved")

    monkeypatch.setattr(Path, "resolve", fail_resolve)

    assert open_inside_root(candidate, root) is None


def test_resolve_inside_root_refuses_a(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A link below the root cannot carry a candidate out of the root."""
    root = tmp_path / "root"
    child = root / "linked"
    child.mkdir(parents=True)
    candidate = child / "track.wav"
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == child,
    )

    assert resolve_inside_root(candidate, root) is None


def test_crosses_link_ignores_a_candidate(tmp_path: Path) -> None:
    """A candidate outside the textual root has no crossed link component."""
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()

    assert filesystem._crosses_link(outside, root) is False


def test_open_inside_root_refuses_a(tmp_path: Path) -> None:
    """A missing file cannot supply the identity needed for a safe open."""
    root = tmp_path / "root"
    root.mkdir()

    assert open_inside_root(root / "missing.wav", root) is None


def test_open_inside_root_case_3(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file whose open identity differs from its entry is refused."""
    root = tmp_path / "root"
    root.mkdir()
    track = root / "track.wav"
    track.write_bytes(b"audio")
    original_fstat = filesystem.os.fstat

    def changed_fstat(descriptor: int):
        info = original_fstat(descriptor)
        return SimpleNamespace(
            st_dev=info.st_dev,
            st_ino=info.st_ino + 1,
            st_mode=info.st_mode,
            st_size=info.st_size,
        )

    monkeypatch.setattr(filesystem.os, "fstat", changed_fstat)

    assert open_inside_root(track, root) is None


def test_open_inside_root_case_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A candidate that changes during the open never reaches the caller."""
    root = tmp_path / "root"
    root.mkdir()
    track = root / "track.wav"
    track.write_bytes(b"audio")
    original_resolve = Path.resolve
    candidate_resolves = 0

    def changing_resolve(path: Path, *args: object, **kwargs: object) -> Path:
        """Change the candidate resolution after the first safe open."""
        nonlocal candidate_resolves
        if path == track:
            candidate_resolves += 1
            if candidate_resolves == 2:
                return root / "outside"
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", changing_resolve)

    assert open_inside_root(track, root) is None


def test_real_children_survives_an_unreadable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A folder that cannot be listed contributes no real children."""
    root = tmp_path / "root"
    root.mkdir()

    def fail_iterdir(path: Path):
        """Fail the directory listing operation."""
        raise OSError("the folder cannot be read")

    monkeypatch.setattr(Path, "iterdir", fail_iterdir)

    assert filesystem.real_children(root) == []


def test_opened_track_facts_reject_an(tmp_path: Path) -> None:
    """An open file with no detected format produces no facts."""
    root = tmp_path / "root"
    root.mkdir()
    track = root / "track.wav"
    track.write_bytes(b"audio")
    opened = open_inside_root(track, root)
    assert opened is not None

    with opened:
        assert read_opened_file_facts(opened, NoneFormatAdapter()) is None


def test_relocation_accepts_no_references(tmp_path: Path) -> None:
    """An empty reference list produces no relocation result."""
    assert find_relocated_files([], tmp_path) == []


def test_safe_root_survives_a_resolution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A root that cannot resolve cannot bound a relocation search."""
    root = tmp_path / "root"
    root.mkdir()

    def fail_resolve(path: Path, *args: object, **kwargs: object) -> Path:
        raise OSError("the path cannot be resolved")

    monkeypatch.setattr(Path, "resolve", fail_resolve)

    assert relocation._safe_root(root) is None


def test_empty_content_hash_never_confirms(tmp_path: Path) -> None:
    """A candidate with the right size stays unresolved when its hash is empty."""
    root = tmp_path / "root"
    root.mkdir()
    candidate = root / "track.wav"
    candidate.write_bytes(b"audio")
    reference = MissingReference(path="old/track.wav", file_size=5, content_hash="")
    hashes = cast(relocation._HashCache, None)

    assert relocation._hash_confirms(reference, candidate, hashes) is False
    results = find_relocated_files([reference], root)
    assert results[0].found_path is None


def test_hash_cache_reports_a_failed(tmp_path: Path) -> None:
    """A candidate that cannot open has no cached digest."""
    root = tmp_path / "root"
    root.mkdir()
    hashes = relocation._HashCache(root)

    assert hashes.digest_of(root / "missing.wav") is None


def test_hash_cache_reports_case_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A candidate whose size cannot be read has no cached digest."""
    root = tmp_path / "root"
    root.mkdir()
    candidate = root / "track.wav"
    candidate.write_bytes(b"audio")
    original_fstat = relocation.os.fstat
    stat_calls = 0

    def fail_second_fstat(descriptor: int):
        """Fail the size stat after the open identity stat."""
        nonlocal stat_calls
        stat_calls += 1
        if stat_calls == 2:
            raise OSError("the open size cannot be read")
        return original_fstat(descriptor)

    monkeypatch.setattr(relocation.os, "fstat", fail_second_fstat)
    hashes = relocation._HashCache(root)

    assert hashes.digest_of(candidate) is None


def test_hash_cache_rejects_an_identity(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A candidate replaced during the hash read has no cached digest."""
    root = tmp_path / "root"
    root.mkdir()
    candidate = root / "track.wav"
    candidate.write_bytes(b"audio")
    original_stat = Path.stat

    def changed_stat(path: Path, *, follow_symlinks: bool = True):
        """Return a changed identity for the candidate path."""
        if path == candidate and follow_symlinks:
            return SimpleNamespace(st_dev=2, st_ino=2)
        return original_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(Path, "stat", changed_stat)
    hashes = relocation._HashCache(root)

    assert hashes.digest_of(candidate) is None


def test_relocation_file_size_reports_a(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A candidate whose entry stat fails has no file size."""
    candidate = tmp_path / "track.wav"

    def fail_lstat(path: Path):
        """Fail the directory entry stat."""
        raise OSError("the file size cannot be read")

    monkeypatch.setattr(relocation.os, "lstat", fail_lstat)

    assert relocation._file_size(candidate) is None


def test_relocation_digest_reports_a_failed() -> None:
    """A handle content read failure produces an empty digest."""

    class ReadFailure:
        """Seek successfully and fail every content read."""

        def seek(self, offset: int, whence: int = 0) -> int:
            """Move the handle to the requested position."""
            return 0

        def read(self, size: int = -1) -> bytes:
            """Fail the content read."""
            raise OSError("the content cannot be read")

    handle = cast(IO[bytes], ReadFailure())

    assert relocation._digest_of_handle(handle) == ""
