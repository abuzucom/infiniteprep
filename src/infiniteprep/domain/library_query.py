"""Compile bounded library expressions to SQLite predicates with bound values."""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass
from typing import Final

from infiniteprep.domain.enums import KeyDisplayNotation, format_canonical_key, parse_key_string
from infiniteprep.domain.search_phrases import SECONDS_PER_MINUTE, expand_phrase

MAX_LENGTH: Final = 2048
MAX_DEPTH: Final = 16
MAX_TERMS: Final = 64
FIELDS = frozenset(
    [
        "artist",
        "title",
        "mix",
        "label",
        "catalog_number",
        "genre",
        "year",
        "bpm",
        "key",
        "duration",
        "format",
        "status",
        "warnings",
    ]
)
ALIASES = {"review_status": "status", "warning": "warnings"}
NUMERIC = {"duration", "year", "bpm"}
OPERATORS = {"=": "=", ">": ">", "<": "<", "<=": "<=", ">=": ">="}
TOKEN = re.compile(r'"[^"]*"|[0-9]+:[0-9]{2}(?:-[0-9]+:[0-9]{2})?|[()]|>=|<=|[:=<>]|[^\s()"=:<>]+')
QUOTE = re.compile(r'("[^"]*")')
TIME_VALUE = re.compile(r"\d+:\d{2}")
FIELD_START = re.compile(
    r"\b(artist|title|mix|label|catalog_number|genre|year|bpm|key|duration|format|status|review_status|warnings?|keyword)\s*[:<>=]"
)


def canonical_key(value: str) -> str:
    parsed = parse_key_string(value)
    return format_canonical_key(*parsed, KeyDisplayNotation.CAMELOT).casefold() if parsed else ""


def parse_number(field: str, text: str) -> float:
    if field == "duration" and TIME_VALUE.fullmatch(text):
        minutes, seconds = map(int, text.split(":"))
        if seconds < SECONDS_PER_MINUTE:
            return minutes * SECONDS_PER_MINUTE + seconds
    try:
        value = float(text)
    except ValueError:
        value = math.nan
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"Enter a valid number for {field}.")
    return value


@dataclass(frozen=True)
class LibraryQuery:
    """A predicate made of fixed SQL fragments and separately bound arguments."""

    predicate: str
    parameters: tuple

    def select_statement(self) -> str:
        """Allow only compiler instructions in the predicate used for execution."""
        allowed = {
            "(",
            ")",
            "NOT",
            "OR",
            "AND",
            "BETWEEN",
            "coalesce",
            "instr",
            "json_extract",
            "value",
            ",",
            "?",
            "0",
            "''",
            ">",
            ">=",
            "=",
            "<",
            "<=",
        }
        tokens = re.findall(r"[A-Za-z_]+|>=|<=|''|[^\s]", self.predicate)
        if any(token not in allowed for token in tokens):
            raise ValueError("Invalid search expression. Enter the search again.")
        return " ".join(("SELECT value ->> '$.id' FROM json_each(?) WHERE", self.predicate))

    def select_ids(self, conn: sqlite3.Connection, records: list[dict]) -> list[str]:
        sql = self.select_statement()
        normalized = [
            {
                key: value.casefold() if isinstance(value, str) and key != "id" else value
                for key, value in record.items()
            }
            for record in records
        ]
        return [row[0] for row in conn.execute(sql, (json.dumps(normalized), *self.parameters))]


def make_term(field: str, op: str, value: str) -> LibraryQuery:
    field = ALIASES.get(field, field)
    if field == "status" and op == "=":
        return LibraryQuery(
            "(coalesce(json_extract(value, ?) = ?, 0) OR coalesce(json_extract(value, ?) = ?, 0))",
            ("$.status", value, "$.status_label", value),
        )
    if field in {"keyword", "status"} and op == ":":
        fields = ("$.text", "$.genre") if field == "keyword" else ("$.status", "$.status_label")
        return LibraryQuery(
            "(instr(coalesce(json_extract(value, ?), ''), ?) > 0 OR "
            "instr(coalesce(json_extract(value, ?), ''), ?) > 0)",
            (fields[0], value, fields[1], value),
        )
    if field not in FIELDS and field != "text":
        raise ValueError("Choose a supported search field.")
    path = "$." + field
    column = "json_extract(value, ?)"
    if field in NUMERIC:
        if op == ":" and "-" in value:
            lower, _, upper = value.partition("-")
            bounds = parse_number(field, lower), parse_number(field, upper)
            if bounds[0] > bounds[1]:
                raise ValueError("Put the lower bound first.")
            return LibraryQuery(f"coalesce({column} BETWEEN ? AND ?, 0)", (path, *bounds))
        symbol = OPERATORS["=" if op == ":" else op]
        return LibraryQuery(f"coalesce({column} {symbol} ?, 0)", (path, parse_number(field, value)))
    if field == "key":
        value = canonical_key(value)
        if not value:
            raise ValueError("Enter a standard, Camelot, or Open Key value.")
        if op == ":":
            op = "="
    if op == ":":
        return LibraryQuery(f"instr(coalesce({column}, ''), ?) > 0", (path, value))
    if op == "=":
        return LibraryQuery(f"coalesce({column}, '') = ?", (path, value))
    raise ValueError("Use a numeric field for this comparison.")


