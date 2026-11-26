#!/usr/bin/env python3
"""Resolve GitHub CLI outside the repository and return bounded account data."""
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.parse
from pathlib import Path

# The proxy constants stay importable from this module for existing callers.
# Executable resolution, the repository-containment test, and PATH filtering
# come from trusted_git so both wrappers apply one set of rules.
try:
    from scripts.trusted_git import (
        MANAGED_PROXY_HOST, MANAGED_PROXY_PORT, PROXY_VARIABLES,
        is_managed_proxy_placeholder, sanitize_managed_proxy,
        is_inside_directory as _is_inside, resolve_executable,
        safe_search_path as _safe_search_path, select_safe_directory,
    )
except ModuleNotFoundError:
    from trusted_git import (
        MANAGED_PROXY_HOST, MANAGED_PROXY_PORT, PROXY_VARIABLES,
        is_managed_proxy_placeholder, sanitize_managed_proxy,
        is_inside_directory as _is_inside, resolve_executable,
        safe_search_path as _safe_search_path, select_safe_directory,
    )


ACCOUNT_OUTPUT_LIMIT = 256
COMMAND_OUTPUT_LIMIT = 1024 * 1024
METADATA_OUTPUT_LIMIT = 65536
# gh prints the GraphQL block and connectivity errors first, so their
# classifiers scan only this prefix of stderr.
ERROR_SCAN_LIMIT = 4096
# Relayed gh output is untrusted text. These characters can move the cursor,
# rewrite the terminal, or reorder displayed text. Tab, newline, and carriage
# return stay so multi-line output keeps its shape.
# Code points: C0 except tab (0x09), newline (0x0A), and carriage return
# (0x0D), then DEL and C1 (0x7F-0x9F), then the Arabic letter mark and the
# bidi marks, embeddings, overrides, and isolates.
TERMINAL_CONTROL_CODEPOINTS = (
    *range(0x00, 0x09), 0x0B, 0x0C, *range(0x0E, 0x20), *range(0x7F, 0xA0),
    0x061C, 0x200E, 0x200F, *range(0x202A, 0x202F), *range(0x2066, 0x206A),
)
TERMINAL_CONTROL_TABLE = dict.fromkeys(TERMINAL_CONTROL_CODEPOINTS, "?")
MAX_GIT_DISCOVERY_DEPTH = 100
GH_TIMEOUT_SECONDS = 5
# GitHub CLI exits with 4 when a command requires authentication.
GH_AUTH_REQUIRED_EXIT = 4
AUTHENTICATION_FAILURE = re.compile(r"HTTP 401\b")
INSTALLATION_TOKEN_FAILURE = "Resource not accessible by integration"
INSTALLATION_TOKEN_STATUS = "HTTP 403"
NETWORK_FAILURE = re.compile(
    r"proxyconnect|connection refused|dial tcp|no such host|i/o timeout"
    r"|TLS handshake",
    re.IGNORECASE,
)
CONNECTIVITY_MARKERS = (
    "error connecting to",
    "dial tcp",
    "no such host",
    "tls handshake timeout",
    "connection refused",
    "network is unreachable",
    "connection reset",
    "i/o timeout",
    "proxyconnect tcp",
    "temporary failure in name resolution",
    "cannot assign requested address",
)
CONNECTIVITY_ERROR_MESSAGE = (
    "error: GitHub CLI cannot reach GitHub; "
    "request approved external network access or retry"
)
TIMEOUT_ERROR_MESSAGE = (
    "error: GitHub CLI timed out; "
    "verify connectivity and request approved external network access or retry"
)
LOGIN = re.compile(r"\A[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
TEXT_OPTIONS = frozenset(("--body", "--title"))
REPOSITORY_COMMANDS = frozenset(("pr", "issue", "run"))
REPOSITORY_NAME = re.compile(r"\A[A-Za-z0-9](?:[A-Za-z0-9._-]{0,98}[A-Za-z0-9])?\Z")
BRANCH_NAME = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._/-]{0,199}\Z")
GLOBAL_VALUE_OPTIONS = frozenset(("-R", "--repo", "--hostname"))
# gh caches under $TMPDIR/gh-cli-cache. A shared temp root lets the first
# account or sandbox own that folder and deny every later caller, so each
# call gets a private directory. Go reads TMPDIR on POSIX and TMP, then
# TEMP, on Windows.
PRIVATE_TEMP_PREFIX = "trusted-gh-"
TEMP_VARIABLES = ("TMPDIR", "TMP", "TEMP")
MAX_REPORTED_PATH = 200
USAGE_ERROR_EXIT_CODE = 2
POLICY_DENIAL_EXIT_CODE = 2
HTTPS_PORT = 443
SSH_PORT = 22
REMOTE_PATH_PART_COUNT = 2
ACCOUNT_FIELD_COUNT = 2
# A repository command path holds a command and one subcommand.
COMMAND_PATH_LENGTH = 2
# Comment threads. Pull request conversation comments live on the issues
# endpoint, so both kinds map to one REST path.
COMMENT_KINDS = frozenset(("issue", "pr"))
COMMENT_DRAFT_DIRECTORY = ".comment-drafts"
COMMENT_BODY_OPTION = "--body-file"
# GitHub caps a comment body at 65536 characters.
MAX_COMMENT_BYTES = 65536
# Draft reads open one descriptor that does not follow a link and never blocks
# on a FIFO, so a path swapped after the check cannot redirect the read.
DRAFT_NO_FOLLOW = getattr(os, "O_NOFOLLOW", 0)
DRAFT_READ_FLAGS = (os.O_RDONLY | DRAFT_NO_FOLLOW | getattr(os, "O_NONBLOCK", 0)
                    | getattr(os, "O_BINARY", 0))
