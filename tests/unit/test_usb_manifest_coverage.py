"""Focused coverage for USB manifest validation, JSON, and confinement paths."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from infiniteprep.services.usb import manifest
from tests.unit.usb_manifest_support import build_manifest

POSIX_DIR_FD_SUPPORT = (
    hasattr(os, "O_NOFOLLOW")
    and hasattr(os, "O_DIRECTORY")
    and os.open in os.supports_dir_fd
    and os.replace in os.supports_dir_fd
    and os.unlink in os.supports_dir_fd
    and os.lstat in os.supports_dir_fd
)


def test_digest_matches_the_canonical_vocabulary_and_assignment_payload() -> None:
    """The digest uses canonical key, vocabulary, assignment, and tag ordering."""
    vocabulary = [
        manifest.ManifestTagEntry(category="Zed", name="B", parent="P"),
        manifest.ManifestTagEntry(category="Alpha", name="A", parent=""),
    ]
    assignments = [
        manifest.ManifestTrackTags(relative_path="z.aiff", tags=("B", "A")),
        manifest.ManifestTrackTags(relative_path="a.aiff", tags=()),
    ]

    digest = manifest.compute_my_tags_digest(vocabulary, assignments)

    assert digest == "6621de332ec16863dbc766d123f1f6ea0a86e5793cb805fccab7969f1accf6dc"


def test_manifest_dictionary_round_trip_uses_the_scope_text() -> None:
    """Manifest conversion serializes the scope and preserves every field."""
    original = build_manifest()

    raw = json.loads(json.dumps(manifest._manifest_to_dict(original)))
    parsed = manifest._manifest_from_dict(raw)

    assert raw["scope"] == original.scope.value
    assert parsed == original


@pytest.mark.parametrize(
    "file_name",
    ["", ".", "..", "folder/manifest.json", "folder\\manifest.json", "manifest\x00.json"],
)
def test_manifest_file_names_reject_separators_and_traversal(file_name: str) -> None:
    """Manifest names cannot name a directory or parent entry."""
    with pytest.raises(ValueError, match="Invalid USB manifest file name"):
        manifest._validate_manifest_file_name(file_name)


@pytest.mark.parametrize("value", [None, 1, False, [], {}])
def test_string_validation_rejects_non_strings(value: object) -> None:
    """String manifest fields reject every non-string value."""
    with pytest.raises(ValueError, match="must be a string"):
        manifest._require_str(value, "field")


@pytest.mark.parametrize("value", ["", "   ", None, 1])
def test_non_empty_string_validation_rejects_blank_values(value: object) -> None:
    """Required string manifest fields reject blank values."""
    with pytest.raises(ValueError, match=r"cannot be empty|must be a string"):
        manifest._require_non_empty_str(value, "field")


def test_relative_path_validation_accepts_a_confined_child() -> None:
    """A relative manifest path names a file below the drive root."""
    assert manifest._require_relative_path("crate/track.aiff", "track") == "crate/track.aiff"


@pytest.mark.parametrize("path", ["../track.aiff", "/track.aiff", "C:\\track.aiff", ""])
def test_relative_path_validation_rejects_escapes(path: str) -> None:
    """A relative manifest path cannot escape the drive root."""
    with pytest.raises(ValueError, match="relative path below"):
        manifest._require_relative_path(path, "track")


@pytest.mark.parametrize("value", [True, -1, "1", 1.5, None])
def test_non_negative_integer_validation_rejects_invalid_values(value: object) -> None:
    """Count fields reject booleans, negative values, and non-integers."""
    with pytest.raises(ValueError, match="non-negative integer"):
        manifest._require_non_negative_int(value, "count")


@pytest.mark.parametrize("raw", [{}, "reader", [None]])
def test_reader_validation_rejects_invalid_entries(raw: object) -> None:
    """Reader results require a list of object entries."""
    with pytest.raises(ValueError, match=r"readers|reader entry"):
        manifest._parse_readers(raw)


@pytest.mark.parametrize(
    "raw",
    [
        [],
        {"": {"exported": 1}},
        {"target": "one"},
        {"target": {"exported": True}},
        {"target": {"failed": -1}},
    ],
)
def test_target_count_validation_rejects_invalid_entries(raw: object) -> None:
    """Target counts require named object records with nonnegative integer counts."""
    with pytest.raises(ValueError, match=r"target_counts|target_counts entry|non-negative"):
        manifest._parse_target_counts(raw)


@pytest.mark.parametrize(
    "raw",
    [
        [None],
        [{"track": "invalid"}],
        [{"track": {"relative_path": "../escape.aiff", "title": "T", "artist": "A"}}],
    ],
)
def test_omission_validation_rejects_invalid_entries(raw: object) -> None:
    """Omissions require tracks with confined relative paths."""
    with pytest.raises(ValueError, match=r"omissions|omission entry|track|relative path"):
        manifest._parse_omissions(raw)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("state", "paused", "Unsupported manifest state"),
        ("completed_at_utc", None, "must include"),
        ("started_at_utc", "2026-11-26T12:00:00", "timezone offset"),
        ("scope", "unknown", "not a valid ExportScope"),
        ("playlist_names", "set", "list of strings"),
        ("track_count", True, "non-negative integer"),
        ("adapter_versions", [], "must be an object"),
        ("readers", {}, "must be a list"),
        ("target_counts", [], "must be an object"),
        ("omissions", {}, "must be a list"),
        ("my_tags_sha256", "", "cannot be empty"),
    ],
)
def test_manifest_parsing_rejects_invalid_fields(
    field: str, value: object, message: str
) -> None:
    """Manifest parsing rejects invalid states, timestamps, collections, and counts."""
    raw = json.loads(json.dumps(manifest._manifest_to_dict(build_manifest())))
    raw[field] = value

    with pytest.raises(ValueError, match=message):
        manifest._manifest_from_dict(raw)


def test_manifest_json_requires_an_object(tmp_path: Path) -> None:
    """A manifest JSON file must contain an object."""
    mount_root = tmp_path / "usb"
    meta_dir = mount_root / manifest.USB_METADATA_DIR_NAME
    meta_dir.mkdir(parents=True)
    (meta_dir / manifest.COMPLETED_MANIFEST_FILE_NAME).write_text("[]", encoding="utf-8")

    with pytest.raises(ValueError, match="must hold a JSON object"):
        manifest.read_completed_manifest(mount_root)


def test_missing_manifest_metadata_returns_none(tmp_path: Path) -> None:
    """Reading a manifest without a metadata directory returns None."""
    mount_root = tmp_path / "usb"
    mount_root.mkdir()

    assert manifest.read_completed_manifest(mount_root) is None
    assert manifest.read_in_progress_manifest(mount_root) is None


def test_in_progress_manifest_is_atomic_json_with_a_trailing_newline(tmp_path: Path) -> None:
    """The in-progress writer produces sorted, indented JSON in its metadata folder."""
    mount_root = tmp_path / "usb"
    mount_root.mkdir()
    original = build_manifest(state=manifest.MANIFEST_STATE_IN_PROGRESS, completed_at_utc=None)

    path = manifest.write_in_progress_manifest(mount_root, original)

    text = path.read_text(encoding="utf-8")
    assert text.endswith("\n")
    assert json.loads(text)["scope"] == original.scope.value
    assert manifest.read_in_progress_manifest(mount_root) == original


def test_manifest_writers_reject_invalid_state_before_creating_metadata(tmp_path: Path) -> None:
    """Invalid manifest state fails before the metadata folder appears."""
    mount_root = tmp_path / "usb"
    mount_root.mkdir()

    with pytest.raises(ValueError, match="state 'in_progress'"):
        manifest.write_in_progress_manifest(mount_root, build_manifest())
    with pytest.raises(ValueError, match="state 'completed'"):
        manifest.write_completed_manifest(
            mount_root,
            build_manifest(state=manifest.MANIFEST_STATE_IN_PROGRESS, completed_at_utc=None),
        )

    assert not (mount_root / manifest.USB_METADATA_DIR_NAME).exists()


def test_completed_writer_requires_a_completion_timestamp(tmp_path: Path) -> None:
    """A completed manifest needs a completion timestamp before writing starts."""
    mount_root = tmp_path / "usb"
    mount_root.mkdir()
    original = build_manifest(completed_at_utc=None)

    with pytest.raises(ValueError, match="UTC completion timestamp"):
        manifest.write_completed_manifest(mount_root, original)


def test_mount_root_resolution_rejects_missing_and_non_directory_paths(tmp_path: Path) -> None:
    """The mount root must exist and name a directory."""
    missing = tmp_path / "missing"
    file_path = tmp_path / "file.txt"
    file_path.write_text("data", encoding="utf-8")

    with pytest.raises(ValueError, match="missing or not a directory"):
        manifest._resolve_mount_root(missing)
    with pytest.raises(ValueError, match="missing or not a directory"):
        manifest._resolve_mount_root(file_path)


def test_metadata_and_manifest_paths_stay_inside_the_mount_root(tmp_path: Path) -> None:
    """Metadata and manifest paths resolve below the mount root."""
    mount_root = tmp_path / "usb"
    mount_root.mkdir()

    meta_dir = manifest.metadata_dir_path(mount_root)
    manifest_path = manifest._confined_manifest_file(
        mount_root, manifest.COMPLETED_MANIFEST_FILE_NAME
    )

    assert meta_dir == mount_root / manifest.USB_METADATA_DIR_NAME
    assert manifest_path.parent == meta_dir
    assert manifest_path.resolve().is_relative_to(mount_root.resolve())


def test_confined_metadata_context_reports_missing_and_creates_directories(
    tmp_path: Path,
) -> None:
    """The metadata context reports absence without creation and creates on request."""
    mount_root = tmp_path / "usb"
    mount_root.mkdir()

    with manifest._open_confined_meta_dir(mount_root, create=False) as opened:
        assert opened is None
    with manifest._open_confined_meta_dir(mount_root, create=True) as opened:
        assert opened is not None
        meta_dir, descriptor = opened
        assert meta_dir == mount_root / manifest.USB_METADATA_DIR_NAME
        if descriptor is not None:
            os.close(descriptor)


@pytest.mark.skipif(not POSIX_DIR_FD_SUPPORT, reason="This platform lacks POSIX dir_fd support.")
def test_posix_manifest_directory_and_file_operations_use_dir_fd(tmp_path: Path) -> None:
    """POSIX metadata and manifest operations use confined directory descriptors."""
    mount_root = tmp_path / "usb"
    mount_root.mkdir()
    meta_dir = mount_root / manifest.USB_METADATA_DIR_NAME

    with manifest._open_confined_meta_dir(mount_root, create=True) as opened:
        assert opened is not None
        resolved_meta_dir, meta_fd = opened
        assert resolved_meta_dir == meta_dir
        assert meta_fd is not None
        descriptor = manifest._open_confined_read_fd(
            resolved_meta_dir, manifest.COMPLETED_MANIFEST_FILE_NAME, dir_fd=meta_fd
        )
        assert descriptor is None

    target = meta_dir / manifest.COMPLETED_MANIFEST_FILE_NAME
    target.write_text("{}", encoding="utf-8")
    with manifest._open_confined_meta_dir(mount_root, create=False) as opened:
        assert opened is not None
        resolved_meta_dir, meta_fd = opened
        assert meta_fd is not None
        descriptor = manifest._open_confined_read_fd(
            resolved_meta_dir, manifest.COMPLETED_MANIFEST_FILE_NAME, dir_fd=meta_fd
        )
        assert descriptor is not None
        os.close(descriptor)


def test_confined_unlink_reports_absence_and_removes_a_regular_file(tmp_path: Path) -> None:
    """The confined unlink helper removes regular files and reports absent ones."""
    mount_root = tmp_path / "usb"
    meta_dir = mount_root / manifest.USB_METADATA_DIR_NAME
    meta_dir.mkdir(parents=True)

    assert not manifest._unlink_confined_file(
        meta_dir, manifest.IN_PROGRESS_MANIFEST_FILE_NAME, dir_fd=None
    )
    target = meta_dir / manifest.IN_PROGRESS_MANIFEST_FILE_NAME
    target.write_text("{}", encoding="utf-8")
    assert manifest._unlink_confined_file(
        meta_dir, manifest.IN_PROGRESS_MANIFEST_FILE_NAME, dir_fd=None
    )
    assert not target.exists()


def test_win32_manifest_context_closes_the_locked_metadata_handle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Win32 metadata branch closes its locked directory handle."""
    mount_root = tmp_path / "usb"
    mount_root.mkdir()
    closed: list[int] = []
    monkeypatch.setattr(manifest, "supports_dir_fd_nofollow", lambda: False)
    monkeypatch.setattr(manifest.sys, "platform", "win32")
    monkeypatch.setattr(manifest, "open_win32_locked_dir", lambda path, expected: 77)
    monkeypatch.setattr(manifest, "close_win32_handle", closed.append)

    with manifest._open_confined_meta_dir(mount_root, create=True) as opened:
        assert opened is not None
        meta_dir, descriptor = opened
        assert meta_dir == mount_root / manifest.USB_METADATA_DIR_NAME
        assert descriptor is None

    assert closed == [77]
