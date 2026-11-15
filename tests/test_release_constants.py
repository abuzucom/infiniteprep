"""Independent pins for the release constants in packaging/release.py (D355).

The release tests read the expected names and permissions from these
constants. This file pins the six current package names by hand. A wrong
constant still fails a test.
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from types import MappingProxyType, ModuleType

import yaml

ROOT = Path(__file__).resolve().parent.parent
RELEASE_PATH = ROOT / "packaging" / "release.py"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "build-packages.yml"
VERSION = "1.2.0"
PINNED_PACKAGES = frozenset(
    {
        f"InfinitePrep-{VERSION}-windows-x64-portable.zip",
        f"InfinitePrep-{VERSION}-windows-x64-setup.exe",
        f"InfinitePrep-{VERSION}-linux-x86_64.AppImage",
        f"InfinitePrep-{VERSION}-linux-x86_64.tar.gz",
        f"InfinitePrep-{VERSION}-linux-x86_64-portable.tar.gz",
        f"InfinitePrep-{VERSION}-macos-arm64.dmg",
    }
)
PINNED_BUNDLE_SUFFIX = ".sigstore.json"
# The release job permissions, pinned by hand (D315, D325, D328).
PINNED_RELEASE_PERMISSIONS = {
    "contents": "write",
    "actions": "read",
    "id-token": "write",
    "attestations": "write",
    "artifact-metadata": "write",
}


def _load_release() -> ModuleType:
    """Import packaging/release.py by file path, as CI runs it."""
    spec = importlib.util.spec_from_file_location("release_constants_target", RELEASE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


RELEASE = _load_release()


class PackageNamePinTest(unittest.TestCase):
    """The asset constants produce the six hand-pinned package names."""

    def test_expected_packages_match_the_pin(self) -> None:
        """expected_packages returns exactly the six pinned names."""
        self.assertEqual(RELEASE.expected_packages(VERSION), PINNED_PACKAGES)

    def test_expected_bundles_match_the_pin(self) -> None:
        """expected_bundles adds the Sigstore suffix to each pinned name (D328)."""
        pinned_bundles = {f"{name}{PINNED_BUNDLE_SUFFIX}" for name in PINNED_PACKAGES}
        self.assertEqual(RELEASE.expected_bundles(VERSION), pinned_bundles)


class ReleasePermissionConstantTest(unittest.TestCase):
    """RELEASE_JOB_PERMISSIONS states the release job's permissions as an immutable mapping."""

    def test_constant_matches_the_pin(self) -> None:
        """The constant states exactly the hand-pinned permissions."""
        self.assertEqual(dict(RELEASE.RELEASE_JOB_PERMISSIONS), PINNED_RELEASE_PERMISSIONS)

    def test_constant_is_read_only(self) -> None:
        """A test or caller cannot change the constant in place."""
        self.assertIsInstance(RELEASE.RELEASE_JOB_PERMISSIONS, MappingProxyType)

    def test_constant_matches_the_release_job(self) -> None:
        """The workflow's release job grants exactly the constant's permissions."""
        workflow = yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))
        permissions = workflow["jobs"]["release"]["permissions"]
        self.assertEqual(permissions, dict(RELEASE.RELEASE_JOB_PERMISSIONS))


if __name__ == "__main__":
    unittest.main()
