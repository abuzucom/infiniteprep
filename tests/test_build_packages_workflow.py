"""Behavioral checks for the manual package build workflow (D83a0)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

WORKFLOW_PATH = Path(__file__).resolve().parent.parent / ".github/workflows/build-packages.yml"
FULL_SHA_REFERENCE = re.compile(r"^[\w.-]+/[\w.-]+@[0-9a-f]{40}$")
PINNED_IMAGES = ["windows-2025", "macos-14", "ubuntu-22.04"]
RETENTION_DAYS = 3


@pytest.fixture(scope="module")
def workflow() -> dict[str, Any]:
    """Return the parsed workflow document."""
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _steps(workflow: dict[str, Any]) -> list[dict[str, Any]]:
    """Return every step of the bundle job."""
    return workflow["jobs"]["bundle"]["steps"]


def test_runs_only_when_started_by_hand(workflow: dict[str, Any]) -> None:
    """The only trigger is workflow_dispatch until the release stage adds tags (D248)."""
    assert list(workflow["on"]) == ["workflow_dispatch"]


def test_token_is_read_only(workflow: dict[str, Any]) -> None:
    """The workflow token can read the repository and nothing else."""
    assert workflow["permissions"] == {"contents": "read"}
    assert "permissions" not in workflow["jobs"]["bundle"]


def test_every_checkout_drops_credentials(workflow: dict[str, Any]) -> None:
    """Each checkout sets persist-credentials to false (Rule 11)."""
    checkouts = [
        step for step in _steps(workflow) if step.get("uses", "").startswith("actions/checkout@")
    ]
    assert checkouts
    assert all(step["with"]["persist-credentials"] is False for step in checkouts)


def test_every_action_is_pinned_to_a_full_sha(workflow: dict[str, Any]) -> None:
    """Every external action names a 40-character commit SHA."""
    references = [step["uses"] for step in _steps(workflow) if "uses" in step]
    assert references
    assert all(FULL_SHA_REFERENCE.fullmatch(reference) for reference in references)


def test_matrix_uses_the_pinned_images(workflow: dict[str, Any]) -> None:
    """The bundle job runs on the three pinned runner images (D251)."""
    job = workflow["jobs"]["bundle"]
    assert job["strategy"]["matrix"]["os"] == PINNED_IMAGES
    assert job["strategy"]["fail-fast"] is False
    assert job["runs-on"] == "${{ matrix.os }}"


def test_build_runs_the_spec_then_the_smoke_check(workflow: dict[str, Any]) -> None:
    """The spec build comes before the built app's smoke check."""
    runs = [step.get("run", "") for step in _steps(workflow)]
    build_index = next(i for i, run in enumerate(runs) if "pyinstaller" in run)
    smoke_index = next(i for i, run in enumerate(runs) if "--smoke-check" in run)
    assert "infiniteprep.spec" in runs[build_index]
    assert build_index < smoke_index


def test_upload_keeps_bundles_for_three_days(workflow: dict[str, Any]) -> None:
    """The bundle upload expires after 3 days and fails when the bundle is missing (D255)."""
    [upload] = [
        step
        for step in _steps(workflow)
        if step.get("uses", "").startswith("actions/upload-artifact@")
    ]
    assert upload["with"]["retention-days"] == RETENTION_DAYS
    assert upload["with"]["if-no-files-found"] == "error"
    assert upload["with"]["path"] == "dist/InfinitePrep"


def test_installs_no_unpinned_pip_upgrade(workflow: dict[str, Any]) -> None:
    """The install step uses the locked requirements and never upgrades pip unpinned."""
    runs = "\n".join(step.get("run", "") for step in _steps(workflow))
    assert "requirements-lock.txt" in runs
    assert "--upgrade pip" not in runs