DRAFT_DIRECTORY_FLAGS = os.O_RDONLY | DRAFT_NO_FOLLOW | getattr(os, "O_DIRECTORY", 0)
THREAD_NUMBER = re.compile(r"\A[1-9][0-9]{0,9}\Z")
COMMENTS_ARGUMENT_COUNT = 2
COMMENT_ARGUMENT_COUNT = 4
# A comment read fetches one page so a long thread cannot exhaust memory.
# GitHub caps per_page at 100.
COMMENT_PAGE_OPTION = "--page"
COMMENT_PAGE_SIZE = 100
COMMENT_PAGE_NUMBER = re.compile(r"\A[1-9][0-9]{0,3}\Z")
MAX_COMMENT_PAGE = 1000
COMMENT_PAGE_ARGUMENT_COUNT = 2
# `gh issue edit` and `gh issue create` call GraphQL. Some sessions block
# GraphQL. Two exact body-file shapes run over REST instead. Any other option
# leaves the command for gh unchanged.
ISSUE_TITLE_OPTION = "--title"
ISSUE_REST_OPTIONS = frozenset((COMMENT_BODY_OPTION, ISSUE_TITLE_OPTION))
ISSUE_STDIN_BODY = "-"
ISSUE_EDIT_PREFIX_LENGTH = 3
ISSUE_CREATE_PREFIX_LENGTH = 2
# `gh pr create`, `gh pr edit`, `gh issue close`, and `gh issue reopen` also
# call GraphQL. Their exact shapes below run over REST. Rule 5 allows only
# draft pull requests, so the create shape requires `--draft`.
PR_DRAFT_FLAG = "--draft"
PR_BASE_OPTION = "--base"
PR_BASE_SHORT_OPTION = "-B"
PR_CREATE_OPTIONS = frozenset((COMMENT_BODY_OPTION, ISSUE_TITLE_OPTION, PR_BASE_OPTION))
ISSUE_CLOSE_REASON_OPTION = "--reason"
ISSUE_CLOSE_REASONS = frozenset(("completed", "not_planned"))
ISSUE_STATE_PREFIX_LENGTH = 3
# The branch gate reads the same override file with the same rules.
PRIMARY_BRANCH_FILE = Path("hooks") / "primary-branch.txt"
DEFAULT_PRIMARY_BRANCH = "main"
REJECTED_PRIMARY_BRANCHES = frozenset(("master", "HEAD"))
MAX_PRIMARY_BRANCH_BYTES = 256
GRAPHQL_UNAVAILABLE = "GraphQL is not available"
GRAPHQL_ERROR_MESSAGE = (
    "error: this session blocks GitHub GraphQL; read comments with "
    "`python scripts/trusted_gh.py comments <issue|pr> <number>`, post with "
    "`python scripts/trusted_gh.py comment <issue|pr> <number> --body-file "
    ".comment-drafts/<file>`, or read other data through a REST `run api` call"
)


def is_connectivity_failure(stderr: str) -> bool:
    """Return whether bounded GitHub CLI error text reports a network failure."""
    if not stderr:
        return False
    sample = stderr[:ERROR_SCAN_LIMIT].casefold()
    return any(marker in sample for marker in CONNECTIVITY_MARKERS) or bool(NETWORK_FAILURE.search(sample))


def find_literal_escape_sequences(arguments: list[str]) -> list[str]:
    """Return prose options containing escape text instead of real newlines."""
    findings = []
    for index, argument in enumerate(arguments[:-1]):
        if argument in TEXT_OPTIONS and "\\n" in arguments[index + 1]:
            findings.append(argument)
    return findings


def _find_git_entry(start: Path) -> Path:
    """Return the nearest .git entry or raise a bounded error."""
    current = start.resolve()
    for _ in range(MAX_GIT_DISCOVERY_DEPTH):
        candidate = current / ".git"
        if candidate.is_dir() or candidate.is_file():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    raise ValueError("repository context is missing; run from a Git checkout")


def _git_config_path(git_entry: Path) -> Path:
    """Return a local Git config path for a directory or worktree pointer."""
    git_dir = _git_dir(git_entry)
    common_file = git_dir / "commondir"
    if common_file.is_file():
        if common_file.stat().st_size > METADATA_OUTPUT_LIMIT:
            raise ValueError("repository context common pointer exceeds the safety limit")
        common_value = common_file.read_text(encoding="utf-8", errors="strict").strip()
        if not common_value or "\n" in common_value or "\r" in common_value:
            raise ValueError("repository context has an invalid common Git directory")
        common_dir = (git_dir / common_value).resolve()
        if not common_dir.is_dir():
            raise ValueError("repository context has a missing common Git directory")
        return common_dir / "config"
    return git_dir / "config"


def _git_dir(git_entry: Path) -> Path:
    """Return the resolved administrative Git directory."""
    if git_entry.is_dir():
        return git_entry
    if git_entry.stat().st_size > METADATA_OUTPUT_LIMIT:
        raise ValueError("repository context Git pointer exceeds the safety limit")
    content = git_entry.read_text(encoding="utf-8", errors="strict")
    marker, separator, value = content.strip().partition(":")
    if marker.strip().lower() != "gitdir" or not separator or not value.strip():
        raise ValueError("repository context has an invalid Git worktree pointer")
    git_dir = (git_entry.parent / value.strip()).resolve()
    if not git_dir.is_dir():
        raise ValueError("repository context has a missing Git worktree directory")
    return git_dir


def _remote_url_from_config(config_path: Path, remote_name: str) -> str:
    """Extract a remote URL from Git config using basic line parsing."""
    if not config_path.is_file():
        raise ValueError("repository config is missing")
    if config_path.stat().st_size > METADATA_OUTPUT_LIMIT:
        raise ValueError("repository config exceeds the safety limit")
    target_section = f'[remote "{remote_name}"]'
    in_target_remote = False
    for line in config_path.read_text(encoding="utf-8", errors="strict").splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            in_target_remote = stripped == target_section
            continue
        if in_target_remote and "=" in stripped:
            key, _, url = stripped.partition("=")
            if key.strip().lower() == "url":
                return url.strip()
    raise ValueError(f"repository config has no {remote_name} remote")


