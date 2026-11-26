#!/usr/bin/env python3
"""Report which policy version each organization repository has adopted.

The report lists an organization's repositories through the GitHub REST API.
It hashes each repository's policy and gate files and matches the digests
against policy-digests.json. It writes a dated Markdown and JSON report plus
`latest` copies, and compares the result with the previous `latest.json`.

The token comes from the ADOPTION_REPORT_TOKEN environment variable. The
report never prints or stores it.
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import sync

API_URL = "https://api.github.com"
TOKEN_VARIABLE = "ADOPTION_REPORT_TOKEN"
EXPIRY_HEADER = "github-authentication-token-expiration"
EXPIRY_DATE_LENGTH = len("2026-01-01")
EXPIRY_WARNING_DAYS = 30
HTTP_TIMEOUT_SECONDS = 30
HTTP_NOT_FOUND = 404
HTTP_CONFLICT = 409
PAGE_SIZE = 100
# Bounds the listing loop. 50 pages cover 5000 repositories.
MAX_PAGES = 50
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
LOOPBACK_HOSTS = frozenset(("127.0.0.1", "localhost"))
NAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
VERSION_PATTERN = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
PINNED_REFERENCE_PATTERN = re.compile(r"^[0-9a-f]{40}$")
REUSABLE_WORKFLOW_PATTERN = re.compile(
    r"abuzucom/agents/\.github/workflows/agents-compliance\.yml@([A-Za-z0-9._/-]+)")
# The source repository runs its own compliance workflow by local path.
# The line-start anchor skips commented-out calls. The backreference accepts
# a bare path or one wrapped in matching quotes. Line matching without a YAML
# parser also accepts the same line inside a multi-line run script.
LOCAL_WORKFLOW_PATTERN = re.compile(
    r"^[ \t]*(?:-[ \t]*)?uses:[ \t]*([\"']?)\./\.github/workflows/"
    r"agents-compliance\.yml\1[ \t]*(?:#.*)?$", re.MULTILINE)
WORKFLOW_DIRECTORY = ".github/workflows/"
WORKFLOW_SUFFIXES = (".yml", ".yaml")
TRANSACTION_HOOK = "enforce_gate_adoption.py"
CLIENT_REGISTRATIONS = {
    "claude": ".claude/settings.json",
    "codex": ".codex/hooks.json",
    "antigravity": ".agents/hooks.json",
    "gemini": ".gemini/settings.json",
}
LATEST_STEM = "latest"


class ReportError(Exception):
    """A failure that stops the report with a recovery message."""


class GitHubReader:
    """Read JSON and raw file text from the GitHub REST API."""

    def __init__(self, api_url: str, token: str):
        parsed = urllib.parse.urlsplit(api_url)
        loopback = parsed.scheme == "http" and parsed.hostname in LOOPBACK_HOSTS
        if parsed.scheme != "https" and not loopback:
            raise ReportError("the API URL must use https. Fix --api-url.")
        self._api_url = api_url.rstrip("/")
        self._token = token
        self.token_expiry = None

    def _read(self, path: str, accept: str, optional: bool) -> bytes | None:
        """Return one response body, or None for an optional missing path."""
        request = urllib.request.Request(self._api_url + path, headers={
            "Accept": accept,
            "Authorization": f"Bearer {self._token}",
            "User-Agent": "adoption-status-report",
        })
        try:
            with urllib.request.urlopen(
                    request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                self.token_expiry = (
                    response.headers.get(EXPIRY_HEADER) or self.token_expiry)
                return response.read(MAX_RESPONSE_BYTES)
        except urllib.error.HTTPError as error:
            if optional and error.code in (HTTP_NOT_FOUND, HTTP_CONFLICT):
                return None
            raise ReportError(
                f"GitHub answered {error.code} for {path}. Check the token "
                f"in {TOKEN_VARIABLE} and its repository access.") from error
        except (urllib.error.URLError, OSError) as error:
            raise ReportError(
                f"GitHub is unreachable for {path} ({error}). Retry the run."
            ) from error

    def read_json(self, path: str, optional: bool = False) -> object:
        """Return one decoded JSON response."""
        body = self._read(path, "application/vnd.github+json", optional)
        if body is None:
            return None
        try:
            return json.loads(body.decode("utf-8"))
        except ValueError as error:
            raise ReportError(
                f"GitHub returned invalid JSON for {path}. Retry the run."
            ) from error

    def read_text(self, path: str) -> str | None:
        """Return raw file text, or None when the file is absent or binary."""
        body = self._read(path, "application/vnd.github.raw", optional=True)
        if body is None:
            return None
        try:
            return body.decode("utf-8")
        except UnicodeDecodeError:
            return None


def quote_path(value: str) -> str:
    """Return one URL-safe path with its separators preserved."""
    return urllib.parse.quote(value, safe="/")


def read_json_file(path: Path, label: str) -> dict:
    """Return one JSON object read from disk."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise ReportError(
            f"the {label} file cannot be read ({error}). Fix its path or "
            "content.") from error
    if not isinstance(document, dict):
        raise ReportError(f"the {label} file must hold a JSON object.")
    return document


