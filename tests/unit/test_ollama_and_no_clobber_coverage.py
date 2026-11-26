"""Focused coverage for Ollama controls and atomic no-replace move bindings."""

from __future__ import annotations

import ctypes
import errno
import hashlib
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

import pytest

from infiniteprep.adapters.llm import ollama_bootstrap
from infiniteprep.adapters.llm.ollama_bootstrap import (
    OllamaBootstrapService,
    build_tags_url,
    is_safe_model_reference,
    parse_model_names,
)
from infiniteprep.adapters.metadata_io import no_clobber_move
from infiniteprep.adapters.metadata_io.no_clobber_move import (
    discard_if_same_file,
    file_identity,
)


class FakeResponse:
    """Return a bounded fake HTTP response."""

    def __init__(self, body: bytes) -> None:
        self.body = body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self, _limit: int = -1) -> bytes:
        return self.body


class FakeNativeRename:
    """Record calls made through a bound native rename function."""

    def __init__(self, result: int = 0) -> None:
        self.result = result
        self.calls: list[tuple[Any, ...]] = []
        self.argtypes: Any = None
        self.restype: Any = None

    def __call__(self, *args: Any) -> int:
        self.calls.append(args)
        return self.result


def test_build_tags_url_removes_the() -> None:
    """The tags URL keeps the server root and removes only an OpenAI suffix."""
    assert build_tags_url("http://127.0.0.1:11434") == "http://127.0.0.1:11434/api/tags"
    assert build_tags_url("http://127.0.0.1:11434/v1/") == "http://127.0.0.1:11434/api/tags"
    assert build_tags_url(" http://localhost:8080/v1 ") == "http://localhost:8080/api/tags"


def test_parse_model_names_filters_malformed() -> None:
    """Only safe names survive and duplicate names keep their first position."""
    payload = {
        "models": [
            {"name": "qwen2.5:3b"},
            {"name": "qwen2.5:3b"},
            {"name": "bad name"},
            {"name": 7},
            {"other": "ignored"},
            "not a dict",
        ]
    }

    assert parse_model_names(payload) == ["qwen2.5:3b"]
    assert parse_model_names("not a dict") == []


@pytest.mark.parametrize(
    "name",
    [
        "model",
        "model:",
        ":tag",
        "model:tag:extra",
        ".model:tag",
        "model..name:tag",
        "model:bad/tag",
        "a/b/c/d:tag",
    ],
)
def test_is_safe_model_reference_rejects(name: str) -> None:
    """The reference grammar rejects missing tags and unsafe path segments."""
    assert is_safe_model_reference(name) is False


def test_is_safe_model_reference_accepts() -> None:
    """The reference grammar accepts one to three safe name segments."""
    assert is_safe_model_reference("qwen2.5:3b") is True
    assert is_safe_model_reference("hf.co/user/model:q4") is True


def test_ollama_list_installed_models_handles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid endpoints, network errors, bad JSON, and oversized bodies return no models."""
    opened: list[Any] = []
    monkeypatch.setattr(
        ollama_bootstrap.urllib.request,
        "urlopen",
        lambda *args, **kwargs: opened.append(args),
    )

    invalid = OllamaBootstrapService(base_url="http://203.0.113.5:11434")
    assert invalid.list_installed_models() == []
    assert opened == []

    monkeypatch.setattr(
        ollama_bootstrap.urllib.request,
        "urlopen",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("offline")),
    )
    assert OllamaBootstrapService().list_installed_models() == []

    monkeypatch.setattr(
        ollama_bootstrap.urllib.request,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(b"not json"),
    )
    assert OllamaBootstrapService().list_installed_models() == []

    monkeypatch.setattr(ollama_bootstrap, "MAX_TAGS_RESPONSE_BYTES", 4)
    monkeypatch.setattr(
        ollama_bootstrap.urllib.request,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(b"12345"),
    )
    assert OllamaBootstrapService().list_installed_models() == []


def test_ollama_list_installed_models_returns(monkeypatch: pytest.MonkeyPatch) -> None:
    """A valid tags response returns safe model names in response order."""
    body = b'{"models": [{"name": "qwen2.5:3b"}, {"name": "llama3:8b"}]}'
    monkeypatch.setattr(
        ollama_bootstrap.urllib.request,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(body),
    )

    assert OllamaBootstrapService().list_installed_models() == ["qwen2.5:3b", "llama3:8b"]


def test_ollama_install_requires_consent_and(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Consent gates installation and the platform selects one fixed command."""
    service = OllamaBootstrapService()
    with pytest.raises(PermissionError, match="consent"):
        service.install_ollama(user_consented=False)

    commands: list[list[str]] = []

    def runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, stdout="installed", stderr="")

    service = OllamaBootstrapService(runner=runner)
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    assert service.install_ollama(user_consented=True) == "installed"
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    service.install_ollama(user_consented=True)
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    assert "install Ollama" in service.install_ollama(user_consented=True)

    assert commands[0][0:2] == ["winget", "install"]
    assert commands[1] == ["brew", "install", "--cask", "ollama"]


def test_ollama_pull_validates_name_and() -> None:
    """A pull rejects an unsafe name before it checks consent."""
    service = OllamaBootstrapService(
        runner=lambda command: subprocess.CompletedProcess(command, 0, stdout="ok", stderr="")
    )

    with pytest.raises(ValueError, match="Invalid Ollama model name"):
        service.pull_model("model", user_consented=True)
    with pytest.raises(PermissionError, match="consent"):
        service.pull_model("qwen2.5:3b", user_consented=False)
    assert service.pull_model("qwen2.5:3b", user_consented=True) == "ok"