def parse_repository_target(url: str) -> str:
    """Extract owner/repo from a GitHub remote URL."""
    cleaned = url.strip()
    cleaned = cleaned.removesuffix(".git")
    if cleaned.startswith("git@github.com:"):
        path = cleaned[len("git@github.com:"):]
    else:
        parsed = urllib.parse.urlsplit(cleaned)
        if parsed.scheme not in ("https", "ssh"):
            raise ValueError("repository remote is not an allowed GitHub URL scheme")
        if (parsed.hostname or "").lower() != "github.com":
            raise ValueError("repository remote is not a GitHub URL")
        if parsed.username and parsed.scheme == "https":
            raise ValueError("repository remote URL cannot contain user credentials")
        if parsed.username not in (None, "", "git") and parsed.scheme == "ssh":
            raise ValueError("repository remote SSH URL must use git user")
        if parsed.password is not None:
            raise ValueError("repository remote URL cannot contain a password")
        if parsed.port not in (None, HTTPS_PORT if parsed.scheme == "https" else SSH_PORT):
            raise ValueError("repository remote uses an invalid port")
        path = parsed.path.lstrip("/")
    parts = path.strip("/").split("/")
    if len(parts) != REMOTE_PATH_PART_COUNT:
        raise ValueError("repository remote path must be owner/repo")
    owner, repo = parts
    if not REPOSITORY_NAME.fullmatch(owner) or not REPOSITORY_NAME.fullmatch(repo):
        raise ValueError("repository remote has invalid owner or repository syntax")
    return f"{owner}/{repo}"


def repository_target(repo_root: Path) -> str:
    """Return the GitHub owner/repo for a repository checkout."""
    git_entry = _find_git_entry(repo_root)
    config_path = _git_config_path(git_entry)
    url = _remote_url_from_config(config_path, "origin")
    return parse_repository_target(url)


def repository_branch(repo_root: Path) -> str:
    """Return the current branch for a repository checkout."""
    git_entry = _find_git_entry(repo_root)
    git_dir = _git_dir(git_entry)
    head_path = git_dir / "HEAD"
    if not head_path.is_file():
        raise ValueError("repository HEAD is missing")
    if head_path.stat().st_size > METADATA_OUTPUT_LIMIT:
        raise ValueError("repository HEAD exceeds the safety limit")
    content = head_path.read_text(encoding="utf-8", errors="strict").strip()
    marker = "ref: refs/heads/"
    if not content.startswith(marker):
        raise ValueError("repository has no named branch; check out a branch first")
    branch = content.removeprefix(marker)
    if not is_valid_branch_name(branch):
        raise ValueError("repository has an invalid current branch")
    return branch


def is_valid_branch_name(name: str) -> bool:
    """Return whether a branch name has safe characters and no empty or dot component."""
    return bool(BRANCH_NAME.fullmatch(name)) and not any(
        component in ("", ".", "..") for component in name.split("/"))


def primary_branch_name(root: Path) -> str:
    """Return the branch in hooks/primary-branch.txt below root, or main.

    The file must be a regular non-link file holding one ASCII line that
    names a valid branch other than master or HEAD.
    """
    content = _read_primary_branch_bytes(Path(root) / PRIMARY_BRANCH_FILE)
    if content is None:
        return DEFAULT_PRIMARY_BRANCH
    lines = content.splitlines()
    name = lines[0].decode("ascii", errors="replace") if len(lines) == 1 else ""
    if not content.isascii() or not is_allowed_base_branch(name):
        raise ValueError(f"{PRIMARY_BRANCH_FILE.as_posix()} must name one valid branch "
                         "other than master or HEAD")
    return name


def is_allowed_base_branch(name: str) -> bool:
    """Return whether a name can serve as a primary or pull request base branch."""
    return name not in REJECTED_PRIMARY_BRANCHES and is_valid_branch_name(name)


def _read_primary_branch_bytes(path: Path) -> bytes | None:
    """Return the override bytes read through one descriptor, or None when absent.

    The open does not follow a link and never blocks on a FIFO. The descriptor
    is checked and read directly, so a path swapped after the open cannot
    change the bytes.
    """
    try:
        descriptor = os.open(path, DRAFT_READ_FLAGS)
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ValueError(f"{PRIMARY_BRANCH_FILE.as_posix()} cannot be opened as a regular "
                         f"file ({type(error).__name__})") from error
    with os.fdopen(descriptor, "rb") as handle:
        details = os.fstat(handle.fileno())
        if not stat.S_ISREG(details.st_mode) or details.st_size > MAX_PRIMARY_BRANCH_BYTES:
            raise ValueError(f"{PRIMARY_BRANCH_FILE.as_posix()} must be a small regular file")
        return handle.read(MAX_PRIMARY_BRANCH_BYTES + 1)


def _has_repository_option(arguments: list[str]) -> bool:
    """Return whether arguments contain a structural repository option."""
    delimiter = arguments.index("--") if "--" in arguments else len(arguments)
    option_tokens = arguments[:delimiter]
    for index, argument in enumerate(option_tokens):
        if argument in ("-R", "--repo") and index + 1 < len(option_tokens):
            return True
        if argument.startswith("--repo=") or (argument.startswith("-R") and len(argument) > len("-R")):
            return True
    return False


def _command_position(arguments: list[str]) -> int | None:
    """Return the index of the first repository command token, or None."""
    delimiter = arguments.index("--") if "--" in arguments else len(arguments)
    for index in range(delimiter):
        if arguments[index] in REPOSITORY_COMMANDS:
            return index
    return None


def _has_base_option(option_tokens: list[str]) -> bool:
    """Return whether option tokens name a pull request base in any gh spelling."""
    return any(token == PR_BASE_OPTION or token.startswith(PR_BASE_OPTION + "=")
               or token.startswith(PR_BASE_SHORT_OPTION) for token in option_tokens)


