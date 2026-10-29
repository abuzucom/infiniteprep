"""Regression tests for the adapter hardening that answers the security review."""

from __future__ import annotations

import hashlib
import io
import logging
import re
import subprocess
import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mutagen.id3 import ID3

from infiniteprep.adapters.catalog import discogs as discogs_module
from infiniteprep.adapters.catalog import musicbrainz as musicbrainz_module
from infiniteprep.adapters.catalog.discogs import DiscogsAdapter
from infiniteprep.adapters.catalog.musicbrainz import MusicBrainzAdapter
from infiniteprep.adapters.fingerprint import chromaprint_acoustid as acoustid_module
from infiniteprep.adapters.fingerprint import fpcalc_bootstrap as fpcalc_module
from infiniteprep.adapters.fingerprint.chromaprint_acoustid import ChromaprintAcoustIDAdapter
from infiniteprep.adapters.fingerprint.fpcalc_bootstrap import FpcalcBootstrapService
from infiniteprep.adapters.llm.ollama_bootstrap import OllamaBootstrapService
from infiniteprep.adapters.metadata_io import mutagen_adapter as mutagen_adapter_module
from infiniteprep.adapters.metadata_io.mutagen_adapter import MutagenAdapter
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import FieldName
from tests.fixtures.audio_factory import create_mp3_fixture

SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")
FAKE_BINARY = b"synthetic fpcalc executable bytes"
DISCOGS_STUB_KEY = "synthetic-discogs-key"