def load_config(path: Path) -> tuple:
    """Return the organization name and the excluded repository names."""
    document = read_json_file(path, "config")
    organization = document.get("organization")
    exclude = document.get("exclude", [])
    if not isinstance(organization, str) or not NAME_PATTERN.match(organization):
        raise ReportError(
            "the config organization must be one GitHub organization name.")
    if not isinstance(exclude, list) or not all(
            isinstance(name, str) for name in exclude):
        raise ReportError("the config exclude list must hold repository names.")
    return organization, frozenset(exclude)


def parse_version(version: str) -> tuple | None:
    """Return a sortable key for one X.Y.Z version, or None when malformed."""
    match = VERSION_PATTERN.match(version)
    if match is None:
        return None
    return tuple(int(part) for part in match.groups())


def load_index(path: Path) -> tuple:
    """Return the version map and its version names, newest first."""
    versions = read_json_file(path, "digest index").get("versions")
    if not isinstance(versions, dict) or not versions:
        raise ReportError("the digest index lists no version.")
    ordered = sorted(
        (name for name in versions if parse_version(name) is not None),
        key=parse_version, reverse=True)
    if not ordered:
        raise ReportError("the digest index lists no X.Y.Z version.")
    return versions, ordered


def list_repositories(reader: GitHubReader, organization: str,
                      exclude: frozenset) -> list:
    """Return the active, unforked, unexcluded repositories, sorted by name."""
    repositories = []
    for page in range(1, MAX_PAGES + 1):
        batch = reader.read_json(
            f"/orgs/{quote_path(organization)}/repos"
            f"?type=all&per_page={PAGE_SIZE}&page={page}")
        if not isinstance(batch, list) or not batch:
            return sorted(repositories, key=lambda item: item["name"])
        for item in batch:
            name = item.get("name", "")
            usable = NAME_PATTERN.match(name) and name not in exclude
            if usable and not item.get("fork") and not item.get("archived"):
                repositories.append(item)
    raise ReportError(
        f"the organization holds more than {MAX_PAGES * PAGE_SIZE} "
        "repositories. Raise MAX_PAGES.")


class RepositoryFiles:
    """Read and cache one repository's files at its default branch."""

    def __init__(self, reader: GitHubReader, organization: str, item: dict):
        self._reader = reader
        self._base = (f"/repos/{quote_path(organization)}/"
                      f"{quote_path(item['name'])}")
        self._texts = {}
        branch = item.get("default_branch") or "main"
        tree = reader.read_json(
            f"{self._base}/git/trees/{quote_path(branch)}?recursive=1",
            optional=True)
        entries = tree.get("tree", []) if isinstance(tree, dict) else []
        self.paths = frozenset(
            entry.get("path", "") for entry in entries
            if entry.get("type") == "blob")
        # A truncated tree omits paths, so every wanted path gets a request.
        self._complete = not (isinstance(tree, dict) and tree.get("truncated"))

    def read_text(self, path: str) -> str | None:
        """Return one file's text, or None when the repository lacks it."""
        if path not in self._texts:
            known_absent = self._complete and path not in self.paths
            self._texts[path] = None if known_absent else self._reader.read_text(
                f"{self._base}/contents/{quote_path(path)}")
        return self._texts[path]

    def read_digest(self, path: str) -> str | None:
        """Return one file's normalized SHA-256, or None when absent."""
        text = self.read_text(path)
        return None if text is None else sync.text_digest(text)


def match_policy_version(files: RepositoryFiles, versions: dict,
                         ordered: list) -> str | None:
    """Return the newest version with an AGENTS.md digest in the repository."""
    digest = files.read_digest(sync.SOURCE)
    for version in ordered:
        if versions[version].get("policy", {}).get(sync.SOURCE) == digest:
            return version
    return None


def count_matches(files: RepositoryFiles, recorded: dict) -> tuple:
    """Return how many recorded files are present and how many match."""
    digests = [(files.read_digest(name), digest)
               for name, digest in recorded.items()]
    present = sum(1 for actual, _ in digests if actual is not None)
    matching = sum(1 for actual, digest in digests if actual == digest)
    return present, matching