def _with_default_pull_base(repository: Path, arguments: list[str]) -> list[str]:
    """Add the primary branch as base to a pull request create that names none.

    gh otherwise targets the GitHub default branch. CI runs only for pull
    requests into a stable branch, and the default branch can differ. An
    explicit base may name any stable branch: primary, test, or develop.
    """
    position = _command_position(arguments)
    delimiter = arguments.index("--") if "--" in arguments else len(arguments)
    option_tokens = arguments[:delimiter]
    if position is None or option_tokens[position:position + COMMAND_PATH_LENGTH] != ["pr", "create"]:
        return list(arguments)
    if _has_base_option(option_tokens):
        return list(arguments)
    try:
        primary = primary_branch_name(_find_git_entry(Path(repository)).parent)
    except (OSError, ValueError) as error:
        raise RestWriteError(f"pull request base cannot default to the primary branch: {error}") from error
    return [*option_tokens, PR_BASE_OPTION, primary, *arguments[delimiter:]]


def with_repository_context(repository: Path, arguments: list[str]) -> list[str]:
    """Add validated repository context to a repository-bound command."""
    arguments = _with_default_pull_base(repository, arguments)
    position = _command_position(arguments)
    if position is None or _has_repository_option(arguments):
        return list(arguments)
    target = repository_target(repository)
    delimiter = arguments.index("--") if "--" in arguments else len(arguments)
    option_tokens = arguments[:delimiter]
    context = [*option_tokens, "--repo", target, *arguments[delimiter:]]
    command = option_tokens[position:position + COMMAND_PATH_LENGTH]
    has_head = any(argument == "--head" or argument.startswith("--head=")
                   for argument in option_tokens)
    if command == ["pr", "create"] and not has_head:
        context[delimiter:delimiter] = (
            "--head", f"{target.split('/')[0]}:{repository_branch(repository)}"
        )
    return context


def _candidate_names() -> tuple[str, ...]:
    """Return accepted GitHub CLI executable names."""
    if os.name == "nt":
        os.environ["NoDefaultCurrentDirectoryInExePath"] = "1"
        return ("gh.exe", "gh.com")
    return ("gh",)


def resolve_gh(repo_root) -> str:
    """Return an absolute GitHub CLI executable outside the repository."""
    return resolve_executable(repo_root, _candidate_names(), "GitHub CLI")


def _safe_directory(repository: Path, executable: Path) -> Path:
    """Return an existing execution directory outside the repository.

    GitHub CLI also accepts the working directory, after the temp directory.
    """
    candidates = (Path(tempfile.gettempdir()), Path.cwd(), executable.parent)
    return select_safe_directory(repository, candidates, "GitHub CLI")


def _is_managed_proxy_placeholder(value: str) -> bool:
    """Return whether a proxy value is the managed Codex placeholder."""
    return is_managed_proxy_placeholder(value)


def _sanitize_proxy_environment(environment: dict) -> None:
    """Remove only managed proxy placeholders from an environment."""
    sanitize_managed_proxy(environment)


def proxy_variable_names(environment: dict) -> list[str]:
    """Return proxy variable names that survive placeholder removal."""
    remaining = dict(environment)
    sanitize_managed_proxy(remaining)
    return [name for name in PROXY_VARIABLES if remaining.get(name)]


class GitHubAccessError(OSError):
    """A GitHub CLI account check failed with a diagnosed category."""

    MESSAGES = {
        "authentication": "GitHub CLI has no authenticated account; "
                          "an active human must sign in",
        "network": "GitHub CLI could not reach GitHub; this is not an "
                   "authentication result",
        "installation_token": "GitHub CLI holds an app installation token, which "
                              "cannot read /user; run this under a user account",
        "unclassified": "GitHub CLI account check failed with exit {returncode}; "
                        "this does not prove missing authentication",
    }

    def __init__(self, category: str, returncode: int):
        super().__init__(self.MESSAGES[category].format(returncode=returncode))
        self.category = category
        self.returncode = returncode


class ConnectivityError(GitHubAccessError):
    """Represent a network reachability failure separate from authentication."""

    def __init__(self, message: str = "", returncode: int = 1):
        super().__init__("network", returncode)
        self.detail = message


def classify_gh_failure(returncode: int, stderr: str) -> str:
    """Return the failure category for a nonzero GitHub CLI result."""
    if returncode == GH_AUTH_REQUIRED_EXIT or AUTHENTICATION_FAILURE.search(stderr):
        return "authentication"
    if INSTALLATION_TOKEN_STATUS in stderr and INSTALLATION_TOKEN_FAILURE in stderr:
        return "installation_token"
    if NETWORK_FAILURE.search(stderr) or is_connectivity_failure(stderr):
        return "network"
    return "unclassified"


def describe_access_error(error: GitHubAccessError) -> str:
    """Return a user-facing message that names proxy variables, not values."""
    message = str(error)
    if error.category != "network":
        return message
    names = proxy_variable_names(dict(os.environ))
    listed = ", ".join(names) if names else "none set"
    return f"{message}; inspect proxy variables: {listed}"



def _sanitize_config_directory(environment: dict, repository: Path) -> None:
    """Preserve only an external existing configuration directory."""
    raw = environment.pop("GH_CONFIG_DIR", None)
    if not raw:
        return
    candidate = Path(raw)
    if not candidate.is_absolute():
        return
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return
    if (not resolved.is_dir()
            or _is_inside(Path(os.path.abspath(candidate)), repository)
            or _is_inside(resolved, repository)):
        return
    environment["GH_CONFIG_DIR"] = str(resolved)