def _build_fpcalc_zip(payload: bytes) -> bytes:
    """Return ZIP bytes holding one fpcalc.exe entry with the given payload."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("chromaprint/fpcalc.exe", payload)
    return buffer.getvalue()


def _new_repository() -> Repository:
    """Return a Repository backed by an in-memory SQLite database."""
    return Repository(DatabaseManager(":memory:"))


# HIGH finding. fpcalc archive digest verification.


def test_pinned_fpcalc_digests_cover_every_release_url() -> None:
    assert set(fpcalc_module.OFFICIAL_FPCALC_SHA256) == set(fpcalc_module.OFFICIAL_FPCALC_URLS)
    for digest in fpcalc_module.OFFICIAL_FPCALC_SHA256.values():
        assert SHA256_HEX_RE.fullmatch(digest)


@pytest.mark.parametrize("missing_digest", [None, ""])
def test_archive_install_rejects_missing_digest(tmp_path: Path, missing_digest: str | None) -> None:
    bootstrap = FpcalcBootstrapService(install_dir=tmp_path / "bin")
    with pytest.raises(ValueError, match="SHA-256"):
        bootstrap.install_from_archive_bytes(
            archive_bytes=_build_fpcalc_zip(FAKE_BINARY),
            expected_sha256=missing_digest,
            user_consented=True,
        )
    assert not (tmp_path / "bin").exists()


def test_install_fpcalc_verifies_pinned_digest_by_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fpcalc_module.platform, "system", lambda: "Windows")
    archive_bytes = _build_fpcalc_zip(FAKE_BINARY)
    bootstrap = FpcalcBootstrapService(
        install_dir=tmp_path / "bin", downloader=lambda _url: archive_bytes
    )
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        bootstrap.install_fpcalc(user_consented=True)
    assert not (tmp_path / "bin").exists()


def test_install_fpcalc_rejects_download_that_misses_caller_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    archive_bytes = _build_fpcalc_zip(FAKE_BINARY)
    monkeypatch.setattr(fpcalc_module.platform, "system", lambda: "Windows")
    monkeypatch.setitem(
        fpcalc_module.OFFICIAL_FPCALC_SHA256, "windows", hashlib.sha256(archive_bytes).hexdigest()
    )
    bootstrap = FpcalcBootstrapService(
        install_dir=tmp_path / "bin", downloader=lambda _url: archive_bytes
    )
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        bootstrap.install_fpcalc(user_consented=True, expected_sha256="0" * 64)
    assert not (tmp_path / "bin").exists()


@pytest.mark.parametrize("system_name", ["Linux", "FreeBSD", ""])
def test_install_fpcalc_refuses_platform_without_pinned_archive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, system_name: str
) -> None:
    monkeypatch.setattr(fpcalc_module.platform, "system", lambda: system_name)
    downloads: list[str] = []

    def record_download(url: str) -> bytes:
        downloads.append(url)
        return b""

    bootstrap = FpcalcBootstrapService(install_dir=tmp_path / "bin", downloader=record_download)
    with pytest.raises(ValueError, match="package manager"):
        bootstrap.install_fpcalc(user_consented=True)
    assert downloads == []
    assert not (tmp_path / "bin").exists()


def test_pinned_fpcalc_platforms_are_windows_and_macos() -> None:
    assert set(fpcalc_module.OFFICIAL_FPCALC_SHA256) == {"windows", "darwin"}


def test_archive_install_accepts_explicit_matching_digest(tmp_path: Path) -> None:
    archive_bytes = _build_fpcalc_zip(FAKE_BINARY)
    bootstrap = FpcalcBootstrapService(install_dir=tmp_path / "bin")
    installed = bootstrap.install_from_archive_bytes(
        archive_bytes=archive_bytes,
        expected_sha256=hashlib.sha256(archive_bytes).hexdigest(),
        user_consented=True,
    )
    assert installed.read_bytes() == FAKE_BINARY


def test_zip_entry_read_stays_within_binary_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"x" * 64
    archive_bytes = _build_fpcalc_zip(payload)
    monkeypatch.setattr(fpcalc_module, "MAX_FPCALC_BINARY_BYTES", len(payload) - 1)
    bootstrap = FpcalcBootstrapService(install_dir=tmp_path / "bin")
    with pytest.raises(ValueError, match="maximum size"):
        bootstrap.install_from_archive_bytes(
            archive_bytes=archive_bytes,
            expected_sha256=hashlib.sha256(archive_bytes).hexdigest(),
            user_consented=True,
        )


# MEDIUM finding. Rename template evaluation.


@pytest.mark.parametrize(
    "template",
    [
        "{artist.__class__}",
        "{artist.__class__.__init__.__globals__}",
        "{title[0]}",
        "{0}",
        "{artist",
        "artist}",
    ],
)
def test_render_filename_ignores_format_syntax(template: str) -> None:
    rendered = MutagenAdapter().render_filename(
        Path("track.mp3"),
        {FieldName.ARTIST: "Deep Dish", FieldName.TITLE: "The Dream"},
        template,
    )
    assert rendered.endswith(".mp3")
    assert "<class" not in rendered
    assert "__" not in rendered


def test_render_filename_substitutes_known_tokens() -> None:
    rendered = MutagenAdapter().render_filename(
        Path("track.mp3"),
        {FieldName.ARTIST: "Deep Dish", FieldName.TITLE: "The Dream"},
        "{artist} - {title}",
    )
    assert rendered == "Deep Dish - The Dream.mp3"


def test_render_filename_unknown_token_uses_default_layout() -> None:
    rendered = MutagenAdapter().render_filename(
        Path("track.mp3"),
        {FieldName.ARTIST: "Deep Dish", FieldName.TITLE: "The Dream"},
        "{unknown_token}",
    )
    assert rendered == "Deep Dish - The Dream.mp3"


# MEDIUM finding. Discogs release identifier validation.


@pytest.mark.parametrize(
    "entity_id", ["../database/search", "123?token=x", "12/34", "", "1" * 17, "12a"]
)
def test_discogs_fetch_rejects_unsafe_release_id(entity_id: str) -> None:
    requested: list[str] = []

    def record_request(url: str, _params: dict[str, str]) -> dict[str, Any]:
        requested.append(url)
        return {}

    adapter = DiscogsAdapter(
        repository=_new_repository(),
        user_token=DISCOGS_STUB_KEY,
        http_get_json=record_request,
        min_interval_sec=0.0,
    )
    with pytest.raises(ValueError, match="Discogs"):
        adapter.fetch(entity_id)
    assert requested == []


@pytest.mark.parametrize("entity_id", ["0", "0000000000000000", "0249504"])
def test_discogs_fetch_rejects_zero_led_release_id(entity_id: str) -> None:
    requested: list[str] = []

    def record_request(url: str, _params: dict[str, str]) -> dict[str, Any]:
        requested.append(url)
        return {}

    adapter = DiscogsAdapter(
        repository=_new_repository(),
        user_token=DISCOGS_STUB_KEY,
        http_get_json=record_request,
        min_interval_sec=0.0,
    )
    with pytest.raises(ValueError, match="Discogs"):
        adapter.fetch(entity_id)
    assert requested == []


def test_discogs_fetch_accepts_numeric_release_id() -> None:
    requested: list[str] = []

    def record_request(url: str, _params: dict[str, str]) -> dict[str, Any]:
        requested.append(url)
        return {}

    adapter = DiscogsAdapter(
        repository=_new_repository(),
        user_token=DISCOGS_STUB_KEY,
        http_get_json=record_request,
        min_interval_sec=0.0,
    )
    adapter.fetch("249504")
    assert requested == [f"{discogs_module.DISCOGS_BASE_URL}/releases/249504"]


# MEDIUM finding. Ollama model name validation.


@pytest.mark.parametrize(
    "model_name", ["-rf", "--insecure", "qwen2.5:", ":3b", "qwen 2.5:3b", "qwen2.5:3b;ls", ""]
)
def test_pull_model_rejects_unsafe_model_name(model_name: str) -> None:
    commands: list[list[str]] = []
    service = OllamaBootstrapService(runner=_command_recorder(commands))
    with pytest.raises(ValueError, match="model name"):
        service.pull_model(model_name, user_consented=True)
    assert commands == []


def test_pull_model_runs_recommended_model() -> None:
    commands: list[list[str]] = []
    service = OllamaBootstrapService(runner=_command_recorder(commands))
    assert service.pull_model("qwen2.5:3b", user_consented=True) == "pulled"
    assert commands == [["ollama", "pull", "qwen2.5:3b"]]


@pytest.mark.parametrize(
    "model_name",
    [
        "a/..:b",
        "a//b:c",
        "a/.hidden:b",
        "a/-x:b",
        "/a:b",
        "a/:b",
        "a/b/c/d:e",
        "a:-b",
        "a:.b",
        "a:b:c",
    ],
)
def test_pull_model_rejects_unsafe_name_segments(model_name: str) -> None:
    commands: list[list[str]] = []
    service = OllamaBootstrapService(runner=_command_recorder(commands))
    with pytest.raises(ValueError, match="model name"):
        service.pull_model(model_name, user_consented=True)
    assert commands == []


@pytest.mark.parametrize(
    "model_name",
    ["library/qwen2.5:3b", "hf.co/user/model-name:q4_K_M", "llama3.2-vision:11b"],
)
def test_pull_model_accepts_namespaced_names(model_name: str) -> None:
    commands: list[list[str]] = []
    service = OllamaBootstrapService(runner=_command_recorder(commands))
    service.pull_model(model_name, user_consented=True)
    assert commands == [["ollama", "pull", model_name]]


# Deprecated install_fpcalc digest parameter and template fallback observability.


def test_install_fpcalc_accepts_caller_digest_that_matches_pin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recwarn: pytest.WarningsRecorder
) -> None:
    archive_bytes = _build_fpcalc_zip(FAKE_BINARY)
    archive_digest = hashlib.sha256(archive_bytes).hexdigest()
    monkeypatch.setattr(fpcalc_module.platform, "system", lambda: "Windows")
    monkeypatch.setitem(fpcalc_module.OFFICIAL_FPCALC_SHA256, "windows", archive_digest)
    bootstrap = FpcalcBootstrapService(
        install_dir=tmp_path / "bin", downloader=lambda _url: archive_bytes
    )
    installed = bootstrap.install_fpcalc(
        user_consented=True, expected_sha256=archive_digest.upper()
    )
    assert installed.read_bytes() == FAKE_BINARY
    assert not [w for w in recwarn if issubclass(w.category, DeprecationWarning)]


def test_render_filename_logs_template_fallback(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=mutagen_adapter_module.__name__):
        rendered = MutagenAdapter().render_filename(
            Path("track.mp3"),
            {FieldName.ARTIST: "Deep Dish", FieldName.TITLE: "The Dream"},
            "{artis} - {title}",
        )
    assert rendered == "Deep Dish - The Dream.mp3"
    assert any("template" in record.getMessage() for record in caplog.records)


def test_render_filename_valid_template_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=mutagen_adapter_module.__name__):
        MutagenAdapter().render_filename(
            Path("track.mp3"),
            {FieldName.ARTIST: "Deep Dish", FieldName.TITLE: "The Dream"},
            "{artist} - {title}",
        )
    assert caplog.records == []


def _command_recorder(
    commands: list[list[str]],
) -> Callable[[list[str]], subprocess.CompletedProcess[str]]:
    """Return a runner that records each command and reports a successful pull."""

    def record_command(cmd: list[str]) -> subprocess.CompletedProcess[str]:
        commands.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="pulled", stderr="")

    return record_command


# MEDIUM finding. Bounded HTTP response reads.


class _OversizedResponse:
    """Fake urlopen response holding a valid JSON body larger than the read cap."""

    def __init__(self, body_bytes: bytes) -> None:
        self._body = io.BytesIO(body_bytes)
        self.requested_sizes: list[int | None] = []

    def __enter__(self) -> _OversizedResponse:
        return self

    def __exit__(self, *_exc_info: object) -> None:
        self._body.close()

    def read(self, size: int | None = None) -> bytes:
        self.requested_sizes.append(size)
        return self._body.read(size if size is not None else -1)


def _oversized_json(limit: int) -> bytes:
    """Return a syntactically valid JSON object exceeding limit bytes."""
    return b'{"padding": "' + b"x" * (limit + 16) + b'"}'


def _patch_urlopen(monkeypatch: pytest.MonkeyPatch, module: Any, limit: int) -> _OversizedResponse:
    """Route the module's urlopen to an oversized fake response."""
    response = _OversizedResponse(_oversized_json(limit))
    monkeypatch.setattr(module.urllib.request, "urlopen", lambda *_a, **_k: response)
    return response


