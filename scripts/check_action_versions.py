#!/usr/bin/env python3
"""Resolve every workflow action pin to the upstream release it names."""

import base64
import re
import subprocess
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

try:
    from scripts.trusted_gh import run_gh
except ModuleNotFoundError:
    from trusted_gh import run_gh

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
USES_LINE = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)")
VERSION = re.compile(r"\d+\.\d+\.\d+")
CHANGELOG_RELEASE = re.compile(r"^## \[(\d+\.\d+\.\d+)\]")
REUSABLE_MARKER = "/.github/workflows/"
GH_TIMEOUT_SECONDS = 60
OWNER_REPO_PART_COUNT = 2
# The GitHub API returns at most 100 tags per page. The default page holds 30.
TAG_PAGE_SIZE = 100
ANNOTATED_TAG_TYPE = "tag"
UNREADABLE_TEXT = "could not read"


@dataclass(frozen=True)
class Pin:
    """One external action reference found in a workflow file."""

    path: str
    owner: str
    repo: str
    sha: str
    workflow: str
    comment: str

    @property
    def repository(self) -> str:
        """Return the owner and repository name that hold the reference."""
        return f"{self.owner}/{self.repo}"

    @property
    def is_reusable_workflow(self) -> bool:
        """Return whether the reference names a reusable workflow."""
        return bool(self.workflow)

    @property
    def claimed_version(self) -> str:
        """Return the version the comment beside the reference names."""
        match = VERSION.search(self.comment)
        return match.group(0) if match else ""

    @property
    def changelog_label(self) -> str:
        """Return the key that names the upstream changelog at the pinned commit."""
        return f"{self.repository}@{self.sha}"


@dataclass(frozen=True)
class GhReply:
    """The output of one gh call, or the reason the call failed."""

    stdout: str = ""
    failure: str = ""


@dataclass
class PinSources:
    """Everything the checker read upstream, plus each source it could not read."""

    action_tags: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    reusable_releases: dict[tuple[str, str], str] = field(default_factory=dict)
    unreadable: dict[str, str] = field(default_factory=dict)


def parse_reference(reference: str) -> tuple[str, str, str] | None:
    """Return the owner, repository, and workflow path of one reference."""
    owner_repo, separator, sha = reference.partition("@")
    if not separator or not FULL_SHA.fullmatch(sha):
        return None
    owner_repo = owner_repo.split(":", 1)[0]
    workflow = ""
    if REUSABLE_MARKER in owner_repo:
        owner_repo, _, remainder = owner_repo.partition(REUSABLE_MARKER)
        workflow = REUSABLE_MARKER + remainder
    # An action may live in a repository subfolder, as in owner/repo/sub/path.
    # Its pin still names a commit of owner/repo, so the tags of that repository apply.
    parts = owner_repo.split("/")
    if len(parts) < OWNER_REPO_PART_COUNT or not all(parts):
        return None
    return parts[0], parts[1], workflow


def collect_pins(path: Path) -> list[Pin]:
    """Return every external action reference in one workflow file."""
    pins = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = USES_LINE.match(line)
        if match is None:
            continue
        reference = match.group(1)
        if reference.startswith("./"):
            continue
        parsed = parse_reference(reference)
        if parsed is None:
            continue
        owner, repo, workflow = parsed
        comment = line.split("#", 1)[1].strip() if "#" in line else ""
        pins.append(
            Pin(
                path=path.name,
                owner=owner,
                repo=repo,
                sha=reference.partition("@")[2],
                workflow=workflow,
                comment=comment,
            )
        )
    return pins


def collect_repository_pins(workflows: Path) -> list[Pin]:
    """Return every external action reference in the workflow directory."""
    pins = []
    for path in sorted(workflows.glob("*.yml")) + sorted(workflows.glob("*.yaml")):
        pins.extend(collect_pins(path))
    return pins


