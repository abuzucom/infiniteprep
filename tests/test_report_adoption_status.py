#!/usr/bin/env python3
"""Cover the adoption status report against a local stand-in for GitHub.

The report reads an organization's repositories through the GitHub REST API.
These tests serve a synthetic organization from a loopback HTTP server, so
the real reader, matcher, and writers all run without network access.
"""
import contextlib
import datetime
import http.server
import io
import json
import socketserver
import sys
import tempfile
import threading
import unittest
import urllib.parse
from pathlib import Path

# scripts/ is never on the path, however the suite is launched.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import report_adoption_status as report
import sync

ORGANIZATION = "acme"
TOKEN = "fixture-token-value"
OLD_VERSION = "1.0.0"
NEW_VERSION = "2.0.0"
REPORT_DATE = datetime.date(2026, 1, 4)
PINNED_REFERENCE = "a" * 40
# A short poll keeps server shutdown from dominating each test.
SERVER_POLL_SECONDS = 0.01
HTTP_OK = 200
HTTP_UNAUTHORIZED = 401
HTTP_NOT_FOUND = 404
FAILURE_EXIT = 1
SOON_EXPIRY = "2026-01-10 00:00:00 UTC"
FAR_EXPIRY = "2026-12-31 00:00:00 UTC"
POLICY_DOCUMENT = "docs/agent-policy/github.md"
SHARED_GATE = "hooks/_gate_core.py"
WORKFLOW_PATH = ".github/workflows/checks.yml"
CLIENT_PATHS = (".claude/settings.json", ".codex/hooks.json",
                ".agents/hooks.json", ".gemini/settings.json")
REGISTRATION = '{"hooks": "python hooks/enforce_gate_adoption.py"}\n'


def build_release(label: str) -> dict:
    """Return the tracked file contents of one synthetic release."""
    return {
        "AGENTS.md": f"# Policy {label}\n",
        POLICY_DOCUMENT: f"# GitHub {label}\n",
        SHARED_GATE: f"gate = '{label}'\n",
    }


RELEASES = {OLD_VERSION: build_release("old"), NEW_VERSION: build_release("new")}


def build_index() -> dict:
    """Return a digest index describing both synthetic releases."""
    versions = {}
    for version, files in RELEASES.items():
        versions[version] = {
            "policy": {name: sync.text_digest(files[name])
                       for name in ("AGENTS.md", POLICY_DOCUMENT)},
            "shared": {SHARED_GATE: sync.text_digest(files[SHARED_GATE])},
        }
    return {"versions": versions}


def build_current_files() -> dict:
    """Return a complete adoption of the newest synthetic release."""
    files = dict(RELEASES[NEW_VERSION])
    files["docs/project-orientation.md"] = "# Orientation\n"
    for name in sync.ADOPTER_POLICY_FILES:
        files.setdefault(name, f"# {name}\n")
    assembled = "".join(
        files[name] for name in (sync.SOURCE,) + tuple(sync.ADOPTER_POLICY_FILES))
    for name in sync.COPIES:
        files[name] = assembled
    for name in CLIENT_PATHS:
        files[name] = REGISTRATION
    files[WORKFLOW_PATH] = (
        "jobs:\n  compliance:\n    uses: abuzucom/agents/.github/workflows/"
        f"agents-compliance.yml@{PINNED_REFERENCE}\n")
    return files


def build_organization() -> dict:
    """Return repository metadata and file contents for the fixture."""
    behind = dict(RELEASES[OLD_VERSION])
    behind[CLIENT_PATHS[0]] = REGISTRATION
    return {
        "current": {"files": build_current_files()},
        "behind": {"files": behind},
        "drifted": {"files": {"AGENTS.md": "# Local rewrite\n"}},
        "bare": {"files": {"README.md": "# Bare\n"}},
        "forked": {"files": {}, "fork": True},
        "retired": {"files": {}, "archived": True},
        "skipped": {"files": dict(RELEASES[NEW_VERSION])},
    }