def test_discogs_http_read_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    limit = discogs_module.MAX_API_RESPONSE_BYTES
    response = _patch_urlopen(monkeypatch, discogs_module, limit)
    adapter = DiscogsAdapter(repository=_new_repository(), user_token=DISCOGS_STUB_KEY)
    with pytest.raises(ValueError, match="maximum byte size"):
        adapter._default_http_get_json(f"{discogs_module.DISCOGS_BASE_URL}/releases/1", {})
    assert response.requested_sizes == [limit + 1]


def test_musicbrainz_http_read_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    limit = musicbrainz_module.MAX_API_RESPONSE_BYTES
    response = _patch_urlopen(monkeypatch, musicbrainz_module, limit)
    adapter = MusicBrainzAdapter(repository=_new_repository())
    with pytest.raises(ValueError, match="maximum byte size"):
        adapter._default_http_get_json(f"{musicbrainz_module.MB_BASE_URL}/recording", {})
    assert response.requested_sizes == [limit + 1]


def test_acoustid_http_read_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    limit = acoustid_module.MAX_API_RESPONSE_BYTES
    response = _patch_urlopen(monkeypatch, acoustid_module, limit)
    adapter = ChromaprintAcoustIDAdapter(repository=_new_repository(), api_key="synthetic")
    with pytest.raises(ValueError, match="maximum byte size"):
        adapter._default_http_get_json("https://api.acoustid.org/v2/lookup", {})
    assert response.requested_sizes == [limit + 1]


