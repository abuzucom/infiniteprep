"""Behavioral checks for the artifact-hash-pinned application lockfile.

The application lockfile pins every package to an exact version and to the
SHA-256 digest of each artifact PyPI publishes for that release. The CI jobs
install the file in pip's hash-checking mode, so a substituted artifact fails
the install instead of reaching the build.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCKFILE = REPO_ROOT / "requirements-lock.txt"
SBOM_LOCKFILE = REPO_ROOT / "requirements-sbom.txt"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
BUNDLE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build-packages.yml"

SHA256_PATTERN = re.compile(r"^--hash=sha256:([0-9a-f]{64})$")
PIN_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;]+)")
UPGRADED_PACKAGE = "pyinstaller-hooks-contrib"
UPGRADED_VERSION = "2026.10"


class LockEntry:
    """One requirement entry of a lockfile with its digests."""

    def __init__(self, name: str, version: str, digests: list[str]) -> None:
        """Record the requirement and the digests that follow it."""
        self.name = name
        self.version = version
        self.digests = digests


def read_lock_entries(path: Path) -> list[LockEntry]:
    """Return the requirement entries of a lockfile.

    A hash line belongs to the requirement above it. A requirement ends at the
    next requirement or at the end of the file.
    """
    entries: list[LockEntry] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.endswith("\\"):
            line = line[:-1].strip()
        if not line or line.startswith("#"):
            continue
        hash_match = SHA256_PATTERN.match(line)
        if hash_match is not None:
            assert entries, "a hash line must follow a requirement"
            entries[-1].digests.append(hash_match.group(1))
            continue
        pin_match = PIN_PATTERN.match(line)
        assert pin_match is not None, f"unparsed lockfile line: {raw_line!r}"
        entries.append(LockEntry(pin_match.group(1), pin_match.group(2), []))
    return entries


def test_every_application_pin_carries_artifact_digests() -> None:
    """Each pinned package names the digests of the artifacts it may install."""
    entries = read_lock_entries(LOCKFILE)
    assert entries
    for entry in entries:
        assert entry.digests, f"{entry.name} pins no artifact digest"


def test_every_artifact_digest_is_a_sha256_hex_value() -> None:
    """Each digest names a SHA-256 value in lowercase hexadecimal."""
    for entry in read_lock_entries(LOCKFILE):
        for digest in entry.digests:
            assert len(digest) == 64
            assert digest == digest.lower()
            assert all(character in "0123456789abcdef" for character in digest)


def test_the_upgraded_package_pins_its_release_with_digests() -> None:
    """The upgraded application dependency names its release and its digests."""
    entries = {entry.name: entry for entry in read_lock_entries(LOCKFILE)}
    upgraded = entries[UPGRADED_PACKAGE]
    assert upgraded.version == UPGRADED_VERSION
    assert upgraded.digests


def test_the_two_lockfiles_lock_separate_environments() -> None:
    """A package that both lockfiles name carries the same version in both."""
    application = {entry.name: entry.version for entry in read_lock_entries(LOCKFILE)}
    sbom = {entry.name: entry.version for entry in read_lock_entries(SBOM_LOCKFILE)}
    assert application
    assert sbom
    assert not set(application) <= set(sbom)
    for name in set(application) & set(sbom):
        assert application[name] == sbom[name], f"{name} differs between the lockfiles"


def test_ci_installs_the_application_lock_in_hash_checking_mode() -> None:
    """The CI jobs verify the artifact digests of the application lockfile."""
    ci_text = CI_WORKFLOW.read_text(encoding="utf-8")
    assert "pip install --require-hashes -r requirements-lock.txt" in ci_text
    assert "pip install --require-hashes --constraint requirements-lock.txt" in ci_text


def test_the_bundle_job_installs_the_application_lock_in_hash_checking_mode() -> None:
    """The bundle job verifies the artifact digests of the application lockfile."""
    bundle_text = BUNDLE_WORKFLOW.read_text(encoding="utf-8")
    assert "pip install --require-hashes -r requirements-lock.txt" in bundle_text


def test_the_bundle_job_installs_the_sbom_lock_in_hash_checking_mode() -> None:
    """The bundle job verifies the artifact digests of the SBOM lockfile."""
    bundle = yaml.safe_load(BUNDLE_WORKFLOW.read_text(encoding="utf-8"))
    steps = bundle["jobs"]["bundle"]["steps"]
    sbom_step = next(step for step in steps if step.get("name") == "Generate Python SBOM")
    assert "--require-hashes" in sbom_step["run"]
    assert "requirements-sbom.txt" in sbom_step["run"]


def _wheel_for_release(directory: Path, name: str, version: str) -> Path:
    """Write a wheel of the given release into a directory and return its path."""
    wheel_name = f"{name}-{version}-py3-none-any.whl"
    wheel_path = directory / wheel_name
    dist_info = f"{name}-{version}.dist-info"
    with zipfile.ZipFile(wheel_path, "w") as archive:
        archive.writestr(f"{name}/__init__.py", "")
        archive.writestr(
            f"{dist_info}/METADATA",
            f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n",
        )
        archive.writestr(
            f"{dist_info}/WHEEL",
            "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
        )
        archive.writestr(f"{dist_info}/RECORD", "")
    return wheel_path


@pytest.mark.skipif(shutil.which(sys.executable) is None, reason="needs a Python interpreter")
def test_pip_rejects_an_artifact_whose_digest_is_not_committed(tmp_path: Path) -> None:
    """A substituted artifact fails the install under hash-checking mode.

    The check runs pip against a local wheel, so it needs no network access.
    pip selects the local artifact and refuses the download because no
    committed digest matches its bytes.
    """
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    _wheel_for_release(wheels, "sample", "1.0")
    requirement = tmp_path / "requirement.txt"
    requirement.write_text(f"sample==1.0 \\\n    --hash=sha256:{'0' * 64}\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(wheels),
            "--target",
            str(tmp_path / "target"),
            "--require-hashes",
            "-r",
            str(requirement),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode != 0
    assert "hash" in (result.stdout + result.stderr).lower()


def test_pip_accepts_an_artifact_whose_digest_is_committed(tmp_path: Path) -> None:
    """The committed digest lets the matching artifact install."""
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    wheel = _wheel_for_release(wheels, "sample", "1.0")
    digest = hashlib.sha256(wheel.read_bytes()).hexdigest()
    requirement = tmp_path / "requirement.txt"
    requirement.write_text(f"sample==1.0 \\\n    --hash=sha256:{digest}\n", encoding="utf-8")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(wheels),
            "--target",
            str(tmp_path / "target"),
            "--require-hashes",
            "-r",
            str(requirement),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