class OrganizationHandler(http.server.BaseHTTPRequestHandler):
    """Serve the subset of the GitHub REST API the report reads."""

    def log_message(self, *arguments):
        """Keep request logging out of the test output."""

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        expiry = self.server.token_expiry
        if expiry:
            self.send_header(report.EXPIRY_HEADER, expiry)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_json(self, document: object) -> None:
        self._send(HTTP_OK, json.dumps(document).encode("utf-8"))

    def do_GET(self):
        """Route one request to the organization fixture.

        http.server dispatches on this exact method name.
        """
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            self._send(HTTP_UNAUTHORIZED, b"{}")
            return
        parsed = urllib.parse.urlsplit(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        parts = [urllib.parse.unquote(part)
                 for part in parsed.path.strip("/").split("/")]
        repositories = self.server.organization
        if parts[:1] == ["orgs"]:
            first_page = query.get("page", ["1"])[0] == "1"
            self._send_json(self._list_repositories() if first_page else [])
            return
        _, _, repository, kind, *remainder = parts
        files = repositories.get(repository, {}).get("files")
        file_path = "/".join(remainder)
        if files is None:
            self._send(HTTP_NOT_FOUND, b"{}")
        elif kind == "git":
            tree = [{"path": name, "type": "blob"} for name in files]
            self._send_json({"tree": tree, "truncated": False})
        elif kind == "contents" and file_path in files:
            self._send(HTTP_OK, files[file_path].encode("utf-8"))
        else:
            self._send(HTTP_NOT_FOUND, b"{}")

    def _list_repositories(self) -> list:
        return [{"name": name, "default_branch": "main",
                 "private": name == "bare",
                 "fork": bool(details.get("fork")),
                 "archived": bool(details.get("archived"))}
                for name, details in self.server.organization.items()]


class LoopbackServer(socketserver.ThreadingTCPServer):
    """A threaded loopback server without the HTTPServer host name lookup.

    http.server.HTTPServer resolves its own host name on every bind. That
    lookup takes seconds on some runners and adds nothing on loopback.
    """

    allow_reuse_address = True


class ReportFixture(unittest.TestCase):
    """A loopback GitHub stand-in plus index, config, and output paths."""

    def setUp(self):
        self.server = LoopbackServer(("127.0.0.1", 0), OrganizationHandler)
        self.server.organization = build_organization()
        self.server.token_expiry = FAR_EXPIRY
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": SERVER_POLL_SECONDS})
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.index = self.root / "policy-digests.json"
        self.index.write_text(json.dumps(build_index()), encoding="utf-8")
        self.config = self.root / "config.json"
        self.config.write_text(json.dumps(
            {"organization": ORGANIZATION, "exclude": ["skipped"]}),
            encoding="utf-8")
        self.output = self.root / "reports"

    def stop_server(self) -> None:
        """Stop the server and join its thread so no task outlives a test."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def run_report(self, token: str | None = TOKEN) -> tuple:
        """Run the report and return its exit code and captured streams."""
        port = self.server.server_address[1]
        environment = {} if token is None else {report.TOKEN_VARIABLE: token}
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = report.main(
                ["--config", str(self.config), "--index", str(self.index),
                 "--output", str(self.output),
                 "--api-url", f"http://127.0.0.1:{port}"],
                environment=environment, today=REPORT_DATE)
        return status, stdout.getvalue(), stderr.getvalue()

    def read_rows(self) -> dict:
        """Return the dated JSON report rows keyed by repository name."""
        document = json.loads(
            (self.output / f"{REPORT_DATE.isoformat()}.json").read_text(
                encoding="utf-8"))
        return {row["name"]: row for row in document["repositories"]}


class RepositoryScopeTest(ReportFixture):
    """The report covers non-fork, non-archived, non-excluded repositories."""

    def test_excluded_repositories_skipped(self):
        status, _, stderr = self.run_report()
        self.assertEqual(status, 0, stderr)
        self.assertEqual(
            sorted(self.read_rows()), ["bare", "behind", "current", "drifted"])


class PolicyStateTest(ReportFixture):
    """Each row names the adopted version and its distance from the newest."""

    def setUp(self):
        super().setUp()
        self.run_report()
        self.rows = self.read_rows()

    def test_current_repository_newest(self):
        policy = self.rows["current"]["policy"]
        self.assertEqual(policy["state"], "current")
        self.assertEqual(policy["version"], NEW_VERSION)
        self.assertEqual(policy["versions_behind"], 0)
        self.assertEqual(policy["files_matching"], policy["files_total"])

    def test_older_adoption_distance(self):
        policy = self.rows["behind"]["policy"]
        self.assertEqual(policy["state"], "behind")
        self.assertEqual(policy["version"], OLD_VERSION)
        self.assertEqual(policy["versions_behind"], 1)

    def test_a_rewritten_policy_reports_drift(self):
        policy = self.rows["drifted"]["policy"]
        self.assertEqual(policy["state"], "drifted")
        self.assertIsNone(policy["version"])

    def test_missing_policy_absent(self):
        self.assertEqual(self.rows["bare"]["policy"]["state"], "absent")

    def test_synchronized_copies_in_sync(self):
        copies = self.rows["current"]["copies"]
        self.assertEqual(copies["in_sync"], len(sync.COPIES))
        self.assertEqual(self.rows["behind"]["copies"]["in_sync"], 0)


class GateAndWiringTest(ReportFixture):
    """Each row reports gate files, client coverage, and CI wiring."""

    def setUp(self):
        super().setUp()
        self.run_report()
        self.rows = self.read_rows()

    def test_matching_gate_files_are_counted(self):
        gates = self.rows["current"]["gates"]
        self.assertEqual(gates["matching"], gates["total"])
        self.assertEqual(self.rows["bare"]["gates"]["present"], 0)

    def test_complete_coverage_not_partial(self):
        gates = self.rows["current"]["gates"]
        self.assertTrue(all(gates["clients"].values()))
        self.assertFalse(gates["partial"])

    def test_single_client_partial(self):
        gates = self.rows["behind"]["gates"]
        self.assertTrue(gates["clients"]["claude"])
        self.assertFalse(gates["clients"]["codex"])
        self.assertTrue(gates["partial"])

    def test_no_gate_not_partial(self):
        self.assertFalse(self.rows["bare"]["gates"]["partial"])

    def test_workflow_pin_reported(self):
        wiring = self.rows["current"]["ci"]
        self.assertEqual(wiring["reference"], PINNED_REFERENCE)
        self.assertTrue(wiring["pinned"])
        self.assertIsNone(self.rows["behind"]["ci"]["reference"])


class OutputTest(ReportFixture):
    """A run writes dated files, the latest copies, and a weekly delta."""

    def test_dated_and_latest_files(self):
        self.run_report()
        stem = REPORT_DATE.isoformat()
        for name in (f"{stem}.md", f"{stem}.json", "latest.md", "latest.json"):
            with self.subTest(name=name):
                self.assertTrue((self.output / name).is_file())
        markdown = (self.output / "latest.md").read_text(encoding="utf-8")
        for name in ("current", "behind", "drifted", "bare"):
            self.assertIn(name, markdown)
        self.assertNotIn("skipped |", markdown)

    def test_first_run_marks_new(self):
        self.run_report()
        document = json.loads(
            (self.output / "latest.json").read_text(encoding="utf-8"))
        changes = {item["name"]: item["change"] for item in document["delta"]}
        self.assertEqual(changes["current"], "new repository")

    def test_later_run_reports_changes(self):
        self.run_report()
        organization = self.server.organization
        organization["bare"]["files"].update(RELEASES[OLD_VERSION])
        organization["behind"]["files"].update(RELEASES[NEW_VERSION])
        organization["current"]["files"]["AGENTS.md"] = "# Broken\n"
        organization["added"] = {"files": {}}
        self.run_report()
        document = json.loads(
            (self.output / "latest.json").read_text(encoding="utf-8"))
        changes = {item["name"]: item["change"] for item in document["delta"]}
        self.assertEqual(changes["bare"], "adopted")
        self.assertEqual(changes["behind"], "upgraded")
        self.assertEqual(changes["current"], "regressed")
        self.assertEqual(changes["added"], "new repository")
        self.assertNotIn("drifted", changes)


class FailureTest(ReportFixture):
    """Credential problems fail the run and never print the token."""

    def test_missing_token_named(self):
        status, _, stderr = self.run_report(token=None)
        self.assertEqual(status, FAILURE_EXIT)
        self.assertIn(report.TOKEN_VARIABLE, stderr)

    def test_rejected_token_no_report(self):
        status, stdout, stderr = self.run_report(token="wrong-token-value")
        self.assertEqual(status, FAILURE_EXIT)
        self.assertFalse(self.output.exists())
        self.assertNotIn("wrong-token-value", stdout + stderr)

    def test_token_absent_from_report(self):
        _, stdout, stderr = self.run_report()
        written = (self.output / "latest.md").read_text(encoding="utf-8")
        written += (self.output / "latest.json").read_text(encoding="utf-8")
        self.assertNotIn(TOKEN, stdout + stderr + written)

    def test_expiring_token_warns(self):
        self.server.token_expiry = SOON_EXPIRY
        self.run_report()
        markdown = (self.output / "latest.md").read_text(encoding="utf-8")
        self.assertIn("expires", markdown)
        self.assertIn("2026-01-10", markdown)

    def test_distant_expiry_no_warning(self):
        self.run_report()
        markdown = (self.output / "latest.md").read_text(encoding="utf-8")
        self.assertNotIn("expires", markdown)

    def test_plain_http_host_refused(self):
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr):
            status = report.main(
                ["--config", str(self.config), "--index", str(self.index),
                 "--output", str(self.output),
                 "--api-url", "http://api.example.invalid"],
                environment={report.TOKEN_VARIABLE: TOKEN}, today=REPORT_DATE)
        self.assertEqual(status, FAILURE_EXIT)
        self.assertIn("https", stderr.getvalue())

    def test_invalid_organization_refused(self):
        self.config.write_text(json.dumps(
            {"organization": "acme/../other", "exclude": []}), encoding="utf-8")
        status, _, stderr = self.run_report()
        self.assertEqual(status, FAILURE_EXIT)
        self.assertIn("organization", stderr)


if __name__ == "__main__":
    unittest.main()