# LOW finding. TXXX frames on repeated ID3 writes.


def test_repeated_mp3_writes_keep_one_txxx_frame(tmp_path: Path) -> None:
    mp3_path = create_mp3_fixture(tmp_path / "track.mp3")
    adapter = MutagenAdapter()
    adapter._write_mp3_tags(mp3_path, {FieldName.CATALOG_NUMBER: "TRIUK014"})
    adapter._write_mp3_tags(mp3_path, {FieldName.CATALOG_NUMBER: "TRIUK015"})
    frames = ID3(str(mp3_path)).getall("TXXX:CATALOGNUMBER")
    assert len(frames) == 1
    assert list(frames[0].text) == ["TRIUK015"]


# Escaped braces and Discogs availability order.


def test_render_filename_keeps_escaped_braces(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger=mutagen_adapter_module.__name__):
        rendered = MutagenAdapter().render_filename(
            Path("track.mp3"),
            {FieldName.ARTIST: "Deep Dish", FieldName.TITLE: "The Dream"},
            "{artist} - {title} [{{label}}]",
        )
    assert rendered == "Deep Dish - The Dream [{label}].mp3"
    assert caplog.records == []


@pytest.mark.parametrize("template", ["{artist} {", "{artist} }", "{{artist}", "{}"])
def test_render_filename_unbalanced_braces_use_default(template: str) -> None:
    rendered = MutagenAdapter().render_filename(
        Path("track.mp3"),
        {FieldName.ARTIST: "Deep Dish", FieldName.TITLE: "The Dream"},
        template,
    )
    assert rendered == "Deep Dish - The Dream.mp3"