def run_gh(repo_root, arguments: list[str], *, runner=None, timeout=None, input_text=None):
    """Run trusted GitHub CLI from outside the repository, with optional stdin."""
    repository = Path(repo_root).resolve()
    executable = Path(resolve_gh(repository))
    environment = dict(os.environ)
    _sanitize_config_directory(environment, repository)
    environment.pop("GH_REPO", None)
    _sanitize_proxy_environment(environment)
    environment.update({"GH_PAGER": "", "GH_PROMPT_DISABLED": "1"})
    environment["PATH"] = _safe_search_path(repository)
    if os.name == "nt":
        environment["NoDefaultCurrentDirectoryInExePath"] = "1"
    execute = runner or subprocess.run
    private = tempfile.mkdtemp(
        prefix=PRIVATE_TEMP_PREFIX, dir=_safe_directory(repository, executable))
    try:
        for name in TEMP_VARIABLES:
            environment[name] = private
        return execute(
            [str(executable), *arguments],
            cwd=private,
            env=environment,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=timeout,
        )
    finally:
        _remove_private_directory(private)


def _remove_private_directory(path: str) -> None:
    """Remove one private gh temp directory, warning when removal fails."""
    try:
        shutil.rmtree(path)
    except OSError as error:
        print(
            f"warning: could not remove GitHub CLI temp directory "
            f"{_bounded_path(path)} ({type(error).__name__}); remove it manually",
            file=sys.stderr,
        )


def _bounded_path(path: object) -> str:
    """Return a path as bounded printable ASCII for diagnostics."""
    return ascii(str(path))[1:-1][:MAX_REPORTED_PATH]


def describe_permission_error(error: PermissionError) -> str:
    """Return a message naming the unwritable path and the recovery step."""
    location = _bounded_path(error.filename) if error.filename else "the temp directory"
    return (f"error: GitHub CLI temporary storage is not writable ({location}); "
            "set TMPDIR to a writable directory and retry")


def parse_account(output: str) -> dict:
    """Parse id and login from tab-separated GitHub API output."""
    if len(output.encode("utf-8")) > ACCOUNT_OUTPUT_LIMIT:
        raise ValueError("GitHub account output exceeds the bound")
    fields = output.strip().split("\t")
    if len(fields) != ACCOUNT_FIELD_COUNT or not fields[0].isdigit() or int(fields[0]) < 1:
        raise ValueError("GitHub account output has an invalid account ID")
    if not LOGIN.fullmatch(fields[1]):
        raise ValueError("GitHub account output has an invalid login")
    return {"id": int(fields[0]), "login": fields[1]}


