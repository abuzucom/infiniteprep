#!/usr/bin/env python3
"""Behavioral tests for the local identifier checker (Rule 8).

Every non-placeholder identity is generated at test time. No test reads the
real account name, home directory, or hostname, and no assertion message
interpolates an environment value.
"""
import ast
import importlib.util
import json
import os
import secrets
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "check_local_paths.py"


def _load_checker():
    """Load the checker from its exact script path."""
    spec = importlib.util.spec_from_file_location("check_local_paths", SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load scripts/check_local_paths.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


checker = _load_checker()


def generated_name() -> str:
    """Return a fresh synthetic account name that no placeholder list holds."""
    return "z" + secrets.token_hex(4)


def scanner(allowlist_text: str = "", identity=None):
    """Build a scanner with an explicit allowlist and optional identity."""
    return checker.build_scanner(allowlist_text, identity or checker.Identity())


def profile_forms(name: str) -> list[str]:
    """Return one line per supported user-profile path form."""
    return [
        "C:\\Users\\" + name + "\\Music\\x.mp3",
        "c:/users/" + name + "/x",
        "D:\\Documents and Settings\\" + name + "\\x",
        "/Users/" + name + "/project",
        "/home/" + name + "/project",
        "/var/home/" + name + "/project",
        "/mnt/c/Users/" + name + "/project",
        "file:///C:/Users/" + name + "/.agentclient/worktrees/w/x.js",
        "\\\\?\\C:\\Users\\" + name + "\\x",
    ]


class ProfilePathTest(unittest.TestCase):
    """Check 2 flags profile paths whose user segment is a real-looking name."""

    def test_incident_shaped_link_is_caught(self):
        name = generated_name()
        line = ("see [utils](file:///C:/Users/" + name
                + "/.agentclient/worktrees/" + generated_name() + "/x.js)")
        findings = scanner().scan_text(line, "docs/x.md")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].line, 1)

    def test_every_profile_form_is_caught(self):
        name = generated_name()
        for line in profile_forms(name):
            with self.subTest(form=profile_forms(name).index(line)):
                self.assertEqual(len(scanner().scan_text(line, "a.txt")), 1)

    def test_placeholders_pass(self):
        for name in ("u", "user", "me", "someone", "dev", "runner",
                     "<name>", "$USER", "%USERNAME%", "${HOME}"):
            for line in profile_forms(name):
                with self.subTest(name=name):
                    self.assertEqual(scanner().scan_text(line, "a.txt"), [])

    def test_non_identifying_absolute_paths_pass(self):
        lines = [
            "#!/usr/bin/env python3", "cmd > /dev/null", "rm -rf /etc/nginx",
            "K:\\Music\\Trance\\x.flac", "D:\\x.mp3", "C:\\work\\build",
            "C:\\Program Files\\nodejs\\node.exe", "file:///etc/passwd",
            "file:///C:/path/to.mjs", "\\\\attacker.example\\share",
            "/Applications/Native Instruments/Traktor.app",
            "https://example.com/users/" + generated_name(),
            "/Volumes/Backup", "ApiDeck /Volumes/ branch",
        ]
        for line in lines:
            with self.subTest(index=lines.index(line)):
                self.assertEqual(scanner().scan_text(line, "a.txt"), [])

    def test_dot_segments_are_not_names(self):
        for line in ("rm -rf /home/../etc", "cd /Users/./x", "C:\\Users\\..\\x"):
            with self.subTest(line=line):
                self.assertEqual(scanner().scan_text(line, "a.txt"), [])

    def test_line_numbers_come_from_original_text(self):
        name = generated_name()
        text = "one\ntwo\n/home/" + name + "/x\n"
        findings = scanner().scan_text(text, "a.txt")
        self.assertEqual([finding.line for finding in findings], [3])


