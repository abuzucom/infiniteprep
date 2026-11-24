"""Behavioral tests for the bounded search that finds a moved audio file (D333)."""

from __future__ import annotations

from pathlib import Path
from typing import IO

import pytest

from infiniteprep.hashing import compute_sha256
from infiniteprep.services.library_import import relocation
from infiniteprep.services.library_import.relocation import (
    MAX_SEARCH_DEPTH,
    MAX_SEARCH_FOLDERS,
    MissingReference,
    find_relocated_files,
)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    """Return a folder that holds the moved files."""
    folder = tmp_path / "music"
    folder.mkdir()
    return folder


def write_track(folder: Path, name: str, content: bytes) -> Path:
    """Write one audio file with the given content and return its path."""
    path = folder / name
    path.write_bytes(content)
    return path


def test_relocation_hashes_one_candidate_once_for_many_references(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file that many references name is read once, not once per reference."""
    original = tmp_path / "old" / "track.wav"
    original.parent.mkdir()
    original.write_bytes(b"the track bytes")
    moved = write_track(root, "track.wav", b"the track bytes")
    digest = compute_sha256(moved)
    references = [
        MissingReference(
            path=str(tmp_path / "old" / "track.wav"),
            file_size=len(b"the track bytes"),
            content_hash=digest,
        )
        for _index in range(5)
    ]
    hashes: list[int] = []
    original_digest = relocation._digest_of_handle

    def counting_digest(handle: IO[bytes]) -> str:
        """Count every file the search reads."""
        hashes.append(1)
        return original_digest(handle)

    monkeypatch.setattr(relocation, "_digest_of_handle", counting_digest)

    results = find_relocated_files(references, root)

    assert all(result.confirmed for result in results)
    assert len(hashes) == 1


def test_relocation_stops_hashing_at_the_work_bound(
    root: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The search gives up hashing once the bound is reached."""
    original = tmp_path / "old" / "track.wav"
    original.parent.mkdir()
    original.write_bytes(b"the track bytes")
    moved = write_track(root, "track.wav", b"the track bytes")
    reference = MissingReference(
        path=str(original),
        file_size=len(b"the track bytes"),
        content_hash=compute_sha256(moved),
    )
    monkeypatch.setattr("infiniteprep.services.library_import.relocation.MAX_HASH_OPERATIONS", 0)

    results = find_relocated_files([reference], root)

    assert results[0].confirmed is False


def test_relocation_finds_a_file_that_moved(root: Path, tmp_path: Path) -> None:
    """A file that moved under a new folder is found and confirmed by its hash."""
    original = tmp_path / "old" / "track.wav"
    original.parent.mkdir()
    original.write_bytes(b"the track bytes")
    (root / "new").mkdir()
    moved = write_track(root / "new", "track.wav", b"the track bytes")
    reference = MissingReference(
        path=str(original),
        file_size=len(b"the track bytes"),
        content_hash=compute_sha256(original),
    )

    results = find_relocated_files([reference], root)

    assert len(results) == 1
    assert results[0].found_path == moved
    assert results[0].confirmed is True


def test_relocation_leaves_a_missing_file_unresolved(root: Path, tmp_path: Path) -> None:
    """A file with no match in the root stays unresolved."""
    original = tmp_path / "old" / "gone.wav"
    original.parent.mkdir()
    original.write_bytes(b"gone bytes")
    reference = MissingReference(
        path=str(original),
        file_size=len(b"gone bytes"),
        content_hash=compute_sha256(original),
    )

    results = find_relocated_files([reference], root)

    assert results[0].found_path is None
    assert results[0].confirmed is False


def test_relocation_refuses_a_file_with_the_same_name_and_another_content(
    root: Path, tmp_path: Path
) -> None:
    """A candidate with the recorded size but another content is not confirmed."""
    original = tmp_path / "old" / "track.wav"
    original.parent.mkdir()
    original.write_bytes(b"aaaa")
    write_track(root, "track.wav", b"bbbb")
    reference = MissingReference(
        path=str(original),
        file_size=4,
        content_hash=compute_sha256(original),
    )

    results = find_relocated_files([reference], root)

    assert results[0].found_path is None
    assert results[0].confirmed is False


def test_relocation_leaves_a_file_without_a_known_size_unresolved(
    root: Path, tmp_path: Path
) -> None:
    """A reference with no recorded size cannot be matched, so it stays unresolved."""
    original = tmp_path / "old" / "track.wav"
    original.parent.mkdir()
    original.write_bytes(b"the track bytes")
    write_track(root, "track.wav", b"the track bytes")
    reference = MissingReference(path=str(original))

    results = find_relocated_files([reference], root)

    assert results[0].found_path is None


def test_relocation_does_not_follow_a_link_out_of_the_root(root: Path, tmp_path: Path) -> None:
    """A file reached through a link out of the root is not found."""
    original = tmp_path / "old" / "track.wav"
    original.parent.mkdir()
    original.write_bytes(b"the track bytes")
    outside = tmp_path / "outside"
    outside.mkdir()
    write_track(outside, "track.wav", b"the track bytes")
    link = root / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("This platform cannot create a directory symbolic link.")
    reference = MissingReference(
        path=str(original),
        file_size=len(b"the track bytes"),
        content_hash=compute_sha256(original),
    )

    results = find_relocated_files([reference], root)

    assert results[0].found_path is None


def test_relocation_stops_at_the_depth_bound(root: Path, tmp_path: Path) -> None:
    """A file below the depth bound of the search stays out of the results."""
    original = tmp_path / "old" / "track.wav"
    original.parent.mkdir()
    original.write_bytes(b"the track bytes")
    deep = root
    for level in range(MAX_SEARCH_DEPTH + 2):
        deep = deep / f"level{level}"
    deep.mkdir(parents=True)
    write_track(deep, "track.wav", b"the track bytes")
    reference = MissingReference(
        path=str(original),
        file_size=len(b"the track bytes"),
        content_hash=compute_sha256(original),
    )

    results = find_relocated_files([reference], root)

    assert results[0].found_path is None


def test_relocation_stops_at_the_folder_bound(root: Path, tmp_path: Path) -> None:
    """A root full of empty folders ends the search."""
    original = tmp_path / "old" / "track.wav"
    original.parent.mkdir()
    original.write_bytes(b"the track bytes")
    for index in range(MAX_SEARCH_FOLDERS + 10):
        (root / f"empty{index}").mkdir()

    reference = MissingReference(
        path=str(original),
        file_size=len(b"the track bytes"),
        content_hash=compute_sha256(original),
    )
    results = find_relocated_files([reference], root)

    assert results[0].found_path is None


def test_relocation_refuses_a_root_that_is_a_link(root: Path, tmp_path: Path) -> None:
    """A root that is a link bounds nothing, so the search refuses it."""
    original = tmp_path / "old" / "track.wav"
    original.parent.mkdir()
    original.write_bytes(b"the track bytes")
    write_track(root, "track.wav", b"the track bytes")
    link = tmp_path / "linked-root"
    try:
        link.symlink_to(root, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("This platform cannot create a directory symbolic link.")
    reference = MissingReference(
        path=str(original),
        file_size=len(b"the track bytes"),
        content_hash=compute_sha256(original),
    )

    results = find_relocated_files([reference], link)

    assert results[0].found_path is None


def test_relocation_returns_one_result_for_every_reference(root: Path) -> None:
    """Every reference gets a result, even when the root holds no file."""
    reference = MissingReference(path="/music/gone.wav", file_size=1, content_hash="abc")

    results = find_relocated_files([reference], root)

    assert len(results) == 1
    assert results[0].original_path == "/music/gone.wav"