def authenticated_account(repo_root) -> dict:
    """Return the authenticated GitHub account through a fixed API request."""
    result = run_gh(
        repo_root,
        ["api", "user", "--jq", "[.id,.login]|@tsv"],
        timeout=GH_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        if is_connectivity_failure(result.stderr):
            raise ConnectivityError("GitHub CLI cannot reach GitHub")
        category = classify_gh_failure(result.returncode, result.stderr or "")
        raise GitHubAccessError(category, result.returncode)
    return parse_account(result.stdout)


def _load_gate_core():
    """Import and return the shared gate module for GitHub command checks."""
    hooks_directory = Path(__file__).resolve().parent.parent / "hooks"
    sys.path.insert(0, str(hooks_directory))
    import _gate_core as gate_core
    return gate_core


def verify_account(repo_root) -> None:
    """Verify the authenticated account, allowing only a CI installation token.

    A GitHub Actions `GITHUB_TOKEN` is an app installation token and cannot
    read /user. Only that failure, only on an Actions runner, skips the check.
    """
    try:
        authenticated_account(repo_root)
    except GitHubAccessError as error:
        if error.category == "installation_token" and os.environ.get("GITHUB_ACTIONS") == "true":
            return
        raise


def _safe_write(stream, text: str) -> None:
    """Write text to a stream, replacing unencodable characters if needed."""
    try:
        stream.write(text)
    except UnicodeEncodeError:
        encoding = getattr(stream, "encoding", None) or "utf-8"
        buffer = getattr(stream, "buffer", None)
        if buffer is not None:
            buffer.write(text.encode(encoding, errors="replace"))
        else:
            sanitized = text.encode(encoding, errors="replace").decode(encoding, errors="replace")
            stream.write(sanitized)


def comment_thread(kind: str, number: str) -> int:
    """Return the thread number for an issue or pr comment target."""
    if kind not in COMMENT_KINDS:
        raise ValueError("comment target kind must be issue or pr")
    if not THREAD_NUMBER.fullmatch(number):
        raise ValueError("comment target number must be a positive integer")
    return int(number)


def comment_body_path(repo_root: Path, raw: str) -> Path:
    """Return the absolute path of a validated draft under .comment-drafts/."""
    return read_comment_draft(repo_root, raw)[0]


def read_comment_draft(repo_root: Path, raw: str) -> tuple[Path, str]:
    """Return the path and validated text of one draft under .comment-drafts/.

    The draft must be a regular non-link ASCII file. The wrapper reads it once
    and posts these exact bytes, so a later change to the file cannot alter
    the posted comment.
    """
    name = _draft_name(raw)
    if not _safe_draft_open_supported():
        raise ValueError("comment posting needs os.open dir_fd and O_NOFOLLOW support, which "
                         "this platform lacks; post the comment through another approved path")
    drafts = _find_git_entry(Path(repo_root)).parent / COMMENT_DRAFT_DIRECTORY
    try:
        body = _read_draft_bytes(drafts, name)
    except (OSError, ValueError) as error:
        raise ValueError(f"comment body file must be a readable regular file directly in "
                         f"{COMMENT_DRAFT_DIRECTORY}/; write the draft there first") from error
    if not body or len(body) > MAX_COMMENT_BYTES:
        raise ValueError(f"comment body must hold 1 to {MAX_COMMENT_BYTES} bytes")
    if not body.isascii():
        raise ValueError("comment body must be ASCII")
    text = body.decode("ascii")
    if find_literal_escape_sequences(["--body", text]):
        raise ValueError("comment body contains literal escape text; use real newlines")
    return drafts / name, text


def _draft_name(raw: str) -> str:
    """Return the single file name of a `.comment-drafts/<name>` argument."""
    prefix = COMMENT_DRAFT_DIRECTORY + "/"
    name = raw[len(prefix):] if raw.startswith(prefix) else ""
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise ValueError(f"comment body file must be {prefix}<name> with no subdirectory")
    return name


def _safe_draft_open_supported() -> bool:
    """Return whether this platform can open a draft relative to a no-follow directory.

    Windows lacks dir_fd and O_NOFOLLOW. A path-based check there cannot stop
    a junction or link swapped in for the drafts directory, so posting fails
    closed instead.
    """
    return os.open in os.supports_dir_fd and bool(DRAFT_NO_FOLLOW)


def _read_draft_bytes(drafts: Path, name: str) -> bytes:
    """Read one draft through a descriptor that no later path swap can redirect.

    The drafts directory and the draft both open with O_NOFOLLOW, the draft
    relative to the directory descriptor.
    """
    directory = os.open(drafts, DRAFT_DIRECTORY_FLAGS)
    try:
        descriptor = os.open(name, DRAFT_READ_FLAGS, dir_fd=directory)
    finally:
        os.close(directory)
    with os.fdopen(descriptor, "rb") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise ValueError("comment draft is not a regular file")
        return handle.read(MAX_COMMENT_BYTES + 1)


def comment_page(extra: list[str]) -> int:
    """Return the page a comment read names, or 1 when it names none."""
    if not extra:
        return 1
    if (len(extra) != COMMENT_PAGE_ARGUMENT_COUNT or extra[0] != COMMENT_PAGE_OPTION
            or not COMMENT_PAGE_NUMBER.match(extra[1]) or int(extra[1]) > MAX_COMMENT_PAGE):
        raise ValueError(f"comments takes <issue|pr> <number> [{COMMENT_PAGE_OPTION} "
                         f"<1-{MAX_COMMENT_PAGE}>]")
    return int(extra[1])


def comment_arguments(repo_root, operation: str,
                      arguments: list[str]) -> tuple[list[str], str | None]:
    """Return the fixed REST GitHub CLI arguments and stdin for one comment operation."""
    if operation == "comments":
        if len(arguments) < COMMENTS_ARGUMENT_COUNT:
            raise ValueError(f"comments takes <issue|pr> <number> [{COMMENT_PAGE_OPTION} <n>]")
        page = comment_page(arguments[COMMENTS_ARGUMENT_COUNT:])
    elif len(arguments) != COMMENT_ARGUMENT_COUNT:
        raise ValueError(f"comment takes <issue|pr> <number> {COMMENT_BODY_OPTION} <path>")
    number = comment_thread(arguments[0], arguments[1])
    endpoint = f"repos/{repository_target(Path(repo_root))}/issues/{number}/comments"
    if operation == "comments":
        return ["api", f"{endpoint}?per_page={COMMENT_PAGE_SIZE}&page={page}"], None
    if arguments[2] != COMMENT_BODY_OPTION:
        raise ValueError(f"comment takes {COMMENT_BODY_OPTION} <path>")
    _path, body = read_comment_draft(Path(repo_root), arguments[3])
    return ["api", "-X", "POST", endpoint, "--input", "-"], json.dumps({"body": body})


def next_page_note(page: int, output: str) -> str:
    """Return a hint naming the next page when a comment read filled its page."""
    if page >= MAX_COMMENT_PAGE:
        return ""
    try:
        comments = json.loads(output)
    except ValueError:
        return ""
    if not isinstance(comments, list) or len(comments) < COMMENT_PAGE_SIZE:
        return ""
    return (f"note: page {page} holds {COMMENT_PAGE_SIZE} comments; read more with "
            f"{COMMENT_PAGE_OPTION} {page + 1}")


class RestWriteError(ValueError):
    """A mapped REST write named a value that the wrapper refuses."""


class IssueBodyError(RestWriteError):
    """A mapped issue command named a body file that the draft rules refuse."""


def _issue_rest_options(options: list[str]) -> dict | None:
    """Return option values when every token is one approved option pair, else None."""
    values = {}
    for index in range(0, len(options), 2):
        name = options[index]
        if name not in ISSUE_REST_OPTIONS or name in values or index + 1 >= len(options):
            return None
        values[name] = options[index + 1]
    if values.get(COMMENT_BODY_OPTION, ISSUE_STDIN_BODY) == ISSUE_STDIN_BODY:
        return None
    return values


def _issue_rest_shape(arguments: list[str]) -> tuple[str, str, dict] | None:
    """Return the method, issue path suffix, and options of an approved issue shape."""
    if arguments[:2] == ["issue", "edit"] and len(arguments) > ISSUE_EDIT_PREFIX_LENGTH:
        number = arguments[2]
        options = _issue_rest_options(arguments[ISSUE_EDIT_PREFIX_LENGTH:])
        if options is None or not THREAD_NUMBER.fullmatch(number):
            return None
        return "PATCH", f"/{int(number)}", options
    if arguments[:2] == ["issue", "create"]:
        options = _issue_rest_options(arguments[ISSUE_CREATE_PREFIX_LENGTH:])
        if options is None or ISSUE_TITLE_OPTION not in options:
            return None
        return "POST", "", options
    return None


def issue_rest_request(repo_root, arguments: list[str]) -> tuple[list[str], str] | None:
    """Return REST CLI arguments and a JSON body for an approved issue shape, else None.

    Callers run this only after the gate checks pass on the original
    arguments. A refused body file raises IssueBodyError so the call fails
    closed instead of falling back to the blocked GraphQL path.
    """
    shape = _issue_rest_shape(list(arguments))
    if shape is None:
        return None
    method, suffix, options = shape
    payload = {"body": _read_rest_body(repo_root, options[COMMENT_BODY_OPTION])}
    if ISSUE_TITLE_OPTION in options:
        payload["title"] = options[ISSUE_TITLE_OPTION]
    return _rest_call(repo_root, method, f"issues{suffix}", payload)


def _read_rest_body(repo_root, raw: str) -> str:
    """Return a validated draft body, or raise IssueBodyError so the call fails closed."""
    try:
        _path, body = read_comment_draft(Path(repo_root), raw)
    except ValueError as error:
        raise IssueBodyError(str(error)) from error
    return body


def _rest_call(repo_root, method: str, path: str, payload: dict) -> tuple[list[str], str]:
    """Return REST CLI arguments for one origin repository path and its JSON body."""
    endpoint = f"repos/{repository_target(Path(repo_root))}/{path}"
    return ["api", "-X", method, endpoint, "--input", "-"], json.dumps(payload)


def _pull_create_options(options: list[str]) -> dict | None:
    """Return values for `--draft --title T --body-file F [--base B]` in any order, else None."""
    values = {}
    index = 0
    while index < len(options):
        name = options[index]
        if name in values:
            return None
        if name == PR_DRAFT_FLAG:
            values[name] = True
            index += 1
            continue
        if name not in PR_CREATE_OPTIONS or index + 1 >= len(options):
            return None
        values[name] = options[index + 1]
        index += 2
    if PR_DRAFT_FLAG not in values or ISSUE_TITLE_OPTION not in values:
        return None
    if values.get(COMMENT_BODY_OPTION, ISSUE_STDIN_BODY) == ISSUE_STDIN_BODY:
        return None
    return values


def _pull_base(repo_root, options: dict) -> str:
    """Return the validated base branch, defaulting to the primary branch.

    Only an absent `--base` takes the default. An explicit value, empty
    included, passes the same rules as the primary branch.
    """
    if PR_BASE_OPTION in options:
        base = options[PR_BASE_OPTION]
    else:
        try:
            base = primary_branch_name(_find_git_entry(Path(repo_root)).parent)
        except (OSError, ValueError) as error:
            raise RestWriteError(str(error)) from error
    if not is_allowed_base_branch(base):
        raise RestWriteError("pull request base must name one valid branch "
                             "other than master or HEAD")
    return base


def pull_rest_request(repo_root, arguments: list[str]) -> tuple[list[str], str] | None:
    """Return REST CLI arguments and a JSON body for a draft create or a body edit, else None."""
    if arguments[:2] == ["pr", "create"]:
        options = _pull_create_options(arguments[ISSUE_CREATE_PREFIX_LENGTH:])
        if options is None:
            return None
        payload = {"title": options[ISSUE_TITLE_OPTION],
                   "body": _read_rest_body(repo_root, options[COMMENT_BODY_OPTION]),
                   "head": repository_branch(Path(repo_root)),
                   "base": _pull_base(repo_root, options), "draft": True}
        return _rest_call(repo_root, "POST", "pulls", payload)
    if arguments[:2] != ["pr", "edit"] or len(arguments) <= ISSUE_EDIT_PREFIX_LENGTH:
        return None
    options = _issue_rest_options(arguments[ISSUE_EDIT_PREFIX_LENGTH:])
    if options is None or not THREAD_NUMBER.fullmatch(arguments[2]):
        return None
    payload = {"body": _read_rest_body(repo_root, options[COMMENT_BODY_OPTION])}
    if ISSUE_TITLE_OPTION in options:
        payload["title"] = options[ISSUE_TITLE_OPTION]
    return _rest_call(repo_root, "PATCH", f"pulls/{int(arguments[2])}", payload)


def issue_state_request(repo_root, arguments: list[str]) -> tuple[list[str], str] | None:
    """Return REST CLI arguments and a JSON body for `issue close` or `issue reopen`, else None."""
    if arguments[:2] not in (["issue", "close"], ["issue", "reopen"]):
        return None
    if len(arguments) < ISSUE_STATE_PREFIX_LENGTH or not THREAD_NUMBER.fullmatch(arguments[2]):
        return None
    extra = arguments[ISSUE_STATE_PREFIX_LENGTH:]
    if arguments[1] == "reopen":
        payload = {"state": "open"} if not extra else None
    elif not extra:
        payload = {"state": "closed"}
    elif (len(extra) == 2 and extra[0] == ISSUE_CLOSE_REASON_OPTION
          and extra[1] in ISSUE_CLOSE_REASONS):
        payload = {"state": "closed", "state_reason": extra[1]}
    else:
        payload = None
    if payload is None:
        return None
    return _rest_call(repo_root, "PATCH", f"issues/{int(arguments[2])}", payload)


def rest_write_request(repo_root, arguments: list[str]) -> tuple[list[str], str] | None:
    """Return the REST request for any approved write shape, else None.

    Callers run this only after the gate checks pass on the original
    arguments. A refused input raises RestWriteError so the call fails closed.
    """
    for build_request in (issue_rest_request, pull_rest_request, issue_state_request):
        request = build_request(repo_root, list(arguments))
        if request is not None:
            return request
    return None


def issue_url(output: str) -> str | None:
    """Return the html_url of a REST issue response, or None when absent."""
    try:
        issue = json.loads(output)
    except ValueError:
        return None
    url = issue.get("html_url") if isinstance(issue, dict) else None
    return url if isinstance(url, str) else None


def _relay_issue_result(result) -> int:
    """Print the issue URL of a successful REST write, else relay the raw result."""
    url = issue_url(result.stdout) if result.returncode == 0 else None
    if url is None:
        return _relay_result(result)
    _safe_write(sys.stdout, strip_terminal_controls(url[:MAX_REPORTED_PATH]) + "\n")
    return 0


def _run_comment_command(repo_root, operation: str, arguments: list[str]) -> int:
    """Run one fixed comment read or post through the authenticated CLI."""
    try:
        gh_arguments, request_body = comment_arguments(repo_root, operation, arguments)
    except (OSError, ValueError) as error:
        print(f"error: {_bounded_path(error)}", file=sys.stderr)
        return USAGE_ERROR_EXIT_CODE
    if operation != "comments":
        return _execute(repo_root, gh_arguments, input_text=request_body)
    page = comment_page(arguments[COMMENTS_ARGUMENT_COUNT:])
    return _execute(repo_root, gh_arguments,
                    output_note=lambda output: next_page_note(page, output))


def _run_requested_command(repo_root, arguments: list[str]) -> int:
    """Run one authenticated GitHub CLI command with bounded output."""
    if not arguments:
        print("error: run requires GitHub CLI arguments", file=sys.stderr)
        return USAGE_ERROR_EXIT_CODE
    if find_literal_escape_sequences(arguments):
        print(
            "error: an argument contains literal escape text; use real newlines "
            "or --body-file",
            file=sys.stderr,
        )
        return POLICY_DENIAL_EXIT_CODE
    try:
        gate_core = _load_gate_core()
    except ImportError:
        print("error: GitHub safety policy is unavailable; repair adoption", file=sys.stderr)
        return POLICY_DENIAL_EXIT_CODE
    decision, reason = gate_core.forge_verdict("gh", arguments)
    if decision == "deny":
        print(f"error: GitHub command denied by policy: {gate_core.sanitize(reason)}",
              file=sys.stderr)
        return POLICY_DENIAL_EXIT_CODE
    return _execute(repo_root, arguments, gate_core)


def _execute(repo_root, arguments: list[str], gate_core=None, input_text=None,
             output_note=None) -> int:
    """Verify the account, run gh, and relay bounded output and failures.

    With `gate_core`, add repository context first and check the final
    arguments again. Comment operations build fixed arguments and pass none.
    `input_text` reaches gh on stdin. `output_note` maps successful stdout
    to an optional stderr note.
    """
    rest_request = None
    try:
        effective_arguments = arguments
        if gate_core is not None:
            effective_arguments = with_repository_context(Path(repo_root), arguments)
            decision, reason = gate_core.forge_verdict("gh", effective_arguments)
            if decision == "deny":
                print(f"error: GitHub command denied by policy: {gate_core.sanitize(reason)}",
                      file=sys.stderr)
                return POLICY_DENIAL_EXIT_CODE
            rest_request = rest_write_request(repo_root, arguments)
        if rest_request is not None:
            effective_arguments, input_text = rest_request
        verify_account(repo_root)
        if input_text is None:
            result = run_gh(repo_root, effective_arguments)
        else:
            result = run_gh(repo_root, effective_arguments, input_text=input_text)
    except subprocess.TimeoutExpired:
        print(TIMEOUT_ERROR_MESSAGE, file=sys.stderr)
        return 1
    except RestWriteError as error:
        print(f"error: {_bounded_path(error)}", file=sys.stderr)
        return USAGE_ERROR_EXIT_CODE
    except ValueError:
        print("error: GitHub CLI input or repository metadata is invalid; inspect and retry",
              file=sys.stderr)
        return 1
    except FileNotFoundError:
        print("error: GitHub CLI or repository metadata is unavailable; inspect installation",
              file=sys.stderr)
        return 1
    except ConnectivityError:
        print(CONNECTIVITY_ERROR_MESSAGE, file=sys.stderr)
        return 1
    except GitHubAccessError as error:
        print(f"error: {describe_access_error(error)}", file=sys.stderr)
        return 1
    except PermissionError as error:
        print(describe_permission_error(error), file=sys.stderr)
        return 1
    except OSError:
        print("error: GitHub CLI execution failed; inspect connectivity and repository context",
              file=sys.stderr)
        return 1
    if result.returncode != 0 and is_connectivity_failure(result.stderr):
        print(CONNECTIVITY_ERROR_MESSAGE, file=sys.stderr)
        return 1
    if result.returncode != 0 and GRAPHQL_UNAVAILABLE in result.stderr[:ERROR_SCAN_LIMIT]:
        print(GRAPHQL_ERROR_MESSAGE, file=sys.stderr)
        return 1
    if rest_request is not None:
        return _relay_issue_result(result)
    return _relay_result(result, output_note)


def strip_terminal_controls(text: str) -> str:
    """Replace terminal control and bidi override characters with '?'."""
    return text.translate(TERMINAL_CONTROL_TABLE)


def _relay_result(result, output_note=None) -> int:
    """Write bounded, control-free gh output, add an optional note, and return the exit code."""
    _safe_write(sys.stdout, strip_terminal_controls(result.stdout[:COMMAND_OUTPUT_LIMIT]))
    _safe_write(sys.stderr, strip_terminal_controls(result.stderr[:COMMAND_OUTPUT_LIMIT]))
    note = output_note(result.stdout) if output_note and result.returncode == 0 else ""
    if note:
        print(note, file=sys.stderr)
    return result.returncode


def main() -> int:
    """Print bounded authenticated account metadata as JSON."""
    if len(sys.argv) > 1:
        _program, operation, *gh_arguments = sys.argv
        if operation in ("comments", "comment"):
            return _run_comment_command(os.getcwd(), operation, gh_arguments)
        if operation != "run":
            print("error: expected 'run', 'comments', 'comment', or no arguments",
                  file=sys.stderr)
            return USAGE_ERROR_EXIT_CODE
        return _run_requested_command(os.getcwd(), gh_arguments)
    try:
        account = authenticated_account(os.getcwd())
    except subprocess.TimeoutExpired:
        print(TIMEOUT_ERROR_MESSAGE, file=sys.stderr)
        return 1
    except ValueError:
        print("error: GitHub account metadata is invalid; inspect authentication", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print("error: GitHub CLI is unavailable; inspect installation", file=sys.stderr)
        return 1
    except ConnectivityError:
        print(CONNECTIVITY_ERROR_MESSAGE, file=sys.stderr)
        return 1
    except GitHubAccessError as error:
        print(f"error: {describe_access_error(error)}", file=sys.stderr)
        return 1
    except PermissionError as error:
        print(describe_permission_error(error), file=sys.stderr)
        return 1
    except OSError:
        print("error: GitHub authentication failed; inspect connectivity and account state",
              file=sys.stderr)
        return 1
    print(json.dumps(account, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
