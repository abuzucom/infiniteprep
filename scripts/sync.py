#!/usr/bin/env python3
"""Sync AGENTS.md to tool-specific copies, and watch the shared gate files.

--check verifies the AGENTS.md copies without writing.
--check-shared verifies the files that must stay byte-identical with the
other repositories adopting these gates, against SHARED_MANIFEST.
--write-shared rewrites that manifest from what is on disk.

sync.py covers the AGENTS.md family by copying. It cannot copy the gate
files. Those files live in repositories it cannot see, so it compares hashes
against a manifest committed in each of them instead. A file that differs
is either a fix one repository has and the other does not, or drift that
belongs in the drift record. Both need somebody to look.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

SOURCE = "AGENTS.md"
SKILL_SOURCE_DIRECTORY = "skills"
SHARED_MANIFEST = "shared-files.json"
LOCAL_POLICY_START = "<!-- local policy:start -->"
LOCAL_POLICY_END = "<!-- local policy:end -->"
SOURCE_ORIENTATION_START = "<!-- source orientation:start -->"
SOURCE_ORIENTATION_END = "<!-- source orientation:end -->"
# Existing callers import these names. They now hold the new markers.
REPOSITORY_ONLY_START = LOCAL_POLICY_START
REPOSITORY_ONLY_END = LOCAL_POLICY_END
SOURCE_ONLY_START = SOURCE_ORIENTATION_START
SOURCE_ONLY_END = SOURCE_ORIENTATION_END
# Adopter policy files still carry the older marker spellings. Adoption
# accepts each one as its replacement and prints a rename notice.
LEGACY_MARKERS = {
    "<!-- repository-only:start -->": LOCAL_POLICY_START,
    "<!-- repository-only:end -->": LOCAL_POLICY_END,
    "<!-- source-only:start -->": SOURCE_ORIENTATION_START,
    "<!-- source-only:end -->": SOURCE_ORIENTATION_END,
}
MAX_POLICY_BYTES = 64 * 1024
MANIFEST_JSON_INDENT = 2
# Files that must be byte-identical wherever these gates are installed. A
# decision reached by one gate and not the other is the failure the whole
# design exists to prevent, so the files carrying decisions are listed here
# and their hashes are committed in every repository holding them.
SHARED_FILES = [
    "hooks/github-command-denylist.txt",
    "hooks/_bash_parser.py",
    "hooks/_cmd_parser.py",
    "hooks/_gate_core.py",
    "hooks/_platform_policy.py",
    "hooks/block_destructive_bash.py",
    "hooks/block_destructive_cmd.py",
    "hooks/block_destructive_powershell.py",
    "hooks/enforce_anti_delegation.py",
    "hooks/enforce_gate_adoption.py",
    "hooks/reinject_agents_policy.py",
    "hooks/require_consent.py",
    "hooks/_trust.py",
    "hooks/enforce_pr_base.py",
    "hooks/deny_mcp_github_writes.py",
    "hooks/enforce_single_question.py",
    "hooks/detect_prose_options.py",
    "hooks/_test_runners.py",
    "hooks/_git_probe.py",
    "hooks/_zones.py",
    "hooks/prepare_session_workspace.py",
    "scripts/delete_zone_path.py",
    "scripts/check_gate_adoption.py",
    "scripts/complete_gate_adoption.py",
    "scripts/check_gate_pr_integrity.py",
    "scripts/check_hook_launchers.py",
    "scripts/check_local_paths.py",
    "tests/gate_corpus.py",
    "tests/json_line_worker.py",
    "tests/json_line_worker_child.py",
    "tests/test_block_destructive_bash.py",
    "tests/test_block_destructive_cmd.py",
    "tests/test_block_destructive_powershell.py",
    "tests/test_check_gate_adoption.py",
    "tests/test_complete_gate_adoption.py",
    "tests/test_check_hook_launchers.py",
    "tests/test_check_local_paths.py",
    "tests/test_check_gate_pr_integrity.py",
    "tests/test_enforce_gate_adoption.py",
    "tests/test_gate_adoption_transaction.py",
    "tests/test_platform_policy.py",
    "docs/enforcement-matrix.md",
    "scripts/_skill_sync.py",
    "scripts/check_forbidden_paths.py",
    "scripts/check_handoff_content.py",
    "scripts/check_shared_neutrality.py",
    "skills/commit-and-draft-pr/SKILL.md",
    "skills/compaction-replan/SKILL.md",
    "skills/dependency-proposal/SKILL.md",
    "skills/destructive-act-authorization/SKILL.md",
    "skills/gate-adoption/SKILL.md",
    "skills/gate-denial-report/SKILL.md",
    "skills/git-identity-recovery/SKILL.md",
    "skills/policy-change-proposal/SKILL.md",
    "skills/pr-steward/SKILL.md",
    "skills/session-branch-recovery/SKILL.md",
    "skills/session-handoff/SKILL.md",
    "skills/interactive-decisions/SKILL.md",
    "skills/test-defect-report/SKILL.md",
    "tests/test_check_forbidden_paths.py",
    "tests/test_check_handoff_content.py",
    "tests/test_check_shared_neutrality.py",
    "tests/test_skill_adoption_required.py",
    "tests/test_skill_command_forms.py",
    "tests/test_skill_module_not_executed.py",
    "tests/test_trusted_test_runners.py",
    "tests/test_primary_pr_base.py",
    "tests/test_more_test_runners.py",
    "tests/test_delete_zone_path.py",
    "tests/test_deletion_zones.py",
    "tests/test_gemini_command_quoting.py",
    "tests/test_deny_mcp_github_writes.py",
    "tests/test_enforce_single_question.py",
    "tests/test_detect_prose_options.py",
    "tests/test_question_hook_wiring.py",
    "tests/test_git_probe.py",
    "tests/test_prepare_session_workspace.py",
    "tests/test_worktree_zone_paths.py",
    "tests/test_skill_path_protection.py",
    "tests/test_sync_skill_copy.py",
]
COPIES = [
    "CLAUDE.md",
    "GEMINI.md",
    "CONVENTIONS.md",
    ".cursorrules",
    ".clinerules",
    ".windsurfrules",
    ".copilot-instructions",
    ".github/copilot-instructions.md",
]
ADOPTER_POLICY_FILES = (
    "docs/agent-policy/adoption.md",
    "docs/agent-policy/enforcement.md",
    "docs/agent-policy/clients.md",
    "docs/agent-policy/github.md",
    "docs/agent-policy/security.md",
    "docs/project-orientation.md",
)
SOURCE_ONLY_POLICY_FILES = ("docs/agent-policy/source-orientation.md",)
SUPPORTING_POLICY_FILES = ADOPTER_POLICY_FILES
# The digest index maps each released version to the digests of its policy
# and shared gate files. Only the source repository holds one. Adopters copy
# this script without it, so every index step skips when the file is absent.
DIGEST_INDEX = "policy-digests.json"
CHANGELOG = "CHANGELOG.md"
POLICY_DIRECTORY = "docs/agent-policy"
# Every adopter rewrites the orientation file, so its digest never matches.
UNINDEXED_POLICY_FILES = frozenset(
    ("docs/project-orientation.md",) + SOURCE_ONLY_POLICY_FILES)
VERSION_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\]")
DIGEST_PATTERN = re.compile(r"^[0-9a-f]{64}$")
REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
GIT_TIMEOUT_SECONDS = 60


def policy_bytes(root: Path) -> bytes:
    """Return canonical policy plus available supporting documents."""
    assembled = assemble_policy_bytes(root)
    if len(assembled) > MAX_POLICY_BYTES:
        raise ValueError("assembled policy exceeds the policy size limit")
    return assembled


def assemble_policy_bytes(root: Path) -> bytes:
    """Return the assembled policy without the combined size limit.

    scripts/check_policy_size.py reports the assembled size. The report
    needs the total even when that total exceeds the limit.
    """
    return b"".join(content for _, content in read_assembled_policy_parts(root))


def _ensure_final_newline(content: bytes) -> bytes:
    """Return content ending in exactly the newline assembly appends."""
    return content + (b"\n" if not content.endswith(b"\n") else b"")


def read_assembled_policy_parts(root: Path) -> list[tuple[str, bytes]]:
    """Return each assembled policy file name with its assembled bytes.

    The parts join to the assembled policy. scripts/check_policy_size.py
    ranks the parts by size.
    """
    source, _ = _inspect_source(root)
    if source.stat().st_size > MAX_POLICY_BYTES:
        raise ValueError("AGENTS.md exceeds the policy size limit")
    parts = [(SOURCE, _ensure_final_newline(source.read_bytes()))]
    root_resolved = root.resolve()
    for relative_name in ADOPTER_POLICY_FILES + SOURCE_ONLY_POLICY_FILES:
        path = _lexical_path(root, relative_name)
        if not path.exists():
            if relative_name in SOURCE_ONLY_POLICY_FILES:
                continue
            raise ValueError(f"{relative_name} is missing")
        details = path.lstat()
        if not stat.S_ISREG(details.st_mode):
            raise ValueError("supporting policy is not a regular file")
        if details.st_size > MAX_POLICY_BYTES:
            raise ValueError("supporting policy exceeds the policy size limit")
        if not path.resolve().is_relative_to(root_resolved):
            raise ValueError("supporting policy escapes the repository")
        content = path.read_bytes()
        content.decode("ascii")
        parts.append((relative_name, _ensure_final_newline(content)))
    return parts


def _replace_legacy_markers(content: str) -> str:
    """Return content with older marker spellings replaced by the new markers."""
    found = [legacy for legacy in LEGACY_MARKERS if legacy in content]
    if not found:
        return content
    for legacy in found:
        content = content.replace(legacy, LEGACY_MARKERS[legacy])
    renames = "; ".join(f"{legacy} -> {LEGACY_MARKERS[legacy]}" for legacy in found)
    print(f"notice: rename older policy markers: {renames}", file=sys.stderr)
    return content


def adoptable_content(content: str) -> str:
    """Return policy content without source repository orientation."""
    content = _replace_legacy_markers(content)
    if content.count(LOCAL_POLICY_START) != 1:
        raise ValueError("local policy start marker must occur once")
    if content.count(LOCAL_POLICY_END) != 1:
        raise ValueError("local policy end marker must occur once")
    prefix, remainder = content.split(LOCAL_POLICY_START, 1)
    _, suffix = remainder.split(LOCAL_POLICY_END, 1)
    if prefix.endswith("\n") and suffix.startswith("\n"):
        suffix = suffix[1:]
    content = prefix + suffix
    if content.count(SOURCE_ORIENTATION_START) != content.count(SOURCE_ORIENTATION_END):
        raise ValueError("source orientation markers must be balanced")
    while SOURCE_ORIENTATION_START in content:
        source_prefix, remainder = content.split(SOURCE_ORIENTATION_START, 1)
        _, source_suffix = remainder.split(SOURCE_ORIENTATION_END, 1)
        content = source_prefix + source_suffix
    return content


def print_adoptable(root: Path) -> int:
    """Print adoptable policy content without changing files."""
    try:
        source, _ = _inspect_source(root)
        content = policy_bytes(source.parent).decode("ascii")
        print(adoptable_content(content), end="")
    except (OSError, UnicodeDecodeError, ValueError):
        print("error: adoptable policy generation failed", file=sys.stderr)
        return 1
    return 0


def _is_within(root: Path, path: Path) -> bool:
    """Return whether path is root or one of its descendants."""
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _lexical_path(root: Path, name: str) -> Path:
    """Return a normalized path that remains lexically beneath root."""
    normalized_root = Path(os.path.abspath(root))
    path = Path(os.path.abspath(root / name))
    if not _is_within(normalized_root, path):
        raise ValueError("path escapes root")
    return path


def _require_resolved_within(root: Path, path: Path) -> None:
    """Reject paths with existing components that resolve outside root."""
    resolved_root = root.resolve(strict=True)
    resolved_path = path.resolve(strict=False)
    if not _is_within(resolved_root, resolved_path):
        raise ValueError("resolved path escapes root")


def _directory_state(path: Path) -> tuple:
    """Return a stable identity for a real directory found with lstat."""
    details = path.lstat()
    if not stat.S_ISDIR(details.st_mode):
        raise ValueError("directory component is not a real directory")
    return details.st_dev, details.st_ino


def _regular_state(path: Path) -> tuple:
    """Return state for a regular, non-symlink file found with lstat."""
    details = path.lstat()
    if not stat.S_ISREG(details.st_mode):
        raise ValueError("path is not a regular file")
    return (
        details.st_dev,
        details.st_ino,
        details.st_size,
        details.st_mtime_ns,
    )


def _target_state(path: Path) -> tuple | None:
    """Return target state without following a link, or None if absent."""
    try:
        return _regular_state(path)
    except FileNotFoundError:
        return None


def _validate_parent_tree(root: Path, parent: Path, create: bool) -> None:
    """Validate each parent with lstat, creating absent directories safely."""
    current = root
    _directory_state(current)
    for part in parent.relative_to(root).parts:
        current = current / part
        try:
            _directory_state(current)
        except FileNotFoundError:
            if not create:
                return
            os.mkdir(current)
            _directory_state(current)
        _require_resolved_within(root, current)


def _inspect_source(root: Path) -> tuple:
    """Validate the source and return its path and current state."""
    source = _lexical_path(root, SOURCE)
    _validate_parent_tree(root, source.parent, create=False)
    source_state = _regular_state(source)
    _require_resolved_within(root, source)
    return source, source_state


def _inspect_target(root: Path, name: str, create: bool) -> tuple:
    """Validate one destination and return its path and current state."""
    target = _lexical_path(root, name)
    _validate_parent_tree(root, target.parent, create=create)
    target_state = _target_state(target)
    _require_resolved_within(root, target)
    return target, target_state


def files_match(source: Path, target: Path) -> bool:
    """Compare regular file contents, normalizing line endings."""
    try:
        _regular_state(source)
        if _target_state(target) is None:
            return False
        expected = policy_bytes(source.parent)
        actual = target.read_bytes().replace(b"\r\n", b"\n")
        return expected.replace(b"\r\n", b"\n") == actual
    except (OSError, RuntimeError, UnicodeDecodeError, ValueError):
        return False


def _copy_to_temp(source: Path, parent: Path) -> Path:
    """Copy source into a flushed temporary file beside the destination."""
    descriptor, temp_name = tempfile.mkstemp(prefix=".sync-", dir=parent)
    temp_path = Path(temp_name)
    complete = False
    try:
        os.close(descriptor)
        shutil.copyfile(source, temp_path)
        assembled = policy_bytes(source.parent)
        if assembled != source.read_bytes():
            temp_path.write_bytes(assembled)
        with temp_path.open("ab") as temp_file:
            temp_file.flush()
            os.fsync(temp_file.fileno())
        temp_path.chmod(stat.S_IMODE(source.lstat().st_mode))
        complete = True
    finally:
        if not complete:
            temp_path.unlink(missing_ok=True)
    return temp_path


def _revalidate_destination(
        root: Path, target: Path, target_state: tuple | None,
        parent_state: tuple) -> None:
    """Reject destination changes made while the temporary copy was written."""
    if _target_state(target) != target_state:
        raise OSError("target changed during copy")
    _require_resolved_within(root, target)
    _validate_parent_tree(root, target.parent, create=False)
    if _directory_state(target.parent) != parent_state:
        raise OSError("target parent changed during copy")


def _atomic_copy(root: Path, source: Path, target: Path) -> None:
    """Copy source to target without exposing a partial destination."""
    source_state = _inspect_source(root)[1]
    target_state = _inspect_target(
        root, str(target.relative_to(root)), create=True)[1]
    parent = target.parent
    parent_state = _directory_state(parent)
    temp_path = _copy_to_temp(source, parent)
    try:
        if _regular_state(source) != source_state:
            raise OSError("source changed during copy")
        _revalidate_destination(root, target, target_state, parent_state)
        os.replace(temp_path, target)
    finally:
        temp_path.unlink(missing_ok=True)


def sync_copies(check_only: bool) -> int:
    """Copy SOURCE over each target, or with --check report stale targets.

    Returns a process exit code: 0 on success, 1 if a check fails.
    """
    targets = []
    stale = []
    try:
        root = Path(__file__).resolve().parent.parent
        source, _ = _inspect_source(root)
        for name in COPIES:
            target, _ = _inspect_target(root, name, create=False)
            targets.append((name, target))
            if not files_match(source, target):
                stale.append(name)
    except (OSError, RuntimeError, ValueError):
        print("error: unsafe or inaccessible sync path", file=sys.stderr)
        return 1

    if check_only:
        if stale:
            print(f"out of sync with {SOURCE}: {', '.join(stale)}", file=sys.stderr)
            print("run: make sync (or python scripts/sync.py)", file=sys.stderr)
            return 1
        print("all copies in sync")
        return 0

    try:
        for name, target in targets:
            if name not in stale:
                continue
            _atomic_copy(root, source, target)
            print(f"synced {name}")
    except (OSError, RuntimeError, ValueError):
        print("error: sync copy failed", file=sys.stderr)
        return 1
    if not stale:
        print("all copies already in sync")
    return 0


def content_digest(path: Path) -> str:
    """Return the file's SHA-256 with line endings normalized.

    Normalizing is what keeps a Windows checkout from reporting every
    shared file as drift. SHA-256 instead of a faster hash because this
    is an integrity comparison, not a cache key (Rule 7).
    """
    return text_digest(path.read_text(encoding="utf-8"))


def text_digest(text: str) -> str:
    """Return the SHA-256 of text with line endings normalized."""
    body = text.replace("\r\n", "\n")
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def read_manifest(root: Path) -> dict:
    """Return the shared-file manifest, or an empty one when absent."""
    manifest = root / SHARED_MANIFEST
    if not manifest.is_file():
        return {}
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except ValueError as error:
        print(f"error: {SHARED_MANIFEST} is not valid JSON ({error})",
              file=sys.stderr)
        return {}
    if not isinstance(data, dict) or not isinstance(data.get("shared", {}), dict):
        print(f"error: {SHARED_MANIFEST} must hold a JSON object whose "
              "\"shared\" entry is a JSON object. Recovery: run "
              "python scripts/sync.py --write-shared.", file=sys.stderr)
        return {}
    return data


def shared_problems(root: Path, recorded: dict) -> list:
    """Return one line per shared file that does not match the manifest."""
    problems = []
    for name in SHARED_FILES:
        path = root / name
        if not path.is_file():
            problems.append(f"{name}: listed as shared but not present here")
            continue
        if name not in recorded:
            problems.append(f"{name}: shared but absent from {SHARED_MANIFEST}")
            continue
        try:
            actual = content_digest(path)
        except (OSError, UnicodeDecodeError) as error:
            problems.append(f"{name}: cannot be read ({error})")
            continue
        if actual != recorded[name]:
            problems.append(f"{name}: differs from the recorded copy")
    return problems


def check_shared(root: Path) -> int:
    """Report shared files that no longer match the committed manifest."""
    manifest = read_manifest(root)
    if not manifest:
        print(f"error: {SHARED_MANIFEST} is missing or unreadable",
              file=sys.stderr)
        return 1
    problems = shared_problems(root, manifest.get("shared", {}))
    if not problems:
        print(f"all {len(SHARED_FILES)} shared files match {SHARED_MANIFEST}")
        return 0
    for line in problems:
        print(line, file=sys.stderr)
    print("Mirror the change into every repository holding these files, then "
          "run: python scripts/sync.py --write-shared in each. If the "
          "difference is intended, record it in the drift file and move the "
          "file out of SHARED_FILES.", file=sys.stderr)
    return 1


def write_shared(root: Path) -> int:
    """Rewrite the manifest from the shared files on disk."""
    recorded = {}
    for name in SHARED_FILES:
        path = root / name
        if not path.is_file():
            print(f"error: {name} is listed as shared but not present here",
                  file=sys.stderr)
            return 1
        recorded[name] = content_digest(path)
    body = {
        "note": ("SHA-256 of every file that must stay byte-identical across "
                 "the repositories adopting these gates. Line endings are "
                 "normalized before hashing. Regenerate with "
                 "scripts/sync.py --write-shared and commit the result in "
                 "every one of them."),
        "shared": recorded,
    }
    text = json.dumps(body, indent=MANIFEST_JSON_INDENT, sort_keys=True) + "\n"
    write_text_atomic(root / SHARED_MANIFEST, text)
    print(f"recorded {len(recorded)} shared files in {SHARED_MANIFEST}")
    return 0


def write_text_atomic(path: Path, text: str) -> None:
    """Replace path with text so a concurrent reader never sees a partial file.

    Concurrent runs over one tree compute identical bytes, so the last
    os.replace wins without a lock.
    """
    descriptor, temporary = tempfile.mkstemp(prefix=".sync-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


SKILL_SYNC_FUNCTIONS = ("skill_problems", "sync_skills")


def _load_skill_sync():
    """Import the skill module from beside this script, or return None.

    A module without both entry points counts as absent, so an incomplete
    copy fails closed with a named file and no traceback.
    """
    try:
        from scripts import _skill_sync as skill_sync
    except ImportError:
        try:
            import _skill_sync as skill_sync
        except ImportError:
            return None
    if not all(callable(getattr(skill_sync, name, None)) for name in SKILL_SYNC_FUNCTIONS):
        return None
    return skill_sync


def sync_skill_copies(root: Path, check_only: bool) -> int:
    """Check or write every skill client copy and return a process exit code.

    A tree without a skills folder has adopted no skills yet, so the step
    does nothing there. A skills folder without the module fails closed.
    """
    if not (root / SKILL_SOURCE_DIRECTORY).is_dir():
        return 0
    skill_sync = _load_skill_sync()
    if skill_sync is None:
        print("error: skills/ exists but scripts/_skill_sync.py is missing or incomplete. "
              "Copy it from the policy source.", file=sys.stderr)
        return 1
    if check_only:
        problems = skill_sync.skill_problems(root)
    else:
        problems = skill_sync.sync_skills(root)
    for problem in problems:
        print(f"error: {problem}", file=sys.stderr)
    return 1 if problems else 0


def read_release_version(changelog_text: str) -> str | None:
    """Return the newest released version named in changelog text."""
    for line in changelog_text.splitlines():
        match = VERSION_HEADING.match(line)
        if match:
            return match.group(1)
    return None


def read_digest_index(root: Path) -> dict | None:
    """Return the version map of the digest index, or None when unusable."""
    try:
        body = json.loads((root / DIGEST_INDEX).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        print(f"error: {DIGEST_INDEX} cannot be read ({error}). Restore it "
              "from version control.", file=sys.stderr)
        return None
    versions = body.get("versions") if isinstance(body, dict) else None
    if not isinstance(versions, dict):
        print(f"error: {DIGEST_INDEX} holds no version map. Restore it from "
              "version control.", file=sys.stderr)
        return None
    return versions


def write_digest_index(root: Path, versions: dict) -> None:
    """Write the version map to the digest index in a stable order."""
    body = {
        "note": ("SHA-256 of the policy and shared gate files of every "
                 "released version. Line endings are normalized before "
                 "hashing. scripts/sync.py records the current version. "
                 "scripts/sync.py --backfill-digests adds versions from Git "
                 "history."),
        "versions": versions,
    }
    (root / DIGEST_INDEX).write_text(
        json.dumps(body, indent=MANIFEST_JSON_INDENT, sort_keys=True) + "\n", encoding="utf-8")


def build_current_entry(root: Path) -> dict:
    """Return the digest entry describing the working tree."""
    policy_names = [SOURCE] + [
        name for name in ADOPTER_POLICY_FILES
        if name not in UNINDEXED_POLICY_FILES]
    return {
        "policy": {name: content_digest(root / name) for name in policy_names},
        "shared": {name: content_digest(root / name) for name in SHARED_FILES},
    }


def sync_digest_index(root: Path, check_only: bool) -> int:
    """Record the current version's digests, or with --check verify them."""
    if not (root / DIGEST_INDEX).is_file():
        return 0
    versions = read_digest_index(root)
    if versions is None:
        return 1
    try:
        changelog_text = (root / CHANGELOG).read_text(encoding="utf-8")
        entry = build_current_entry(root)
    except (OSError, UnicodeDecodeError) as error:
        print(f"error: {DIGEST_INDEX} inputs cannot be read ({error})",
              file=sys.stderr)
        return 1
    version = read_release_version(changelog_text)
    if version is None:
        print(f"error: {CHANGELOG} names no released version for "
              f"{DIGEST_INDEX}. Add a version heading.", file=sys.stderr)
        return 1
    if versions.get(version) == entry:
        print(f"{DIGEST_INDEX} matches version {version}")
        return 0
    if check_only:
        print(f"{DIGEST_INDEX} does not match version {version}",
              file=sys.stderr)
        print("run: make sync (or python scripts/sync.py)", file=sys.stderr)
        return 1
    versions[version] = entry
    write_digest_index(root, versions)
    print(f"recorded version {version} in {DIGEST_INDEX}")
    return 0


