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

from infiniteprep.adapters.catalog import discogs as discogs_module
from infiniteprep.adapters.catalog import musicbrainz as musicbrainz_module
from infiniteprep.adapters.catalog.discogs import DiscogsAdapter
from infiniteprep.adapters.catalog.musicbrainz import MusicBrainzAdapter
from infiniteprep.adapters.fingerprint import chromaprint_acoustid as acoustid_module
from infiniteprep.adapters.fingerprint import fpcalc_bootstrap as fpcalc_module
from infiniteprep.adapters.fingerprint.chromaprint_acoustid import ChromaprintAcoustIDAdapter
from infiniteprep.adapters.fingerprint.fpcalc_bootstrap import FpcalcBootstrapService
from infiniteprep.adapters.llm.ollama_bootstrap import OllamaBootstrapService
from infiniteprep.adapters.metadata_io import metadata_tag_adapter as metadata_tag_adapter_module
from infiniteprep.adapters.metadata_io.id3_containers import read_mp3_tag
from infiniteprep.adapters.metadata_io.id3v2 import decode_txxx, frame_key
from infiniteprep.adapters.metadata_io.metadata_tag_adapter import MetadataTagAdapter
from infiniteprep.domain.enums import FieldName
from tests.fixtures.audio_factory import create_mp3_fixture
from tests.fixtures.call_recorder import CallRecorder
from tests.fixtures.memory_repository import build_memory_repository

SHA256_HEX_RE = re.compile(r"[0-9a-f]{64}")
FAKE_BINARY = b"synthetic fpcalc executable bytes"
DISCOGS_STUB_KEY = "synthetic-discogs-key"
# discogs.SAFE_RELEASE_ID_RE accepts at most 16 digits.
MAX_RELEASE_ID_DIGITS = 16


def _build_fpcalc_zip(payload: bytes) -> bytes:
    """Return ZIP bytes holding one fpcalc.exe entry with the given payload."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("chromaprint/fpcalc.exe", payload)
    return buffer.getvalue()


# HIGH finding. fpcalc archive digest verification.


def test_fpcalc_digests_cover_release_urls() -> None:
    """Every pinned release URL has a well-formed SHA-256 digest."""
    assert set(fpcalc_module.OFFICIAL_FPCALC_SHA256) == set(fpcalc_module.OFFICIAL_FPCALC_URLS)
    for digest in fpcalc_module.OFFICIAL_FPCALC_SHA256.values():
        assert SHA256_HEX_RE.fullmatch(digest)


@pytest.mark.parametrize("missing_digest", [None, ""])
def test_archive_install_rejects_missing_digest(tmp_path: Path, missing_digest: str | None) -> None:
    """An archive install without a digest raises and creates no install folder."""
    bootstrap = FpcalcBootstrapService(install_dir=tmp_path / "bin")
    with pytest.raises(ValueError, match="SHA-256"):
        bootstrap.install_from_archive_bytes(
            archive_bytes=_build_fpcalc_zip(FAKE_BINARY),
            expected_sha256=missing_digest,
            user_consented=True,
        )
    assert not (tmp_path / "bin").exists()


def test_fpcalc_checks_pinned_digest(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A download that misses the pinned digest raises and installs nothing."""
    monkeypatch.setattr(fpcalc_module.platform, "system", CallRecorder(reply="Windows"))
    archive_bytes = _build_fpcalc_zip(FAKE_BINARY)
    bootstrap = FpcalcBootstrapService(
        install_dir=tmp_path / "bin", downloader=CallRecorder(reply=archive_bytes)
    )
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        bootstrap.install_fpcalc(user_consented=True)
    assert not (tmp_path / "bin").exists()


