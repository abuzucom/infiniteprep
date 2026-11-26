"""Recognize ordinary language in a library query before boolean parsing."""

import re

NUMBER_WORDS = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
]
DURATION_UNITS = {"minute": 60, "minutes": 60, "second": 1, "seconds": 1}
COMPARISONS = {
    "at least": ">=",
    "at most": "<=",
    "more than": ">",
    "less than": "<",
    "longer than": ">",
    "shorter than": "<",
    "over": ">",
    "under": "<",
}
PHRASES = re.compile(
    r"\b(artist|title|mix|label|catalog number|genre|year|bpm|key|duration|format|"
    r"review status|warnings?)\s+(contains|is exactly|is|equals)\s+"
)
COMPARISON = re.compile(
    r"\b(at least|at most|more than|less than|longer than|shorter than|over|under)"
    r"\s+([\w.:]+)\s+(bpm|minutes?|seconds?)\b"
)
BETWEEN = re.compile(r"\bbetween\s+([\d.]+)\s+and\s+([\d.]+)\s+(bpm|minutes?|seconds?)\b")


def numeric_phrase(value: str, unit: str) -> tuple[str, str]:
    """Convert a spoken quantity to a numeric field and value."""
    if value in NUMBER_WORDS:
        value = str(NUMBER_WORDS.index(value))
    if unit == "bpm":
        return "bpm", value
    try:
        return "duration", str(float(value) * DURATION_UNITS[unit])
    except ValueError as error:
        raise ValueError("Enter a number of minutes or seconds.") from error


def replace_range(match: re.Match) -> str:
    low, high, unit = match.groups()
    field, lower = numeric_phrase(low, unit)
    _, upper = numeric_phrase(high, unit)
    return f" {field}:{lower}-{upper}"


def replace_comparison(match: re.Match) -> str:
    operator, quantity, unit = match.groups()
    field, value = numeric_phrase(quantity, unit)
    return f" {field}{COMPARISONS[operator]}{value}"


def expand_phrase(text: str) -> str:
    """Translate recognized phrases while leaving quoted literals untouched."""
    text = PHRASES.sub(
        lambda m: m[1].replace(" ", "_") + (":" if m[2] == "contains" else "="), text
    )
    text = re.sub(r"\b(?:tracks? by|by artist)\s+", "artist:", text)
    text = re.sub(r"\b(?:tracks? )?in the key of\s+", "key:", text)
    text = re.sub(
        r"\b(?:tracks? )?released (after|before|in) (\d{4})\b",
        lambda m: "year" + {"after": ">", "before": "<", "in": "="}[m[1]] + m[2],
        text,
    )
    text = re.sub(r"\b(?:tracks? )?without warnings\b", 'warnings=""', text)
    text = re.sub(r"\b(?:tracks? )?with warnings\b", 'not warnings=""', text)
    text = BETWEEN.sub(replace_range, text)
    text = COMPARISON.sub(replace_comparison, text)
    text = re.sub(r"\btracks?\s+(?=(?:bpm|duration)[:<>=])", "", text)
    return text