def call_gh(arguments: list[str]) -> GhReply:
    """Run one gh call and return its output, or the reason it failed.

    A timeout, a gh that cannot start, or a nonzero exit all become a failure
    reason. The caller reports that reason instead of a missing release.
    """
    try:
        result = run_gh(Path.cwd(), arguments, timeout=GH_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return GhReply(failure=f"gh timed out after {GH_TIMEOUT_SECONDS} s")
    except (OSError, ValueError) as error:
        return GhReply(failure=f"gh could not run: {error}")
    if result.returncode != 0:
        stderr_lines = result.stderr.strip().splitlines()
        reason = stderr_lines[0] if stderr_lines else f"gh exited {result.returncode}"
        return GhReply(failure=reason)
    return GhReply(stdout=result.stdout)


def parse_tag_lines(text: str) -> dict[str, list[str]]:
    """Return the tag names of each commit in "name sha" listing lines."""
    tags: dict[str, list[str]] = {}
    for line in text.splitlines():
        name, _, sha = line.partition(" ")
        if FULL_SHA.fullmatch(sha):
            tags.setdefault(sha, []).append(name)
    return tags


def parse_changelog_release(text: str) -> str:
    """Return the first release heading of a changelog, or an empty string."""
    for line in text.splitlines():
        match = CHANGELOG_RELEASE.match(line)
        if match:
            return match.group(1)
    return ""


def lookup_claimed_commit(repository: str, version: str) -> str:
    """Return the commit of tag v<version>, or an empty string when it cannot be read.

    An annotated tag points at a tag object, which names the commit in turn.
    """
    reply = call_gh(
        [
            "api",
            f"repos/{repository}/git/ref/tags/v{version}",
            "--jq",
            '.object.type + " " + .object.sha',
        ]
    )
    if reply.failure:
        return ""
    kind, _, sha = reply.stdout.strip().partition(" ")
    if kind == ANNOTATED_TAG_TYPE:
        tag_reply = call_gh(["api", f"repos/{repository}/git/tags/{sha}", "--jq", ".object.sha"])
        sha = "" if tag_reply.failure else tag_reply.stdout.strip()
    return sha if FULL_SHA.fullmatch(sha) else ""


def list_repository_tags(repository: str) -> GhReply:
    """Return every "name sha" tag line of one repository, a full page at a time."""
    return call_gh(
        [
            "api",
            f"repos/{repository}/tags?per_page={TAG_PAGE_SIZE}",
            "--paginate",
            "--jq",
            '.[]|.name + " " + .commit.sha',
        ]
    )


def resolve_repository_tags(
    repository: str, pins: list[Pin], unreadable: dict[str, str]
) -> dict[str, list[str]]:
    """Return the release tags of each pinned commit of one repository.

    A pin whose claimed tag names its commit needs one ref lookup. Only the other
    pins trigger the full tag listing. A failed listing goes into unreadable.
    """
    tags: dict[str, list[str]] = {}
    claimed_commits: dict[str, str] = {}
    unresolved = []
    for pin in pins:
        claimed = pin.claimed_version
        if claimed and claimed not in claimed_commits:
            claimed_commits[claimed] = lookup_claimed_commit(repository, claimed)
        if claimed and claimed_commits[claimed] == pin.sha:
            tags.setdefault(pin.sha, []).append(f"v{claimed}")
        else:
            unresolved.append(pin)
    if not unresolved:
        return tags
    reply = list_repository_tags(repository)
    if reply.failure:
        unreadable[repository] = reply.failure
        return tags
    for sha, names in parse_tag_lines(reply.stdout).items():
        tags.setdefault(sha, []).extend(names)
    return tags


def fetch_reusable_release(pin: Pin) -> GhReply:
    """Return the release the upstream changelog records at the pinned commit.

    The reply holds the release in stdout, or the reason the changelog was unreadable.
    """
    reply = call_gh(
        [
            "api",
            f"repos/{pin.repository}/contents/CHANGELOG.md?ref={pin.sha}",
            "--jq",
            ".content",
        ]
    )
    if reply.failure:
        return reply
    try:
        # binascii.Error, raised for bad base64, is a subclass of ValueError.
        text = base64.b64decode("".join(reply.stdout.split())).decode("utf-8", "replace")
    except ValueError:
        return GhReply(failure="the changelog content is not valid base64")
    return GhReply(stdout=parse_changelog_release(text))


def resolve_pin_sources(pins: list[Pin]) -> PinSources:
    """Read the upstream tags and changelogs that every pin needs, once each."""
    sources = PinSources()
    action_pins: dict[str, list[Pin]] = {}
    for pin in pins:
        if not pin.is_reusable_workflow:
            action_pins.setdefault(pin.repository, []).append(pin)
    for repository in sorted(action_pins):
        sources.action_tags[repository] = resolve_repository_tags(
            repository, action_pins[repository], sources.unreadable
        )
    for pin in pins:
        key = (pin.repository, pin.sha)
        if not pin.is_reusable_workflow or key in sources.reusable_releases:
            continue
        reply = fetch_reusable_release(pin)
        sources.reusable_releases[key] = reply.stdout
        if reply.failure:
            sources.unreadable[pin.changelog_label] = reply.failure
    return sources


def resolve_action_tags(pins: list[Pin]) -> dict[str, dict[str, list[str]]]:
    """Return the upstream tag names for each pinned commit of each repository."""
    action_pins = [pin for pin in pins if not pin.is_reusable_workflow]
    return resolve_pin_sources(action_pins).action_tags


def resolve_reusable_release(pin: Pin) -> str:
    """Return the release the upstream changelog records at the pinned commit."""
    return fetch_reusable_release(pin).stdout


def reusable_violation(pin: Pin, upstream: str, unreadable: Mapping[str, str]) -> str:
    """Return the message for one reusable workflow pin, or an empty string."""
    claimed = pin.claimed_version
    if not upstream:
        failure = unreadable.get(pin.changelog_label, "")
        if failure:
            source = f"the changelog of {pin.changelog_label}"
            return f"{pin.path}: {UNREADABLE_TEXT} {source}: {failure}"
        return f"{pin.path}: {pin.repository} names no release at {pin.sha}"
    if claimed and claimed != upstream:
        return (
            f"{pin.path}: {pin.repository} names {claimed} but the pinned commit records {upstream}"
        )
    return ""


def action_violation(pin: Pin, names: list[str], unreadable: Mapping[str, str]) -> str:
    """Return the message for one action pin, or an empty string."""
    releases = sorted(
        name[1:] for name in names if name.startswith("v") and VERSION.fullmatch(name[1:])
    )
    if not releases:
        failure = unreadable.get(pin.repository, "")
        if failure:
            return f"{pin.path}: {UNREADABLE_TEXT} the tags of {pin.repository}: {failure}"
        return f"{pin.path}: {pin.repository} pins no release at {pin.sha}"
    claimed = pin.claimed_version
    if claimed and claimed not in releases:
        return (
            f"{pin.path}: {pin.repository} names {claimed} but the pinned "
            f"commit is {', '.join(releases)}"
        )
    return ""


def find_violations(
    pins: list[Pin],
    action_tags: dict[str, dict[str, list[str]]],
    reusable_releases: dict[tuple[str, str], str],
    unreadable: Mapping[str, str] | None = None,
) -> list[str]:
    """Return one message for every pin that disagrees with its upstream release.

    A pin whose upstream source could not be read gets a message that names the
    failed fetch. That failure never reads as a missing release.
    """
    failures = unreadable or {}
    violations = []
    for pin in pins:
        if pin.is_reusable_workflow:
            upstream = reusable_releases.get((pin.repository, pin.sha), "")
            message = reusable_violation(pin, upstream, failures)
        else:
            names = action_tags.get(pin.repository, {}).get(pin.sha, [])
            message = action_violation(pin, names, failures)
        if message:
            violations.append(message)
    return violations


def main() -> int:
    """Check every workflow pin against its upstream release."""
    root = Path(__file__).resolve().parent.parent
    workflows = root / ".github" / "workflows"
    pins = collect_repository_pins(workflows)
    if not pins:
        print("error: no action references found", file=sys.stderr)
        return 1
    sources = resolve_pin_sources(pins)
    violations = find_violations(
        pins, sources.action_tags, sources.reusable_releases, unreadable=sources.unreadable
    )
    for violation in violations:
        print(violation, file=sys.stderr)
    if sources.unreadable:
        print("fix: check the gh login and the network, then rerun", file=sys.stderr)
    # A fetch failure says nothing about the pin itself, so only real pin findings
    # get the pinning advice.
    if any(f": {UNREADABLE_TEXT} " not in violation for violation in violations):
        print("fix: pin the commit of a release tag and name that release", file=sys.stderr)
    if violations:
        return 1
    print(f"every action reference matches its upstream release ({len(pins)} references)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