class QueryParser:
    """Parse AND before OR with bounded groups and prefix negation."""

    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.index = 0
        self.parameters: list = []
        self.terms = 0

    def peek(self, offset: int = 0) -> str:
        index = self.index + offset
        return self.tokens[index] if index < len(self.tokens) else ""

    def take(self) -> str:
        token = self.peek()
        self.index += 1
        return token

    def expression(self, depth: int = 0) -> str:
        clauses = [self.conjunction(depth)]
        while self.peek() == "or":
            self.take()
            clauses.append(self.conjunction(depth))
        return "(" + " OR ".join(clauses) + ")"

    def conjunction(self, depth: int) -> str:
        clauses = [self.factor(depth)]
        while self.peek() not in {"", ")", "or"}:
            if self.peek() == "and":
                self.take()
            clauses.append(self.factor(depth))
        return "(" + " AND ".join(clauses) + ")"

    def factor(self, depth: int) -> str:
        if depth > MAX_DEPTH:
            raise ValueError("Use fewer nested groups or negations.")
        if self.peek() == "not":
            self.take()
            return "(NOT " + self.factor(depth + 1) + ")"
        if self.peek() == "(":
            self.take()
            result = self.expression(depth + 1)
            if self.take() != ")":
                raise ValueError("Close the search group with a parenthesis.")
            return result
        self.terms += 1
        if self.terms > MAX_TERMS:
            raise ValueError("Use fewer search terms.")
        field, op = "text", ":"
        if self.peek(1) in {":", *OPERATORS}:
            field, op = self.take(), self.take()
        words = []
        while self.peek() not in {"", "(", ")", "and", "or", "not", ":", *OPERATORS}:
            if self.peek(1) in {":", *OPERATORS}:
                break
            words.append(self.take().strip('"'))
        if not words or (not any(words) and op != "="):
            raise ValueError("Enter a value for each search term.")
        term = make_term(field, op, " ".join(words))
        self.parameters.extend(term.parameters)
        return term.predicate


def _normalize_source(source: str) -> tuple[str, str]:
    """Return the folded search text and its expanded form, or raise ValueError."""
    if len(source) > MAX_LENGTH:
        raise ValueError(f"Limit the search to {MAX_LENGTH} characters.")
    text = source.strip().casefold()
    if text.count('"') % 2:
        raise ValueError("Close the quoted phrase.")
    parts = QUOTE.split(text)
    # A trailing duration applies to every genre in a spoken genre list.
    genre = re.fullmatch(r"([\w -]+) tracks? (shorter than|longer than|under|over) (.+)", text)
    if genre:
        names = genre[1].split(" or ")
        if all(name.strip() for name in names):
            parts = [
                "("
                + " or ".join("keyword:" + name for name in names)
                + ") and "
                + genre[2]
                + " "
                + genre[3]
            ]
    normalized = "".join(part if part.startswith('"') else expand_phrase(part) for part in parts)
    return text, normalized


def _simple_term(text: str, normalized: str) -> str | None:
    """Return the substring term of a search without fields or phrases, or None."""
    if normalized != text:
        return None
    if any(FIELD_START.search(part) for part in QUOTE.split(normalized)[::2]):
        return None
    return text.removeprefix('"').removesuffix('"')


def plain_search_term(source: str) -> str | None:
    """Return the folded term of a plain substring search.

    A search with fields, phrases, or an error returns None. Such a search has no
    single term to mark in the table.
    """
    try:
        text, normalized = _normalize_source(source)
    except ValueError:
        return None
    return _simple_term(text, normalized)


def query_fields(source: str) -> frozenset[str]:
    """Return the canonical names of the fields a search names.

    The names come from the expanded search. A spoken phrase names the fields of
    its compiled form. A search with an error names no fields.
    """
    try:
        _text, normalized = _normalize_source(source)
    except ValueError:
        return frozenset()
    names: set[str] = set()
    for part in QUOTE.split(normalized)[::2]:
        names.update(ALIASES.get(match[1], match[1]) for match in FIELD_START.finditer(part))
    return frozenset(names)


def parse_library_query(source: str) -> LibraryQuery:
    """Preserve ordinary substring searches and compile structured expressions."""
    text, normalized = _normalize_source(source)
    term = _simple_term(text, normalized)
    if term is not None:
        return make_term("text", ":", term)
    parser = QueryParser(TOKEN.findall(normalized))
    predicate = parser.expression()
    if parser.peek():
        raise ValueError("Remove the extra closing parenthesis.")
    return LibraryQuery(predicate, tuple(parser.parameters))