def test_fpcalc_rejects_digest_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller digest that differs from the download raises and installs nothing."""
    archive_bytes = _build_fpcalc_zip(FAKE_BINARY)
    monkeypatch.setattr(fpcalc_module.platform, "system", CallRecorder(reply="Windows"))
    monkeypatch.setitem(
        fpcalc_module.OFFICIAL_FPCALC_SHA256, "windows", hashlib.sha256(archive_bytes).hexdigest()
    )
    bootstrap = FpcalcBootstrapService(
        install_dir=tmp_path / "bin", downloader=CallRecorder(reply=archive_bytes)
    )
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        bootstrap.install_fpcalc(user_consented=True, expected_sha256="0" * 64)
    assert not (tmp_path / "bin").exists()


@pytest.mark.parametrize("system_name", ["Linux", "FreeBSD", ""])
def test_fpcalc_refuses_unpinned_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, system_name: str
) -> None:
    """A platform without a pinned archive points to the package manager and downloads nothing."""
    monkeypatch.setattr(fpcalc_module.platform, "system", CallRecorder(reply=system_name))
    downloads: list[str] = []

    def record_download(url: str) -> bytes:
        downloads.append(url)
        return b""

    bootstrap = FpcalcBootstrapService(install_dir=tmp_path / "bin", downloader=record_download)
    with pytest.raises(ValueError, match="package manager"):
        bootstrap.install_fpcalc(user_consented=True)
    assert downloads == []
    assert not (tmp_path / "bin").exists()


def test_fpcalc_platforms_are_windows_macos() -> None:
    """Pinned fpcalc archives exist for Windows and macOS only."""
    assert set(fpcalc_module.OFFICIAL_FPCALC_SHA256) == {"windows", "darwin"}


def test_archive_accepts_matching_digest(tmp_path: Path) -> None:
    """An archive with a matching digest installs the binary unchanged."""
    archive_bytes = _build_fpcalc_zip(FAKE_BINARY)
    bootstrap = FpcalcBootstrapService(install_dir=tmp_path / "bin")
    installed = bootstrap.install_from_archive_bytes(
        archive_bytes=archive_bytes,
        expected_sha256=hashlib.sha256(archive_bytes).hexdigest(),
        user_consented=True,
    )
    assert installed.read_bytes() == FAKE_BINARY


def test_zip_read_stays_within_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A ZIP entry larger than the binary cap raises a size error."""
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
    """Attribute, index, and positional format fields never reach Python formatting."""
    rendered = MetadataTagAdapter().render_filename(
        Path("track.mp3"),
        {FieldName.ARTIST: "Deep Dish", FieldName.TITLE: "The Dream"},
        template,
    )
    assert rendered.endswith(".mp3")
    assert "<class" not in rendered
    assert "__" not in rendered


def test_render_filename_substitutes_known_tokens() -> None:
    """Known tokens take the field values."""
    rendered = MetadataTagAdapter().render_filename(
        Path("track.mp3"),
        {FieldName.ARTIST: "Deep Dish", FieldName.TITLE: "The Dream"},
        "{artist} - {title}",
    )
    assert rendered == "Deep Dish - The Dream.mp3"


def test_render_filename_unknown_token() -> None:
    """An unknown token falls back to the artist and title layout."""
    rendered = MetadataTagAdapter().render_filename(
        Path("track.mp3"),
        {FieldName.ARTIST: "Deep Dish", FieldName.TITLE: "The Dream"},
        "{unknown_token}",
    )
    assert rendered == "Deep Dish - The Dream.mp3"


# MEDIUM finding. Discogs release identifier validation.


@pytest.mark.parametrize(
    "entity_id",
    [
        # Path, query, and separator characters, an empty id, an overlong id, and a letter.
        "../database/search",
        "123?token=x",
        "12/34",
        "",
        "1" * (MAX_RELEASE_ID_DIGITS + 1),
        "12a",
        # Ids that start with zero name no Discogs release.
        "0",
        "0000000000000000",
        "0249504",
    ],
)
def test_discogs_rejects_bad_release_id(entity_id: str) -> None:
    """An unsafe or zero-led release id raises before the adapter sends any request."""
    requested: list[str] = []

    def record_request(url: str, _params: dict[str, str]) -> dict[str, Any]:
        requested.append(url)
        return {}

    adapter = DiscogsAdapter(
        repository=build_memory_repository(),
        user_token=DISCOGS_STUB_KEY,
        http_get_json=record_request,
        min_interval_sec=0.0,
    )
    with pytest.raises(ValueError, match="Discogs"):
        adapter.fetch(entity_id)
    assert requested == []


