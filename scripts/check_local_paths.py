#!/usr/bin/env python3
"""Block real local identifiers in committed text (Rule 8).

Two checks share one pass per line:

- Check 2 flags user-profile paths (`C:\\Users\\<name>`, `/Users/<name>`,
  `/home/<name>`, and the same paths inside `file:` URIs) whose user segment
  is not a placeholder and is not an approved `.local-path-allowlist` pair.
- Check 1 runs only on a developer machine. It flags the local account name,
  home directory, and computer name, read from OS APIs at run time.

The checker never stores, logs, or echoes a runtime identity value. Only
`--report`, which refuses to run in CI, prints committed user-segment names
so that a human can approve allowlist entries one by one.

Exit codes: 0 clean, 1 findings or a fail-closed error, 2 usage error.
"""
import argparse
import html
import importlib.util
import json
import os
import platform
import re
import socket
import sys
import unicodedata
import urllib.parse
from dataclasses import dataclass, field
from pathlib import Path

ALLOWLIST_NAME = ".local-path-allowlist"
MAX_ALLOWLIST_BYTES = 256 * 1024
MAX_ALLOWLIST_ENTRIES = 10_000
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TOTAL_BYTES = 256 * 1024 * 1024
MAX_LOG_CHARACTERS = 256 * 1024 * 1024
MAX_COMMITS = 1_000
MAX_EVENT_BYTES = 1024 * 1024
MAX_PRINTED_FINDINGS = 1_000
MAX_SANITIZED_LENGTH = 300
# Shorter identity values match ordinary words, so they count only as a
# profile path segment.
MIN_FULL_TOKEN_LENGTH = 4
OBJECT_ID_PATTERN = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")

PLACEHOLDER_NAMES = frozenset((
    "u", "user", "username", "me", "you", "someone", "somebody", "dev",
    "developer", "runner", "example", "name", "test", "demo",
))
SYSTEM_ACCOUNTS = frozenset((
    "root", "admin", "administrator", "nobody", "localhost", "guest",
    "default", "public", "system", "ubuntu", "vagrant", "codespace",
))
TEMPLATE_PREFIXES = ("<", "$", "%", "{", "~")
ACCOUNT, HOME, HOST = "account name", "home directory", "computer name"

# Each alternative is anchored by a lookbehind and a fixed root, and the
# name class excludes separators, so matching stays linear.
PROFILE_PATTERN = re.compile(
    r"(?:(?<![a-z0-9_])[a-z]:[\\/]+(?:users|documents and settings)"
    r"|(?<![\w.])/mnt/[a-z]/users"
    r"|(?<![\w.])/(?:users|home|var/home))"
    # A user segment never starts with a dot, so `/home/..` is traversal.
    r"[\\/]+([^\\/\s\"'`<>|:*?,;()\[\]{}.][^\\/\s\"'`<>|:*?,;()\[\]{}]*)"
)
ESCAPE_PATTERN = re.compile(r"\\(?:u([0-9a-fA-F]{4})|x([0-9a-fA-F]{2})|(\\))")
FAST_PATH_BLOCKERS = ("\\", "%", "&", "\x00")
ALLOWLIST_PATH_PATTERN = re.compile(r"[A-Za-z0-9._@+=-]+(?:/[A-Za-z0-9._@+= -]+)*")
ALLOWLIST_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{1,63}")
HUNK_PATTERN = re.compile(r"@@ -[0-9,]+ \+([0-9]+)")
DISCLOSURE_PROCEDURE = """\
Local-path disclosure procedure (README "Local-Path Gate"):
1. Scan every tracked file with --report. This mode refuses to run in CI.
2. Disclose every finding below to the active human. Omit no finding.
3. Propose one <file><TAB><name> line per finding. For a name that may be
   real, also propose a placeholder replacement. Decide each one separately.
4. Never propose an allowlist entry for a runtime identity match.
5. Write only the approved lines.
6. Adopt nothing while any finding remains unresolved.
7. Keep findings, names, and decisions out of commits, pull requests, the
   changelog, handoff files, and CI logs."""