class NormalizationTest(unittest.TestCase):
    """H6: encoded and obfuscated names are still caught."""

    def test_encoded_forms_are_caught(self):
        name = generated_name()
        spaced = "\u200b".join(name)
        forms = [
            "C:\\\\Users\\\\" + name + "\\\\x",
            "C:\\u005cUsers\\u005c" + name,
            "file:///C:%5CUsers%5C" + name + "%5Cx",
            "C:&#92;Users&#92;" + name,
            "/\uff35sers/" + name + "/x",
            "/home/" + spaced + "/x",
            "/HOME/" + name.upper() + "/x",
        ]
        for line in forms:
            with self.subTest(index=forms.index(line)):
                self.assertEqual(len(scanner().scan_text(line, "a.txt")), 1)

    def test_escape_decoding_does_not_hide_raw_names(self):
        name = "u" + "beef" + secrets.token_hex(2)
        line = "C:\\Users\\" + name + "\\x"
        self.assertEqual(len(scanner().scan_text(line, "a.txt")), 1)

    def test_utf16_blob_is_scanned(self):
        name = generated_name()
        content = ("/home/" + name + "/x\n").encode("utf-16")
        text = checker.decode_blob(content)
        self.assertEqual(len(scanner().scan_text(text, "a.txt")), 1)

    def test_invalid_bytes_do_not_hide_names(self):
        name = generated_name()
        content = b"\xff\xfe\xfd /home/" + name.encode() + b"/x"
        text = checker.decode_blob(content)
        self.assertEqual(len(scanner().scan_text(text, "a.txt")), 1)

    def test_scanned_file_path_is_checked(self):
        name = generated_name()
        findings = scanner().scan_path("fixtures/home/" + name + "/x.txt")
        self.assertEqual(findings, [])
        findings = scanner().scan_path("C:/Users/" + name + "/x.txt")
        self.assertEqual(len(findings), 1)


class AllowlistTest(unittest.TestCase):
    """H9 and H10: exact file and name pairs, validated before matching."""

    def test_pair_exempts_only_its_file(self):
        name = generated_name()
        text = "/Users/" + name + "/x"
        active = scanner("tests/a.ts\t" + name + "\n")
        self.assertEqual(active.scan_text(text, "tests/a.ts"), [])
        self.assertEqual(len(active.scan_text(text, "tests/b.ts")), 1)

    def test_comments_and_blank_lines_are_valid(self):
        self.assertIsNotNone(scanner("# header\n\n"))

    def test_invalid_entries_fail_closed(self):
        invalid = [
            "*\tname", "a.ts\t*", "a.ts\tx", "../a.ts\tname", "/abs\tname",
            "a.ts\tna me", "a.ts", "a.ts\tname\textra", "a.ts\tna\x07me",
            "dir/*.ts\tname",
        ]
        for entry in invalid:
            with self.subTest(index=invalid.index(entry)):
                with self.assertRaises(checker.AllowlistError):
                    scanner(entry + "\n")

    def test_error_names_line_not_value(self):
        name = generated_name()
        with self.assertRaises(checker.AllowlistError) as raised:
            scanner("a.ts\t" + name + "*\n")
        self.assertNotIn(name, str(raised.exception))
        self.assertIn("line 1", str(raised.exception))

    def test_runtime_home_leaf_entry_is_rejected(self):
        leaf = generated_name()
        identity = checker.Identity(homes=("/home/" + leaf,))
        with self.assertRaises(checker.AllowlistError):
            scanner("a.ts\t" + leaf + "\n", identity)

    def test_runtime_identity_entry_is_rejected(self):
        name = generated_name()
        identity = checker.Identity(names=(name,))
        with self.assertRaises(checker.AllowlistError):
            scanner("a.ts\t" + name + "\n", identity)


