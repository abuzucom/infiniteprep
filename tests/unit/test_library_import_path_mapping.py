"""Behavioral tests for the path styles and the prefix mappings (D333)."""

from __future__ import annotations

from pathlib import Path

import pytest

from infiniteprep.services.library_import.path_mapping import (
    PathMapping,
    PathMappingError,
    PathStyle,
    apply_mapping,
    detect_path_style,
    normalize_path,
    resolve_inside_root,
    reverse_mapping,
)

WINDOWS_PATH = "D:\\Music\\Track.mp3"
POSIX_PATH = "/Users/user/Music/Track.mp3"
FILE_URL_PATH = "file:///Users/user/My%20Music/Track.mp3"
TRAKTOR_PATH = "/:C/Users/user/Music/Track.mp3"
ROOT = "/Users/user/Music"


def test_detect_path_style_reads_each_of_the_four_styles() -> None:
    """Each path style is recognized from the shape of the path."""
    assert detect_path_style(WINDOWS_PATH) is PathStyle.WINDOWS
    assert detect_path_style(POSIX_PATH) is PathStyle.POSIX
    assert detect_path_style(FILE_URL_PATH) is PathStyle.FILE_URL
    assert detect_path_style(TRAKTOR_PATH) is PathStyle.TRAKTOR_VOLUME


def test_detect_path_style_treats_a_unc_path_as_posix() -> None:
    """A path with no drive letter and no volume keeps the plain style."""
    assert detect_path_style("\\\\server\\share\\Track.mp3") is PathStyle.POSIX


def test_normalize_path_keeps_the_style_of_the_path() -> None:
    """Normalization keeps the style, so a display line stays readable."""
    assert normalize_path(WINDOWS_PATH) == "D:/Music/Track.mp3"
    assert normalize_path(POSIX_PATH) == POSIX_PATH
    assert normalize_path(FILE_URL_PATH) == "file:///Users/user/My Music/Track.mp3"
    assert normalize_path(TRAKTOR_PATH) == "/:C/Users/user/Music/Track.mp3"


def test_apply_mapping_moves_a_path_onto_the_target_prefix() -> None:
    """A path under the source prefix moves onto the target prefix."""
    mapping = PathMapping(source_prefix="D:\\Music", target_prefix=ROOT)

    assert apply_mapping(WINDOWS_PATH, mapping) == "/Users/user/Music/Track.mp3"


def test_apply_mapping_leaves_a_path_the_mapping_does_not_cover() -> None:
    """A path outside the source prefix keeps its own text."""
    mapping = PathMapping(source_prefix="D:\\Music", target_prefix=ROOT)

    assert apply_mapping("E:\\Other\\Track.mp3", mapping) == "E:/Other/Track.mp3"


def test_apply_mapping_refuses_a_path_that_climbs_above_the_root() -> None:
    """A path that climbs above the target prefix is refused."""
    mapping = PathMapping(source_prefix="D:\\Music", target_prefix=ROOT)

    with pytest.raises(PathMappingError):
        apply_mapping("D:\\Music\\..\\..\\etc\\passwd", mapping)


def test_reverse_mapping_moves_a_local_path_back_onto_the_source_prefix() -> None:
    """The reverse mapping puts a local path back into the source layout."""
    mapping = PathMapping(source_prefix="D:\\Music", target_prefix=ROOT)

    assert reverse_mapping("/Users/user/Music/Track.mp3", mapping) == "D:/Music/Track.mp3"


def test_reverse_mapping_leaves_a_path_the_mapping_does_not_cover() -> None:
    """A path outside the target prefix keeps its own text."""
    mapping = PathMapping(source_prefix="D:\\Music", target_prefix=ROOT)

    assert reverse_mapping("/Users/user/Other/Track.mp3", mapping) == "/Users/user/Other/Track.mp3"


def test_reverse_mapping_refuses_a_path_that_climbs_above_the_source_prefix() -> None:
    """A local path that climbs above the target prefix is refused."""
    mapping = PathMapping(source_prefix="D:\\Music", target_prefix=ROOT)

    with pytest.raises(PathMappingError):
        reverse_mapping("/Users/user/Music/../etc/passwd", mapping)


def test_reverse_mapping_refuses_a_remainder_that_names_a_drive() -> None:
    """A remainder that names a drive of its own is refused."""
    mapping = PathMapping(source_prefix="D:\\Music", target_prefix=ROOT)

    with pytest.raises(PathMappingError):
        reverse_mapping("/Users/user/Music/C:/Windows/Track.mp3", mapping)


def test_apply_mapping_refuses_a_remainder_that_names_a_drive() -> None:
    """A source remainder that names a drive of its own is refused."""
    mapping = PathMapping(source_prefix="D:\\Music", target_prefix=ROOT)

    with pytest.raises(PathMappingError):
        apply_mapping("D:\\Music\\C:/Windows\\Track.mp3", mapping)


def test_a_file_url_path_maps_onto_a_posix_target() -> None:
    """A file URL with an encoded name maps onto a plain target path."""
    mapping = PathMapping(source_prefix="file:///Users/user/My Music", target_prefix=ROOT)

    assert apply_mapping(FILE_URL_PATH, mapping) == "/Users/user/Music/Track.mp3"


def test_a_traktor_volume_path_maps_onto_a_windows_target() -> None:
    """A Traktor volume path maps onto a Windows target path."""
    mapping = PathMapping(source_prefix="/:C/Users/user/Music", target_prefix="E:\\Music")

    assert apply_mapping(TRAKTOR_PATH, mapping) == "E:/Music/Track.mp3"


def test_a_nested_file_url_is_refused() -> None:
    """A file URL that keeps naming a file URL is refused rather than expanded."""
    nested = "file://" * 8 + "/Users/user/Music/Track.mp3"

    with pytest.raises(PathMappingError):
        normalize_path(nested)


def test_a_path_inside_the_root_resolves(tmp_path: Path) -> None:
    """A path below the root resolves to the file it names."""
    root = tmp_path / "music"
    root.mkdir()
    track = root / "track.wav"
    track.write_bytes(b"bytes")

    resolved = resolve_inside_root(track, root)

    assert resolved == track.resolve()


def test_a_path_outside_the_root_does_not_resolve(tmp_path: Path) -> None:
    """A path above the root does not resolve."""
    root = tmp_path / "music"
    root.mkdir()
    outside = tmp_path / "secret.wav"
    outside.write_bytes(b"secret bytes")

    assert resolve_inside_root(outside, root) is None


def test_a_path_that_leaves_the_root_through_a_link_does_not_resolve(tmp_path: Path) -> None:
    """A folder inside the root that links out of it cannot carry a path out."""
    root = tmp_path / "music"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.wav").write_bytes(b"secret bytes")
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("This platform cannot create a directory symbolic link.")

    assert resolve_inside_root(root / "link" / "secret.wav", root) is None


def test_a_relative_root_does_not_resolve(tmp_path: Path) -> None:
    """A root that is not an absolute path cannot bound anything."""
    root = tmp_path / "music"
    root.mkdir()
    track = root / "track.wav"
    track.write_bytes(b"bytes")

    assert resolve_inside_root(track, Path(".")) is None