def test_ollama_exec_array_uses_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    """The subprocess seam receives an argument array and disables the shell."""
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="done", stderr="")

    monkeypatch.setattr(ollama_bootstrap.subprocess, "run", run)
    service = OllamaBootstrapService()

    assert service._exec_array(["ollama", "pull", "qwen2.5:3b"]).stdout == "done"
    assert calls[0][0] == ["ollama", "pull", "qwen2.5:3b"]
    assert calls[0][1]["shell"] is False


def test_ollama_detects_and_resolves_the(monkeypatch: pytest.MonkeyPatch) -> None:
    """The executable lookup returns a path only when the PATH lookup succeeds."""
    monkeypatch.setattr(ollama_bootstrap.shutil, "which", lambda name: None)
    service = OllamaBootstrapService()
    assert service.is_ollama_installed() is False
    assert service.resolve_ollama_path() is None

    monkeypatch.setattr(ollama_bootstrap.shutil, "which", lambda name: "/usr/bin/ollama")
    assert service.is_ollama_installed() is True
    assert service.resolve_ollama_path() == Path("/usr/bin/ollama")


def test_linux_renameat2_binding_uses_noreplace() -> None:
    """The Linux binding passes both current-directory descriptors and the no-replace flag."""
    native = FakeNativeRename()
    libc = cast(Any, type("Libc", (), {"renameat2": native})())
    rename = no_clobber_move._linux_renameat2(libc)

    assert rename is not None
    assert rename(b"source", b"destination") == 0
    assert native.calls == [(-100, b"source", -100, b"destination", 1)]


def test_macos_renamex_binding_uses_exclusive() -> None:
    """The macOS binding passes the exclusive rename flag."""
    native = FakeNativeRename()
    libc = cast(Any, type("Libc", (), {"renamex_np": native})())
    rename = no_clobber_move._macos_renamex_np(libc)

    assert rename is not None
    assert rename(b"source", b"destination") == 0
    assert native.calls == [(b"source", b"destination", 4)]


def test_native_rename_binding_handles_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing symbols, unsupported platforms, and unavailable C libraries return None."""
    no_clobber_move.native_no_replace_rename.cache_clear()
    monkeypatch.setattr(sys, "platform", "freebsd")
    monkeypatch.setattr(no_clobber_move.ctypes, "CDLL", lambda *args, **kwargs: object())
    assert no_clobber_move.native_no_replace_rename() is None

    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(
        no_clobber_move.ctypes,
        "CDLL",
        lambda *args, **kwargs: type("Libc", (), {})(),
    )
    assert no_clobber_move.native_no_replace_rename() is None

    def fail_cdll(*args: Any, **kwargs: Any) -> Any:
        raise OSError("library unavailable")

    monkeypatch.setattr(no_clobber_move.ctypes, "CDLL", fail_cdll)
    assert no_clobber_move.native_no_replace_rename() is None
    no_clobber_move.native_no_replace_rename.cache_clear()


def test_rename_error_paths_report_existing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Native errors distinguish an existing destination from an unsupported operation."""

    def existing(_source: bytes, _destination: bytes) -> int:
        ctypes.set_errno(errno.EEXIST)
        return -1

    monkeypatch.setattr(no_clobber_move, "native_no_replace_rename", lambda: existing)
    with pytest.raises(FileExistsError):
        no_clobber_move._rename_without_replacing(Path("source"), Path("destination"))

    def unsupported(_source: bytes, _destination: bytes) -> int:
        ctypes.set_errno(errno.EINVAL)
        return -1

    monkeypatch.setattr(no_clobber_move, "native_no_replace_rename", lambda: unsupported)
    assert no_clobber_move._rename_without_replacing(Path("source"), Path("destination")) is False


def test_windows_move_binding_uses_os(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Windows path uses os.rename without an existence check."""
    calls: list[tuple[Path, Path]] = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(
        no_clobber_move.os,
        "rename",
        lambda source, destination: calls.append((source, destination)),
    )

    no_clobber_move._move_without_replacing(Path("source"), Path("destination"))

    assert calls == [(Path("source"), Path("destination"))]


def test_discard_if_same_file_checks(tmp_path: Path) -> None:
    """Discard removes only the expected file and restores a changed file."""
    source = tmp_path / "track.wav"
    source.write_bytes(b"audio")
    identity = file_identity(source)
    digest = hashlib.sha256(b"audio").hexdigest()

    assert discard_if_same_file(source, identity, digest) is True
    assert not source.exists()

    source.write_bytes(b"changed")
    assert discard_if_same_file(source, identity, digest) is False
    assert source.read_bytes() == b"changed"

    other_identity = (identity[0], identity[1] + 1)
    assert discard_if_same_file(source, other_identity) is False
    assert source.read_bytes() == b"changed"


def test_discard_if_same_file_handles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing source and a failed private move both preserve the source."""
    source = tmp_path / "track.wav"
    source.write_bytes(b"audio")
    identity = file_identity(source)
    monkeypatch.setattr(
        no_clobber_move,
        "move_to_new_path",
        lambda *_args: (_ for _ in ()).throw(FileNotFoundError()),
    )
    assert discard_if_same_file(source, identity) is False
    assert source.exists()

    monkeypatch.setattr(
        no_clobber_move,
        "move_to_new_path",
        lambda *_args: (_ for _ in ()).throw(OSError("cannot move")),
    )
    assert discard_if_same_file(source, identity) is False
    assert source.exists()