def describe_policy(files: RepositoryFiles, versions: dict, ordered: list,
                    version: str | None) -> dict:
    """Return the policy state of one repository."""
    reference = versions[version or ordered[0]].get("policy", {})
    _, matching = count_matches(files, reference)
    if version is None:
        state = "absent" if files.read_text(sync.SOURCE) is None else "drifted"
        behind = None
    else:
        behind = ordered.index(version)
        state = "current" if behind == 0 else "behind"
    return {"state": state, "version": version, "versions_behind": behind,
            "files_matching": matching, "files_total": len(reference)}


def describe_copies(files: RepositoryFiles) -> dict:
    """Return how many synchronized copies equal the assembled policy.

    The assembly follows sync.policy_bytes. Source-only orientation joins
    the policy only where the repository holds it.
    """
    names = (sync.SOURCE,) + tuple(sync.ADOPTER_POLICY_FILES)
    parts = [files.read_text(name) for name in names]
    source_only = [files.read_text(name)
                   for name in sync.SOURCE_ONLY_POLICY_FILES]
    parts += [part for part in source_only if part is not None]
    in_sync = 0
    if all(part is not None for part in parts):
        assembled = sync.text_digest("".join(
            part if part.endswith("\n") else part + "\n" for part in parts))
        in_sync = sum(1 for name in sync.COPIES
                      if files.read_digest(name) == assembled)
    return {"in_sync": in_sync, "total": len(sync.COPIES)}


def describe_gates(files: RepositoryFiles, shared: dict) -> dict:
    """Return gate file coverage and per-client registration state."""
    present, matching = count_matches(files, shared)
    clients = {
        client: TRANSACTION_HOOK in (files.read_text(path) or "")
        for client, path in CLIENT_REGISTRATIONS.items()}
    registered = sum(1 for active in clients.values() if active)
    started = present > 0 or registered > 0
    complete = present == len(shared) and registered == len(clients)
    return {"present": present, "matching": matching, "total": len(shared),
            "clients": clients, "partial": started and not complete}


def describe_wiring(files: RepositoryFiles) -> dict:
    """Return the reusable compliance workflow reference, when one exists.

    local reads true when any workflow calls the compliance workflow by local
    path. reference and pinned come from the first remote call.
    """
    workflows = sorted(
        path for path in files.paths
        if path.startswith(WORKFLOW_DIRECTORY)
        and path.endswith(WORKFLOW_SUFFIXES))
    texts = [files.read_text(path) or "" for path in workflows]
    local = any(LOCAL_WORKFLOW_PATTERN.search(text) for text in texts)
    for text in texts:
        match = REUSABLE_WORKFLOW_PATTERN.search(text)
        if match:
            reference = match.group(1)
            pinned = bool(PINNED_REFERENCE_PATTERN.match(reference))
            return {"reference": reference, "pinned": pinned, "local": local}
    return {"reference": None, "pinned": False, "local": local}


def build_row(reader: GitHubReader, organization: str, item: dict,
              versions: dict, ordered: list) -> dict:
    """Return the report row for one repository."""
    files = RepositoryFiles(reader, organization, item)
    version = match_policy_version(files, versions, ordered)
    shared = versions[version or ordered[0]].get("shared", {})
    return {
        "name": item["name"],
        "private": bool(item.get("private")),
        "policy": describe_policy(files, versions, ordered, version),
        "copies": describe_copies(files),
        "gates": describe_gates(files, shared),
        "ci": describe_wiring(files),
    }


def classify_change(before: dict | None, after: dict) -> str | None:
    """Return the weekly change label for one repository, or None."""
    if before is None:
        return "new repository"
    old = parse_version(before.get("policy", {}).get("version") or "")
    new = parse_version(after["policy"]["version"] or "")
    if old == new:
        return None
    if old is None:
        return "adopted"
    if new is None or new < old:
        return "regressed"
    return "upgraded"


def build_delta(previous_rows: list, rows: list) -> list:
    """Return the changes between the previous report and this one."""
    previous = {row.get("name"): row for row in previous_rows
                if isinstance(row, dict)}
    delta = []
    for row in rows:
        change = classify_change(previous.get(row["name"]), row)
        if change is not None:
            delta.append({"name": row["name"], "change": change})
    current_names = {row["name"] for row in rows}
    for name in sorted(set(previous) - current_names):
        delta.append({"name": name, "change": "removed"})
    return delta


