"""Define migration 1 as the schema every library held before numbered migrations (D349)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Final

VERSION: Final[int] = 1
SCHEMA_PATH: Final[Path] = Path(__file__).resolve().parent.parent / "schema.sql"
# Literal queries keep backup verification free of SQL built from table names.
ROW_COUNT_QUERIES: Final[dict[str, str]] = {
    "source_groups": "SELECT COUNT(*) FROM source_groups",
    "file_assets": "SELECT COUNT(*) FROM file_assets",
    "candidate_matches": "SELECT COUNT(*) FROM candidate_matches",
    "field_proposals": "SELECT COUNT(*) FROM field_proposals",
    "analysis_results": "SELECT COUNT(*) FROM analysis_results",
    "approved_fields": "SELECT COUNT(*) FROM approved_fields",
    "change_records": "SELECT COUNT(*) FROM change_records",
    "jobs": "SELECT COUNT(*) FROM jobs",
    "provider_cache": "SELECT COUNT(*) FROM provider_cache",
}


def read_schema_sql() -> str:
    """Return the text of db/schema.sql."""
    return SCHEMA_PATH.read_text(encoding="utf-8")


def split_sql_statements(script: str) -> list[str]:
    """Split a script into complete statements.

    executescript() commits any open transaction first. The runner keeps every step inside
    one transaction. Each statement therefore runs through execute().
    """
    statements: list[str] = []
    pending: list[str] = []
    for line in script.splitlines(keepends=True):
        pending.append(line)
        candidate = "".join(pending)
        if sqlite3.complete_statement(candidate):
            statements.append(candidate.strip())
            pending.clear()
    remainder = "".join(pending).strip()
    if any(not line.strip().startswith("--") for line in remainder.splitlines() if line.strip()):
        raise ValueError("schema.sql ends with an incomplete SQL statement. Fix the file.")
    return statements


def apply(conn: sqlite3.Connection) -> None:
    """Create every table and index that schema.sql defines. IF NOT EXISTS skips existing ones."""
    for statement in split_sql_statements(read_schema_sql()):
        conn.execute(statement)
