"""Unit tests for Milestone 3: Settings, Credentials, fpcalc, AcoustID, MB, and Jobs."""

from __future__ import annotations

import hashlib
import io
import tarfile
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from keyring.errors import KeyringError

from infiniteprep.adapters.analyzers.mock_analyzer import MockAudioAnalyzerAdapter
from infiniteprep.adapters.catalog.musicbrainz import MusicBrainzAdapter, escape_lucene_term
from infiniteprep.adapters.fingerprint.chromaprint_acoustid import ChromaprintAcoustIDAdapter
from infiniteprep.adapters.fingerprint.fpcalc_bootstrap import FpcalcBootstrapService
from infiniteprep.config.credentials import CredentialStore, CredentialStoreError
from infiniteprep.config.settings import AppSettings, SettingsManager, validate_endpoint_url
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import EntityType, SourceType, TaskStatus
from infiniteprep.domain.ports import MetadataSearchQuery
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.job_orchestrator import JobOrchestrator
from tests.fixtures.audio_factory import create_wav_fixture


def test_settings_and_credential_store_with_ssrf_validation(tmp_path: Path) -> None:
    """Verify JSON settings, strict SSRF endpoint validation, and fail-closed CredentialStore."""
    cfg_path = tmp_path / "settings.json"
    mgr = SettingsManager(cfg_path)
    loaded = mgr.load()
    assert isinstance(loaded, AppSettings)
    assert loaded.ollama_model == "qwen2.5:3b"

    loaded.ollama_model = "qwen2.5:7b"
    loaded.enable_discogs = True
    mgr.save(loaded)
    reloaded = mgr.load()
    assert reloaded.ollama_model == "qwen2.5:7b"
    assert reloaded.enable_discogs is True

    # SSRF validation: allow localhost HTTP and remote HTTPS, block private/reserved/metadata
    assert validate_endpoint_url("http://127.0.0.1:11434/v1")[0] is True
    assert validate_endpoint_url("https://api.openai.com/v1")[0] is True
    assert validate_endpoint_url("http://169.254.169.254/latest/meta-data")[0] is False
    assert validate_endpoint_url("https://10.0.0.1/v1")[0] is False
    assert validate_endpoint_url("https://192.168.1.1/v1")[0] is False
    assert validate_endpoint_url("https://172.16.0.1/v1")[0] is False
    assert validate_endpoint_url("https://100.64.0.1/v1")[0] is False
    assert validate_endpoint_url("https://127.0.0.1/v1")[0] is False
    assert validate_endpoint_url("https://[::1]/v1")[0] is False
    assert validate_endpoint_url("https://metadata.google.internal/v1")[0] is False
    assert validate_endpoint_url("https://service.internal/v1")[0] is False
    assert validate_endpoint_url("https://user:pass@api.openai.com/v1")[0] is False
    assert validate_endpoint_url("file:///etc/passwd")[0] is False

    # Memory backend explicitly enabled
    creds = CredentialStore(use_memory_backend=True)
    creds.set_secret("acoustid_api_key", "secret-acoustid-123")
    assert creds.get_secret("acoustid_api_key") == "secret-acoustid-123"
    creds.delete_secret("acoustid_api_key")
    assert creds.get_secret("acoustid_api_key") is None

    # Keyring backend fails closed (does NOT fall back to plaintext memory)
    strict_creds = CredentialStore(use_memory_backend=False)
    with (
        patch("infiniteprep.config.credentials.keyring.set_password", side_effect=KeyringError),
        pytest.raises(CredentialStoreError, match="refusing plaintext fallback"),
    ):
        strict_creds.set_secret("discogs_token", "do-not-store-in-ram")
    assert strict_creds._memory.get("discogs_token") is None


def test_fpcalc_bootstrap_requires_explicit_user_consent_and_verifies_sha256(
    tmp_path: Path,
) -> None:
    """Verify FpcalcBootstrapService refuses unconsented downloads and extracts ZIP and TAR.GZ."""
    bin_dir = tmp_path / "bin"
    bootstrap = FpcalcBootstrapService(install_dir=bin_dir)

    with pytest.raises(PermissionError, match="consent"):
        bootstrap.install_fpcalc(user_consented=False)

    # 1. Create a synthetic ZIP archive containing a dummy fpcalc binary
    archive_bytes_io = tmp_path / "chromaprint_mock.zip"
    dummy_exe_name = bootstrap.binary_filename()
    dummy_payload = b"#!/bin/sh\necho DURATION=240\necho FINGERPRINT=AQAAE\n"
    with zipfile.ZipFile(archive_bytes_io, "w") as zf:
        zf.writestr(f"chromaprint-fpcalc/{dummy_exe_name}", dummy_payload)

    raw_zip_bytes = archive_bytes_io.read_bytes()
    expected_sha256 = hashlib.sha256(raw_zip_bytes).hexdigest()

    installed_path = bootstrap.install_from_archive_bytes(
        archive_bytes=raw_zip_bytes,
        expected_sha256=expected_sha256,
        user_consented=True,
    )
    assert installed_path.exists()
    assert bootstrap.resolve_fpcalc_path() == installed_path.resolve()

    # 2. Verify macOS/Linux .tar.gz archive extraction
    tar_buf = io.BytesIO()
    with tarfile.open(fileobj=tar_buf, mode="w:gz") as tf:
        tar_info = tarfile.TarInfo(name=f"chromaprint-fpcalc-1.5.1/{dummy_exe_name}")
        tar_info.size = len(dummy_payload)
        tf.addfile(tar_info, io.BytesIO(dummy_payload))
    raw_tar_bytes = tar_buf.getvalue()
    tar_sha256 = hashlib.sha256(raw_tar_bytes).hexdigest()

    tar_bin_dir = tmp_path / "bin_tar"
    tar_bootstrap = FpcalcBootstrapService(install_dir=tar_bin_dir)
    tar_installed = tar_bootstrap.install_from_archive_bytes(
        archive_bytes=raw_tar_bytes,
        expected_sha256=tar_sha256,
        user_consented=True,
    )
    assert tar_installed.exists()
    assert tar_installed.read_bytes() == dummy_payload

    # Tampered hash must raise ValueError
    with pytest.raises(ValueError, match="SHA-256"):
        bootstrap.install_from_archive_bytes(
            archive_bytes=raw_zip_bytes,
            expected_sha256="0" * 64,
            user_consented=True,
        )