class IdentityTest(unittest.TestCase):
    """Check 1: runtime identity values, bucketed before matching."""

    def test_full_identity_token_is_caught_anywhere(self):
        name = generated_name()
        host = "h" + secrets.token_hex(5)
        identity = checker.Identity(names=(name,), hosts=(host,))
        active = scanner("", identity)
        self.assertEqual(len(active.scan_text("by " + name, "a.txt")), 1)
        self.assertEqual(len(active.scan_text("on " + host + ".local", "a.txt")), 1)
        self.assertEqual(active.scan_text("by x" + name + "x", "a.txt"), [])

    def test_regex_metacharacters_match_literally(self):
        identity = checker.Identity(names=("a.b+c",))
        active = scanner("", identity)
        self.assertEqual(active.scan_text("aXb+c", "a.txt"), [])
        self.assertEqual(len(active.scan_text("by a.b+c", "a.txt")), 1)

    def test_short_name_matches_only_as_path_segment(self):
        short = "q" + secrets.choice("bcdfghjkmnpqrstvwxz")
        identity = checker.Identity(names=(short,))
        active = scanner("", identity)
        self.assertEqual(active.scan_text(short + " appears as a word", "a"), [])
        self.assertEqual(len(active.scan_text("/home/" + short + "/x", "a")), 1)

    def test_placeholder_and_system_values_are_skipped(self):
        identity = checker.Identity(names=("root", "runner", "user"),
                                    hosts=("localhost",))
        active = scanner("", identity)
        self.assertEqual(active.scan_text("repository root on localhost", "a"), [])
        self.assertIn("account name", active.skipped_categories)

    def test_empty_identity_is_reported(self):
        active = scanner("", checker.Identity())
        self.assertFalse(active.has_identity)

    def test_home_at_nonstandard_root_is_caught(self):
        name = generated_name()
        identity = checker.Identity(homes=("D:\\Profiles\\" + name,))
        active = scanner("", identity)
        line = "D:/Profiles/" + name + "/x"
        self.assertEqual(len(active.scan_text(line, "a.txt")), 1)

    def test_environment_values_add_but_never_replace(self):
        name = generated_name()
        fake = "y" + secrets.token_hex(4)
        with patch.object(checker, "_os_identity",
                          return_value=checker.Identity(names=(name,))):
            with patch.dict(os.environ, {"USER": fake, "HOME": "/tmp"}, clear=False):
                collected = checker.collect_identity()
        self.assertIn(name, collected.names)
        self.assertIn(fake, collected.names)

    def test_missing_passwd_entry_is_reported_not_raised(self):
        warnings = []
        with patch.object(checker, "_posix_account", side_effect=KeyError(1)):
            identity = checker._os_identity(warnings.append)
        self.assertIsInstance(identity, checker.Identity)
        if os.name != "nt":
            self.assertTrue(any("account" in warning for warning in warnings))


class OutputTest(unittest.TestCase):
    """Self-disclosure guard and J5 sanitizing."""

    def test_finding_message_redacts_name(self):
        name = generated_name()
        finding = scanner().scan_text("/home/" + name + "/x", "a.txt")[0]
        self.assertNotIn(name, finding.message(reveal=False))
        self.assertIn(name, finding.message(reveal=True))

    def test_identity_match_never_reveals_value(self):
        name = generated_name()
        active = scanner("", checker.Identity(names=(name,)))
        finding = active.scan_text("by " + name, "a.txt")[0]
        self.assertNotIn(name, finding.message(reveal=True))
        self.assertIn("account name", finding.message(reveal=True))

    def test_sanitize_neutralizes_control_and_workflow_commands(self):
        text = checker.sanitize("x\n::error::forged\x1b[31m")
        self.assertNotIn("\n", text)
        self.assertNotIn("\x1b", text)
        self.assertFalse(checker.sanitize("::add-mask::x").startswith("::"))


def run_cli(arguments: list[str], cwd: Path, environment: dict | None = None):
    """Run the checker CLI in isolated mode with an argument array."""
    env = {key: value for key, value in os.environ.items()
           if key not in ("CI", "GITHUB_ACTIONS", "GITHUB_EVENT_PATH")}
    env.update(environment or {})
    return subprocess.run(
        [sys.executable, "-I", str(SCRIPT), *arguments],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=60,
    )