def test_discogs_fetch_accepts_numeric_id() -> None:
    """A plain numeric release id reaches the Discogs releases endpoint."""
    requested: list[str] = []

    def record_request(url: str, _params: dict[str, str]) -> dict[str, Any]:
        requested.append(url)
        return {}

    adapter = DiscogsAdapter(
        repository=build_memory_repository(),
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
def test_pull_model_rejects_unsafe_name(model_name: str) -> None:
    """Option-like, empty, spaced, or shell-shaped names raise before any command runs."""
    commands: list[list[str]] = []
    service = OllamaBootstrapService(runner=_command_recorder(commands))
    with pytest.raises(ValueError, match="model name"):
        service.pull_model(model_name, user_consented=True)
    assert commands == []


def test_pull_model_runs_recommended_model() -> None:
    """The recommended model pulls with one fixed ollama command."""
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
def test_pull_model_rejects_unsafe_segments(model_name: str) -> None:
    """Empty, hidden, dotted, option-like, or extra segments raise before any command runs."""
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
    """Namespaced and host-prefixed model names pull unchanged."""
    commands: list[list[str]] = []
    service = OllamaBootstrapService(runner=_command_recorder(commands))
    service.pull_model(model_name, user_consented=True)
    assert commands == [["ollama", "pull", model_name]]


# Deprecated install_fpcalc digest parameter and template fallback observability.


def test_fpcalc_accepts_caller_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, recwarn: pytest.WarningsRecorder
) -> None:
    """An uppercase caller digest that matches the pin installs with no deprecation warning."""
    archive_bytes = _build_fpcalc_zip(FAKE_BINARY)
    archive_digest = hashlib.sha256(archive_bytes).hexdigest()
    monkeypatch.setattr(fpcalc_module.platform, "system", CallRecorder(reply="Windows"))
    monkeypatch.setitem(fpcalc_module.OFFICIAL_FPCALC_SHA256, "windows", archive_digest)
    bootstrap = FpcalcBootstrapService(
        install_dir=tmp_path / "bin", downloader=CallRecorder(reply=archive_bytes)
    )
    installed = bootstrap.install_fpcalc(
        user_consented=True, expected_sha256=archive_digest.upper()
    )
    assert installed.read_bytes() == FAKE_BINARY
    assert not [w for w in recwarn if issubclass(w.category, DeprecationWarning)]


def test_render_filename_logs_template_fallback(caplog: pytest.LogCaptureFixture) -> None:
    """A misspelled token falls back to the default layout and logs a template warning."""
    with caplog.at_level(logging.WARNING, logger=metadata_tag_adapter_module.__name__):
        rendered = MetadataTagAdapter().render_filename(
            Path("track.mp3"),
            {FieldName.ARTIST: "Deep Dish", FieldName.TITLE: "The Dream"},
            "{artis} - {title}",
        )
    assert rendered == "Deep Dish - The Dream.mp3"
    assert any("template" in record.getMessage() for record in caplog.records)