def read_git_output(root: Path, *arguments: str) -> str | None:
    """Return the output of one Git command, or None when it fails."""
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments], capture_output=True,
            check=False, timeout=GIT_TIMEOUT_SECONDS)
        if result.returncode != 0:
            return None
        return result.stdout.decode("utf-8")
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError):
        return None


def list_revision_policy_files(root: Path, revision: str) -> list:
    """Return the indexed policy paths that exist at one revision."""
    listing = read_git_output(
        root, "ls-tree", "--name-only", revision, f"{POLICY_DIRECTORY}/")
    names = [SOURCE]
    for name in (listing or "").splitlines():
        if name.endswith(".md") and name not in UNINDEXED_POLICY_FILES:
            names.append(name)
    return names


def read_revision_shared(root: Path, revision: str) -> dict:
    """Return the shared digests one revision recorded in its manifest."""
    manifest_text = read_git_output(
        root, "show", f"{revision}:{SHARED_MANIFEST}")
    if manifest_text is None:
        return {}
    try:
        recorded = json.loads(manifest_text).get("shared", {})
    except (AttributeError, ValueError):
        return {}
    if not isinstance(recorded, dict):
        return {}
    return {name: digest for name, digest in recorded.items()
            if isinstance(digest, str) and DIGEST_PATTERN.match(digest)}


