"""Focused coverage for remaining settings validation and AcoustID failure branches."""

from __future__ import annotations

import os
import socket
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from infiniteprep.adapters.fingerprint.chromaprint_acoustid import ChromaprintAcoustIDAdapter
from infiniteprep.config import settings as settings_module
from infiniteprep.config.settings import AppSettings, SettingsManager, validate_endpoint_url
from infiniteprep.domain.enums import KeyDisplayNotation
from infiniteprep.domain.tag_output import TagOutputFormat
from tests.fixtures.memory_repository import build_memory_repository

STUB_KEY = "synthetic"


def test_non_ip_text_is_not() -> None:
    """A hostname that does not parse as an IP address passes the IP classifier."""
    assert settings_module._is_disallowed_ip("example.test") is False


@pytest.mark.parametrize(
    "url",
    [
        "https://2130706433/v1",
        "https://0x7f000001/v1",
        "https://0.999/v1",
    ],
)
def test_numeric_and_ambiguous_https_hosts(url: str) -> None:
    """Decimal, hexadecimal, and octal-looking hosts cannot bypass IP validation."""
    valid, reason = validate_endpoint_url(url)

    assert valid is False
    assert reason is not None
    assert "numeric or ambiguous" in reason


def test_unsupported_url_scheme_is_refused() -> None:
    """Only HTTP and HTTPS endpoint schemes pass validation."""
    valid, reason = validate_endpoint_url("ftp://example.test/v1")

    assert valid is False
    assert reason is not None
    assert "Unsupported URL scheme" in reason