class CliTest(unittest.TestCase):
    """File, report, event, and allowlist-presence behavior."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.allowlist = self.root / ".local-path-allowlist"
        self.allowlist.write_text("# approved pairs\n", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def _cli(self, arguments, environment=None):
        return run_cli(arguments, self.root, environment)

    def test_clean_file_passes_and_dirty_file_fails_redacted(self):
        name = generated_name()
        (self.root / "clean.txt").write_text("/home/u/x\n", encoding="utf-8")
        (self.root / "dirty.txt").write_text("/home/" + name + "/x\n", encoding="utf-8")
        self.assertEqual(self._cli(["clean.txt"]).returncode, 0)
        result = self._cli(["dirty.txt"])
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(name, result.stdout + result.stderr)

    def test_report_lists_every_finding_with_proposals(self):
        names = [generated_name() for _ in range(3)]
        body = "".join("/home/" + name + "/x\n" for name in names)
        (self.root / "f.txt").write_text(body, encoding="utf-8")
        result = self._cli(["--report", "f.txt"])
        self.assertEqual(result.returncode, 0)
        for name in names:
            self.assertIn("f.txt\t" + name, result.stdout)

    def test_report_prints_procedure_first(self):
        (self.root / "f.txt").write_text("clean\n", encoding="utf-8")
        result = self._cli(["--report", "f.txt"])
        self.assertEqual(result.returncode, 0)
        self.assertTrue(result.stdout.startswith("Local-path disclosure procedure"))
        self.assertIn("Omit no finding", result.stdout)

    def test_report_refused_in_ci(self):
        (self.root / "f.txt").write_text("x\n", encoding="utf-8")
        result = self._cli(["--report", "f.txt"], {"CI": "true"})
        self.assertEqual(result.returncode, 2)

    def test_missing_allowlist_is_partial_adoption(self):
        self.allowlist.unlink()
        (self.root / "f.txt").write_text("x\n", encoding="utf-8")
        result = self._cli(["f.txt"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("partially adopted", result.stderr)

    def test_unreadable_file_fails_closed(self):
        result = self._cli(["missing.txt"])
        self.assertEqual(result.returncode, 1)
        self.assertIn("Recovery", result.stderr)

    def test_event_title_and_body_are_scanned(self):
        name = generated_name()
        event = {"pull_request": {"title": "fix", "body": "/home/" + name + "/x"}}
        path = self.root / "event.json"
        path.write_text(json.dumps(event), encoding="utf-8")
        result = self._cli(["--event"], {"GITHUB_EVENT_PATH": str(path)})
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(name, result.stdout + result.stderr)

    def test_planted_stdlib_module_is_not_imported(self):
        scripts = REPO_ROOT / "scripts"
        self.assertFalse((scripts / "re.py").exists())
        result = run_cli(["--help"], self.root)
        self.assertEqual(result.returncode, 0)


def _git(repo: Path, *arguments: str) -> str:
    """Run git with an argument array inside a temporary repository."""
    environment = dict(os.environ)
    environment.update({
        "GIT_AUTHOR_NAME": "Test Author", "GIT_COMMITTER_NAME": "Test Author",
        "GIT_AUTHOR_EMAIL": "author@example.invalid",
        "GIT_COMMITTER_EMAIL": "author@example.invalid",
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
    })
    result = subprocess.run(["git", *arguments], cwd=repo, env=environment,
                            capture_output=True, text=True, check=True)
    return result.stdout.strip()


class GitRangeTest(unittest.TestCase):
    """Range and unpushed modes read content, paths, and messages in bulk."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)
        _git(self.repo, "init", "-q", "-b", "main")
        (self.repo / "a.txt").write_text("start\n", encoding="utf-8")
        _git(self.repo, "add", "a.txt")
        _git(self.repo, "commit", "-q", "-m", "chore: start")
        self.base = _git(self.repo, "rev-parse", "HEAD")
        (self.repo / ".local-path-allowlist").write_text("", encoding="utf-8")

    def tearDown(self):
        self.temporary.cleanup()

    def _cli(self, arguments):
        return run_cli(arguments, self.repo)

    def _commit(self, path: str, text: str, message: str) -> str:
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
        _git(self.repo, "add", "--", path)
        _git(self.repo, "commit", "-q", "-m", message)
        return _git(self.repo, "rev-parse", "HEAD")

    def test_range_flags_added_content(self):
        head = self._commit("b.txt", "/home/" + generated_name() + "/x\n", "feat: b")
        result = self._cli(["--base", self.base, "--head", head])
        self.assertEqual(result.returncode, 1)

    def test_range_flags_message_and_path(self):
        name = generated_name()
        head = self._commit("c.txt", "clean\n", "docs: see /Users/" + name + "/x")
        self.assertEqual(self._cli(["--base", self.base, "--head", head]).returncode, 1)
        # A colon cannot appear in a Windows file name, so the path case uses
        # a synthetic log record in the exact `_git_log` output format.
        log = ("\x1e" + "a" * 40 + "\x1fdocs: ok\n\x1d\n"
               "+++ b/C:/Users/" + name + ".txt\n@@ -0,0 +1 @@\n+clean\n")
        findings = checker.scan_log(scanner(), log)
        self.assertEqual(len(findings), 1)

    def test_range_clean_passes(self):
        head = self._commit("d.txt", "/home/u/x\n", "docs: clean")
        self.assertEqual(self._cli(["--base", self.base, "--head", head]).returncode, 0)

    def test_unpushed_without_remote_scans_all_commits(self):
        self._commit("e.txt", "/home/" + generated_name() + "/x\n", "feat: e")
        self.assertEqual(self._cli(["--unpushed"]).returncode, 1)

    def test_range_rejects_non_sha_revisions(self):
        result = self._cli(["--base", "--output=/tmp/x", "--head", "HEAD"])
        self.assertEqual(result.returncode, 2)