def test_chromaprint_acoustid_and_musicbrainz_adapters_with_cache(tmp_path: Path) -> None:
    """Verify ChromaprintAcoustIDAdapter and MusicBrainzAdapter use SQLite cache."""
    db = DatabaseManager(":memory:")
    repo = Repository(db)
    wav_path = create_wav_fixture(tmp_path / "track.wav")

    def fake_fpcalc_runner(audio_path: Path) -> tuple[float, str]:
        assert audio_path == wav_path
        return 480.0, "AQAA_SYNTHETIC_FINGERPRINT"

    def fake_acoustid_http(_url: str, _params: dict[str, str]) -> dict[str, Any]:
        return {
            "status": "ok",
            "results": [
                {
                    "id": "acoustid-999",
                    "score": 0.96,
                    "recordings": [
                        {
                            "id": "mb-rec-101",
                            "title": "The Dream (Sharam's Deep Dish Dreamscape Mix)",
                            "duration": 480,
                            "artists": [{"name": "Deep Dish"}],
                            "releasegroups": [{"id": "mb-rg-101", "title": "The Dream"}],
                        }
                    ],
                }
            ],
        }

    acoustid = ChromaprintAcoustIDAdapter(
        repository=repo,
        api_key="test-acoustid-key",
        fpcalc_runner=fake_fpcalc_runner,
        http_get_json=fake_acoustid_http,
    )
    assert acoustid.is_available()[0] is True
    fp_res = acoustid.identify(wav_path, file_id="file-1")
    assert fp_res.raw_fingerprint == "AQAA_SYNTHETIC_FINGERPRINT"
    assert len(fp_res.matches) == 1
    assert fp_res.matches[0].confidence == 0.96
    assert fp_res.matches[0].recording_ids == ["mb-rec-101"]

    http_calls = 0

    def fake_mb_http(url: str, _params: dict[str, str]) -> dict[str, Any]:
        nonlocal http_calls
        http_calls += 1
        if "recording" in url:
            return {
                "recordings": [
                    {
                        "id": "mb-rec-101",
                        "title": "The Dream (Sharam's Deep Dish Dreamscape Mix)",
                        "length": 480000,
                        "artist-credit": [{"name": "Deep Dish"}],
                        "releases": [
                            {
                                "id": "mb-rel-202",
                                "title": "The Dream",
                                "date": "1994",
                                "country": "GB",
                                "label-info": [
                                    {
                                        "catalog-number": "TRIUK014",
                                        "label": {"name": "Tribal America"},
                                    }
                                ],
                                "media": [
                                    {
                                        "format": '12" Vinyl',
                                        "position": 1,
                                        "track": [{"number": "A1"}],
                                    }
                                ],
                            }
                        ],
                    }
                ]
            }
        return {"releases": []}

    mb = MusicBrainzAdapter(repository=repo, http_get_json=fake_mb_http, min_interval_sec=0.0)
    query = MetadataSearchQuery(artist="Deep Dish", title="The Dream", duration=480.0)
    matches_first = mb.search(query, file_id="file-1")
    matches_cached = mb.search(query, file_id="file-1")
    assert len(matches_first) == 1
    assert len(matches_cached) == 1
    assert http_calls == 1  # Second call served from SQLite provider_cache
    assert matches_first[0].release_details.catalog_number == "TRIUK014"
    assert matches_first[0].release_details.label == "Tribal America"
    assert escape_lucene_term('DJ "Quote" + Mix') == r"DJ \"Quote\" \+ Mix"
    db.close()


def test_job_orchestrator_executes_and_persists_candidates_and_analysis(tmp_path: Path) -> None:
    """Verify JobOrchestrator runs fingerprint, catalog search, and audio analysis idempotently."""
    db = DatabaseManager(":memory:")
    repo = Repository(db)
    importer = ImportService(repo)
    wav_path = create_wav_fixture(
        tmp_path / "01_deep_dish.wav",
        tags={"artist": "Deep Dish", "title": "The Dream"},
    )
    report = importer.import_paths([wav_path], source_type=SourceType.VINYL)
    asset = report.imported_files[0]

    orchestrator = JobOrchestrator(
        repository=repo,
        audio_analyzer=MockAudioAnalyzerAdapter(),
    )
    summary = orchestrator.process_files([asset.id])
    assert summary.completed_files == 1
    assert summary.failed_files == 0

    refreshed = repo.get_file_by_id(asset.id)
    assert refreshed is not None
    assert refreshed.task_status == TaskStatus.COMPLETED
    assert len(repo.list_candidate_matches(asset.id, EntityType.RELEASE)) >= 1
    assert len(repo.list_analysis_results(asset.id)) == 1
    db.close()
