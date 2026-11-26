#!/usr/bin/env python3
"""Run trusted compliance checks against one immutable Git tree."""
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

MAX_BLOB_SIZE = 50 * 1024 * 1024
MAX_TOTAL_BLOB_BYTES = 256 * 1024 * 1024
MAX_TREE_ENTRIES = 100_000
MAX_VIOLATIONS = 1_000
MAX_TREE_OUTPUT = 64 * 1024 * 1024
MAX_GIT_DIAGNOSTIC = 64 * 1024
# A runner event payload stays far below this bound. The cap bounds the read.
MAX_EVENT_PAYLOAD_BYTES = 1024 * 1024
# check_conflict_markers keeps its own _sanitize copy capped at 200, so each
# file stays standalone. tests/test_tree_checker_parity.py pins the shared
# rendering below both caps.
MAX_DIAGNOSTIC_LENGTH = 500
MAX_LATIN1_CODE_POINT = 0xFF
UTF32_UNIT_BYTES = 4
UTF16_UNIT_BYTES = 2
# ASCII-heavy wide text holds NUL bytes at fixed positions in each code unit.
UTF32_NUL_HEAVY_RATIO = 0.6
UTF16_NUL_HEAVY_RATIO = 0.4
NUL_SPARSE_RATIO = 0.2
CHECKOUT_FETCH_DEPTH = 201
TRUSTED_STEP_COUNT = 5
SETUP_PYTHON_STEP_INDEX = 2
# Each command-line option consumes its name and one value.
OPTION_PAIR_WIDTH = 2
OBJECT_ID_PATTERN = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")
ACTION_PATTERN = re.compile(r"[^@\s]+@[0-9a-fA-F]{40}\Z")
PYTHON_VERSION_PATTERN = re.compile(r"^3\.\d+(?:\.\d+)?$")
CODE_SUFFIXES = {
    ".c", ".cc", ".cjs", ".cpp", ".cs", ".go", ".java", ".js",
    ".jsx", ".mjs", ".php", ".py", ".pyw", ".rb", ".rs", ".ts",
    ".tsx",
}
TRUSTED_REQUIREMENTS_COMMAND = (
    "python -m pip install --requirement "
    "trusted-base/requirements-checkers.txt"
)
# The wheel-only form keeps a Python with no PyYAML wheel from building the
# sdist. No hash covers the sdist build backend. The privileged job must use it,
# so a pull request cannot drop back to a source build.
TRUSTED_WHEEL_COMMAND = TRUSTED_REQUIREMENTS_COMMAND + " --only-binary :all:"
TRUSTED_REQUIREMENTS_COMMANDS = (TRUSTED_WHEEL_COMMAND,)
INSTALL_STEP_INDEX = 3
TRUSTED_SCAN_COMMAND = (
    'python "$TRUSTED_CHECKER" --repo "$PR_REPO" --tree "$PR_HEAD_SHA" '
    '--base "$PR_BASE_SHA" --branch "$PR_HEAD_BRANCH" '
    '--pr-author "$PR_AUTHOR"'
)
CHECKOUT_ACTION = "actions/checkout@11d5960a326750d5838078e36cf38b85af677262"
SETUP_PYTHON_ACTION = (
    "actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065"
)
TRUSTED_ENVIRONMENT = {
    "PR_BASE_REPOSITORY": "${{ github.event.pull_request.base.repo.full_name }}",
    "PR_BASE_SHA": "${{ github.event.pull_request.base.sha }}",
    "PR_HEAD_BRANCH": "${{ github.event.pull_request.head.ref }}",
    "PR_HEAD_REPOSITORY": "${{ github.event.pull_request.head.repo.full_name }}",
    "PR_HEAD_SHA": "${{ github.event.pull_request.head.sha }}",
    "PR_AUTHOR": "${{ github.event.pull_request.user.login }}",
    "PR_REPO": "pr-head",
    "TRUSTED_CHECKER": "trusted-base/scripts/check_compliance_tree.py",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES": (
        "${{ github.workspace }}/trusted-base/.git/objects"
    ),
}
SUPPORTED_ENTRIES = {
    (b"100644", b"blob"),
    (b"100755", b"blob"),
    (b"120000", b"blob"),
}
CHECKER_NAMES = (
    "check_secrets_heuristic",
    "check_persist_credentials",
    "check_weak_hashing",
    "check_dockerfile_root",
    "check_conflict_markers",
    "check_banned_agents",
    "check_commit_attribution",
    "check_commit_message",
    "check_git_identity",
    "check_branch_name",
    "check_local_paths",
)
BOT_REGISTRY_NAME = "trusted_bot_identities"
PULL_TARGET_TOP_KEYS = frozenset(
    {"name", True, "on", "concurrency", "permissions", "env", "jobs"})
# The exemption manifest sits beside the trusted base checker, like the
# local-path allowlist, so a pull request cannot widen its own exemptions.
EXEMPTIONS_FILE = ".compliance-tree-exemptions.json"
MAX_EXEMPTIONS_BYTES = 64 * 1024
EXEMPTIONS_VERSION = 1
EXEMPTION_KEYS = frozenset(("version", "pull_request_target", "rule7_excluded_trees"))
REVIEWED_REQUIRED_KEYS = frozenset(("secrets", "sha256"))
REVIEWED_ENTRY_KEYS = frozenset(("secrets", "sha256", "reusable_workflows", "permissions"))
# Structural rules cannot judge script text, so each reviewed caller and
# each local reusable target is pinned to the exact reviewed blob bytes.
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}\Z")
LOCAL_TARGET_PREFIX = "./"
# A bound reusable workflow is a local workflow file or a remote one pinned
# to a full commit SHA, so a pull request cannot substitute its target.
LOCAL_REUSABLE_PATTERN = re.compile(r"\./\.github/workflows/[A-Za-z0-9_-][A-Za-z0-9._-]*\.ya?ml\Z")
REMOTE_REUSABLE_PATTERN = re.compile(
    r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/\.github/workflows/[A-Za-z0-9_-][A-Za-z0-9._-]*\.ya?ml"
    r"@[0-9a-fA-F]{40}\Z")
REVIEWED_WORKFLOW_PATTERN = re.compile(r"\.github/workflows/[A-Za-z0-9_-][A-Za-z0-9._-]*\.ya?ml\Z")
SECRET_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
# Segments that start with a dot are refused, which also refuses `.` and
# `..` traversal and every client configuration directory.
TREE_SEGMENT_PATTERN = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9._-]*\Z")
TREE_WILDCARD = "*"
PROTECTED_TREE_ROOTS = frozenset(("scripts", "hooks"))
REVIEWED_TOP_PERMISSIONS = ({}, {"contents": "read"})
SECRET_REFERENCE_PATTERN = re.compile(r"secrets\s*\.\s*([A-Za-z_][A-Za-z0-9_]*)", re.IGNORECASE)
SECRET_INDEX_PATTERN = re.compile(r"secrets\s*\[", re.IGNORECASE)
# The whole secrets context, not followed by a property access, carries every
# secret past the named allowlist. The pattern runs on one expression body.
WHOLE_SECRETS_PATTERN = re.compile(r"\bsecrets\b(?!\s*\.)", re.IGNORECASE)
# Event fields such as titles and branch names carry attacker text. The
# word boundary leaves github.event_name, which carries no such text.
# Any use of the whole github context, not followed by a property access,
# reaches the same fields. The pattern runs on one expression body at a time.
EVENT_CONTEXT_PATTERN = re.compile(
    r"\bgithub(?:\.event\b|\.head_ref\b|\b(?!\s*\.))", re.IGNORECASE)
