"""Test one changelog entry per UTC day, independent of timezone and DST."""
import ast
import contextlib
import importlib.util
import io
import os
import subprocess
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

CHECKER_PATH = Path(__file__).resolve().parent.parent / "scripts" / "check_changelog.py"
REPOSITORY_CHANGELOG = CHECKER_PATH.parent.parent / "CHANGELOG.md"
BASE_HEADING = ("1.0.0", "2026-01-01")
ONE_DAY = timedelta(days=1)
ONE_SECOND = timedelta(seconds=1)
# Each UTC midnight sits on a DST transition day; the offsets are the ones
# in force on either side of that transition.
DST_TRANSITION_OFFSETS = (
    (date(2026, 3, 8), (timedelta(hours=-6), timedelta(hours=-5))),
    (date(2026, 11, 1), (timedelta(hours=-5), timedelta(hours=-6))),
    (date(2026, 3, 29), (timedelta(0), timedelta(hours=1))),
    (date(2026, 10, 25), (timedelta(hours=1), timedelta(0))),
    (date(2026, 4, 5), (timedelta(hours=11), timedelta(hours=10, minutes=30))),
    (date(2026, 10, 4), (timedelta(hours=10, minutes=30), timedelta(hours=11))),
    (date(2026, 6, 1), (timedelta(hours=5, minutes=45), timedelta(hours=14),
                        timedelta(hours=-12))),
)
# POSIX TZ strings with the UTC offsets, in seconds, that each may report.
POSIX_ZONES = (
    ("<+14>-14", {50400}),
    ("<-12>12", {-43200}),
    ("CST6CDT,M3.2.0,M11.1.0", {-21600, -18000}),
    ("<+1030>-10:30<+11>-11,M10.1.0,M4.1.0", {37800, 39600}),
)
WINDOWS_ZONES = (("EST5EDT", {-18000, -14400}), ("PST8PDT", {-28800, -25200}))
EXTREME_ZONES = ("<+14>-14", "<-12>12")
LOCAL_CLOCK_CALLS = frozenset({
    ("date", "today"), ("datetime", "today"), ("time", "localtime"), ("time", "mktime"),
})
PROBE_SOURCE = (
    "import time; moment = time.localtime(); "
    "print(time.strftime('%Y-%m-%d', moment), moment.tm_gmtoff)"
)