def test_valid_filename_template_logs_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """A valid template renders without any log record."""
    with caplog.at_level(logging.WARNING, logger=metadata_tag_adapter_module.__name__):
        MetadataTagAdapter().render_filename(
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
    """Route the module's no-redirect opener to an oversized fake response."""
    response = _OversizedResponse(_oversized_json(limit))
    monkeypatch.setattr(module.http_openers, "open_without_redirects", CallRecorder(reply=response))
    return response


def test_discogs_http_read_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """The Discogs reader reads one byte past the cap and rejects the oversized body."""
    limit = discogs_module.MAX_API_RESPONSE_BYTES
    response = _patch_urlopen(monkeypatch, discogs_module, limit)
    adapter = DiscogsAdapter(repository=build_memory_repository(), user_token=DISCOGS_STUB_KEY)
    with pytest.raises(ValueError, match="maximum byte size"):
        adapter._default_http_get_json(f"{discogs_module.DISCOGS_BASE_URL}/releases/1", {})
    assert response.requested_sizes == [limit + 1]


def test_musicbrainz_http_read_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """The MusicBrainz reader reads one byte past the cap and rejects the oversized body."""
    limit = musicbrainz_module.MAX_API_RESPONSE_BYTES
    response = _patch_urlopen(monkeypatch, musicbrainz_module, limit)
    adapter = MusicBrainzAdapter(repository=build_memory_repository())
    with pytest.raises(ValueError, match="maximum byte size"):
        adapter._default_http_get_json(f"{musicbrainz_module.MB_BASE_URL}/recording", {})
    assert response.requested_sizes == [limit + 1]


def test_acoustid_http_read_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """The AcoustID reader reads one byte past the cap and rejects the oversized body."""
    limit = acoustid_module.MAX_API_RESPONSE_BYTES
    response = _patch_urlopen(monkeypatch, acoustid_module, limit)
    adapter = ChromaprintAcoustIDAdapter(repository=build_memory_repository(), api_key="synthetic")
    with pytest.raises(ValueError, match="maximum byte size"):
        adapter._default_http_get_json("https://api.acoustid.org/v2/lookup", {})
    assert response.requested_sizes == [limit + 1]


# LOW finding. TXXX frames on repeated ID3 writes.


def test_repeated_mp3_writes_one_txxx(tmp_path: Path) -> None:
    """A second catalog number write replaces the TXXX frame instead of adding one."""
    mp3_path = create_mp3_fixture(tmp_path / "track.mp3")
    adapter = MetadataTagAdapter()
    adapter._write_mp3_tags(mp3_path, {FieldName.CATALOG_NUMBER: "TRIUK014"})
    adapter._write_mp3_tags(mp3_path, {FieldName.CATALOG_NUMBER: "TRIUK015"})
    tag = read_mp3_tag(mp3_path.read_bytes())
    assert tag is not None
    frames = [frame for frame in tag.frames if frame_key(frame) == "TXXX:CATALOGNUMBER"]
    assert len(frames) == 1
    assert decode_txxx(frames[0])[1] == ["TRIUK015"]


# Escaped braces and Discogs availability order.


def test_render_filename_keeps_escaped_braces(caplog: pytest.LogCaptureFixture) -> None:
    """Doubled braces render as literal braces with no fallback warning."""
    with caplog.at_level(logging.WARNING, logger=metadata_tag_adapter_module.__name__):
        rendered = MetadataTagAdapter().render_filename(
            Path("track.mp3"),
            {FieldName.ARTIST: "Deep Dish", FieldName.TITLE: "The Dream"},
            "{artist} - {title} [{{label}}]",
        )
    assert rendered == "Deep Dish - The Dream [{label}].mp3"
    assert caplog.records == []


@pytest.mark.parametrize("template", ["{artist} {", "{artist} }", "{{artist}", "{}"])
def test_unbalanced_filename_braces_use_default(template: str) -> None:
    """Unbalanced or empty braces fall back to the default layout."""
    rendered = MetadataTagAdapter().render_filename(
        Path("track.mp3"),
        {FieldName.ARTIST: "Deep Dish", FieldName.TITLE: "The Dream"},
        template,
    )
    assert rendered == "Deep Dish - The Dream.mp3"


def test_discogs_unavailable_returns_empty() -> None:
    """Without a token the adapter reports unavailable and fetches nothing for any id."""
    adapter = DiscogsAdapter(
        repository=build_memory_repository(), user_token=None, min_interval_sec=0.0
    )
    assert adapter.is_available()[0] is False
    assert adapter.fetch("../database/search") == []


def test_fpcalc_rejects_bad_digest_early(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller digest that differs from the pin raises before any download."""
    monkeypatch.setattr(fpcalc_module.platform, "system", CallRecorder(reply="Windows"))
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


def test_tar_read_stays_within_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A TAR entry larger than the binary cap raises and leaves no fpcalc file."""
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


def test_tar_entry_at_cap_installs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A TAR entry exactly at the binary cap installs whole."""
    payload = b"y" * 64
    archive_bytes = _build_fpcalc_tar(payload)
    monkeypatch.setattr(fpcalc_module, "MAX_FPCALC_BINARY_BYTES", len(payload))
    monkeypatch.setattr(fpcalc_module.platform, "system", CallRecorder(reply="Darwin"))
    bootstrap = FpcalcBootstrapService(install_dir=tmp_path / "bin")
    installed = bootstrap.install_from_archive_bytes(
        archive_bytes=archive_bytes,
        expected_sha256=hashlib.sha256(archive_bytes).hexdigest(),
        user_consented=True,
    )
    assert installed.read_bytes() == payload


@pytest.mark.parametrize("bad_digest", [" ", "0", "g" * 64, "a" * 63, "a" * 65])
def test_archive_install_rejects_malformed_digest(tmp_path: Path, bad_digest: str) -> None:
    """A digest that is not 64 hex digits raises and creates no install folder."""
    bootstrap = FpcalcBootstrapService(install_dir=tmp_path / "bin")
    with pytest.raises(ValueError, match="64 hexadecimal"):
        bootstrap.install_from_archive_bytes(
            archive_bytes=_build_fpcalc_zip(FAKE_BINARY),
            expected_sha256=bad_digest,
            user_consented=True,
        )
    assert not (tmp_path / "bin").exists()


@pytest.mark.parametrize("model_name", ["a/x..:b", "a..b:c", "model:v1..2"])
def test_pull_model_rejects_consecutive_dots(model_name: str) -> None:
    """A name with two dots in a row raises before any command runs."""
    commands: list[list[str]] = []
    service = OllamaBootstrapService(runner=_command_recorder(commands))
    with pytest.raises(ValueError, match="model name"):
        service.pull_model(model_name, user_consented=True)
    assert commands == []