EXPRESSION_OPEN = "${{"
EXPRESSION_CLOSE = "}}"
# A condition or a concurrency group evaluates event data without passing it
# to a step, an action input, or the environment. Only these structural
# fields qualify. A nested key with the same name gains no exemption.
WORKFLOW_SAFE_KEYS = frozenset(("concurrency",))
JOB_SAFE_KEYS = frozenset(("if", "concurrency"))
STEP_SAFE_KEYS = frozenset(("if",))
PERMISSION_SCOPES = frozenset((
    "actions", "attestations", "checks", "contents", "deployments", "discussions",
    "id-token", "issues", "models", "packages", "pages", "pull-requests",
    "repository-projects", "security-events", "statuses",
))
PERMISSION_LEVELS = frozenset(("none", "read", "write"))
WRITE_LEVEL = "write"


@dataclass(frozen=True)
class ReviewedWorkflow:
    """Secret names and reusable workflows that one reviewed caller may use."""

    secrets: frozenset
    reusable_workflows: frozenset
    write_scopes: frozenset = frozenset()
    caller_digest: str = ""
    local_digests: tuple = ()


@dataclass(frozen=True)
class TreeExemptions:
    """Reviewed exemptions that the trusted base branch declares."""

    pull_target: dict
    rule7_trees: tuple


NO_EXEMPTIONS = TreeExemptions({}, ())


def _sanitize(value: object) -> str:
    """Render untrusted data as bounded printable ASCII."""
    rendered = []
    for character in str(value):
        if " " <= character <= "~":
            rendered.append(character)
        elif ord(character) <= MAX_LATIN1_CODE_POINT:
            rendered.append(f"\\x{ord(character):02x}")
        else:
            rendered.append(f"\\u{ord(character):04x}")
    text = "".join(rendered)
    if len(text) > MAX_DIAGNOSTIC_LENGTH:
        return text[:MAX_DIAGNOSTIC_LENGTH] + "...[truncated]"
    return text