class InstallerTest(unittest.TestCase):
    """The adoption installer refuses while findings are unresolved."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)
        _git(self.repo, "init", "-q", "-b", "main")
        scripts = self.repo / "scripts"
        scripts.mkdir()
        for name in ("check_local_paths.py", "trusted_git.py"):
            (scripts / name).write_bytes((REPO_ROOT / "scripts" / name).read_bytes())
        (self.repo / ".local-path-allowlist").write_text("", encoding="utf-8")
        spec = importlib.util.spec_from_file_location(
            "complete_gate_adoption", REPO_ROOT / "scripts" / "complete_gate_adoption.py")
        self.installer = importlib.util.module_from_spec(spec)
        sys.path.insert(0, str(REPO_ROOT / "scripts"))
        try:
            spec.loader.exec_module(self.installer)
        finally:
            sys.path.remove(str(REPO_ROOT / "scripts"))

    def tearDown(self):
        self.temporary.cleanup()

    def _track(self, text: str) -> None:
        (self.repo / "fixture.txt").write_text(text, encoding="utf-8")
        _git(self.repo, "add", "--all")

    def test_unresolved_finding_refuses(self):
        self._track("/home/" + generated_name() + "/x\n")
        with self.assertRaises(ValueError):
            self.installer._validate_local_paths(self.repo)

    def test_approved_entry_allows(self):
        name = generated_name()
        self._track("/home/" + name + "/x\n")
        (self.repo / ".local-path-allowlist").write_text(
            "fixture.txt\t" + name + "\n", encoding="utf-8")
        self.installer._validate_local_paths(self.repo)

    def test_missing_allowlist_refuses(self):
        self._track("clean\n")
        (self.repo / ".local-path-allowlist").unlink()
        with self.assertRaises(ValueError):
            self.installer._validate_local_paths(self.repo)


class ScalingTest(unittest.TestCase):
    """F-series: work grows linearly with input lines."""

    def test_normalization_runs_once_per_line(self):
        calls = []
        original = checker._normalize_line

        def counting(line):
            calls.append(1)
            return original(line)

        lines = "\n".join("plain text line %d" % index for index in range(1000))
        with patch.object(checker, "_normalize_line", side_effect=counting):
            scanner().scan_text(lines, "a.txt")
        self.assertLessEqual(len(calls), 1000)

    def test_adversarial_input_is_bounded(self):
        line = "/home/" + "a" * 200_000 + "\\" * 200_000
        findings = scanner().scan_text(line, "a.txt")
        self.assertEqual(len(findings), 1)


class HandlerShapeTest(unittest.TestCase):
    """Rule 13 mechanical check: no empty or silent except blocks."""

    def test_no_silent_handlers(self):
        tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
        silent = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.ExceptHandler):
                continue
            body = node.body
            only_noop = all(
                isinstance(statement, (ast.Pass, ast.Continue))
                or (isinstance(statement, ast.Expr)
                    and isinstance(statement.value, ast.Constant))
                for statement in body)
            if only_noop:
                silent.append(node.lineno)
        self.assertEqual(silent, [])


if __name__ == "__main__":
    unittest.main()
