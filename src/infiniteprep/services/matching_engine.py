"""DJ-aware string normalization, mix/remixer parsing, and candidate scoring engine."""

from __future__ import annotations

import re
from typing import Final

from infiniteprep.domain.models import CandidateMatch, FileAsset
from infiniteprep.domain.similarity import SIMILARITY_PRECISION_DIGITS, string_similarity

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
# Each capture before a whitespace run ends on a non-space character. A lazy capture that can
# also hold spaces splits one run many ways and backtracks quadratically on long runs.
PAREN_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^([^\(\[\-]*[^\(\[\-\s])\s*[\(\[]([^\)\]]+)[\)\]]$"
)
POSSESSIVE_REMIXER_PATTERN: Final[re.Pattern[str]] = re.compile(r"^([A-Za-z0-9 &\.]+?)'s\s+.+$")
BY_REMIXER_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"^([A-Za-z0-9 &.]*[A-Za-z0-9&.])\s+(?:remix|mix|dub|rework|edit)$", re.IGNORECASE
)
# A keyword counts only as a whole word. "Dublin" holds "dub" and names no mix.
MIX_KEYWORD_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\b(?:" + "|".join(MIX_KEYWORDS) + r")\b", re.IGNORECASE
)
# Words that describe a mix. A phrase built only from these words names no remixer.
MIX_DESCRIPTOR_WORDS: Final[frozenset[str]] = frozenset(
    {
        *MIX_KEYWORDS,
        "original",
        "radio",
        "full",
        "long",
        "short",
        "main",
        "album",
        "single",
        "inch",
        "the",
        "a",
    }
)
# Stems of common contractions. "It's Alright Mix" holds no possessive remixer.
CONTRACTION_STEMS: Final[frozenset[str]] = frozenset(
    {"it", "let", "that", "what", "he", "she", "there", "here", "who", "where"}
)
PAREN_BASE_GROUP: Final[int] = 1
PAREN_MIX_GROUP: Final[int] = 2
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
    if MIX_KEYWORD_PATTERN.search(inside) is None:
        return cleaned, None, None

    remixer = _infer_remixer_from_mix_name(inside)
    return base_title, inside, remixer


def _infer_remixer_from_mix_name(mix_name: str) -> str | None:
    """Extract remixer name from 'Sharam's ... Mix' or 'Danny Tenaglia Dub'."""
    possessive_match = POSSESSIVE_REMIXER_PATTERN.match(mix_name)
    if possessive_match:
        owner = possessive_match.group(PAREN_BASE_GROUP).strip()
        return None if owner.lower() in CONTRACTION_STEMS else owner
    by_match = BY_REMIXER_PATTERN.match(mix_name)
    if by_match:
        candidate_remixer = by_match.group(PAREN_BASE_GROUP).strip()
        if not _is_mix_descriptor_phrase(candidate_remixer):
            return candidate_remixer
    return None


def _is_mix_descriptor_phrase(phrase: str) -> bool:
    """Return whether every word of the phrase is a mix descriptor or a number."""
    words = phrase.lower().split()
    return all(word in MIX_DESCRIPTOR_WORDS or word.isdigit() for word in words)


def duration_similarity(file_duration: float | None, candidate_duration: float | None) -> float:
    """Score duration agreement in [0.0, 1.0], penalizing large discrepancies."""
    if (
        file_duration is None
        or candidate_duration is None
        or file_duration <= 0.0
        or candidate_duration <= 0.0
    ):
        return DEFAULT_MISSING_DURATION_SCORE
    diff = abs(file_duration - candidate_duration)
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
        standard_tags = asset.original_tags.get("standard_tags", {})
        current_artist = standard_tags.get("artist")
        current_title = standard_tags.get("title")

        artist_score = (
            string_similarity(current_artist, candidate.artist)
            if current_artist
            else DEFAULT_MISSING_FIELD_SIMILARITY
        )
        base_current_title, _, _ = parse_dj_title_and_mix(current_title or "")
        base_candidate_title, _, _ = parse_dj_title_and_mix(candidate.title or "")
        title_score = (
            string_similarity(base_current_title, base_candidate_title)
            if base_current_title
            else DEFAULT_MISSING_FIELD_SIMILARITY
        )
        duration_score = duration_similarity(asset.duration, candidate.duration)
        provider_score = (
            candidate.match_score
            if candidate.match_score > 0.0
            else DEFAULT_MISSING_FIELD_SIMILARITY
        )

        weighted = (
            (WEIGHT_PROVIDER * provider_score)
            + (WEIGHT_ARTIST * artist_score)
            + (WEIGHT_TITLE * title_score)
            + (WEIGHT_DURATION * duration_score)
        )
        return round(max(0.0, min(1.0, weighted)), SIMILARITY_PRECISION_DIGITS)
