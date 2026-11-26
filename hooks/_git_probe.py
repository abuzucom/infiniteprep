"""Hardened read-only Git calls for the zone delete check.

Repository configuration can name commands Git runs, such as
core.fsmonitor. The probe disables that hook, keeps an allowlisted
environment, takes no optional locks, bounds time and output, and treats
every failure as unknown. Callers then fail closed.

The probe runs the Git that `scripts/trusted_git.py` resolves. That lookup
skips relative PATH entries, links, and programs inside the repository. A
repository cannot then supply a Git that hides tracked files.

This module uses only the standard library and imports no gate module.
"""
import functools
import importlib.util
import os
import subprocess
import sys
import threading
from pathlib import Path

GIT_TIMEOUT_SECONDS = 5
MAX_OUTPUT_BYTES = 256 * 1024
READ_CHUNK_BYTES = 64 * 1024
RESOLVER_PATH = Path(__file__).resolve().parent.parent / "scripts" / "trusted_git.py"
# Only these variables reach Git. GIT_OPTIONAL_LOCKS=0 keeps a read from
# contending for the index lock with another session's commit.
ENVIRONMENT_KEYS = ("PATH", "SYSTEMROOT", "HOME", "USERPROFILE", "TMPDIR", "TEMP", "TMP")
FIXED_ENVIRONMENT = {"GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0", "LC_ALL": "C"}


def _environment() -> dict:
    """Return the allowlisted environment for a probe call."""
    environment = {key: os.environ[key] for key in ENVIRONMENT_KEYS if key in os.environ}
    environment.update(FIXED_ENVIRONMENT)
    return environment


def _collect_output(process, result: list) -> None:
    """Read until end of output, at most one byte past the cap, and kill Git past it.

    A pipe read can return fewer bytes than requested. The loop keeps
    reading until an empty read marks the end of the output.
    """
    chunks = []
    total = 0
    try:
        while True:
            chunk = process.stdout.read(min(READ_CHUNK_BYTES, MAX_OUTPUT_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_OUTPUT_BYTES:
                process.kill()
                result.append(None)
                return
    except OSError:
        result.append(None)
        return
    result.append(b"".join(chunks))


def _run_bounded(command: list):
    """Run Git with bounded stdout, discarded stderr, and a supervised reader.

    Returns the stdout bytes, or None on a launch failure, timeout, nonzero
    exit, or output over the cap.
    """
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                   stderr=subprocess.DEVNULL, env=_environment())
    except OSError:
        return None
    output = []
    timed_out = False
    with process:
        reader = threading.Thread(target=_collect_output, args=(process, output))
        reader.start()
        try:
            process.wait(timeout=GIT_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            timed_out = True
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            reader.join()
    if timed_out or process.returncode != 0 or not output:
        return None
    return output[0]


@functools.lru_cache(maxsize=1)
def _load_resolver():
    """Load the trusted Git resolver from this repository's scripts folder."""
    spec = importlib.util.spec_from_file_location("_probe_trusted_git", RESOLVER_PATH)
    if spec is None or spec.loader is None:
        raise ImportError("trusted Git resolver is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _trusted_git(project_dir: str) -> str:
    """Return an absolute Git executable outside the project, or "" when none qualifies."""
    try:
        return _load_resolver().resolve_git(project_dir)
    except (OSError, ImportError, SyntaxError, AttributeError) as error:
        print(f"_git_probe: no trusted Git ({type(error).__name__})", file=sys.stderr)
        return ""


def tracked_paths(project_dir: str, paths: list):
    """Return the tracked files at or below the given repository-relative paths.

    Returns None when no trusted Git exists, or when Git fails, times out, or
    prints more than the cap.
    """
    git = _trusted_git(project_dir)
    if not git:
        return None
    command = [git, "-C", project_dir, "-c", "core.fsmonitor=false", "ls-files", "-z",
               "--", *paths]
    stdout = _run_bounded(command)
    if stdout is None:
        return None
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError:
        return None
    return frozenset(name for name in text.split("\0") if name)
