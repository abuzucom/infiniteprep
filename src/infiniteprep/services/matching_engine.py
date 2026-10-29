"""DJ-aware string normalization, mix/remixer parsing, and candidate scoring engine."""

from __future__ import annotations

import re
from difflib import SequenceMatcher
from typing import Final

from infiniteprep.domain.models import CandidateMatch, FileAsset

MIX_KEYWORDS: Final[tuple[str, ...]] = (
    "mix",
    "remix",
    "dub",
    "edit",
    "version",
    "vocal",
    "instrumental",
    "club",
    "extended",
    "rework",
    "bootleg",
    "vip",
)
PAREN_PATTERN: Final[re.Pattern[str]] = re.compile(r"^([^\(\[\-]+?)\s*[\(\[]([^\)\]]+)[\)\]]$")
POSSESSIVE_REMIXER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^([A-Za-z0-9 &\.]+?)'s\s+.+$")
BY_REMIXER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^([A-Za-z0-9 &\.]+?)\s+(?:remix|mix|dub|rework|edit)$", re.IGNORECASE
)
PAREN_BASE_GROUP: Final[int] = 1
PAREN_MIX_GROUP: Final[int] = 2
SIMILARITY_PRECISION_DIGITS: Final[int] = 4
DEFAULT_MISSING_DURATION_SCORE: Final[float] = 0.75
DURATION_EXACT_THRESHOLD_SEC: Final[float] = 2.0
DURATION_LARGE_DIFF_THRESHOLD_SEC: Final[float] = 30.0
MIN_DURATION_SCORE: Final[float] = 0.20
DURATION_FALLOFF_DIVISOR_SEC: Final[float] = 35.0
DEFAULT_MISSING_FIELD_SIMILARITY: Final[float] = 0.85
WEIGHT_PROVIDER: Final[float] = 0.30
WEIGHT_ARTIST: Final[float] = 0.25
WEIGHT_TITLE: Final[float] = 0.25
WEIGHT_DURATION: Final[float] = 0.20


def parse_dj_title_and_mix(raw_title: str) -> tuple[str, str | None, str | None]:
    """Split 'Track Title (Remixer Mix Name)' into (base_title, mix_name, remixer)."""
    cleaned = raw_title.strip()
    if not cleaned:
        return "", None, None
    match = PAREN_PATTERN.match(cleaned)
    if not match:
        return cleaned, None, None
    base_title = match.group(PAREN_BASE_GROUP).strip()
    inside = match.group(PAREN_MIX_GROUP).strip()
    lower_inside = inside.lower()
    if not any(kw in lower_inside for kw in MIX_KEYWORDS):
        return cleaned, None, None

    remixer = _infer_remixer_from_mix_name(inside)
    return base_title, inside, remixer


def _infer_remixer_from_mix_name(mix_name: str) -> str | None:
    """Extract remixer name from 'Sharam's ... Mix' or 'Danny Tenaglia Dub'."""
    poss = POSSESSIVE_REMIXER_PATTERN.match(mix_name)
    if poss:
        return poss.group(PAREN_BASE_GROUP).strip()
    generic_prefixes = {"original", "extended", "club", "radio", "vocal", "instrumental", "dub"}
    by_match = BY_REMIXER_PATTERN.match(mix_name)
    if by_match:
        candidate_remixer = by_match.group(PAREN_BASE_GROUP).strip()
        if candidate_remixer.lower() not in generic_prefixes:
            return candidate_remixer
    return None


def string_similarity(left: str | None, right: str | None) -> float:
    """Compute normalized case-insensitive similarity in [0.0, 1.0]."""
    if not left or not right:
        return 0.0
    a = left.strip().lower()
    b = right.strip().lower()
    if a == b:
        return 1.0
    return round(SequenceMatcher(None, a, b).ratio(), SIMILARITY_PRECISION_DIGITS)


def duration_similarity(file_dur: float | None, cand_dur: float | None) -> float:
    """Score duration agreement in [0.0, 1.0], penalizing large discrepancies."""
    if file_dur is None or cand_dur is None or file_dur <= 0.0 or cand_dur <= 0.0:
        return DEFAULT_MISSING_DURATION_SCORE
    diff = abs(file_dur - cand_dur)
    if diff <= DURATION_EXACT_THRESHOLD_SEC:
        return 1.0
    if diff >= DURATION_LARGE_DIFF_THRESHOLD_SEC:
        return MIN_DURATION_SCORE
    return round(
        max(MIN_DURATION_SCORE, 1.0 - (diff / DURATION_FALLOFF_DIVISOR_SEC)),
        SIMILARITY_PRECISION_DIGITS,
    )


class MatchingEngine:
    """Scores CandidateMatches against a FileAsset's existing tags and duration."""

    def compute_application_match_score(self, asset: FileAsset, candidate: CandidateMatch) -> float:
        """Compute composite application_match_score in [0.0, 1.0] separately from provider."""
        std = asset.original_tags.get("standard_tags", {})
        cur_artist = std.get("artist")
        cur_title = std.get("title")

        art_sim = (
            string_similarity(cur_artist, candidate.artist)
            if cur_artist
            else DEFAULT_MISSING_FIELD_SIMILARITY
        )
        base_cur, _, _ = parse_dj_title_and_mix(cur_title or "")
        base_cand, _, _ = parse_dj_title_and_mix(candidate.title or "")
        tit_sim = (
            string_similarity(base_cur, base_cand) if base_cur else DEFAULT_MISSING_FIELD_SIMILARITY
        )
        dur_sim = duration_similarity(asset.duration, candidate.duration)
        prov_score = (
            candidate.match_score
            if candidate.match_score > 0.0
            else DEFAULT_MISSING_FIELD_SIMILARITY
        )

        weighted = (
            (WEIGHT_PROVIDER * prov_score)
            + (WEIGHT_ARTIST * art_sim)
            + (WEIGHT_TITLE * tit_sim)
            + (WEIGHT_DURATION * dur_sim)
        )
        return round(max(0.0, min(1.0, weighted)), SIMILARITY_PRECISION_DIGITS)