def _load_module(path: Path, name: str) -> ModuleType:
    """Load a module from an exact trusted sibling path."""
    spec = importlib.util.spec_from_file_location(f"_compliance_{name}", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"could not load trusted checker {_sanitize(name)}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_trusted(name: str) -> ModuleType:
    """Load one module from the orchestrator's own directory."""
    path = Path(__file__).resolve().parent / f"{name}.py"
    if not path.is_file():
        raise RuntimeError(f"trusted checker is missing: {name}.py")
    return _load_module(path, name)


def _load_checkers() -> dict[str, ModuleType]:
    """Load every checker from the orchestrator's own directory."""
    return {name: _load_trusted(name) for name in CHECKER_NAMES}


def _trusted_root() -> Path:
    """Return the repository root of the trusted checker copy."""
    return Path(__file__).resolve().parent.parent


def _manifest_error(detail: str) -> RuntimeError:
    """Build one fail-closed manifest error with its recovery action."""
    return RuntimeError(
        f"{EXEMPTIONS_FILE} is invalid: {detail}. Fix: correct the manifest "
        "on the base branch through a reviewed gate change.")


def _read_exemptions_text(path: Path) -> str | None:
    """Return the ASCII manifest text, or None when the manifest is absent."""
    if path.is_symlink():
        raise _manifest_error("the manifest must not be a symlink")
    if not path.exists():
        return None
    if not path.is_file() or path.stat().st_size > MAX_EXEMPTIONS_BYTES:
        raise _manifest_error(f"the manifest must be a regular file of at most {MAX_EXEMPTIONS_BYTES} bytes")
    try:
        return path.read_bytes().decode("ascii")
    except UnicodeDecodeError as error:
        raise _manifest_error("the manifest must be ASCII") from error


def _parse_reviewed_workflows(value: object) -> dict:
    """Map each reviewed workflow path to its allowed secret names."""
    if not isinstance(value, dict):
        raise _manifest_error("pull_request_target must be an object")
    reviewed = {}
    for path, entry in value.items():
        if not REVIEWED_WORKFLOW_PATTERN.fullmatch(path):
            raise _manifest_error(f"{_sanitize(path)} is not a workflow path")
        if (not isinstance(entry, dict) or not REVIEWED_REQUIRED_KEYS <= set(entry)
                or not set(entry) <= REVIEWED_ENTRY_KEYS):
            raise _manifest_error(
                f"{_sanitize(path)} must hold a secrets list and optional "
                "reusable_workflows and permissions values")
        targets, local_digests = _parse_reusable_workflows(
            path, entry.get("reusable_workflows", []))
        reviewed[path] = ReviewedWorkflow(
            _parse_secret_names(path, entry["secrets"]), targets,
            _parse_write_scopes(path, entry.get("permissions", {})),
            _parse_digest(path, entry["sha256"]), local_digests)
    return reviewed


def _parse_digest(path: str, digest: object) -> str:
    """Return one lowercase SHA-256 hex digest from the manifest."""
    if not isinstance(digest, str) or not SHA256_PATTERN.fullmatch(digest):
        raise _manifest_error(f"{_sanitize(path)} needs a lowercase 64-character sha256")
    return digest


def _parse_write_scopes(path: str, permissions: object) -> frozenset:
    """Return the token scopes that a reviewed caller's jobs may set to write."""
    if not isinstance(permissions, dict) or not all(
            scope in PERMISSION_SCOPES and level == WRITE_LEVEL
            for scope, level in permissions.items()):
        raise _manifest_error(
            f"{_sanitize(path)} permissions must map known scopes to write")
    return frozenset(permissions)


def _parse_secret_names(path: str, names: object) -> frozenset:
    """Return the upper-case secret names one reviewed caller may reference."""
    if not isinstance(names, list) or not all(
            isinstance(name, str) and SECRET_NAME_PATTERN.fullmatch(name) for name in names):
        raise _manifest_error(f"{_sanitize(path)} secrets must be secret names")
    return frozenset(name.upper() for name in names)


def _parse_reusable_workflows(path: str, targets: object) -> tuple:
    """Return the allowed job-level `uses:` values and each local target's pin.

    A list names remote targets only. An object maps each local target to its
    sha256 and each remote target to null, since its commit SHA already pins it.
    """
    if isinstance(targets, list):
        targets = dict.fromkeys(targets)
    if not isinstance(targets, dict):
        raise _manifest_error(f"{_sanitize(path)} reusable_workflows must be a list or an object")
    local_digests = []
    for target, digest in targets.items():
        if isinstance(target, str) and REMOTE_REUSABLE_PATTERN.fullmatch(target) and digest is None:
            continue
        if isinstance(target, str) and LOCAL_REUSABLE_PATTERN.fullmatch(target):
            local_digests.append((target, _parse_digest(path, digest)))
            continue
        raise _manifest_error(
            f"{_sanitize(path)} reusable_workflows must map local workflow paths to a "
            "sha256 and remote workflows pinned to a full commit SHA to null")
    return frozenset(targets), tuple(local_digests)


def _parse_tree_pattern(pattern: object) -> tuple:
    """Split one `segment/.../` fixture tree pattern into checked segments."""
    if not isinstance(pattern, str) or not pattern.endswith("/"):
        raise _manifest_error("each excluded tree must be text that ends with /")
    segments = tuple(pattern[:-1].split("/"))
    for segment in segments:
        if segment != TREE_WILDCARD and not TREE_SEGMENT_PATTERN.fullmatch(segment):
            raise _manifest_error(f"{_sanitize(pattern)} holds an unsafe segment")
    if segments[0] == TREE_WILDCARD or segments[0] in PROTECTED_TREE_ROOTS:
        raise _manifest_error(f"{_sanitize(pattern)} must start below a literal unprotected directory")
    return segments


def load_exemptions(root: Path) -> TreeExemptions:
    """Load the exemption manifest from one trusted root, or none when absent."""
    text = _read_exemptions_text(root / EXEMPTIONS_FILE)
    if text is None:
        return NO_EXEMPTIONS
    try:
        document = json.loads(text)
    except json.JSONDecodeError as error:
        raise _manifest_error("the manifest is not JSON") from error
    if not isinstance(document, dict) or not set(document) <= EXEMPTION_KEYS:
        raise _manifest_error(f"the manifest allows only {sorted(EXEMPTION_KEYS)}")
    version = document.get("version")
    if type(version) is not int or version != EXEMPTIONS_VERSION:
        raise _manifest_error(f"version must be {EXEMPTIONS_VERSION}")
    trees = document.get("rule7_excluded_trees", [])
    if not isinstance(trees, list):
        raise _manifest_error("rule7_excluded_trees must be a list")
    return TreeExemptions(
        _parse_reviewed_workflows(document.get("pull_request_target", {})),
        tuple(_parse_tree_pattern(pattern) for pattern in trees))


def _pinned_digest_violations(exemptions: TreeExemptions | None, digests: dict) -> list[str]:
    """Compare head blob digests with every caller and local target pin."""
    violations = []
    for path, reviewed in sorted((exemptions or NO_EXEMPTIONS).pull_target.items()):
        if path not in digests:
            continue
        prefix = f"{path}: "
        if digests[path] != reviewed.caller_digest:
            violations.append(
                prefix + "reviewed pull_request_target content does not match the manifest sha256")
        for target, expected in reviewed.local_digests:
            actual = digests.get(target[len(LOCAL_TARGET_PREFIX):])
            if actual is None:
                violations.append(f"{prefix}bound reusable workflow {target} is absent from the tree")
            elif actual != expected:
                violations.append(
                    f"{prefix}bound reusable workflow {target} does not match the manifest sha256")
    return violations


def _tree_matches(segments: tuple, pattern: tuple) -> bool:
    """Return whether a file path sits below one excluded tree pattern."""
    if len(segments) <= len(pattern):
        return False
    return all(expected in (TREE_WILDCARD, actual) for expected, actual in zip(pattern, segments))


def _rule7_excluded(path: str, exemptions: TreeExemptions | None) -> bool:
    """Return whether the base manifest excludes a path from the Rule 7 scan."""
    if exemptions is None:
        return False
    segments = tuple(path.split("/"))
    return any(_tree_matches(segments, pattern) for pattern in exemptions.rule7_trees)


def _is_within(path: Path, parent: Path) -> bool:
    """Return True when path resolves to parent or one of its descendants."""
    try:
        return os.path.commonpath((str(path), str(parent))) == str(parent)
    except ValueError:
        return False


def _safe_search_path(repo: Path) -> str:
    """Return PATH without relative entries or directories in the candidate repo.

    A relative entry resolves against the process working directory. A
    caller can point that directory into the candidate repo.
    """
    safe_entries = []
    for raw_entry in os.environ.get("PATH", "").split(os.pathsep):
        if not raw_entry or not Path(raw_entry).is_absolute():
            continue
        try:
            entry = Path(raw_entry).resolve(strict=False)
        except OSError:
            continue
        if not _is_within(entry, repo):
            safe_entries.append(str(entry))
    return os.pathsep.join(safe_entries)


def _safe_execution_directory(repo: Path, executable: str) -> Path:
    """Return an existing process directory outside the candidate repo."""
    for candidate in (Path(tempfile.gettempdir()), Path(executable).parent):
        try:
            resolved = candidate.resolve(strict=True)
        except OSError:
            continue
        if resolved.is_dir() and not _is_within(resolved, repo):
            return resolved
    raise RuntimeError("no safe external Git execution directory is available")


def _validate_executable(raw_executable: object, repo: Path) -> str:
    """Return an absolute executable path outside the candidate repo."""
    try:
        executable = Path(str(raw_executable)).resolve(strict=True)
    except OSError as error:
        raise RuntimeError(f"could not resolve trusted Git: {_sanitize(error)}") from error
    if not executable.is_file() or _is_within(executable, repo):
        raise RuntimeError("trusted Git is not a file outside the candidate repo")
    if os.name != "nt" and not os.access(executable, os.X_OK):
        raise RuntimeError("trusted Git is not executable")
    return str(executable)


def _resolve_git(repo: Path) -> tuple[str, str]:
    """Resolve Git through trusted_git.py or a candidate-free PATH."""
    scripts_dir = Path(__file__).resolve().parent
    resolver_path = scripts_dir / "trusted_git.py"
    safe_path = _safe_search_path(repo)
    if resolver_path.is_file():
        resolver = _load_module(resolver_path, "trusted_git")
        resolve_git = getattr(resolver, "resolve_git", None)
        if not callable(resolve_git):
            raise RuntimeError("trusted_git.py does not define resolve_git")
        return _validate_executable(resolve_git(repo), repo), safe_path
    # The search covers only absolute entries outside the candidate repo, and
    # _validate_executable re-checks the result.
    executable = shutil.which("git", path=safe_path)
    if executable is None:
        raise RuntimeError("could not find Git outside the candidate repo")
    return _validate_executable(executable, repo), safe_path


def _git_environment(safe_path: str) -> dict[str, str]:
    """Return a noninteractive Git environment with unsafe features disabled.

    This stays a denylist. The immutable compliance workflow passes the
    candidate objects through GIT_ALTERNATE_OBJECT_DIRECTORIES. The
    trusted_git allowlist would drop that variable.
    """
    environment = os.environ.copy()
    for name in (
        "GIT_COMMON_DIR", "GIT_CONFIG", "GIT_CONFIG_COUNT",
        "GIT_CONFIG_PARAMETERS", "GIT_DIR", "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY", "GIT_WORK_TREE",
    ):
        environment.pop(name, None)
    for name in list(environment):
        if name.startswith(("GIT_CONFIG_KEY_", "GIT_CONFIG_VALUE_")):
            environment.pop(name)
    environment.update({
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_NO_LAZY_FETCH": "1",
        "GIT_NO_REPLACE_OBJECTS": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_PROTOCOL_FROM_USER": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "PATH": safe_path,
    })
    return environment


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    """Stop and join a Git process that exceeded an output bound."""
    process.terminate()
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _capture_bounded(
    command: list[str], repo: Path, environment: dict[str, str], limit: int,
) -> tuple[int, bytes]:
    """Capture at most limit bytes from one Git process."""
    process = subprocess.Popen(
        command,
        cwd=_safe_execution_directory(repo, command[0]),
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        output = process.stdout.read(limit + 1)
        if len(output) > limit:
            _stop_process(process)
            raise RuntimeError(f"Git output exceeds limit ({limit} bytes)")
        return_code = process.wait()
    except BaseException:
        if process.poll() is None:
            _stop_process(process)
        raise
    finally:
        process.stdout.close()
    return return_code, output


def _supports_option(
    executable: str, option: str, repo: Path, environment: dict[str, str],
) -> bool:
    """Return True when trusted Git accepts one global safety option."""
    command = [executable, option, "--version"]
    return_code, _ = _capture_bounded(
        command, repo, environment, MAX_GIT_DIAGNOSTIC)
    return return_code == 0


def _git_prefix(
    executable: str, repo: Path, environment: dict[str, str],
) -> list[str]:
    """Build the common trusted Git object-command prefix."""
    prefix = [executable, "-C", str(repo), "--no-pager"]
    if _supports_option(executable, "--no-replace-objects", repo, environment):
        prefix.append("--no-replace-objects")
    if _supports_option(executable, "--no-lazy-fetch", repo, environment):
        prefix.append("--no-lazy-fetch")
    prefix.extend(("-c", "core.fsmonitor="))
    return prefix


def _run_git(
    prefix: list[str], arguments: list[str], repo: Path,
    environment: dict[str, str], limit: int,
) -> bytes:
    """Run one bounded Git command and raise a sanitized-safe error."""
    return_code, output = _capture_bounded(
        prefix + arguments, repo, environment, limit)
    if return_code != 0:
        message = output.decode("utf-8", errors="replace").strip()
        raise RuntimeError(
            f"git {arguments[0]} failed (exit {return_code}): "
            f"{_sanitize(message)}"
        )
    return output


def _validate_object_id(object_id: str) -> str:
    """Return a normalized 40- or 64-character hexadecimal object ID."""
    if not OBJECT_ID_PATTERN.fullmatch(object_id):
        raise RuntimeError(
            "--tree requires a validated hexadecimal object id "
            "with 40 or 64 characters"
        )
    return object_id.lower()


def _validate_tree_path(raw_path: bytes) -> str:
    """Decode and validate one repository-relative tree path."""
    try:
        path = raw_path.decode("utf-8")
    except UnicodeDecodeError as error:
        raise RuntimeError("tree entry path is not valid UTF-8") from error
    parts = path.split("/")
    if not path or path.startswith("/") or any(part in ("", ".", "..") for part in parts):
        raise RuntimeError(f"malformed tree entry path: {_sanitize(path)}")
    return path


def _parse_tree_entries(raw_output: bytes) -> list[tuple[str, str, str]]:
    """Parse complete ls-tree records into supported tracked blobs.

    check_conflict_markers keeps its own copy so each file stays standalone.
    tests/test_tree_parser_parity.py pins both copies to one record grammar.
    """
    if raw_output and not raw_output.endswith(b"\x00"):
        raise RuntimeError("truncated git ls-tree output")
    records = raw_output[:-1].split(b"\x00") if raw_output else []
    entries = []
    seen = set()
    for record in records:
        if len(entries) >= MAX_TREE_ENTRIES:
            raise RuntimeError(
                f"tree has more than {MAX_TREE_ENTRIES} entries")
        try:
            metadata, raw_path = record.split(b"\t", 1)
            mode, object_type, raw_id = metadata.split(b" ")
            object_id = raw_id.decode("ascii")
        except (ValueError, UnicodeDecodeError) as error:
            raise RuntimeError("malformed git ls-tree entry") from error
        path = _validate_tree_path(raw_path)
        if path in seen or not OBJECT_ID_PATTERN.fullmatch(object_id):
            raise RuntimeError(f"malformed git ls-tree entry: {_sanitize(path)}")
        if (mode, object_type) not in SUPPORTED_ENTRIES:
            raise RuntimeError(f"unsupported git ls-tree entry: {_sanitize(path)}")
        seen.add(path)
        entries.append((path, object_id.lower(), mode.decode("ascii")))
    return entries


def _parse_blob_size(raw_size: bytes, path: str) -> int:
    """Parse and bound one exact cat-file size response."""
    if not re.fullmatch(rb"(?:0|[1-9][0-9]*)\n", raw_size):
        raise RuntimeError(f"malformed blob size for {_sanitize(path)}")
    size = int(raw_size[:-1])
    if size > MAX_BLOB_SIZE:
        raise RuntimeError(
            f"{_sanitize(path)}: blob size ({size} bytes) exceeds "
            f"limit ({MAX_BLOB_SIZE} bytes)"
        )
    return size


def _read_blob(
    prefix: list[str], repo: Path, environment: dict[str, str],
    path: str, object_id: str,
) -> bytes:
    """Read one blob after checking and enforcing its exact size."""
    raw_size = _run_git(
        prefix, ["cat-file", "-s", "--", object_id], repo,
        environment, MAX_GIT_DIAGNOSTIC)
    size = _parse_blob_size(raw_size, path)
    return_code, content = _capture_bounded(
        prefix + ["cat-file", "blob", "--", object_id],
        repo, environment, size)
    if return_code != 0:
        raise RuntimeError(f"git cat-file failed while reading {_sanitize(path)}")
    if len(content) != size:
        raise RuntimeError(
            f"{_sanitize(path)}: blob read {len(content)} bytes; expected {size}"
        )
    return content


def _decode_blob(content: bytes) -> str:
    """Decode BOM-aware Unicode, using Latin-1 as a one-to-one fallback."""
    encodings = (
        (b"\xff\xfe\x00\x00", "utf-32"),
        (b"\x00\x00\xfe\xff", "utf-32"),
        (b"\xef\xbb\xbf", "utf-8-sig"),
        (b"\xff\xfe", "utf-16"),
        (b"\xfe\xff", "utf-16"),
    )
    for marker, encoding in encodings:
        if not content.startswith(marker):
            continue
        try:
            return content.decode(encoding).lstrip("\ufeff")
        except UnicodeDecodeError:
            return content.decode("latin-1")
    inferred = _decode_bomless_unicode(content)
    if inferred is not None:
        return inferred
    try:
        return content.decode("utf-8").lstrip("\ufeff")
    except UnicodeDecodeError:
        return content.decode("latin-1")


def _zero_ratio(content: bytes, offset: int, width: int) -> float:
    """Return the NUL ratio at one byte position in fixed-width units."""
    sample = content[offset::width]
    return sample.count(0) / len(sample) if sample else 0.0


def _decode_bomless_unicode(content: bytes) -> str | None:
    """Decode UTF-16/32 text identified by its byte-position NUL pattern."""
    candidates = []
    if len(content) % UTF32_UNIT_BYTES == 0 and len(content) >= UTF32_UNIT_BYTES:
        ratios = [_zero_ratio(content, offset, UTF32_UNIT_BYTES) for offset in range(UTF32_UNIT_BYTES)]
        if min(ratios[1:]) > UTF32_NUL_HEAVY_RATIO and ratios[0] < NUL_SPARSE_RATIO:
            candidates.append("utf-32-le")
        if min(ratios[:-1]) > UTF32_NUL_HEAVY_RATIO and ratios[-1] < NUL_SPARSE_RATIO:
            candidates.append("utf-32-be")
    if len(content) % UTF16_UNIT_BYTES == 0 and len(content) >= UTF16_UNIT_BYTES:
        even = _zero_ratio(content, 0, UTF16_UNIT_BYTES)
        odd = _zero_ratio(content, 1, UTF16_UNIT_BYTES)
        if odd > UTF16_NUL_HEAVY_RATIO and even < NUL_SPARSE_RATIO:
            candidates.append("utf-16-le")
        if even > UTF16_NUL_HEAVY_RATIO and odd < NUL_SPARSE_RATIO:
            candidates.append("utf-16-be")
    for encoding in candidates:
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    return None


def _expression_bodies(value: str):
    """Yield each `${{ ... }}` body in one linear pass.

    An opening without a close extends to the end of the text. No later
    close can exist after it, so the scan stops there.
    """
    position = value.find(EXPRESSION_OPEN)
    while position != -1:
        start = position + len(EXPRESSION_OPEN)
        end = value.find(EXPRESSION_CLOSE, start)
        if end == -1:
            yield value[start:]
            return
        yield value[start:end]
        position = value.find(EXPRESSION_OPEN, end + len(EXPRESSION_CLOSE))


def _has_event_expression(value: object) -> bool:
    """Return whether a text value interpolates event data or the whole github context."""
    if not isinstance(value, str):
        return False
    return any(EVENT_CONTEXT_PATTERN.search(body) for body in _expression_bodies(value))


def _walk_values(root: object):
    """Yield nested YAML values once, including alias graphs."""
    stack = [root]
    visited = set()
    while stack:
        value = stack.pop()
        if isinstance(value, (dict, list)):
            identity = id(value)
            if identity in visited:
                continue
            visited.add(identity)
        yield value
        if isinstance(value, dict):
            stack.extend(value.values())
        elif isinstance(value, list):
            stack.extend(value)


def _action_violations(document: object, path: str) -> list[str]:
    """Require immutable revisions on every external workflow action."""
    violations = []
    for value in _walk_values(document):
        if not isinstance(value, dict) or "uses" not in value:
            continue
        reference = value["uses"]
        if not isinstance(reference, str):
            violations.append(f"{path}: action reference must be text")
        elif not reference.startswith("./") and not ACTION_PATTERN.fullmatch(reference):
            violations.append(
                f"{path}: action '{reference}' must use a full commit SHA")
    return violations


def _pull_target_trigger(document: dict) -> bool:
    """Return whether a workflow declares pull_request_target."""
    trigger = document.get("on", document.get(True))
    if isinstance(trigger, str):
        return trigger == "pull_request_target"
    if isinstance(trigger, list):
        return "pull_request_target" in trigger
    return isinstance(trigger, dict) and "pull_request_target" in trigger


def _checkout_step(base: bool) -> dict:
    """Return one exact trusted checkout step schema."""
    role = "trusted base" if base else "pull request head"
    prefix = "BASE" if base else "HEAD"
    return {
        "name": f"Check out the exact {role}",
        "uses": CHECKOUT_ACTION,
        "with": {
            "repository": f"${{{{ env.PR_{prefix}_REPOSITORY }}}}",
            "ref": f"${{{{ env.PR_{prefix}_SHA }}}}",
            "path": "trusted-base" if base else "pr-head",
            "persist-credentials": False,
            "fetch-depth": CHECKOUT_FETCH_DEPTH,
        },
    }


def _trusted_steps(install_command: str = TRUSTED_WHEEL_COMMAND) -> list[dict]:
    """Return the exact privileged step sequence allowed for promotion.

    `install_command` must be one of TRUSTED_REQUIREMENTS_COMMANDS.
    """
    return [
        _checkout_step(True),
        _checkout_step(False),
        {
            "name": "Set up Python",
            "uses": SETUP_PYTHON_ACTION,
            "with": {"python-version": "3.x"},
        },
        {
            "name": "Install trusted checker dependencies",
            "run": install_command,
        },
        {
            "name": "Scan immutable pull request objects with the trusted checker",
            "env": {"GH_TOKEN": "${{ github.token }}"},
            "run": TRUSTED_SCAN_COMMAND,
        },
    ]


def _candidate_install_command(candidate_job) -> str:
    """Return the candidate's install command when it is an accepted form.

    Any other command falls back to the wheel-only form, so the exact
    schema comparison still rejects it.
    """
    steps = candidate_job.get("steps") if isinstance(candidate_job, dict) else None
    if not isinstance(steps, list) or len(steps) != TRUSTED_STEP_COUNT:
        return TRUSTED_WHEEL_COMMAND
    install_step = steps[INSTALL_STEP_INDEX]
    command = install_step.get("run") if isinstance(install_step, dict) else None
    if command in TRUSTED_REQUIREMENTS_COMMANDS:
        return command
    return TRUSTED_WHEEL_COMMAND


def _reviewed_secret_messages(jobs: dict, text: str, allowed: frozenset) -> list[str]:
    """Allow only secret references that the base manifest lists."""
    messages = []
    if SECRET_INDEX_PATTERN.search(text):
        messages.append("indexes secrets")
    if any(isinstance(job, dict) and job.get("secrets") == "inherit" for job in jobs.values()):
        messages.append("inherits secrets")
    if any(WHOLE_SECRETS_PATTERN.search(body) for body in _expression_bodies(text)):
        messages.append("references the whole secrets context")
    referenced = {match.group(1).upper() for match in SECRET_REFERENCE_PATTERN.finditer(text)}
    messages.extend(f"references unlisted secret {name}" for name in sorted(referenced - allowed))
    return messages


def _reviewed_step_messages(step: object) -> set:
    """Refuse code checkout and event interpolation in one step."""
    if not isinstance(step, dict):
        return {"has a non-mapping step"}
    messages = set()
    uses = step.get("uses")
    if isinstance(uses, str) and uses.lower().startswith("actions/checkout"):
        messages.add("checks out code")
    for value in _walk_values([step.get("run"), step.get("with")]):
        if _has_event_expression(value):
            messages.add("interpolates event data in a step")
    return messages


def _reviewed_call_messages(job: dict, reusable_workflows: frozenset) -> set:
    """Bind a job-level reusable workflow call and refuse event data in its inputs."""
    messages = set()
    target = job.get("uses")
    if target is not None and not isinstance(target, str):
        messages.add("has a non-text job uses value")
    elif target is not None and target not in reusable_workflows:
        messages.add(f"calls unbound reusable workflow {target}")
    for value in _walk_values(job.get("with")):
        if _has_event_expression(value):
            messages.add("interpolates event data in job inputs")
    return messages


def _event_scan_roots(document: dict) -> list:
    """Return every workflow value outside the structural condition and concurrency fields."""
    roots = [item for key, item in document.items() if key not in WORKFLOW_SAFE_KEYS | {"jobs"}]
    jobs = document.get("jobs")
    if not isinstance(jobs, dict):
        return roots + [jobs]
    for job in jobs.values():
        if not isinstance(job, dict):
            roots.append(job)
            continue
        roots.extend(item for key, item in job.items() if key not in JOB_SAFE_KEYS | {"steps"})
        steps = job.get("steps")
        if not isinstance(steps, list):
            roots.append(steps)
            continue
        for step in steps:
            if isinstance(step, dict):
                roots.extend(item for key, item in step.items() if key not in STEP_SAFE_KEYS)
            else:
                roots.append(step)
    return roots


def _event_data_outside_safe_keys(document: dict) -> bool:
    """Return whether event data appears anywhere but a condition or concurrency group."""
    return any(_has_event_expression(value) for value in _walk_values(_event_scan_roots(document)))


def _reviewed_permission_messages(name: object, job: dict, write_scopes: frozenset) -> set:
    """Keep one job's token permissions within the manifest's write scopes."""
    if "permissions" not in job:
        return set()
    permissions = job["permissions"]
    label = f"job {_sanitize(name)}"
    if not isinstance(permissions, dict):
        return {f"{label} permissions are not a scope mapping"}
    messages = set()
    for scope, level in permissions.items():
        if level not in PERMISSION_LEVELS:
            messages.add(f"{label} sets an invalid level for {_sanitize(scope)}")
        elif level == WRITE_LEVEL and scope not in write_scopes:
            messages.add(f"{label} grants unlisted write scope {_sanitize(scope)}")
    return messages


def _reviewed_job_messages(jobs: dict, reviewed: ReviewedWorkflow) -> list[str]:
    """Apply the reviewed call, permission, and step rules to every job."""
    messages = set()
    for name, job in jobs.items():
        if isinstance(job, dict):
            messages.update(_reviewed_call_messages(job, reviewed.reusable_workflows))
            messages.update(_reviewed_permission_messages(name, job, reviewed.write_scopes))
        steps = job.get("steps", []) if isinstance(job, dict) else []
        if not isinstance(steps, list):
            messages.add("has a non-list steps value")
            continue
        for step in steps:
            messages.update(_reviewed_step_messages(step))
    return sorted(messages)


def _reviewed_pull_target_violations(
    document: dict, text: str, path: str, reviewed: ReviewedWorkflow,
) -> list[str]:
    """Apply the documented rule set to a workflow the base manifest reviews."""
    messages = []
    if not set(document).issubset(PULL_TARGET_TOP_KEYS):
        messages.append("has unsupported top-level keys")
    if document.get("permissions") not in REVIEWED_TOP_PERMISSIONS:
        messages.append("top-level permissions are not empty or contents: read")
    jobs = document.get("jobs")
    if not isinstance(jobs, dict) or not jobs:
        messages.append("has no jobs mapping")
        jobs = {}
    messages.extend(_reviewed_secret_messages(jobs, text, reviewed.secrets))
    messages.extend(_reviewed_job_messages(jobs, reviewed))
    if _event_data_outside_safe_keys(document):
        messages.append("interpolates event data outside if and concurrency")
    return [f"{path}: reviewed pull_request_target {message}" for message in messages]


def _pull_target_violations(
    document: dict, text: str, path: str, exemptions: TreeExemptions | None = None,
) -> list[str]:
    """Require the exact closed privileged workflow execution surface."""
    if not _pull_target_trigger(document):
        return []
    reviewed = (exemptions or NO_EXEMPTIONS).pull_target.get(path)
    if reviewed is not None:
        return _reviewed_pull_target_violations(document, text, path, reviewed)
    raw_jobs = document.get("jobs")
    candidate_job = raw_jobs.get("immutable-compliance") if isinstance(raw_jobs, dict) else None
    install_command = _candidate_install_command(candidate_job)
    expected_job = {
        "runs-on": "ubuntu-latest",
        "permissions": {"contents": "read"},
        "steps": _trusted_steps(install_command),
    }
    trusted_job = dict(expected_job)
    if isinstance(candidate_job, dict):
        candidate_job = dict(candidate_job)
        candidate_steps = candidate_job.get("steps")
        if isinstance(candidate_steps, list) and len(candidate_steps) == TRUSTED_STEP_COUNT:
            python_step = candidate_steps[SETUP_PYTHON_STEP_INDEX]
            version = (python_step.get("with", {}).get("python-version")
                       if isinstance(python_step, dict)
                       else None)
            version_text = str(version) if isinstance(version, (str, float, int)) else ""
            if PYTHON_VERSION_PATTERN.fullmatch(version_text):
                candidate_steps = list(candidate_steps)
                python_step = dict(python_step)
                python_step["with"] = {"python-version": version_text}
                candidate_steps[SETUP_PYTHON_STEP_INDEX] = python_step
                candidate_job["steps"] = candidate_steps
                trusted_steps = _trusted_steps(install_command)
                trusted_steps[SETUP_PYTHON_STEP_INDEX] = dict(trusted_steps[SETUP_PYTHON_STEP_INDEX])
                trusted_steps[SETUP_PYTHON_STEP_INDEX]["with"] = {"python-version": version_text}
                trusted_job["steps"] = trusted_steps
    candidate_jobs = dict(raw_jobs) if isinstance(raw_jobs, dict) else {}
    if isinstance(candidate_job, dict):
        candidate_jobs["immutable-compliance"] = candidate_job
    job_schema_ok = document.get("jobs") in (
        {"immutable-compliance": expected_job},
        {"immutable-compliance": trusted_job},
    ) or candidate_jobs in (
        {"immutable-compliance": expected_job},
        {"immutable-compliance": trusted_job},
    )
    checks = (
        (set(document).issubset(PULL_TARGET_TOP_KEYS), "has unsupported top-level keys"),
        (document.get("permissions") == {"contents": "read"}, "permissions are not read-only"),
        (document.get("env") == TRUSTED_ENVIRONMENT, "does not bind exact trusted inputs"),
        (job_schema_ok, "job schema is not trusted"),
        (re.search(r"secrets\s*(?:\.|\[)", text, re.IGNORECASE) is None, "references secrets"),
    )
    return [
        f"{path}: pull_request_target {message}"
        for passed, message in checks if not passed
    ]


def _workflow_violations(
    text: str, path: str, persist_checker: ModuleType,
    exemptions: TreeExemptions | None = None,
) -> list[str]:
    """Check immutable actions and privileged workflow execution policy."""
    try:
        documents = persist_checker.load_documents(text)
    except persist_checker.yaml.YAMLError:
        return [f"{path}: malformed workflow YAML cannot be checked"]
    violations = []
    for document in documents:
        violations.extend(_action_violations(document, path))
        if isinstance(document, dict):
            violations.extend(_pull_target_violations(document, text, path, exemptions))
    return violations


def _scan_blob(
    path: str, text: str, mode: str, checkers: dict[str, ModuleType],
    exemptions: TreeExemptions | None = None,
) -> list[str]:
    """Dispatch one decoded tracked blob to every applicable checker."""
    violations = list(
        checkers["check_secrets_heuristic"].find_violations(text, path))
    if mode == "120000":
        critical = (
            path == "requirements-checkers.txt"
            or path == "scripts/trusted_git.py"
            or path.startswith("scripts/check_")
            or path.startswith(".github/workflows/")
        )
        if critical:
            violations.append(
                f"{path}: trusted compliance files must not be symlinks")
        return violations
    lower_path = path.lower()
    name = lower_path.rsplit("/", 1)[-1]
    is_yaml = lower_path.endswith((".yml", ".yaml"))
    is_action = lower_path.startswith(".github/actions/") and name in (
        "action.yml", "action.yaml")
    if (lower_path.startswith(".github/workflows/") and is_yaml) or is_action:
        violations.extend(
            checkers["check_persist_credentials"].find_violations(text, path))
        violations.extend(_workflow_violations(
            text, path, checkers["check_persist_credentials"], exemptions))
    if Path(lower_path).suffix in CODE_SUFFIXES and not _rule7_excluded(path, exemptions):
        violations.extend(
            checkers["check_weak_hashing"].find_violations(text, path))
    is_dockerfile = name in ("dockerfile", "containerfile") or name.startswith(
        ("dockerfile.", "dockerfile-", "containerfile.", "containerfile-"))
    if is_dockerfile or is_yaml:
        violations.extend(
            checkers["check_dockerfile_root"].find_violations(text, path))
    return violations


@contextmanager
def _trusted_path(executable: str, safe_path: str):
    """Make bare `git` calls from the conflict checker resolve safely."""
    old_path = os.environ.get("PATH")
    os.environ["PATH"] = os.pathsep.join((str(Path(executable).parent), safe_path))
    try:
        yield
    finally:
        if old_path is None:
            os.environ.pop("PATH", None)
        else:
            os.environ["PATH"] = old_path


def _scan_conflicts(
    checker: ModuleType, repo: Path, object_id: str,
    executable: str, safe_path: str,
) -> list[str]:
    """Invoke the trusted conflict checker's immutable tree mode."""
    immutable_scan = getattr(checker, "_check_tree", None)
    if not callable(immutable_scan):
        raise RuntimeError("conflict checker has no immutable tree scan")
    with _trusted_path(executable, safe_path):
        return list(immutable_scan(str(repo), object_id))


def _local_path_scanner(checkers: dict[str, ModuleType]) -> object:
    """Build one local-path scanner from the trusted base allowlist.

    The allowlist beside this trusted checker belongs to the base revision,
    so a pull request cannot approve its own entries (H2). No runtime
    identity is read in CI.
    """
    local_paths = checkers["check_local_paths"]
    trusted_root = Path(__file__).resolve().parent.parent
    return local_paths.build_scanner(
        local_paths.load_allowlist(trusted_root), local_paths.Identity())


def _local_path_violations(findings: list) -> list[str]:
    """Render local-path findings without revealing matched names."""
    return [finding.message(reveal=False) for finding in findings]


def _scan_tree(
    repo: Path, object_id: str, checkers: dict[str, ModuleType],
    local_scanner: object | None = None, exemptions: TreeExemptions | None = None,
) -> list[str]:
    """Scan all supported blobs and conflict markers in one exact object."""
    executable, safe_path = _resolve_git(repo)
    environment = _git_environment(safe_path)
    prefix = _git_prefix(executable, repo, environment)
    object_type = _run_git(
        prefix, ["cat-file", "-t", "--", object_id], repo,
        environment, MAX_GIT_DIAGNOSTIC)
    if object_type not in (b"commit\n", b"tree\n"):
        raise RuntimeError("--tree object must be a commit or tree")
    raw_entries = _run_git(
        prefix, ["ls-tree", "-r", "-z", "--full-tree", object_id],
        repo, environment, MAX_TREE_OUTPUT)
    entries = _parse_tree_entries(raw_entries)
    violations = []
    total_bytes = 0
    blobs = {}
    workflow_digests = {}
    for path, blob_id, mode in entries:
        content = blobs.get(blob_id)
        if content is None:
            content = _read_blob(prefix, repo, environment, path, blob_id)
            blobs[blob_id] = content
        if path.startswith(".github/workflows/") and mode != "120000":
            workflow_digests[path] = hashlib.sha256(content).hexdigest()
        total_bytes += len(content)
        if total_bytes > MAX_TOTAL_BLOB_BYTES:
            raise RuntimeError(
                f"tree content exceeds limit ({MAX_TOTAL_BLOB_BYTES} bytes)")
        text = _decode_blob(content)
        violations.extend(_scan_blob(path, text, mode, checkers, exemptions))
        if local_scanner is not None:
            violations.extend(_local_path_violations(
                local_scanner.scan_text(text, path)
                + local_scanner.scan_path(path)))
        if len(violations) >= MAX_VIOLATIONS:
            violations = violations[:MAX_VIOLATIONS]
            violations.append(
                f"reached violation limit ({MAX_VIOLATIONS}), stopping")
            return violations
    violations.extend(_pinned_digest_violations(exemptions, workflow_digests))
    violations.extend(_scan_conflicts(
        checkers["check_conflict_markers"], repo, object_id,
        executable, safe_path))
    return violations


def _scan_metadata(
    values: dict[str, str], repo: Path, checkers: dict[str, ModuleType],
    local_scanner: object | None = None,
) -> list[str]:
    """Run trusted commit and branch checks for workflow metadata."""
    author = values.get("--pr-author", "")
    violations = checkers["check_banned_agents"].find_violations([], author)
    bots = _load_trusted(BOT_REGISTRY_NAME)
    events = bots.LazyEventAuthor(os.environ.get("GITHUB_EVENT_PATH", ""))
    violations.extend(_branch_violations(
        values.get("--branch", ""), author, bots, events, checkers))
    if values.get("--base", ""):
        violations.extend(_scan_commit_range(values, repo, checkers, bots, events))
    if local_scanner is not None:
        violations.extend(_local_path_metadata(values, repo, checkers, local_scanner))
    if events.error is not None:
        violations.append(
            f"pull request event unreadable ({_sanitize(events.error)}); bot "
            "exemptions denied. Re-run the workflow; if it persists, report "
            "the runner event file.")
    return violations


def _has_pull_request_payload(event_path: str) -> bool:
    """Return whether the runner event file contains pull request data."""
    if not event_path:
        return False
    try:
        path = Path(event_path)
        if not path.is_file() or path.stat().st_size > MAX_EVENT_PAYLOAD_BYTES:
            return False
        payload = json.loads(path.read_text(encoding="utf-8"))
        return isinstance(payload, dict) and isinstance(payload.get("pull_request"), dict)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False


def _local_path_metadata(
    values: dict[str, str], repo: Path, checkers: dict[str, ModuleType],
    local_scanner: object,
) -> list[str]:
    """Scan the branch, range messages and patches, and PR text (H7)."""
    local_paths = checkers["check_local_paths"]
    findings = local_scanner.scan_path(values.get("--branch", ""))
    if values.get("--base", ""):
        base = _validate_object_id(values["--base"])
        log = local_paths._git_log(
            repo, ["--end-of-options", f"{base}..{values['--tree']}"])
        findings += local_paths.scan_log(local_scanner, log)
    event_path = os.environ.get("GITHUB_EVENT_PATH", "")
    if event_path and _has_pull_request_payload(event_path):
        findings += local_paths.scan_event(local_scanner, event_path)
    return _local_path_violations(findings)


def _branch_violations(
    branch: str, author: str, bots: ModuleType, events: object,
    checkers: dict[str, ModuleType],
) -> list[str]:
    """Apply branch naming unless a verified trusted bot owns the branch."""
    if not branch:
        return []
    branch_checker = checkers["check_branch_name"]
    bot = bots.match_exempt_bot(author, branch)
    if bot is None:
        return branch_checker.find_violations(branch)
    if bot.account_id is None:
        return []
    event = events.get()
    if event is not None and bots.event_verifies(bot, branch, event):
        return []
    violations = branch_checker.find_violations(branch)
    if event is not None:
        violations.append(
            f"{bot.login} account ID or head ref does not match the pull "
            "request event")
    elif events.error is None:
        violations.append(
            f"{bot.login} branch exemption needs the GitHub pull request "
            "event (ID verification); local runs cannot grant it")
    return violations


def _scan_commit_range(
    values: dict[str, str], repo: Path, checkers: dict[str, ModuleType],
    bots: ModuleType, events: object,
) -> list[str]:
    """Run trusted identity, attribution, and message checks on the range."""
    banned = checkers["check_banned_agents"]
    violations = []
    base = _validate_object_id(values["--base"])
    head = values["--tree"]
    commits = banned.load_commits(base, head, repo)
    violations.extend(banned.find_violations(commits))
    # _load_checkers raises for any missing CHECKER_NAMES module, so the
    # attribution checker is always present here.
    attribution = checkers["check_commit_attribution"]
    violations.extend(attribution.trailer_violations(commits))
    repository_name = os.environ.get("PR_BASE_REPOSITORY", "")
    if repository_name:
        try:
            violations.extend(
                attribution.github_identity_violations(
                    commits, repository_name, str(repo)
                )
            )
        except (OSError, RuntimeError, subprocess.SubprocessError, ValueError) as error:
            violations.append(
                "commit attribution lookup failed: " + _sanitize(error)
            )
    identity = checkers["check_git_identity"]
    identities = identity.log_identities(
        ["--end-of-options", f"{base}..{head}"], repo)
    violations.extend(identity.find_violations(identities))
    violations.extend(bots.bot_commit_violations(
        identities, values.get("--pr-author", ""), events))
    messages = checkers["check_commit_message"]
    commit_messages = messages.load_commit_messages(base, head, repo)
    warnings = messages.find_message_violations(commit_messages)
    for warning in warnings:
        print(_sanitize(warning))
    return violations


def _parse_cli(arguments: list[str]) -> tuple[dict[str, str], bool]:
    """Parse required tree inputs and reserved workflow metadata."""
    option_names = {
        "--repo", "--tree", "--base", "--branch", "--pr-author",
    }
    values = {}
    index = 0
    while index < len(arguments):
        option = arguments[index]
        if option in ("-h", "--help"):
            return {}, True
        if option not in option_names:
            raise RuntimeError(f"unknown argument: {_sanitize(option)}")
        if option in values:
            raise RuntimeError(f"{option} may be supplied only once")
        if index + 1 >= len(arguments):
            raise RuntimeError(f"{option} requires a value")
        values[option] = arguments[index + 1]
        index += OPTION_PAIR_WIDTH
    missing = [option for option in ("--repo", "--tree") if option not in values]
    if missing:
        raise RuntimeError(f"required argument missing: {', '.join(missing)}")
    return values, False


def _resolve_repo(raw_path: str) -> Path:
    """Resolve the candidate repository without reading candidate code."""
    try:
        repo = Path(raw_path).resolve(strict=True)
    except OSError as error:
        raise RuntimeError(f"could not resolve --repo: {_sanitize(error)}") from error
    if not repo.is_dir():
        raise RuntimeError("--repo must name a directory")
    return repo


def _usage() -> str:
    """Return the stable CLI usage text."""
    return (
        "usage: check_compliance_tree.py --repo PATH --tree OID "
        "[--base OID] [--branch VALUE] [--pr-author VALUE]"
    )


def _report(violations: list[str]) -> int:
    """Print sanitized violations and return the implied status."""
    for violation in violations:
        print(_sanitize(violation), file=sys.stderr)
    return 1 if violations else 0


def main() -> int:
    """Run the immutable compliance CLI and fail closed on every error."""
    try:
        values, help_requested = _parse_cli(sys.argv[1:])
        if help_requested:
            print(_usage())
            return 0
        repo = _resolve_repo(values["--repo"])
        object_id = _validate_object_id(values["--tree"])
        values["--tree"] = object_id
        checkers = _load_checkers()
        local_scanner = _local_path_scanner(checkers)
        exemptions = load_exemptions(_trusted_root())
        violations = _scan_tree(repo, object_id, checkers, local_scanner, exemptions)
        violations.extend(_scan_metadata(values, repo, checkers, local_scanner))
        return _report(violations)
    except (Exception, KeyboardInterrupt) as error:
        print(f"error: {_sanitize(error)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