def test_discogs_fetch_unavailable_returns_empty_for_any_id() -> None:
    adapter = DiscogsAdapter(repository=_new_repository(), user_token=None, min_interval_sec=0.0)
    assert adapter.is_available()[0] is False
    assert adapter.fetch("../database/search") == []


def test_install_fpcalc_rejects_caller_digest_before_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fpcalc_module.platform, "system", lambda: "Windows")
    downloads: list[str] = []

    def record_download(url: str) -> bytes:
        downloads.append(url)
        return _build_fpcalc_zip(FAKE_BINARY)

    bootstrap = FpcalcBootstrapService(install_dir=tmp_path / "bin", downloader=record_download)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        bootstrap.install_fpcalc(user_consented=True, expected_sha256="0" * 64)
    assert downloads == []
    assert not (tmp_path / "bin").exists()


def _build_fpcalc_tar(payload: bytes) -> bytes:
    """Return gzip TAR bytes holding one fpcalc entry with the given payload."""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        member = tarfile.TarInfo("chromaprint/fpcalc")
        member.size = len(payload)
        archive.addfile(member, io.BytesIO(payload))
    return buffer.getvalue()


def test_tar_entry_read_stays_within_binary_cap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"x" * 64
    archive_bytes = _build_fpcalc_tar(payload)
    monkeypatch.setattr(fpcalc_module, "MAX_FPCALC_BINARY_BYTES", len(payload) - 1)
    bootstrap = FpcalcBootstrapService(install_dir=tmp_path / "bin")
    with pytest.raises(ValueError, match="maximum size"):
        bootstrap.install_from_archive_bytes(
            archive_bytes=archive_bytes,
            expected_sha256=hashlib.sha256(archive_bytes).hexdigest(),
            user_consented=True,
        )
    assert not any((tmp_path / "bin").glob("fpcalc*"))


def test_tar_entry_at_cap_installs_whole_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    payload = b"y" * 64
    archive_bytes = _build_fpcalc_tar(payload)
    monkeypatch.setattr(fpcalc_module, "MAX_FPCALC_BINARY_BYTES", len(payload))
    monkeypatch.setattr(fpcalc_module.platform, "system", lambda: "Darwin")
    bootstrap = FpcalcBootstrapService(install_dir=tmp_path / "bin")
    installed = bootstrap.install_from_archive_bytes(
        archive_bytes=archive_bytes,
        expected_sha256=hashlib.sha256(archive_bytes).hexdigest(),
        user_consented=True,
    )
    assert installed.read_bytes() == payload