def _load_checker():
    """Import the changelog checker from its script path."""
    spec = importlib.util.spec_from_file_location("check_changelog", CHECKER_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def _changelog(*headings: tuple) -> str:
    """Return changelog text with one Fixed item per (version, date) heading."""
    sections = [f"## [{version}] ({release_date})\n\n### Fixed\n- Change {version}.\n\n"
                for version, release_date in headings]
    return "# Changelog\n\n" + "".join(sections)


def _utc_today() -> date:
    """Return the current UTC date for bracketing subprocess runs."""
    return datetime.now(timezone.utc).date()


def _run_git(repository: Path, *arguments: str) -> str:
    """Run a real git command in a throwaway repository and return stdout."""
    result = subprocess.run(
        ["git", *arguments], cwd=repository, check=True, capture_output=True,
        text=True, encoding="utf-8")
    return result.stdout.strip()


def _init_repository(repository: Path) -> str:
    """Create a repository whose first commit holds the base changelog."""
    _run_git(repository, "init", "-q", "-b", "main")
    _run_git(repository, "config", "user.name", "Test User")
    _run_git(repository, "config", "user.email", "test@example.com")
    return _commit_changelog(repository, _changelog(BASE_HEADING))


def _commit_changelog(repository: Path, changelog_text: str) -> str:
    """Commit a changelog text and return the new commit hash."""
    _stage_changelog(repository, changelog_text)
    _run_git(repository, "commit", "-q", "-m", "docs: update changelog")
    return _run_git(repository, "rev-parse", "HEAD")


def _stage_changelog(repository: Path, changelog_text: str) -> None:
    """Write and stage a changelog text."""
    (repository / "CHANGELOG.md").write_text(changelog_text, encoding="utf-8", newline="\n")
    _run_git(repository, "add", "CHANGELOG.md")


def _zone_environment(zone_spec: str) -> dict:
    """Return the current environment with TZ set to one zone."""
    child_environment = dict(os.environ)
    child_environment["TZ"] = zone_spec
    return child_environment


def _run_checker(checker_arguments: list, child_environment: dict) -> subprocess.CompletedProcess:
    """Run the real checker script in a child process."""
    return subprocess.run(
        [sys.executable, str(CHECKER_PATH), *checker_arguments], env=child_environment,
        check=False, capture_output=True, text=True, encoding="utf-8")


def _probe_zone(zone_spec: str) -> tuple:
    """Return (local date, UTC offset seconds) seen by a child in one zone."""
    result = subprocess.run(
        [sys.executable, "-c", PROBE_SOURCE], env=_zone_environment(zone_spec),
        check=True, capture_output=True, text=True, encoding="utf-8")
    local_date_text, offset_text = result.stdout.split()
    return date.fromisoformat(local_date_text), int(offset_text)


def _is_local_clock_call(call_node: ast.Call) -> bool:
    """Return whether an AST call reads local wall-clock time."""
    function = call_node.func
    if not isinstance(function, ast.Attribute) or not isinstance(function.value, ast.Name):
        return False
    owner_and_name = (function.value.id, function.attr)
    if owner_and_name in LOCAL_CLOCK_CALLS:
        return True
    if owner_and_name != ("datetime", "now"):
        return False
    return len(call_node.args) != 1 or ast.unparse(call_node.args[0]) != "timezone.utc"


class WholeFileDateTest(unittest.TestCase):
    """Exercise date rules across one changelog document."""

    def test_same_date_twice_fails(self):
        findings = checker.find_violations(
            _changelog(("1.0.1", "2026-09-14"), ("1.0.0", "2026-09-14")))
        self.assertTrue(any("repeats line" in finding for finding in findings), findings)

    def test_later_date_below_earlier_date_fails(self):
        findings = checker.find_violations(
            _changelog(("1.0.1", "2026-09-13"), ("1.0.0", "2026-09-14")))
        self.assertTrue(any("go back in time" in finding for finding in findings), findings)

    def test_strictly_descending_dates_pass(self):
        findings = checker.find_violations(
            _changelog(("1.0.2", "2026-09-15"), ("1.0.1", "2026-09-14"), ("1.0.0", "2026-09-13")))
        self.assertEqual(findings, [])

    def test_invalid_date_reports_only_itself(self):
        findings = checker.find_violations(
            _changelog(("1.0.1", "2026-02-30"), ("1.0.0", "2026-02-01")))
        self.assertEqual(len(findings), 1, findings)
        self.assertIn("invalid release date 2026-02-30", findings[0])

    def test_repository_changelog_passes(self):
        text = REPOSITORY_CHANGELOG.read_text(encoding="utf-8")
        self.assertEqual(checker.find_violations(text), [])


class RangeDateTest(unittest.TestCase):
    """Exercise date rules across a base and head changelog."""

    BASE = _changelog(("2.0.0", "2026-09-13"))
    CHANGED = ["CHANGELOG.md"]

    def test_same_day_rename_passes(self):
        head = _changelog(("2.0.1", "2026-09-13"))
        self.assertEqual(checker.find_range_violations(self.BASE, head, self.CHANGED), [])

    def test_keeping_both_same_day_headings_fails(self):
        head = _changelog(("2.0.1", "2026-09-13"), ("2.0.0", "2026-09-13"))
        findings = checker.find_range_violations(self.BASE, head, self.CHANGED)
        self.assertTrue(any("repeats line" in finding for finding in findings), findings)

    def test_head_date_before_base_date_fails(self):
        head = _changelog(("2.0.1", "2026-09-12"))
        findings = checker.find_range_violations(self.BASE, head, self.CHANGED)
        self.assertTrue(any("back in time" in finding for finding in findings), findings)

    def test_head_date_after_today_fails(self):
        head = _changelog(("2.0.1", "2026-09-14"), ("2.0.0", "2026-09-13"))
        findings = checker.find_range_violations(
            self.BASE, head, self.CHANGED, today=date(2026, 9, 13))
        self.assertTrue(any("after the UTC date" in finding for finding in findings), findings)

    def test_empty_base_is_not_compared(self):
        head = _changelog(("1.0.0", "2026-09-13"))
        findings = checker.find_range_violations("", head, self.CHANGED, today=date(2026, 9, 13))
        self.assertEqual(findings, [])


class StagedDateTest(unittest.TestCase):
    """Exercise staged rules without and with a real Git index."""

    HEAD = _changelog(("2.0.0", "2026-09-13"))
    STAGED = _changelog(("2.0.1", "2026-09-14"), ("2.0.0", "2026-09-13"))

    def test_today_date_passes(self):
        findings = checker._staged_findings(
            ["CHANGELOG.md"], self.STAGED, self.HEAD, date(2026, 9, 14))
        self.assertEqual(findings, [])

    def test_stale_date_fails(self):
        findings = checker._staged_findings(
            ["CHANGELOG.md"], self.STAGED, self.HEAD, date(2026, 9, 15))
        self.assertTrue(any("2026-09-15" in finding for finding in findings), findings)

    def test_missing_version_advance_fails(self):
        findings = checker._staged_findings(
            ["CHANGELOG.md"], self.HEAD, self.HEAD, date(2026, 9, 13))
        self.assertTrue(any("advance" in finding for finding in findings), findings)

    def test_unstaged_changelog_fails(self):
        findings = checker._staged_findings(
            ["README.md"], self.STAGED, self.HEAD, date(2026, 9, 14))
        self.assertTrue(any("CHANGELOG.md" in finding for finding in findings), findings)

    def test_unversioned_staged_changelog_returns_failure_without_raising(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            _init_repository(repository)
            _stage_changelog(repository, "# Changelog\n")
            self.assertEqual(checker.check_staged(repository, today=date(2026, 1, 1)), 1)

    def test_unstaged_changelog_reports_missing_entry(self):
        with tempfile.TemporaryDirectory() as directory:
            repository = Path(directory)
            _init_repository(repository)
            (repository / "README.md").write_text("# Project\n", encoding="utf-8", newline="\n")
            _run_git(repository, "add", "README.md")
            captured_errors = io.StringIO()
            with contextlib.redirect_stderr(captured_errors):
                exit_code = checker.check_staged(repository, today=date(2026, 1, 1))
            self.assertEqual(exit_code, 1)
            self.assertIn("staged changes require a versioned CHANGELOG.md entry",
                          captured_errors.getvalue())


class UtcDateConversionTest(unittest.TestCase):
    """Convert instants around DST transitions to their UTC date."""

    def test_offsets_around_utc_midnight_on_transition_days(self):
        for transition_day, offsets in DST_TRANSITION_OFFSETS:
            for offset in offsets:
                with self.subTest(day=transition_day, offset=offset):
                    self._assert_utc_dates_around_midnight(transition_day, offset)

    def _assert_utc_dates_around_midnight(self, transition_day: date, offset: timedelta):
        """Check one second before and at UTC midnight seen from one offset."""
        midnight = datetime.combine(transition_day, datetime.min.time(), timezone.utc)
        for utc_instant in (midnight - ONE_SECOND, midnight):
            local_instant = utc_instant.astimezone(timezone(offset))
            self.assertEqual(checker.utc_date(local_instant), utc_instant.date())

    def test_naive_datetime_is_rejected(self):
        with self.assertRaises(ValueError):
            checker.utc_date(datetime(2026, 3, 8, 23, 59, 59))


class ProcessTimezoneTest(unittest.TestCase):
    """Run the real checker under several process timezones."""

    ZONES = WINDOWS_ZONES if os.name == "nt" else POSIX_ZONES

    @classmethod
    def setUpClass(cls):
        cls._directory = tempfile.TemporaryDirectory()
        cls.staged_repository = Path(cls._directory.name) / "staged"
        cls.range_repository = Path(cls._directory.name) / "range"
        cls.staged_repository.mkdir()
        cls.range_repository.mkdir()
        _init_repository(cls.staged_repository)
        cls.range_base = _init_repository(cls.range_repository)

    @classmethod
    def tearDownClass(cls):
        cls._directory.cleanup()

    def test_utc_date_decides_every_verdict(self):
        for zone_spec, allowed_offsets in self.ZONES:
            with self.subTest(zone=zone_spec):
                self._assert_zone_verdicts(zone_spec, allowed_offsets)
        # The Windows C runtime cannot represent these POSIX zones; there the
        # utc_date conversion table carries the offset coverage instead.
        if os.name != "nt":
            self._assert_extreme_zones_differ_from_utc()

    def _assert_extreme_zones_differ_from_utc(self):
        """Prove the local-date failure path runs: +14 or -12 differs from UTC."""
        utc_before_probe = _utc_today()
        local_dates = {_probe_zone(zone_spec)[0] for zone_spec in EXTREME_ZONES}
        utc_after_probe = _utc_today()
        self.assertTrue(local_dates - {utc_before_probe, utc_after_probe}, local_dates)

    def _assert_zone_verdicts(self, zone_spec: str, allowed_offsets: set):
        """Check that UTC dates pass and differing local dates fail in one zone."""
        local_date, offset_seconds = _probe_zone(zone_spec)
        if os.name != "nt":
            self.assertIn(offset_seconds, allowed_offsets)
        self._assert_staged_verdict(zone_spec, _utc_today())
        self._assert_range_verdict(zone_spec, _utc_today())
        self._assert_staged_verdict(zone_spec, local_date)
        self._assert_range_verdict(zone_spec, local_date)

    def _assert_staged_verdict(self, zone_spec: str, entry_date: date):
        """Stage an entry dated entry_date and check the staged verdict."""
        _stage_changelog(self.staged_repository,
                         _changelog(("1.0.1", entry_date.isoformat()), BASE_HEADING))
        utc_before_run = _utc_today()
        result = _run_checker(["--staged", "--repo", str(self.staged_repository)],
                              _zone_environment(zone_spec))
        utc_after_run = _utc_today()
        if utc_before_run != utc_after_run:
            return
        expected_code = 0 if entry_date == utc_before_run else 1
        self.assertEqual(result.returncode, expected_code, result.stdout + result.stderr)

    def _assert_range_verdict(self, zone_spec: str, entry_date: date):
        """Commit an entry dated entry_date on the base and check the range verdict."""
        _run_git(self.range_repository, "checkout", "-q", "--detach", self.range_base)
        head = _commit_changelog(self.range_repository,
                                 _changelog(("1.0.1", entry_date.isoformat()), BASE_HEADING))
        utc_before_run = _utc_today()
        result = _run_checker(
            ["--repo", str(self.range_repository), "--base", self.range_base, "--head", head],
            _zone_environment(zone_spec))
        utc_after_run = _utc_today()
        if utc_before_run != utc_after_run:
            return
        expected_code = 1 if entry_date > utc_before_run else 0
        self.assertEqual(result.returncode, expected_code, result.stdout + result.stderr)


class StaticClockGuardTest(unittest.TestCase):
    """Keep local wall-clock reads out of the checker source."""

    def test_checker_reads_only_utc_time(self):
        tree = ast.parse(CHECKER_PATH.read_text(encoding="utf-8"))
        local_calls = [ast.unparse(node) for node in ast.walk(tree)
                       if isinstance(node, ast.Call) and _is_local_clock_call(node)]
        self.assertEqual(local_calls, [])


if __name__ == "__main__":
    unittest.main()
