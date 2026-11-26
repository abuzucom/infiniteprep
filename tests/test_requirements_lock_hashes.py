"""Behavioral checks for the artifact-hash-pinned application lockfile.

The application lockfile pins every package to an exact version and to the
SHA-256 digest of each artifact PyPI publishes for that release. The CI jobs
install the file in pip's hash-checking mode, so a substituted artifact fails
the install instead of reaching the build.

The file uses the unittest style of the other top-level tests. The policy
suite discovers the top-level test tree with unittest, and the OS probe jobs
install the checker requirements alone.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
LOCKFILE = REPO_ROOT / "requirements-lock.txt"
SBOM_LOCKFILE = REPO_ROOT / "requirements-sbom.txt"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
BUNDLE_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "build-packages.yml"

SHA256_PATTERN = re.compile(r"^--hash=sha256:([0-9a-f]{64})$")
PIN_PATTERN = re.compile(r"^([A-Za-z0-9_.-]+)==([^\s;]+)")
HASH_OPTION = "--hash="
PIP_TIMEOUT_SECONDS = 120
# pip prints this line when an artifact's bytes match no committed digest.
HASH_MISMATCH_TEXT = "THESE PACKAGES DO NOT MATCH THE HASHES"
UPGRADED_PACKAGE = "pyinstaller-hooks-contrib"
UPGRADED_VERSION = "2026.10"


class LockEntry:
    """One requirement entry of a lockfile with its digests."""

    def __init__(self, name: str, version: str) -> None:
        """Record the requirement. The digests follow it."""
        self.name = name
        self.version = version
        self.digests: list[str] = []


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
        entries.append(LockEntry(pin_match.group(1), pin_match.group(2)))
    return entries


def write_wheel(directory: Path, name: str, version: str) -> Path:
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


def run_pip_install(
    requirement: Path, wheels: Path, target: Path
) -> subprocess.CompletedProcess[str]:
    """Install a requirement from a local wheel directory and return the result."""
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--find-links",
            str(wheels),
            "--target",
            str(target),
            "--require-hashes",
            "-r",
            str(requirement),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        # A hung pip run fails the test instead of stalling the job until its timeout.
        timeout=PIP_TIMEOUT_SECONDS,
    )


class ApplicationLockHashesTest(unittest.TestCase):
    """Keep the application lockfile pinned to artifact digests."""

    def test_app_pins_carry_digests(self) -> None:
        """Each pinned package names the digests of the artifacts it may install."""
        entries = read_lock_entries(LOCKFILE)
        self.assertTrue(entries)
        for entry in entries:
            self.assertTrue(entry.digests, f"{entry.name} pins no artifact digest")

    def test_raw_requirement_lines_reach_digests(self) -> None:
        """A raw scan finds as many requirements as the parser maps to digests.

        The scan does not use the parser, so a parser that drops an entry or
        attaches its hashes to the wrong requirement fails here.
        """
        lines = LOCKFILE.read_text(encoding="utf-8").splitlines()
        raw_requirements = [line for line in lines if PIN_PATTERN.match(line.strip())]
        raw_hashes = [line for line in lines if line.strip().startswith(HASH_OPTION)]
        entries = read_lock_entries(LOCKFILE)
        self.assertEqual(len(entries), len(raw_requirements))
        self.assertEqual(sum(len(entry.digests) for entry in entries), len(raw_hashes))

    def test_upgraded_package_pins_digests(self) -> None:
        """The upgraded application dependency names its release and its digests."""
        entries = {entry.name: entry for entry in read_lock_entries(LOCKFILE)}
        upgraded = entries[UPGRADED_PACKAGE]
        self.assertEqual(upgraded.version, UPGRADED_VERSION)
        self.assertTrue(upgraded.digests)

    def test_lockfiles_lock_separate_environments(self) -> None:
        """A package that both lockfiles name carries the same version in both."""
        application = {entry.name: entry.version for entry in read_lock_entries(LOCKFILE)}
        sbom = {entry.name: entry.version for entry in read_lock_entries(SBOM_LOCKFILE)}
        self.assertTrue(application)
        self.assertTrue(sbom)
        self.assertFalse(set(application) <= set(sbom))
        for name in set(application) & set(sbom):
            self.assertEqual(application[name], sbom[name], f"{name} differs between the lockfiles")

    def test_ci_hash_checks_app_lock(self) -> None:
        """The CI jobs verify the artifact digests of the application lockfile."""
        ci_text = CI_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("pip install --require-hashes -r requirements-lock.txt", ci_text)
        self.assertIn("pip install --require-hashes --constraint requirements-lock.txt", ci_text)

    def test_bundle_hash_checks_app_lock(self) -> None:
        """The bundle job verifies the artifact digests of the application lockfile."""
        bundle_text = BUNDLE_WORKFLOW.read_text(encoding="utf-8")
        self.assertIn("pip install --require-hashes -r requirements-lock.txt", bundle_text)

    def test_bundle_hash_checks_sbom_lock(self) -> None:
        """The bundle job verifies the artifact digests of the SBOM lockfile."""
        bundle = yaml.safe_load(BUNDLE_WORKFLOW.read_text(encoding="utf-8"))
        steps = bundle["jobs"]["bundle"]["steps"]
        sbom_step = next(step for step in steps if step.get("name") == "Generate Python SBOM")
        self.assertIn("--require-hashes", sbom_step["run"])
        self.assertIn("requirements-sbom.txt", sbom_step["run"])


class PipHashEnforcementTest(unittest.TestCase):
    """Prove that pip refuses an artifact whose digest the file does not name."""

    def setUp(self) -> None:
        """Create a local wheel directory for the install checks."""
        self.directory = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.directory, True)
        self.wheels = self.directory / "wheels"
        self.wheels.mkdir()
        self.wheel = write_wheel(self.wheels, "sample", "1.0")
        self.digest = hashlib.sha256(self.wheel.read_bytes()).hexdigest()

    def _requirement(self, digest: str) -> Path:
        """Write a requirements file that names one digest and return its path."""
        path = self.directory / "requirement.txt"
        path.write_text(f"sample==1.0 \\\n    --hash=sha256:{digest}\n", encoding="utf-8")
        return path

    def test_pip_rejects_uncommitted_digest(self) -> None:
        """A substituted artifact fails the install under hash-checking mode.

        The check runs pip against a local wheel, so it needs no network
        access. pip selects the local artifact and refuses the download
        because no committed digest matches its bytes.
        """
        requirement = self._requirement("0" * 64)
        result = run_pip_install(requirement, self.wheels, self.directory / "target")
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn(HASH_MISMATCH_TEXT, result.stdout + result.stderr)

    def test_pip_accepts_committed_digest(self) -> None:
        """The committed digest lets the matching artifact install."""
        requirement = self._requirement(self.digest)
        result = run_pip_install(requirement, self.wheels, self.directory / "target")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