def describe_expiry(expiry: str | None, today: datetime.date) -> str | None:
    """Return a warning when the token expires within the warning window."""
    try:
        expires_on = datetime.date.fromisoformat(
            (expiry or "")[:EXPIRY_DATE_LENGTH])
    except ValueError:
        return None
    remaining = (expires_on - today).days
    if remaining >= EXPIRY_WARNING_DAYS:
        return None
    return (f"The report token expires on {expires_on.isoformat()}, "
            f"{remaining} days from this run. Replace it before that date.")


def format_row(row: dict) -> str:
    """Return one Markdown table line for a repository row."""
    policy, gates = row["policy"], row["gates"]
    behind = "" if policy["versions_behind"] is None else policy["versions_behind"]
    clients = ", ".join(
        name for name, active in gates["clients"].items() if active) or "none"
    reference = row["ci"]["reference"] or (
        "local" if row["ci"].get("local") else "none")
    if row["ci"]["reference"] and not row["ci"]["pinned"]:
        reference += " (unpinned)"
    cells = [
        row["name"], "private" if row["private"] else "public",
        policy["state"], policy["version"] or "", behind,
        f"{policy['files_matching']}/{policy['files_total']}",
        f"{row['copies']['in_sync']}/{row['copies']['total']}",
        f"{gates['matching']}/{gates['total']}", clients,
        "yes" if gates["partial"] else "no", reference,
    ]
    return "| " + " | ".join(str(cell) for cell in cells) + " |"


def render_markdown(document: dict) -> str:
    """Return the Markdown report for one run."""
    lines = [
        f"# Adoption status {document['date']}", "",
        f"Organization: `{document['organization']}`. "
        f"Newest policy version: {document['latest_version']}.", "",
    ]
    if document["warnings"]:
        lines += ["## Warnings", ""]
        lines += [f"- {warning}" for warning in document["warnings"]]
        lines.append("")
    lines += ["## Changes since the previous report", ""]
    lines += [f"- `{item['name']}`: {item['change']}"
              for item in document["delta"]] or ["- None."]
    lines += [
        "", "## Repositories", "",
        "| Repository | Visibility | Policy | Version | Behind | Policy files "
        "| Copies | Gate files | Clients | Partial | CI reference |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    lines += [format_row(row) for row in document["repositories"]]
    return "\n".join(lines) + "\n"


def read_previous_rows(output: Path) -> list:
    """Return the rows of the previous latest report, or an empty list."""
    latest = output / f"{LATEST_STEM}.json"
    if not latest.is_file():
        return []
    rows = read_json_file(latest, "previous report").get("repositories", [])
    return rows if isinstance(rows, list) else []


def write_report(output: Path, document: dict) -> None:
    """Write the dated report files and refresh the latest copies."""
    output.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(
        document, indent=sync.MANIFEST_JSON_INDENT, sort_keys=True) + "\n"
    markdown = render_markdown(document)
    for stem in (document["date"], LATEST_STEM):
        (output / f"{stem}.json").write_text(encoded, encoding="utf-8")
        (output / f"{stem}.md").write_text(markdown, encoding="utf-8")


def build_report(options: argparse.Namespace, token: str,
                 today: datetime.date) -> dict:
    """Read the organization and return the complete report document."""
    organization, exclude = load_config(Path(options.config))
    versions, ordered = load_index(Path(options.index))
    reader = GitHubReader(options.api_url, token)
    rows = [build_row(reader, organization, item, versions, ordered)
            for item in list_repositories(reader, organization, exclude)]
    warning = describe_expiry(reader.token_expiry, today)
    return {
        "date": today.isoformat(),
        "organization": organization,
        "latest_version": ordered[0],
        "warnings": [warning] if warning else [],
        "delta": build_delta(read_previous_rows(Path(options.output)), rows),
        "repositories": rows,
    }


def main(argv: list, environment: dict | None = None,
         today: datetime.date | None = None) -> int:
    """Write the adoption report and return a process exit code."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", required=True, help="report config JSON")
    parser.add_argument("--index", required=True, help="policy-digests.json")
    parser.add_argument("--output", required=True, help="report directory")
    parser.add_argument("--api-url", default=API_URL, help="GitHub API URL")
    options = parser.parse_args(argv)
    environment = os.environ if environment is None else environment
    today = today or datetime.datetime.now(datetime.timezone.utc).date()
    token = environment.get(TOKEN_VARIABLE, "")
    try:
        if not token:
            raise ReportError(
                f"{TOKEN_VARIABLE} is unset. Export a token with read access "
                "to the organization repositories.")
        document = build_report(options, token, today)
        write_report(Path(options.output), document)
    except (ReportError, OSError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1
    print(f"wrote the adoption report for {len(document['repositories'])} "
          f"repositories to {options.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