def build_revision_entry(root: Path, revision: str) -> dict:
    """Return the digest entry describing one committed revision."""
    policy = {}
    for name in list_revision_policy_files(root, revision):
        text = read_git_output(root, "show", f"{revision}:{name}")
        if text is not None:
            policy[name] = text_digest(text)
    return {"policy": policy, "shared": read_revision_shared(root, revision)}


def find_version_revisions(root: Path) -> dict | None:
    """Map each released version to the newest revision that names it."""
    history = read_git_output(root, "rev-list", "--first-parent", "HEAD")
    if history is None:
        return None
    revisions = {}
    for revision in history.splitlines():
        if not REVISION_PATTERN.match(revision):
            continue
        changelog_text = read_git_output(
            root, "show", f"{revision}:{CHANGELOG}")
        version = read_release_version(changelog_text or "")
        if version is not None and version not in revisions:
            revisions[version] = revision
    return revisions


def backfill_digest_index(root: Path) -> int:
    """Add an index entry for every released version found in Git history.

    Existing entries stay untouched, so a repeated run changes nothing.
    """
    if not (root / DIGEST_INDEX).is_file():
        print(f"error: {DIGEST_INDEX} is missing. Only the source repository "
              "holds a digest index.", file=sys.stderr)
        return 1
    versions = read_digest_index(root)
    if versions is None:
        return 1
    revisions = find_version_revisions(root)
    if revisions is None:
        print("error: Git history cannot be read. Run the backfill inside a "
              "complete clone.", file=sys.stderr)
        return 1
    missing = [version for version in revisions if version not in versions]
    for version in missing:
        versions[version] = build_revision_entry(root, revisions[version])
    if missing:
        write_digest_index(root, versions)
    print(f"added {len(missing)} versions to {DIGEST_INDEX}")
    return 0