class AllowlistError(ValueError):
    """The allowlist is malformed or names a runtime identity."""


class AllowlistMissingError(AllowlistError):
    """The allowlist file is absent, so the gate is partially adopted."""


class ScanLimitError(RuntimeError):
    """Content exceeds a scan bound, so it was not fully scanned."""


@dataclass(frozen=True)
class Identity:
    """Runtime identity values. They are held in memory only."""

    names: tuple = ()
    homes: tuple = ()
    hosts: tuple = ()


@dataclass(frozen=True)
class Finding:
    """One match. A profile finding carries the committed name."""

    path: str
    line: int
    kind: str
    detail: str
    name: str = ""

    def message(self, reveal: bool) -> str:
        """Return one sanitized line. Identity values never appear."""
        where = f"{sanitize(self.path)}:{self.line}"
        if self.kind == "identity":
            return (f"{where}: matches this computer's {self.detail} (Rule 8). "
                    "Fix: replace it with a placeholder.")
        shown = sanitize(self.name) if reveal else "<redacted>"
        return (f"{where}: user-profile path names '{shown}' (Rule 8). Fix: "
                "use a placeholder or a repository-relative path.")


def sanitize(value: object) -> str:
    """Render untrusted text as one bounded line of printable ASCII."""
    text = "".join(ch if " " <= ch <= "~" else "?" for ch in str(value))
    text = text.lstrip(":")
    if len(text) > MAX_SANITIZED_LENGTH:
        return text[:MAX_SANITIZED_LENGTH] + "...[truncated]"
    return text


def _decode_escape(match: re.Match) -> str:
    """Replace one JSON or C escape with the character it encodes."""
    code = match.group(1) or match.group(2)
    return chr(int(code, 16)) if code else "\\"


def _fold(text: str) -> str:
    """Apply NFKC, drop NUL and format characters, and casefold."""
    text = unicodedata.normalize("NFKC", text)
    text = "".join(ch for ch in text
                   if ch != "\x00" and unicodedata.category(ch) != "Cf")
    return text.casefold()


def _normalize_line(line: str) -> tuple:
    """Return the matching forms of one line, cheapest path first.

    A plain ASCII line without escape, percent, entity, or NUL characters
    cannot change under decoding or NFKC, so lowercasing is enough.
    """
    if line.isascii() and not any(ch in line for ch in FAST_PATH_BLOCKERS):
        return (line.lower(),)
    raw = _fold(line)
    decoded = _fold(html.unescape(urllib.parse.unquote(
        ESCAPE_PATTERN.sub(_decode_escape, line))))
    return (raw,) if decoded == raw else (raw, decoded)


def _is_placeholder(name: str) -> bool:
    """Return whether a casefolded name is a placeholder or template."""
    return name in PLACEHOLDER_NAMES or name.startswith(TEMPLATE_PREFIXES)


def decode_blob(content: bytes) -> str:
    """Decode BOM-aware Unicode, using Latin-1 as a one-to-one fallback.

    Latin-1 maps every byte, so decoding never fails and no byte is dropped.
    """
    boms = ((b"\xff\xfe\x00\x00", "utf-32"), (b"\x00\x00\xfe\xff", "utf-32"),
            (b"\xef\xbb\xbf", "utf-8-sig"), (b"\xff\xfe", "utf-16"),
            (b"\xfe\xff", "utf-16"))
    encodings = [name for marker, name in boms if content.startswith(marker)][:1]
    encodings += _bomless_wide_encodings(content) + ["utf-8"]
    for encoding in encodings:
        text = _decode_as(content, encoding)
        if text is not None:
            return text.lstrip("\ufeff")
    return content.decode("latin-1")


def _decode_as(content: bytes, encoding: str) -> str | None:
    """Decode with one candidate encoding, or return None when it misfits."""
    try:
        return content.decode(encoding)
    except UnicodeDecodeError:
        # A misfit is an expected result: the caller tries the next
        # candidate and ends at Latin-1, which accepts every byte.
        return None


