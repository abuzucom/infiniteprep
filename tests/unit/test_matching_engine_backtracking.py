"""Title parsing regexes stay linear on long whitespace runs and keep the same matches."""

from __future__ import annotations

import itertools
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from infiniteprep.services.matching_engine import (
    BY_REMIXER_PATTERN,
    PAREN_PATTERN,
    parse_dj_title_and_mix,
)

ROOT = Path(__file__).resolve().parents[2]
# The child process fails the test instead of hanging the suite on a slow pattern.
CHILD_TIMEOUT_SECONDS = 10
# A quadratic pattern takes minutes on this length. A linear pattern takes milliseconds.
WHITESPACE_RUN_LENGTH = 60_000
EQUIVALENCE_MAX_TOKENS = 6
ENGINE_IMPORT = "from infiniteprep.services.matching_engine import parse_dj_title_and_mix\n"

# The previous patterns serve as the reference for each capture.
REFERENCE_PAREN_PATTERN = re.compile(r"^([^\(\[\-]+?)\s*[\(\[]([^\)\]]+)[\)\]]$")
REFERENCE_BY_REMIXER_PATTERN = re.compile(
    r"^([A-Za-z0-9 &\.]+?)\s+(?:remix|mix|dub|rework|edit)$", re.IGNORECASE
)
TITLE_TOKENS = ("a", " ", "\t", "(", "[", ")", "-")
MIX_NAME_TOKENS = ("a", " ", "\t", "&", "mix", "Remix", "\n")


def run_child(statement: str) -> None:
    """Run one title parse in a child process under the time bound."""
    child_env = dict(os.environ)
    child_env["PYTHONPATH"] = os.pathsep.join(
        part for part in (str(ROOT / "src"), child_env.get("PYTHONPATH", "")) if part
    )
    subprocess.run(
        [sys.executable, "-c", ENGINE_IMPORT + statement],
        cwd=ROOT,
        env=child_env,
        check=True,
        capture_output=True,
        text=True,
        timeout=CHILD_TIMEOUT_SECONDS,
    )


def build_samples(tokens: tuple[str, ...]) -> list[str]:
    """Return every stripped sequence of the tokens up to the maximum length."""
    return [
        "".join(sequence).strip()
        for length in range(EQUIVALENCE_MAX_TOKENS + 1)
        for sequence in itertools.product(tokens, repeat=length)
    ]


def capture_groups(pattern: re.Pattern[str], text: str) -> tuple[str, ...] | None:
    """Return the capture groups of a full match, or None without a match."""
    match = pattern.match(text)
    return match.groups() if match else None


@pytest.mark.parametrize(
    "title_expression",
    [
        f"'Track' + ' ' * {WHITESPACE_RUN_LENGTH} + '(' + 'b' * {WHITESPACE_RUN_LENGTH}",
        f"'Track (Danny' + ' ' * {WHITESPACE_RUN_LENGTH} + 'x Mix)'",
    ],
)
def test_long_whitespace_runs_parse_quickly(title_expression: str) -> None:
    """A title with a long whitespace run finishes within the time bound."""
    run_child(f"parse_dj_title_and_mix({title_expression})\n")


def test_paren_pattern_captures_match_the_reference() -> None:
    """The bracket split captures the same title and mix text as the reference."""
    for text in build_samples(TITLE_TOKENS):
        expected = capture_groups(REFERENCE_PAREN_PATTERN, text)
        assert capture_groups(PAREN_PATTERN, text) == expected, repr(text)


def test_by_remixer_captures_match_reference() -> None:
    """The remixer capture matches the reference on stripped mix names."""
    for text in build_samples(MIX_NAME_TOKENS):
        expected = capture_groups(REFERENCE_BY_REMIXER_PATTERN, text)
        assert capture_groups(BY_REMIXER_PATTERN, text) == expected, repr(text)


def test_remixer_survives_extra_spaces() -> None:
    """Extra spaces between the remixer and the mix keyword keep the remixer."""
    assert parse_dj_title_and_mix("Track (Danny Tenaglia   Dub)") == (
        "Track",
        "Danny Tenaglia   Dub",
        "Danny Tenaglia",
    )
