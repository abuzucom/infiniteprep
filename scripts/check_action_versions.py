#!/usr/bin/env python3
"""Resolve every workflow action pin to the upstream release it names."""

import base64
import re
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    from scripts.trusted_gh import run_gh
except ModuleNotFoundError:
    from trusted_gh import run_gh

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
USES_LINE = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)")
VERSION = re.compile(r"\d+\.\d+\.\d+")
REUSABLE_MARKER = "/.github/workflows/"
GH_TIMEOUT_SECONDS = 60


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
    parts = owner_repo.split("/")
    if len(parts) != 2:
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


def resolve_action_tags(pins: list[Pin]) -> dict[str, dict[str, list[str]]]:
    """Return the upstream tag names for each pinned commit of each repository."""
    resolved: dict[str, dict[str, list[str]]] = {}
    repositories = sorted({pin.repository for pin in pins if not pin.is_reusable_workflow})
    for repository in repositories:
        result = run_gh(
            Path.cwd(),
            [
                "api",
                f"repos/{repository}/tags",
                "--paginate",
                "--jq",
                '.[]|.name + " " + .commit.sha',
            ],
            timeout=GH_TIMEOUT_SECONDS,
        )
        tags: dict[str, list[str]] = {}
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                name, _, sha = line.partition(" ")
                if FULL_SHA.fullmatch(sha):
                    tags.setdefault(sha, []).append(name)
        resolved[repository] = tags
    return resolved


def resolve_reusable_release(pin: Pin) -> str:
    """Return the release the upstream changelog records at the pinned commit."""
    result = run_gh(
        Path.cwd(),
        [
            "api",
            f"repos/{pin.repository}/contents/CHANGELOG.md?ref={pin.sha}",
            "--jq",
            ".content",
        ],
        timeout=GH_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        return ""
    try:
        text = base64.b64decode("".join(result.stdout.split())).decode("utf-8", "replace")
    except (ValueError, UnicodeDecodeError):
        return ""
    for line in text.splitlines():
        match = re.match(r"^## \[(\d+\.\d+\.\d+)\]", line)
        if match:
            return match.group(1)
    return ""


def find_violations(
    pins: list[Pin],
    action_tags: dict[str, dict[str, list[str]]],
    reusable_releases: dict[tuple[str, str], str],
) -> list[str]:
    """Return one message for every pin that disagrees with its upstream release."""
    violations = []
    for pin in pins:
        claimed = pin.claimed_version
        if pin.is_reusable_workflow:
            upstream = reusable_releases.get((pin.repository, pin.sha), "")
            if not upstream:
                violations.append(f"{pin.path}: {pin.repository} names no release at {pin.sha}")
            elif claimed and claimed != upstream:
                violations.append(
                    f"{pin.path}: {pin.repository} names {claimed} but the pinned "
                    f"commit records {upstream}"
                )
            continue
        names = action_tags.get(pin.repository, {}).get(pin.sha, [])
        releases = sorted(
            name[1:] for name in names if name.startswith("v") and VERSION.fullmatch(name[1:])
        )
        if not releases:
            violations.append(f"{pin.path}: {pin.repository} pins no release at {pin.sha}")
            continue
        if claimed and claimed not in releases:
            violations.append(
                f"{pin.path}: {pin.repository} names {claimed} but the pinned "
                f"commit is {', '.join(releases)}"
            )
    return violations


def main() -> int:
    """Check every workflow pin against its upstream release."""
    root = Path(__file__).resolve().parent.parent
    workflows = root / ".github" / "workflows"
    pins = collect_repository_pins(workflows)
    if not pins:
        print("error: no action references found", file=sys.stderr)
        return 1
    action_tags = resolve_action_tags(pins)
    reusable_releases = {}
    for pin in pins:
        key = (pin.repository, pin.sha)
        if pin.is_reusable_workflow and key not in reusable_releases:
            reusable_releases[key] = resolve_reusable_release(pin)
    violations = find_violations(pins, action_tags, reusable_releases)
    for violation in violations:
        print(violation, file=sys.stderr)
    if violations:
        print("fix: pin the commit of a release tag and name that release", file=sys.stderr)
        return 1
    print(f"every action reference matches its upstream release ({len(pins)} references)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