def test_https_dns_rejects_mixed_public(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One private DNS answer rejects an otherwise public hostname."""

    def records() -> list[tuple[Any, ...]]:
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.8", 443)),
        ]

    monkeypatch.setattr(
        settings_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: records(),
    )
    valid, reason = validate_endpoint_url("https://example.test/v1", resolve_dns=True)
    assert valid is False
    assert reason is not None
    assert "disallowed IP" in reason


def test_https_dns_accepts_only_public(monkeypatch: pytest.MonkeyPatch) -> None:
    """A hostname with only public DNS answers passes HTTPS validation."""
    answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]
    monkeypatch.setattr(
        settings_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: answers,
    )
    assert validate_endpoint_url("https://example.test/v1", resolve_dns=True) == (True, None)


def test_https_dns_rejects_empty_and(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty answer and a resolver failure both reject the endpoint."""
    monkeypatch.setattr(
        settings_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [],
    )
    valid, reason = validate_endpoint_url("https://example.test/v1", resolve_dns=True)
    assert valid is False
    assert reason is not None
    assert "No DNS records" in reason

    def fail_dns(*_args: Any, **_kwargs: Any) -> Any:
        raise OSError("resolver unavailable")

    monkeypatch.setattr(settings_module.socket, "getaddrinfo", fail_dns)
    valid, reason = validate_endpoint_url("https://example.test/v1", resolve_dns=True)
    assert valid is False
    assert reason is not None
    assert "DNS resolution failed" in reason


def test_localhost_dns_rejects_a_non(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A localhost name that resolves to a public address cannot use plain HTTP."""
    answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 8080))]
    monkeypatch.setattr(
        settings_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: answers,
    )
    valid, reason = validate_endpoint_url("http://localhost:8080/v1", resolve_dns=True)
    assert valid is False
    assert reason is not None
    assert "non-loopback" in reason


def test_loopback_literals_skip_dns_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Numeric loopback hosts pass without a DNS lookup."""

    def refuse_dns(*_args: Any, **_kwargs: Any) -> Any:
        pytest.fail("Loopback literals must not resolve through DNS.")

    monkeypatch.setattr(settings_module.socket, "getaddrinfo", refuse_dns)

    assert validate_endpoint_url("http://127.0.0.1:11434/v1", resolve_dns=True) == (
        True,
        None,
    )
    assert validate_endpoint_url("http://[::1]:11434/v1", resolve_dns=True) == (True, None)


def test_localhost_trailing_dot_resolves_as(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A trailing root dot does not bypass the localhost-only HTTP rule."""
    answers = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 8080))]
    monkeypatch.setattr(
        settings_module.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: answers,
    )

    assert validate_endpoint_url("http://localhost.:8080/v1", resolve_dns=True) == (
        True,
        None,
    )


class FakeOs:
    """Provide a narrow operating-system seam for directory synchronization."""

    def __init__(self, name: str, **operations: Any) -> None:
        self.name = name
        self.O_RDONLY = os.O_RDONLY
        self.__dict__.update(operations)


def test_sync_directory_skips_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Windows settings writes do not open the containing directory."""
    fake_os = FakeOs("nt", open=lambda *_args: pytest.fail("Windows must not open the folder."))
    monkeypatch.setattr(settings_module, "os", fake_os)

    settings_module._sync_directory(Path("settings"))


def test_sync_directory_handles_an_open(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A POSIX directory that cannot open leaves the written settings in place."""

    def fail_open(*_args: Any) -> Any:
        raise OSError("open failed")

    fake_os = FakeOs("posix", open=fail_open)
    monkeypatch.setattr(settings_module, "os", fake_os)

    settings_module._sync_directory(tmp_path)


def test_sync_directory_closes_after_an(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed directory flush still closes the directory descriptor."""
    closed: list[int] = []

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("flush failed")

    fake_os = FakeOs(
        "posix",
        open=lambda *_args: 7,
        fsync=fail_fsync,
        close=closed.append,
    )
    monkeypatch.setattr(settings_module, "os", fake_os)

    settings_module._sync_directory(tmp_path)

    assert closed == [7]


def test_sync_directory_flushes_and_closes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A successful POSIX directory flush closes its descriptor."""
    flushed: list[int] = []
    closed: list[int] = []
    fake_os = FakeOs(
        "posix",
        open=lambda *_args: 9,
        fsync=flushed.append,
        close=closed.append,
    )
    monkeypatch.setattr(settings_module, "os", fake_os)

    settings_module._sync_directory(tmp_path)

    assert flushed == [9]
    assert closed == [9]


def test_atomic_settings_write_keeps_the(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A temporary-file flush failure preserves the old settings and removes the temp file."""
    path = tmp_path / "settings.json"
    path.write_text('{"old": true}', encoding="utf-8")
    manager = SettingsManager(path)

    def fail_fsync(_descriptor: int) -> None:
        raise OSError("flush failed")

    monkeypatch.setattr(settings_module.os, "fsync", fail_fsync)

    with pytest.raises(OSError, match="flush failed"):
        manager.save(AppSettings())

    assert path.read_text(encoding="utf-8") == '{"old": true}'
    assert list(tmp_path.glob("settings.json.*.tmp")) == []


def test_atomic_settings_write_case_2(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A failed atomic replace preserves the old settings and removes the temp file."""
    path = tmp_path / "settings.json"
    path.write_text('{"old": true}', encoding="utf-8")
    manager = SettingsManager(path)

    def fail_replace(_source: Path, _destination: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr(settings_module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        manager.save(AppSettings())

    assert path.read_text(encoding="utf-8") == '{"old": true}'
    assert list(tmp_path.glob("settings.json.*.tmp")) == []


def test_atomic_settings_write_cleans_up(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An interrupted replace still removes the temporary settings file."""
    path = tmp_path / "settings.json"
    path.write_text('{"old": true}', encoding="utf-8")
    manager = SettingsManager(path)

    def interrupt_replace(_source: Path, _destination: Path) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(settings_module.os, "replace", interrupt_replace)

    with pytest.raises(KeyboardInterrupt):
        manager.save(AppSettings())

    assert path.read_text(encoding="utf-8") == '{"old": true}'
    assert list(tmp_path.glob("settings.json.*.tmp")) == []


def test_default_settings_path_uses_the(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A manager without an explicit path uses the current config directory."""
    monkeypatch.setattr(
        settings_module.storage_paths,
        "current",
        lambda: SimpleNamespace(config_dir=tmp_path),
    )

    manager = SettingsManager()

    assert manager.settings_path == tmp_path / "settings.json"


def test_load_tag_output_returns_saved(tmp_path: Path) -> None:
    """The tag-output helper returns the saved notation, padding, and precision."""
    manager = SettingsManager(tmp_path / "settings.json")
    manager.save(
        AppSettings(
            tag_key_notation=KeyDisplayNotation.STANDARD.value,
            tag_key_leading_zero=True,
            tag_bpm_decimals=2,
        )
    )

    assert manager.load_tag_output() == TagOutputFormat(
        KeyDisplayNotation.STANDARD,
        True,
        2,
    )


class MissingFpcalcBootstrap:
    """Report that no fpcalc binary is installed."""

    def resolve_fpcalc_path(self) -> Path | None:
        """Return no fpcalc path."""
        return None


def test_identify_reports_a_missing_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The public identify path reports a missing default fpcalc binary as a warning."""
    adapter = ChromaprintAcoustIDAdapter(
        repository=build_memory_repository(),
        api_key=STUB_KEY,
        bootstrap_service=MissingFpcalcBootstrap(),
    )
    monkeypatch.setattr(adapter, "is_available", lambda: (True, None))

    result = adapter.identify(tmp_path / "track.wav")

    assert result.raw_fingerprint is None
    assert result.matches == []
    assert len(result.warnings) == 1
    assert "fpcalc execution failed" in result.warnings[0]
    assert "fpcalc binary not found" in result.warnings[0]