def _bomless_wide_encodings(content: bytes) -> list:
    """Return UTF-16 candidates whose NUL byte pattern identifies them."""
    if len(content) < 2 or len(content) % 2:
        return []
    even = content[0::2].count(0) / (len(content) // 2)
    odd = content[1::2].count(0) / (len(content) // 2)
    if odd > 0.4 and even < 0.2:
        return ["utf-16-le"]
    if even > 0.4 and odd < 0.2:
        return ["utf-16-be"]
    return []


def parse_allowlist(text: str) -> frozenset:
    """Return validated (path, casefolded name) pairs.

    Validation finishes before any matching. One bad line fails the whole
    run, and errors name the line number, never the value.
    """
    if len(text.encode("utf-8")) > MAX_ALLOWLIST_BYTES:
        raise AllowlistError(f"{ALLOWLIST_NAME} exceeds {MAX_ALLOWLIST_BYTES} bytes")
    pairs = set()
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip() or line.startswith("#"):
            continue
        fields = line.split("\t")
        valid = (
            len(fields) == 2
            and ALLOWLIST_PATH_PATTERN.fullmatch(fields[0]) is not None
            and ".." not in fields[0].split("/")
            and ALLOWLIST_NAME_PATTERN.fullmatch(fields[1]) is not None
        )
        if not valid:
            raise AllowlistError(
                f"{ALLOWLIST_NAME} line {number} is invalid. Recovery: use "
                "'<tracked repo-relative file><TAB><name>', where the name "
                "has 2 or more letters, digits, '.', '-' or '_'.")
        pairs.add((fields[0], fields[1].casefold(), number))
        if len(pairs) > MAX_ALLOWLIST_ENTRIES:
            raise AllowlistError(
                f"{ALLOWLIST_NAME} exceeds {MAX_ALLOWLIST_ENTRIES} entries")
    return frozenset(pairs)


def _bucket_identity(identity: Identity) -> tuple:
    """Sort identity values into full tokens, short names, homes, skips.

    The buckets are disjoint, so later code never re-checks a skipped value.
    """
    full, short, homes, skipped = {}, set(), [], set()
    for category, values in ((ACCOUNT, identity.names), (HOST, identity.hosts)):
        for value in values:
            folded = _fold(value).strip()
            labels = {folded, folded.split(".", 1)[0]} if category == HOST else {folded}
            for label in filter(None, labels):
                if _is_placeholder(label) or label in SYSTEM_ACCOUNTS:
                    skipped.add(category)
                elif len(label) < MIN_FULL_TOKEN_LENGTH:
                    short.add(label)
                else:
                    full[label] = category
    for value in identity.homes:
        home = _fold(value).replace("\\", "/").rstrip("/")
        leaf = home.rsplit("/", 1)[-1]
        if not leaf or _is_placeholder(leaf) or leaf in SYSTEM_ACCOUNTS:
            skipped.add(HOME)
        else:
            homes.append(home)
    return full, frozenset(short), tuple(homes), frozenset(skipped)


def _token_pattern(values) -> re.Pattern | None:
    """Compile one escaped alternation, longest first, or return None."""
    if not values:
        return None
    alternation = "|".join(re.escape(value)
                           for value in sorted(values, key=len, reverse=True))
    return re.compile(rf"(?<![a-z0-9_])(?:{alternation})(?![a-z0-9_])")


@dataclass
class Scanner:
    """Compiled, immutable scan state. Build once per process (F1)."""

    allowlist: frozenset
    pair_set: frozenset = frozenset()
    full_identity: dict = field(default_factory=dict)
    short_names: frozenset = frozenset()
    homes: tuple = ()
    skipped_categories: frozenset = frozenset()
    token_pattern: re.Pattern | None = None
    home_pattern: re.Pattern | None = None

    @property
    def has_identity(self) -> bool:
        """Return whether Check 1 has at least one value to protect."""
        return bool(self.full_identity or self.short_names or self.homes)

    def scan_path(self, path: str) -> list:
        """Scan a repository path string itself (H7)."""
        return self.scan_line(path, path, 0)

    def scan_text(self, text: str, path: str) -> list:
        """Scan decoded text line by line. Line numbers are 1-based."""
        findings = []
        for number, line in enumerate(text.splitlines(), start=1):
            findings.extend(self.scan_line(line, path, number))
        return findings

    def scan_line(self, line: str, path: str, number: int) -> list:
        """Return findings for one line, deduplicated across its forms.

        The raw form wins. A decoded form is consulted only when the raw
        form has no finding, so one name never counts twice.
        """
        found = {}
        for form in _normalize_line(line):
            for key, finding in self._match_form(form, path, number):
                found.setdefault(key, finding)
            if found:
                break
        return list(found.values())

    def _match_form(self, form: str, path: str, number: int):
        """Yield (key, finding) pairs for one normalized form."""
        for match in PROFILE_PATTERN.finditer(form):
            name = match.group(1)
            if name in self.full_identity or name in self.short_names:
                yield ACCOUNT, Finding(path, number, "identity", ACCOUNT)
            elif not _is_placeholder(name) and (path, name) not in self.pair_set:
                yield name, Finding(path, number, "profile", "profile", name)
        if self.token_pattern is not None:
            for match in self.token_pattern.finditer(form):
                category = self.full_identity[match.group(0)]
                yield category, Finding(path, number, "identity", category)
        if self.home_pattern is not None and self.home_pattern.search(
                form.replace("\\", "/")):
            yield HOME, Finding(path, number, "identity", HOME)


def build_scanner(allowlist_text: str, identity: Identity) -> Scanner:
    """Validate the allowlist, bucket the identity, and compile patterns."""
    pairs = parse_allowlist(allowlist_text)
    full, short, homes, skipped = _bucket_identity(identity)
    # A home leaf can differ from the account name, so it counts as a
    # runtime identity value too.
    protected = set(full) | short | {home.rsplit("/", 1)[-1] for home in homes}
    for _path, name, number in pairs:
        if name in protected:
            raise AllowlistError(
                f"{ALLOWLIST_NAME} line {number} names this computer's account, "
                "home, or host. Recovery: replace the committed name with a "
                "placeholder instead of allowlisting it.")
    home_pattern = None
    if homes:
        alternation = "|".join(re.escape(home) for home in homes)
        home_pattern = re.compile(rf"(?:{alternation})(?![a-z0-9_.-])")
    # The pair set is read once per match, so it is built once here (F4).
    pair_set = frozenset((entry[0], entry[1]) for entry in pairs)
    return Scanner(pairs, pair_set, full, short, homes, skipped,
                   _token_pattern(full), home_pattern)


def _warn(message: str) -> None:
    """Print one sanitized warning to stderr."""
    print("warning: " + sanitize(message), file=sys.stderr)


def _posix_account() -> tuple:
    """Return the account name and home from the password database."""
    import pwd
    entry = pwd.getpwuid(os.getuid())
    return entry.pw_name, entry.pw_dir


def _windows_account() -> tuple:
    """Return the account name and profile path from Win32 APIs."""
    import ctypes
    import uuid
    size = ctypes.c_ulong(257)
    buffer = ctypes.create_unicode_buffer(size.value)
    name = buffer.value if ctypes.windll.advapi32.GetUserNameW(
        buffer, ctypes.byref(size)) else ""
    folder_id = ctypes.create_string_buffer(
        uuid.UUID("5E6C858F-0E22-4760-9AFE-EA3317B67173").bytes_le, 16)
    pointer = ctypes.c_wchar_p()
    status = ctypes.windll.shell32.SHGetKnownFolderPath(
        folder_id, 0, None, ctypes.byref(pointer))
    home = pointer.value if status == 0 and pointer.value else ""
    ctypes.windll.ole32.CoTaskMemFree(pointer)
    return name, home


def _os_identity(warn=_warn) -> Identity:
    """Read identity from OS APIs (H11). Each failed source is reported."""
    names, homes, hosts = [], [], []
    reader = _windows_account if os.name == "nt" else _posix_account
    try:
        name, home = reader()
        names.append(name)
        homes.append(home)
    except (KeyError, OSError) as error:
        warn(f"identity source account unavailable ({type(error).__name__}); "
             "Check 1 coverage reduced. Recovery: run as a user with an "
             "account entry, or rely on Check 2 and CI.")
    try:
        hosts.append(socket.gethostname())
    except OSError as error:
        warn(f"identity source hostname unavailable ({type(error).__name__}); "
             "Check 1 coverage reduced.")
    hosts.append(platform.node())
    return Identity(tuple(filter(None, names)), tuple(filter(None, homes)),
                    tuple(filter(None, hosts)))


def collect_identity(warn=_warn) -> Identity:
    """Return OS identity plus environment values.

    Environment values only add to the set. They never replace OS values,
    so overriding HOME or USER cannot blind Check 1.
    """
    base = _os_identity(warn)
    environment = os.environ
    names = base.names + tuple(environment.get(key, "")
                               for key in ("USER", "USERNAME", "LOGNAME"))
    homes = base.homes + tuple(environment.get(key, "")
                               for key in ("HOME", "USERPROFILE"))
    hosts = base.hosts + tuple(environment.get(key, "")
                               for key in ("COMPUTERNAME", "HOSTNAME"))
    return Identity(*(tuple(dict.fromkeys(filter(None, values)))
                      for values in (names, homes, hosts)))


def load_allowlist(root: Path) -> str:
    """Read the allowlist beside `root`. A missing file fails closed (E3)."""
    path = root / ALLOWLIST_NAME
    try:
        if path.is_symlink():
            raise AllowlistError(f"{ALLOWLIST_NAME} must be a regular file")
        return path.read_text(encoding="utf-8")
    except FileNotFoundError as error:
        raise AllowlistMissingError(
            f"{ALLOWLIST_NAME} missing; the local-path gate is partially "
            "adopted (Rule 18). Recovery: run the adoption disclosure "
            "procedure and land the approved allowlist with the checker."
        ) from error
    except (OSError, UnicodeDecodeError) as error:
        raise AllowlistError(
            f"cannot read {ALLOWLIST_NAME} ({type(error).__name__}). Recovery: "
            "restore the file from the base branch or fix its permissions."
        ) from error


_DEFAULT_SCANNER = []


def find_violations(text: str, path: str) -> list:
    """Return redacted messages, matching the other checkers' contract.

    The scanner is built once per process from the allowlist beside this
    script, without runtime identity (F1).
    """
    if not _DEFAULT_SCANNER:
        root = Path(__file__).resolve().parent.parent
        _DEFAULT_SCANNER.append(build_scanner(load_allowlist(root), Identity()))
    findings = _DEFAULT_SCANNER[0].scan_text(text, path)
    findings += _DEFAULT_SCANNER[0].scan_path(path)
    return [finding.message(reveal=False) for finding in findings]


def _load_sibling(name: str):
    """Load a sibling script by fixed path, never through sys.path (J9)."""
    path = Path(__file__).resolve().parent / f"{name}.py"
    if path.is_symlink() or not path.is_file():
        raise ImportError(f"trusted sibling {name}.py is missing or not a regular file")
    spec = importlib.util.spec_from_file_location(f"_local_paths_{name}", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load trusted sibling {name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git_log(repo: Path, revisions: list) -> str:
    """Return messages and added lines for revisions in one git call (F8).

    Merges are included, and textconv and external diff drivers are off
    (J3). Reaching the commit cap fails closed instead of scanning a part.
    """
    run_git = _load_sibling("trusted_git").run_git
    arguments = [
        "-c", "core.quotepath=false", "log", "--no-ext-diff", "--no-textconv",
        "--text", "-p", "-U0", "--no-color", "--diff-merges=first-parent",
        f"--max-count={MAX_COMMITS + 1}", "--format=%x1e%H%x1f%B%x1d",
        *revisions,
    ]
    result = run_git(repo, arguments, check=True)
    if len(result.stdout) > MAX_LOG_CHARACTERS:
        raise ScanLimitError("git log output exceeds the scan limit")
    if result.stdout.count("\x1e") > MAX_COMMITS:
        raise ScanLimitError(f"more than {MAX_COMMITS} commits")
    return result.stdout


def _scan_patch(scanner: Scanner, sha: str, patch: str) -> list:
    """Scan added lines and new paths of one commit's patch."""
    findings, path, number = [], f"commit {sha[:12]}", 0
    for line in patch.splitlines():
        if line.startswith("+++ "):
            path = line[4:].removeprefix("b/")
            findings += scanner.scan_path(path)
            continue
        hunk = HUNK_PATTERN.match(line)
        if hunk:
            number = int(hunk.group(1))
        elif line.startswith("+"):
            findings += scanner.scan_line(line[1:], path, number)
            number += 1
    return findings


def scan_log(scanner: Scanner, log: str) -> list:
    """Scan every commit message and patch from `_git_log` output."""
    findings = []
    for record in log.split("\x1e")[1:]:
        sha, _separator, rest = record.partition("\x1f")
        message, _separator, patch = rest.partition("\x1d")
        findings += scanner.scan_text(message, f"commit {sha[:12]} message")
        findings += _scan_patch(scanner, sha, patch)
    return findings


def _unpushed_revisions(repo: Path) -> list:
    """Return revisions for unpushed commits. No remote means all of HEAD."""
    run_git = _load_sibling("trusted_git").run_git
    remotes = run_git(repo, ["remote"], check=True).stdout.split()
    return ["HEAD", "--not", "--remotes"] if remotes else ["HEAD"]


def scan_files(scanner: Scanner, paths: list) -> list:
    """Scan file contents and names, failing closed past the byte caps."""
    findings, total = [], 0
    for name in paths:
        path = Path(name)
        size = path.stat().st_size
        total += size
        if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
            raise ScanLimitError(f"{sanitize(name)} exceeds the scan size limit")
        findings += scanner.scan_text(decode_blob(path.read_bytes()), name)
        findings += scanner.scan_path(name)
    return findings


def scan_event(scanner: Scanner, event_path: str) -> list:
    """Scan the pull request title and body from the runner event file (J4)."""
    path = Path(event_path)
    if path.stat().st_size > MAX_EVENT_BYTES:
        raise ScanLimitError("pull request event exceeds the size limit")
    payload = json.loads(path.read_text(encoding="utf-8"))
    request = payload.get("pull_request") if isinstance(payload, dict) else None
    if not isinstance(request, dict):
        raise ValueError("event payload lacks pull request data")
    findings = []
    for field_name in ("title", "body"):
        value = request.get(field_name) or ""
        if not isinstance(value, str):
            raise ValueError(f"pull request {field_name} must be text")
        findings += scanner.scan_text(value, f"pull_request.{field_name}")
    return findings


def _object_id(value: str) -> str:
    """Accept only a full commit SHA, so no revision can act as an option."""
    if not OBJECT_ID_PATTERN.fullmatch(value):
        raise argparse.ArgumentTypeError("must be a full commit SHA")
    return value


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser. No flag or variable disables a check (H8)."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="*", help="files to scan (pre-commit)")
    parser.add_argument("--report", action="store_true",
                        help="list every finding with allowlist proposals; "
                             "exits 0; refused in CI")
    parser.add_argument("--base", type=_object_id, help="range base SHA")
    parser.add_argument("--head", type=_object_id, help="range head SHA")
    parser.add_argument("--unpushed", action="store_true",
                        help="scan commits absent from every remote")
    parser.add_argument("--event", action="store_true",
                        help="scan the pull request title and body")
    return parser


def _validate_mode(parser: argparse.ArgumentParser, args) -> None:
    """Reject ambiguous mode combinations before any work."""
    modes = [bool(args.files), args.base is not None, args.unpushed, args.event]
    if (args.base is None) != (args.head is None):
        parser.error("--base and --head must be given together")
    if sum(modes) != 1:
        parser.error("choose exactly one of FILES, --base/--head, --unpushed, --event")
    if args.report and not args.files:
        parser.error("--report scans FILES only")
    if args.report and (os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS")):
        print("error: --report is disabled in CI. Recovery: run it locally.",
              file=sys.stderr)
        sys.exit(2)


def _build_for_mode(args) -> Scanner:
    """Build one scanner. CI event mode skips runtime identity."""
    identity = Identity() if args.event else collect_identity()
    scanner = build_scanner(load_allowlist(Path.cwd()), identity)
    if not args.event:
        for category in sorted(scanner.skipped_categories):
            _warn(f"{category} is a placeholder or system value and is not "
                  "protected by Check 1")
        if not scanner.has_identity:
            _warn("no protectable identity; Check 1 skipped. Check 2 and CI "
                  "still apply.")
    return scanner


def _collect(args, scanner: Scanner) -> list:
    """Run the selected mode and return its findings."""
    if args.files:
        return scan_files(scanner, args.files)
    if args.event:
        event_path = os.environ.get("GITHUB_EVENT_PATH", "")
        if not event_path:
            raise ValueError("GITHUB_EVENT_PATH is unset")
        return scan_event(scanner, event_path)
    repo = Path.cwd()
    revisions = (["--end-of-options", f"{args.base}..{args.head}"]
                 if args.base else _unpushed_revisions(repo))
    return scan_log(scanner, _git_log(repo, revisions))


def _print_report(findings: list) -> int:
    """Print every finding with one proposal per finding (disclosure)."""
    print(DISCLOSURE_PROCEDURE)
    for finding in findings:
        print(finding.message(reveal=True))
    proposals = [finding for finding in findings if finding.kind == "profile"]
    if proposals:
        print("Proposed .local-path-allowlist entries. Approve each one "
              "individually; approving one approves no other:")
    for finding in proposals:
        print(f"{sanitize(finding.path)}\t{sanitize(finding.name)}")
    if any(finding.kind == "identity" for finding in findings):
        print("Runtime identity matches cannot be allowlisted. Replace them.")
    return 0


def _print_findings(findings: list) -> int:
    """Print redacted findings, bounded, and return the exit status."""
    for finding in findings[:MAX_PRINTED_FINDINGS]:
        print(finding.message(reveal=False))
    hidden = len(findings) - MAX_PRINTED_FINDINGS
    if hidden > 0:
        print(f"{hidden} more findings not shown")
    return 1 if findings else 0


def main(argv: list | None = None) -> int:
    """Run the CLI. Every failure exits nonzero with a recovery action."""
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_mode(parser, args)
    try:
        findings = _collect(args, _build_for_mode(args))
    except AllowlistError as error:
        print(f"error: {sanitize(error)}", file=sys.stderr)
        return 1
    except ScanLimitError as error:
        print(f"error: {sanitize(error)}; content not fully scanned. "
              "Recovery: split the change.", file=sys.stderr)
        return 1
    except (OSError, ValueError, ImportError) as error:
        print(f"error: local path scan failed ({type(error).__name__}). "
              "Recovery: check the inputs and repository state, then rerun.",
              file=sys.stderr)
        return 1
    except Exception as error:
        # The one broad handler (H5): fail closed and print only the type,
        # never arguments or traceback text that could carry an identity.
        print(f"error: internal failure ({type(error).__name__}). Recovery: "
              "report the command and exit code. No content was approved.",
              file=sys.stderr)
        return 1
    return _print_report(findings) if args.report else _print_findings(findings)


if __name__ == "__main__":
    sys.exit(main())