def parse_arguments(argv: list) -> argparse.Namespace:
    """Parse at most one mode flag. Unknown or combined flags exit 2."""
    parser = argparse.ArgumentParser(
        prog="sync.py", allow_abbrev=False,
        description="Write policy copies, or check them with --check.")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--check", action="store_true",
                       help="report stale copies and digests without writing")
    modes.add_argument("--print-adoptable", action="store_true",
                       help="print the adoptable policy text")
    modes.add_argument("--check-shared", action="store_true",
                       help="compare shared files with the manifest")
    modes.add_argument("--write-shared", action="store_true",
                       help="record shared-file digests in the manifest")
    modes.add_argument("--backfill-digests", action="store_true",
                       help="add missing versions to the digest index")
    return parser.parse_args(argv)


def main(argv: list) -> int:
    """Dispatch on the mode flag and return a process exit code."""
    arguments = parse_arguments(argv)
    root = Path(__file__).resolve().parent.parent
    if arguments.print_adoptable:
        return print_adoptable(root)
    if arguments.check_shared:
        return check_shared(root)
    if arguments.write_shared:
        return write_shared(root)
    if arguments.backfill_digests:
        return backfill_digest_index(root)
    check_only = arguments.check
    copy_status = max(sync_copies(check_only), sync_skill_copies(root, check_only))
    # A check reports stale copies and a stale index together. A write stops
    # at a failed copy, so the index never records a half-synchronized tree.
    if copy_status != 0 and not check_only:
        return copy_status
    return max(copy_status, sync_digest_index(root, check_only))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
