#!/usr/bin/env python3
"""Cover how the adoption report rates the policy source repository.

The source repository holds docs/agent-policy/source-orientation.md and
calls its compliance workflow through a local path. Its synchronized copies
include the source orientation. The report must rate those copies and
that workflow call the way scripts/sync.py and the source layout define
them, not as adopter drift.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import sync

try:
    from tests.test_report_adoption_status import (
        REPORT_DATE,
        ReportFixture,
        build_current_files,
    )
except ImportError:
    from test_report_adoption_status import (
        REPORT_DATE,
        ReportFixture,
        build_current_files,
    )

SOURCE_REPOSITORY = "source"
SOURCE_ORIENTATION = sync.SOURCE_ONLY_POLICY_FILES[0]
LOCAL_WORKFLOW_CALL = (
    "jobs:\n  compliance:\n"
    "    uses: ./.github/workflows/agents-compliance.yml\n")
WORKFLOW_PATH = ".github/workflows/checks.yml"


def build_source_files() -> dict:
    """Return a source repository layout with source orientation."""
    files = build_current_files()
    files[SOURCE_ORIENTATION] = "# Source orientation\n"
    assembled = "".join(
        files[name] for name in
        (sync.SOURCE,) + tuple(sync.ADOPTER_POLICY_FILES)
        + tuple(sync.SOURCE_ONLY_POLICY_FILES))
    for name in sync.COPIES:
        files[name] = assembled
    files[WORKFLOW_PATH] = LOCAL_WORKFLOW_CALL
    return files


class SourceRepositoryTest(ReportFixture):
    """The source repository reports in-sync copies and a local CI call."""

    def setUp(self) -> None:
        super().setUp()
        self.server.organization[SOURCE_REPOSITORY] = {
            "files": build_source_files()}
        self.run_report()
        self.rows = self.read_rows()

    def test_source_orientation_counted(self) -> None:
        """Copies assembled with source orientation are in sync."""
        copies = self.rows[SOURCE_REPOSITORY]["copies"]
        self.assertEqual(copies["in_sync"], copies["total"])

    def test_adopter_copies_in_sync(
            self) -> None:
        """An adopter without the source orientation file keeps its count."""
        copies = self.rows["current"]["copies"]
        self.assertEqual(copies["in_sync"], copies["total"])

    def test_local_workflow_call_reported(self) -> None:
        """A local call to the compliance workflow sets ci.local."""
        wiring = self.rows[SOURCE_REPOSITORY]["ci"]
        self.assertTrue(wiring["local"])
        self.assertIsNone(wiring["reference"])
        self.assertFalse(wiring["pinned"])

    def test_pinned_adopter_is_not_local(self) -> None:
        """A pinned remote call keeps its reference and sets no local flag."""
        wiring = self.rows["current"]["ci"]
        self.assertFalse(wiring["local"])
        self.assertTrue(wiring["pinned"])

    def test_missing_workflow_not_local(self) -> None:
        """No compliance workflow call leaves the local flag false."""
        self.assertFalse(self.rows["bare"]["ci"]["local"])

    def test_markdown_names_the_local_call(self) -> None:
        """The Markdown CI column shows local for the source repository."""
        markdown = (self.output / f"{REPORT_DATE.isoformat()}.md").read_text(
            encoding="utf-8")
        source_line = next(line for line in markdown.splitlines()
                           if line.startswith(f"| {SOURCE_REPOSITORY} |"))
        self.assertTrue(source_line.rstrip().endswith("| local |"))


PINNED_CALL = (
    "jobs:\n  compliance:\n    uses: abuzucom/agents/.github/workflows/"
    "agents-compliance.yml@" + "b" * 40 + "\n")
COMMENTED_LOCAL_CALL = (
    "jobs:\n  compliance:\n"
    "    # uses: ./.github/workflows/agents-compliance.yml\n"
    "    runs-on: ubuntu-latest\n")


class MixedWorkflowTest(ReportFixture):
    """Every workflow file counts toward the local flag."""

    def setUp(self) -> None:
        super().setUp()
        organization = self.server.organization
        organization["remote-first"] = {"files": {
            ".github/workflows/a.yml": PINNED_CALL,
            ".github/workflows/z.yml": LOCAL_WORKFLOW_CALL}}
        organization["local-first"] = {"files": {
            ".github/workflows/a.yml": LOCAL_WORKFLOW_CALL,
            ".github/workflows/z.yml": PINNED_CALL}}
        organization["commented"] = {"files": {
            ".github/workflows/a.yml": COMMENTED_LOCAL_CALL}}
        self.run_report()
        self.rows = self.read_rows()

    def test_local_after_pinned_call(self) -> None:
        """A later local call still sets local beside the pinned reference."""
        for name in ("remote-first", "local-first"):
            with self.subTest(repository=name):
                wiring = self.rows[name]["ci"]
                self.assertTrue(wiring["local"])
                self.assertEqual(wiring["reference"], "b" * 40)
                self.assertTrue(wiring["pinned"])

    def test_commented_local_call_ignored(self) -> None:
        """A commented-out workflow call is not an active call."""
        self.assertFalse(self.rows["commented"]["ci"]["local"])


DOUBLE_QUOTED_LOCAL_CALL = (
    "jobs:\n  compliance:\n"
    '    uses: "./.github/workflows/agents-compliance.yml"\n')
SINGLE_QUOTED_LOCAL_CALL = (
    "jobs:\n  compliance:\n"
    "    uses: './.github/workflows/agents-compliance.yml'\n")


class QuotedLocalCallTest(ReportFixture):
    """A quoted local path is the same workflow call."""

    def setUp(self) -> None:
        super().setUp()
        organization = self.server.organization
        organization["double-quoted"] = {"files": {
            ".github/workflows/a.yml": DOUBLE_QUOTED_LOCAL_CALL}}
        organization["single-quoted"] = {"files": {
            ".github/workflows/a.yml": SINGLE_QUOTED_LOCAL_CALL}}
        self.run_report()
        self.rows = self.read_rows()

    def test_quoted_local_calls_are_reported(self) -> None:
        """Single and double quotes around the path still set local."""
        for name in ("double-quoted", "single-quoted"):
            with self.subTest(repository=name):
                self.assertTrue(self.rows[name]["ci"]["local"])
