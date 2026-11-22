"""Unit tests for Milestone 5: Discogs, LLM Validator, Ollama, Artwork, and Reconciliation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from PySide6.QtGui import QColor, QImage

from infiniteprep.adapters.catalog.acrcloud_optin import ACRCloudOptInAdapter
from infiniteprep.adapters.catalog.beatport_stub import BeatportStubAdapter
from infiniteprep.adapters.catalog.discogs import DISCOGS_TTL_SEC, DiscogsAdapter
from infiniteprep.adapters.llm.ollama_bootstrap import OllamaBootstrapService
from infiniteprep.adapters.llm.ollama_openai import OllamaOpenAIReasoningAdapter
from infiniteprep.adapters.llm.validator import ClosedWorldValidator
from infiniteprep.db import repositories as repositories_module
from infiniteprep.db.connection import DatabaseManager
from infiniteprep.db.repositories import Repository
from infiniteprep.domain.enums import (
    EntityType,
    FieldName,
    KeyMode,
    PitchClass,
    SourceType,
)
from infiniteprep.domain.models import AnalysisResult, CandidateMatch, ReleaseDetails
from infiniteprep.domain.ports import MetadataSearchQuery
from infiniteprep.services.artwork_service import ArtworkService
from infiniteprep.services.import_service import ImportService
from infiniteprep.services.matching_engine import MatchingEngine, parse_dj_title_and_mix
from infiniteprep.services.reconciliation import (
    ReconciliationService,
    detect_vinyl_pitch_drift,
)
from tests.fixtures.audio_factory import create_wav_fixture
from tests.fixtures.call_recorder import CallRecorder

DISCOGS_STUB_KEY = "synthetic-discogs-key"


def test_discogs_adapter_cache_and_stubs() -> None:
    """Verify DiscogsAdapter parses vinyl positions and caches responses with 6-hour TTL."""
    db = DatabaseManager(":memory:")
    repo = Repository(db)
    calls = 0

    def fake_discogs_http(url: str, _params: dict[str, str]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if "database/search" in url:
            return {
                "results": [
                    {
                        "id": 5001,
                        "title": "Deep Dish - The Dream",
                        "year": "1994",
                        "country": "UK",
                        "label": ["Tribal America"],
                        "catno": "TRIUK014",
                        "format": ["Vinyl", '12"', "33 1/3 RPM"],
                        "genre": ["Electronic"],
                        "style": ["Progressive House"],
                    }
                ]
            }
        return {}

    mock_key = "test-discogs-key"
    discogs = DiscogsAdapter(
        repository=repo,
        user_token=mock_key,
        http_get_json=fake_discogs_http,
        min_interval_sec=0.0,
    )
    assert discogs.is_available()[0] is True
    query = MetadataSearchQuery(artist="Deep Dish", title="The Dream", catalog_number="TRIUK014")
    res1 = discogs.search(query, file_id="file-1")
    res2 = discogs.search(query, file_id="file-1")
    assert len(res1) == 1
    assert len(res2) == 1
    assert calls == 1
    assert res1[0].release_details.catalog_number == "TRIUK014"
    assert res1[0].release_details.label == "Tribal America"

    acr = ACRCloudOptInAdapter(enabled_by_user=False)
    assert acr.is_available()[0] is False
    bp = BeatportStubAdapter()
    assert bp.is_available()[0] is False
    db.close()


@pytest.mark.parametrize(
    ("read_offset_sec", "expected_calls"),
    [(DISCOGS_TTL_SEC - 1, 1), (DISCOGS_TTL_SEC + 1, 2)],
)
def test_discogs_cache_serves_until_the_ttl_ends(
    monkeypatch: pytest.MonkeyPatch, read_offset_sec: int, expected_calls: int
) -> None:
    """A repeat search reads the cache inside the 6-hour TTL and fetches again after it."""
    calls = 0

    def count_search(_url: str, _params: dict[str, str]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"results": [{"id": 5001, "title": "Deep Dish - The Dream", "year": "1994"}]}

    with DatabaseManager(":memory:") as db:
        discogs = DiscogsAdapter(
            repository=Repository(db),
            user_token=DISCOGS_STUB_KEY,
            http_get_json=count_search,
            min_interval_sec=0.0,
        )
        query = MetadataSearchQuery(artist="Deep Dish", title="The Dream")
        discogs.search(query, file_id="file-1")
        # The cache compares stored expiry text with this clock. A later reading
        # stands in for elapsed time without a real wait.
        later = datetime.now(UTC) + timedelta(seconds=read_offset_sec)
        monkeypatch.setattr(
            repositories_module,
            "utc_now_iso",
            CallRecorder(reply=later.isoformat(timespec="microseconds")),
        )
        discogs.search(query, file_id="file-1")
    assert calls == expected_calls


def test_closed_world_validator_ollama_consent() -> None:
    """Verify ClosedWorldValidator rejects hallucinated IDs/values and Ollama requires consent."""
    validator = ClosedWorldValidator()
    allowed = [
        {"id": "cand-1", "value": "Sharam's Deep Dish Dreamscape Mix"},
        {"id": "cand-2", "value": "Danny Tenaglia Dub"},
    ]

    valid_output = {
        "selected_candidate_id": "cand-1",
        "proposed_value": "Sharam's Deep Dish Dreamscape Mix",
        "rationale": "Duration 480s matches cand-1.",
    }
    is_ok, err = validator.validate_response(valid_output, allowed)
    assert is_ok is True
    assert err is None

    hallucinated_output = {
        "selected_candidate_id": "cand-999",
        "proposed_value": "Invented AI Remix",
        "rationale": "Guessed remix name.",
    }
    is_ok_bad, err_bad = validator.validate_response(hallucinated_output, allowed)
    assert is_ok_bad is False
    assert err_bad is not None

    # Verify OllamaOpenAIReasoningAdapter discards hallucinated LLM output
    def fake_llm_post(_url: str, _body: dict[str, Any]) -> dict[str, Any]:
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"selected_candidate_id": "cand-999", '
                        '"proposed_value": "Hallucinated Mix"}'
                    }
                }
            ]
        }

    llm = OllamaOpenAIReasoningAdapter(
        base_url="http://127.0.0.1:11434/v1",
        model="qwen2.5:3b",
        http_post_json=fake_llm_post,
    )
    arb = llm.arbitrate({"allowed_candidates": allowed})
    assert arb["selected_candidate_id"] is None
    assert arb["proposed_value"] is None
    assert "discarded" in arb["rationale"].lower()

    # Verify OllamaBootstrapService enforces user_consented=True
    bootstrap = OllamaBootstrapService()
    with pytest.raises(PermissionError, match="consent"):
        bootstrap.install_ollama(user_consented=False)
    with pytest.raises(PermissionError, match="consent"):
        bootstrap.pull_model("qwen2.5:3b", user_consented=False)


def test_artwork_resized_to_xdj_safe_jpeg(tmp_path: Path) -> None:
    """Verify ArtworkService scales 1400x1400 cover art down to <= 800x800 JPEG for XDJ-RX3."""
    big_img = QImage(1400, 1400, QImage.Format.Format_RGB32)
    big_img.fill(QColor("#2563eb"))
    src_png = tmp_path / "giant_cover.png"
    big_img.save(str(src_png), "PNG")

    art_svc = ArtworkService()
    jpeg_bytes = art_svc.normalize_image_bytes(src_png.read_bytes(), max_dimension=800)
    assert len(jpeg_bytes) > 100

    loaded = QImage.fromData(jpeg_bytes, "JPG")
    assert not loaded.isNull()
    assert loaded.width() <= 800
    assert loaded.height() <= 800

    # Verify decompression-bomb / oversized dimension rejection
    bomb_img = QImage(4200, 100, QImage.Format.Format_RGB32)
    bomb_png = tmp_path / "bomb.png"
    bomb_img.save(str(bomb_png), "PNG")
    with pytest.raises(ValueError, match="decompression-bomb"):
        art_svc.normalize_image_bytes(bomb_png.read_bytes())


def test_reconciliation_rules_and_pitch_drift(tmp_path: Path) -> None:
    """Check DJ mix parsing, pitch drift detection, and the proposals of a full reconcile."""
    parsed_title, parsed_mix, parsed_remixer = parse_dj_title_and_mix(
        "The Dream (Sharam's Deep Dish Dreamscape Mix)"
    )
    assert parsed_title == "The Dream"
    assert parsed_mix == "Sharam's Deep Dish Dreamscape Mix"
    assert parsed_remixer == "Sharam"

    # Vinyl pitch drift detection: catalog 240.0s vs rip 225.8s (~+6.3% faster at +6% pitch)
    drift_warn = detect_vinyl_pitch_drift(
        file_duration_sec=225.8,
        catalog_duration_sec=240.0,
        analyzed_bpm=127.2,
    )
    assert drift_warn is not None
    assert "pitch" in drift_warn.lower()

    # A full reconcile over a recording, a release, and a local analysis. The rule tests below
    # check Rules 1, 2, and 5 one at a time.
    db = DatabaseManager(":memory:")
    repo = Repository(db)
    importer = ImportService(repo)
    wav_path = create_wav_fixture(
        tmp_path / "A1_the_dream.wav",
        duration_sec=4.0,
        tags={"artist": "Deep Dish", "title": "The Dream"},
    )
    asset = importer.import_paths([wav_path], source_type=SourceType.VINYL).imported_files[0]

    # Recording candidate (high confidence on artist/title/mix, no label)
    rec_cand = CandidateMatch(
        id="c-rec-1",
        file_id=asset.id,
        provider="acoustid",
        provider_entity_id="acid-1",
        entity_type=EntityType.RECORDING,
        artist="Deep Dish Presents Prana",
        title="The Dream (Sharam's Deep Dish Dreamscape Mix)",
        duration=4.0,
        match_score=0.95,
    )
    # Release candidate (authoritative for label, catalog_number, vinyl_position)
    rel_cand = CandidateMatch(
        id="c-rel-1",
        file_id=asset.id,
        provider="discogs",
        provider_entity_id="rel-5001",
        entity_type=EntityType.RELEASE,
        artist="Deep Dish Presents Prana",
        title="The Dream",
        mix_name="Sharam's Deep Dish Dreamscape Mix",
        remixer="Sharam",
        duration=4.0,
        release_details=ReleaseDetails(
            release_title="The Dream 12-Inch",
            label="Tribal America",
            catalog_number="TRIUK014",
            vinyl_position="A1",
            year="1994",
            genre="Progressive House",
        ),
        match_score=0.92,
    )
    repo.upsert_candidate_match(rec_cand)
    repo.upsert_candidate_match(rel_cand)

    # Local analyzer result (Rule 5: Local audio analysis is authoritative for BPM and Key)
    ana = AnalysisResult(
        id="ana-rec-1",
        file_id=asset.id,
        analyzer_name="local_dsp",
        analyzer_version="0.1.0",
        configuration={},
        bpm=124.0,
        key_tonic=PitchClass.F_SHARP,
        key_mode=KeyMode.MINOR,
        analysis_strength=0.91,
    )
    repo.upsert_analysis_result(ana)

    reconciler = ReconciliationService(repository=repo, matching_engine=MatchingEngine())
    proposals = reconciler.reconcile_file(asset.id)
    assert len(proposals) >= 6

    by_field = {p.field_name: p for p in repo.list_field_proposals(asset.id)}
    assert by_field[FieldName.MIX_NAME.value].proposed_value == "Sharam's Deep Dish Dreamscape Mix"
    assert by_field[FieldName.LABEL.value].proposed_value == "Tribal America"
    assert by_field[FieldName.CATALOG_NUMBER.value].proposed_value == "TRIUK014"
    assert by_field[FieldName.BPM.value].proposed_value == "124.0"
    assert by_field[FieldName.BPM.value].provider == "local_dsp"
    db.close()


RULE_TEST_ARTIST = "Deep Dish"
RULE_TEST_TITLE = "The Dream"
RULE_TEST_LABEL = "Tribal America"
RULE_TEST_CATALOG = "TRIUK014"
RULE_TEST_SCORE = 0.95
LOCAL_ANALYZER = "local_dsp"


def import_rule_track(tmp_path: Path, repo: Repository) -> str:
    """Import one tagged WAV and return its file id."""
    wav_path = create_wav_fixture(
        tmp_path / "rule_track.wav",
        duration_sec=4.0,
        tags={"artist": RULE_TEST_ARTIST, "title": RULE_TEST_TITLE},
    )
    return ImportService(repo).import_paths([wav_path]).imported_files[0].id


def build_recording_candidate(file_id: str, catalog_number: str | None) -> CandidateMatch:
    """Return a MusicBrainz recording candidate that carries a label in its release details."""
    return CandidateMatch(
        id="c-rule-rec",
        file_id=file_id,
        provider="musicbrainz",
        provider_entity_id="mb-rec-1",
        entity_type=EntityType.RECORDING,
        artist=RULE_TEST_ARTIST,
        title=RULE_TEST_TITLE,
        duration=4.0,
        release_details=ReleaseDetails(label=RULE_TEST_LABEL, catalog_number=catalog_number),
        match_score=RULE_TEST_SCORE,
    )


def reconcile_proposal_providers(repo: Repository, file_id: str) -> dict[str, list[str]]:
    """Reconcile one file and return the providers of the proposals for each field."""
    ReconciliationService(repository=repo, matching_engine=MatchingEngine()).reconcile_file(file_id)
    providers: dict[str, list[str]] = {}
    for proposal in repo.list_field_proposals(file_id):
        providers.setdefault(proposal.field_name, []).append(proposal.provider)
    return providers


def test_rule_1_recording_gives_no_label(tmp_path: Path) -> None:
    """Rule 1: a recording candidate without a catalog number gives no label proposal."""
    with DatabaseManager(":memory:") as db:
        repo = Repository(db)
        file_id = import_rule_track(tmp_path, repo)
        repo.upsert_candidate_match(build_recording_candidate(file_id, catalog_number=None))
        providers = reconcile_proposal_providers(repo, file_id)
    assert FieldName.ARTIST.value in providers
    assert FieldName.LABEL.value not in providers
    assert FieldName.CATALOG_NUMBER.value not in providers


def test_rule_2_catalog_number_admits_label(tmp_path: Path) -> None:
    """Rule 2: a recording with a verified catalog number proposes the label and catalog."""
    with DatabaseManager(":memory:") as db:
        repo = Repository(db)
        file_id = import_rule_track(tmp_path, repo)
        candidate = build_recording_candidate(file_id, catalog_number=RULE_TEST_CATALOG)
        repo.upsert_candidate_match(candidate)
        providers = reconcile_proposal_providers(repo, file_id)
    assert providers[FieldName.LABEL.value] == ["musicbrainz"]
    assert providers[FieldName.CATALOG_NUMBER.value] == ["musicbrainz"]


def test_rule_5_bpm_and_key_from_analysis(tmp_path: Path) -> None:
    """Rule 5: BPM and key proposals come from the local analyzer and never from a candidate."""
    with DatabaseManager(":memory:") as db:
        repo = Repository(db)
        file_id = import_rule_track(tmp_path, repo)
        repo.upsert_candidate_match(
            build_recording_candidate(file_id, catalog_number=RULE_TEST_CATALOG)
        )
        repo.upsert_analysis_result(
            AnalysisResult(
                id="ana-rule-5",
                file_id=file_id,
                analyzer_name=LOCAL_ANALYZER,
                analyzer_version="0.1.0",
                configuration={},
                bpm=124.0,
                key_tonic=PitchClass.F_SHARP,
                key_mode=KeyMode.MINOR,
                analysis_strength=0.91,
            )
        )
        providers = reconcile_proposal_providers(repo, file_id)
    assert providers[FieldName.BPM.value] == [LOCAL_ANALYZER]
    assert providers[FieldName.KEY.value] == [LOCAL_ANALYZER]
