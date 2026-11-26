"""Fault-injection coverage for USB manifest parsing and confinement helpers."""

from __future__ import annotations

import errno
from pathlib import Path

import pytest

from infiniteprep.services.usb import manifest


def set_posix_constants(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide POSIX descriptor flags on the Windows test host."""
    monkeypatch.setattr(manifest.os, "O_DIRECTORY", 0x10000, raising=False)
    monkeypatch.setattr(manifest.os, "O_NOFOLLOW", 0x20000, raising=False)


def test_string_dictionary_parser_rejects_non_string_pairs() -> None:
    """String dictionaries reject non-string keys and values."""
    with pytest.raises(ValueError, match="string pairs"):
        manifest._parse_str_dict({"reader": 1}, "readers")
    with pytest.raises(ValueError, match="string pairs"):
        manifest._parse_str_dict({1: "1.0"}, "readers")


def test_mount_root_rejects_a_linked_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """A linked mount root fails before path resolution."""
    monkeypatch.setattr(manifest, "is_symlink_or_reparse_point", lambda path: True)
    with pytest.raises(ValueError, match="Mount root cannot be a symbolic link"):
        manifest._resolve_mount_root(Path("usb"))


def test_open_meta_child_creates_and_opens_a_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    """Creating the metadata child uses the supplied base descriptor."""
    set_posix_constants(monkeypatch)
    created: list[str] = []
    monkeypatch.setattr(manifest.os, "mkdir", lambda name, mode, dir_fd: created.append(name))
    monkeypatch.setattr(manifest.os, "open", lambda name, flags, dir_fd=None: 21)

    descriptor = manifest._open_meta_child_fd(7, create=True)

    assert descriptor == 21
    assert created == [manifest.USB_METADATA_DIR_NAME]


def test_open_meta_child_reports_a_missing_directory(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing metadata child returns None."""
    set_posix_constants(monkeypatch)
    monkeypatch.setattr(
        manifest.os,
        "open",
        lambda name, flags, dir_fd=None: (_ for _ in ()).throw(FileNotFoundError()),
    )
    assert manifest._open_meta_child_fd(7, create=False) is None


def test_open_meta_child_translates_link_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    """A linked metadata child becomes a confined-path ValueError."""
    set_posix_constants(monkeypatch)
    monkeypatch.setattr(
        manifest.os,
        "open",
        lambda name, flags, dir_fd=None: (_ for _ in ()).throw(OSError(errno.ELOOP, "loop")),
    )
    with pytest.raises(ValueError, match="symbolic link"):
        manifest._open_meta_child_fd(7, create=False)


def test_prepare_path_meta_dir_reports_missing_and_linked_directories(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The path fallback reports a missing child and rejects a linked child."""
    base = tmp_path / "usb"
    base.mkdir()
    meta_dir = base / manifest.USB_METADATA_DIR_NAME
    assert manifest._prepare_path_meta_dir(base, meta_dir, create=False) is None

    meta_dir.mkdir()
    monkeypatch.setattr(manifest, "is_symlink_or_reparse_point", lambda path: True)
    with pytest.raises(ValueError, match="symbolic link"):
        manifest._prepare_path_meta_dir(base, meta_dir, create=False)


def test_prepare_path_meta_dir_rejects_outside_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A metadata directory that resolves outside the mount root fails."""
    base = tmp_path / "usb"
    base.mkdir()
    meta_dir = base / manifest.USB_METADATA_DIR_NAME
    meta_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    original_resolve = manifest.Path.resolve

    def resolve_metadata(path: Path, strict: bool = False) -> Path:
        if path.name == manifest.USB_METADATA_DIR_NAME:
            return outside
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(manifest.Path, "resolve", resolve_metadata)
    with pytest.raises(ValueError, match="outside the mount root"):
        manifest._prepare_path_meta_dir(base, meta_dir, create=False)


def test_metadata_path_rejects_links_and_outside_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Metadata paths reject linked children and children resolving outside the root."""
    mount_root = tmp_path / "usb"
    mount_root.mkdir()
    monkeypatch.setattr(manifest, "is_symlink_or_reparse_point", lambda path: True)
    with pytest.raises(ValueError, match="symbolic link"):
        manifest.metadata_dir_path(mount_root)

    monkeypatch.setattr(manifest, "is_symlink_or_reparse_point", lambda path: False)
    outside = tmp_path / "outside"
    outside.mkdir()
    original_resolve = manifest.Path.resolve

    def resolve_metadata(path: Path, strict: bool = False) -> Path:
        if path.name == manifest.USB_METADATA_DIR_NAME:
            return outside
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(manifest.Path, "resolve", resolve_metadata)
    with pytest.raises(ValueError, match="outside the mount root"):
        manifest.metadata_dir_path(mount_root)


def test_confined_manifest_path_rejects_links_and_outside_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Manifest paths reject linked targets and targets resolving outside the root."""
    mount_root = tmp_path / "usb"
    mount_root.mkdir()
    monkeypatch.setattr(manifest, "is_symlink_or_reparse_point", lambda path: True)
    with pytest.raises(ValueError, match="symbolic link"):
        manifest._confined_manifest_file(mount_root, manifest.COMPLETED_MANIFEST_FILE_NAME)

    monkeypatch.setattr(manifest, "is_symlink_or_reparse_point", lambda path: False)
    outside = tmp_path / "outside.json"
    outside.write_text("{}", encoding="utf-8")
    original_resolve = manifest.Path.resolve

    def resolve_manifest(path: Path, strict: bool = False) -> Path:
        if path.name == manifest.COMPLETED_MANIFEST_FILE_NAME:
            return outside
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(manifest.Path, "resolve", resolve_manifest)
    with pytest.raises(ValueError, match="outside the mount root"):
        manifest._confined_manifest_file(mount_root, manifest.COMPLETED_MANIFEST_FILE_NAME)


def test_confined_meta_context_rejects_a_linked_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The metadata context rejects a linked child before opening it."""
    mount_root = tmp_path / "usb"
    mount_root.mkdir()
    monkeypatch.setattr(manifest, "is_symlink_or_reparse_point", lambda path: True)
    with (
        pytest.raises(ValueError, match="symbolic link"),
        manifest._open_confined_meta_dir(mount_root, create=False),
    ):
        pass


def test_confined_meta_context_handles_missing_and_closed_dir_fds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The descriptor metadata context yields None for a missing child and closes handles."""
    set_posix_constants(monkeypatch)
    mount_root = tmp_path / "usb"
    mount_root.mkdir()
    closed: list[int] = []
    monkeypatch.setattr(manifest, "supports_dir_fd_nofollow", lambda: True)
    monkeypatch.setattr(manifest.os, "open", lambda path, flags: 30)
    monkeypatch.setattr(manifest.os, "close", closed.append)
    monkeypatch.setattr(manifest, "_open_meta_child_fd", lambda base_fd, create: None)
    with manifest._open_confined_meta_dir(mount_root, create=False) as opened:
        assert opened is None
    assert closed == [30]

    monkeypatch.setattr(manifest, "_open_meta_child_fd", lambda base_fd, create: 31)
    with manifest._open_confined_meta_dir(mount_root, create=False) as opened:
        assert opened is not None
        _meta_dir, meta_fd = opened
        assert meta_fd == 31
    assert closed == [30, 30, 31]


def test_confined_meta_context_handles_win32_missing_and_locked_handles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The Win32 metadata context handles a missing child and closes a locked handle."""
    mount_root = tmp_path / "usb"
    mount_root.mkdir()
    closed: list[int] = []
    monkeypatch.setattr(manifest, "supports_dir_fd_nofollow", lambda: False)
    monkeypatch.setattr(manifest.sys, "platform", "win32")
    monkeypatch.setattr(manifest, "open_win32_locked_dir", lambda path, expected: None)
    with manifest._open_confined_meta_dir(mount_root, create=False) as opened:
        assert opened is None

    monkeypatch.setattr(manifest, "open_win32_locked_dir", lambda path, expected: 77)
    monkeypatch.setattr(manifest, "close_win32_handle", closed.append)
    with manifest._open_confined_meta_dir(mount_root, create=True) as opened:
        assert opened is not None
        _meta_dir, meta_fd = opened
        assert meta_fd is None
    assert closed == [77]


def test_confined_read_fd_reports_missing_and_linked_dir_fd_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Descriptor reads report a missing file and reject a linked file."""
    set_posix_constants(monkeypatch)
    monkeypatch.setattr(
        manifest.os,
        "open",
        lambda name, flags, dir_fd=None: (_ for _ in ()).throw(FileNotFoundError()),
    )
    assert manifest._open_confined_read_fd(Path("meta"), "manifest.json", dir_fd=4) is None

    monkeypatch.setattr(
        manifest.os,
        "open",
        lambda name, flags, dir_fd=None: (_ for _ in ()).throw(OSError(errno.ELOOP, "loop")),
    )
    with pytest.raises(ValueError, match="symbolic link"):
        manifest._open_confined_read_fd(Path("meta"), "manifest.json", dir_fd=4)


def test_confined_read_fd_handles_win32_missing_and_regular_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Win32 reads return None for a missing file and a descriptor for a regular file."""
    meta_dir = tmp_path / "meta"
    meta_dir.mkdir()
    monkeypatch.setattr(manifest.sys, "platform", "win32")
    monkeypatch.setattr(manifest, "open_win32_confined_read_fd", lambda target, parent: None)
    assert manifest._open_confined_read_fd(meta_dir, "missing.json", dir_fd=None) is None

    monkeypatch.setattr(manifest, "open_win32_confined_read_fd", lambda target, parent: 12)
    assert manifest._open_confined_read_fd(meta_dir, "data.json", dir_fd=None) == 12


def test_confined_read_fd_closes_failed_posix_identity_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed POSIX identity check closes the opened descriptor."""
    meta_dir = tmp_path / "meta"
    meta_dir.mkdir()
    target = meta_dir / "data.json"
    target.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(manifest.sys, "platform", "linux")
    monkeypatch.setattr(manifest, "reject_symlink_target", lambda parent, name: None)
    monkeypatch.setattr(
        manifest,
        "verify_fd_identity_and_parent",
        lambda descriptor, path, parent: (_ for _ in ()).throw(ValueError("changed")),
    )
    closed: list[int] = []
    real_close = manifest.os.close
    monkeypatch.setattr(
        manifest.os,
        "close",
        lambda descriptor: (closed.append(descriptor), real_close(descriptor))[1],
    )
    with pytest.raises(ValueError, match="changed"):
        manifest._open_confined_read_fd(meta_dir, "data.json", dir_fd=None)
    assert len(closed) == 1


def test_unlink_confined_file_handles_dir_fd_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """Descriptor unlink reports a missing file and removes an existing file."""
    monkeypatch.setattr(manifest, "reject_symlink_target", lambda parent, name, dir_fd=None: None)
    monkeypatch.setattr(
        manifest.os,
        "unlink",
        lambda name, dir_fd: (_ for _ in ()).throw(FileNotFoundError()),
    )
    assert not manifest._unlink_confined_file(Path("meta"), "manifest.json", dir_fd=4)

    removed: list[str] = []
    monkeypatch.setattr(manifest.os, "unlink", lambda name, dir_fd: removed.append(name))
    assert manifest._unlink_confined_file(Path("meta"), "manifest.json", dir_fd=4)
    assert removed == ["manifest.json"]


def test_unlink_confined_file_handles_win32_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """Win32 unlink reports a missing file and a removed file."""
    monkeypatch.setattr(manifest.sys, "platform", "win32")
    monkeypatch.setattr(manifest, "unlink_win32_confined_file", lambda target, parent: False)
    assert not manifest._unlink_confined_file(Path("meta"), "manifest.json", dir_fd=None)

    monkeypatch.setattr(manifest, "unlink_win32_confined_file", lambda target, parent: True)
    assert manifest._unlink_confined_file(Path("meta"), "manifest.json", dir_fd=None)


def test_unlink_confined_file_rejects_outside_posix_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A POSIX manifest target resolving outside its parent fails."""
    monkeypatch.setattr(manifest.sys, "platform", "linux")
    meta_dir = tmp_path / "meta"
    meta_dir.mkdir()
    target = meta_dir / "manifest.json"
    target.write_text("{}", encoding="utf-8")
    other_parent = tmp_path / "other"
    other_parent.mkdir()
    original_resolve = manifest.Path.resolve

    def resolve_manifest(path: Path, strict: bool = False) -> Path:
        if path.name == "manifest.json":
            return other_parent / "outside.json"
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(manifest.Path, "resolve", resolve_manifest)
    with pytest.raises(ValueError, match="outside the mount root"):
        manifest._unlink_confined_file(meta_dir, "manifest.json", dir_fd=None)


def test_public_read_and_remove_manifest_report_missing_files(tmp_path: Path) -> None:
    """Public manifest helpers return None or False when the target file is absent."""
    mount_root = tmp_path / "usb"
    (mount_root / manifest.USB_METADATA_DIR_NAME).mkdir(parents=True)

    assert manifest.read_completed_manifest(mount_root) is None
    assert manifest.read_in_progress_manifest(mount_root) is None
    assert not manifest.remove_in_progress_manifest(mount_root)


def test_public_remove_manifest_reports_a_missing_metadata_directory(tmp_path: Path) -> None:
    """Removing an in-progress manifest without metadata storage returns False."""
    mount_root = tmp_path / "usb"
    mount_root.mkdir()

    assert not manifest.remove_in_progress_manifest(mount_root)


def test_confined_json_text_rejects_an_invalid_file_name(tmp_path: Path) -> None:
    """Confined JSON reads reject invalid manifest file names before opening."""
    with pytest.raises(ValueError, match="Invalid USB manifest file name"):
        manifest._read_confined_json_text(tmp_path, "../manifest.json", dir_fd=None)
