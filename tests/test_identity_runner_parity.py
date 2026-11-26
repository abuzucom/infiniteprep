#!/usr/bin/env python3
"""The standalone identity runner matches trusted Git hardening (S5, V-08, S-10).

Each class stays small because the hook coverage runner gives every test
class one shard with a fixed time limit.
"""
import io
import os
import re
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import check_git_identity
import trusted_git

PLACEHOLDER_PROXIES = {
    "HTTP_PROXY": "http://127.0.0.1:9",
    "HTTPS_PROXY": "localhost:9",
    "all_proxy": "http://[::1]:9",
}
REAL_PROXY = {"ALL_PROXY": "http://proxy.example.test:8080"}
PROXY_NAMES = tuple(trusted_git.PROXY_VARIABLES)


def capture_environment(run, *arguments, **options) -> dict:
    """Run one runner with a recording subprocess and return its child environment."""
    captured = {}

    def runner(command, **kwargs):
        captured.update(kwargs)
        return subprocess.CompletedProcess(command, 0, "", "")

    run(tempfile.gettempdir(), list(arguments), runner=runner, **options)
    return captured


class ConstantParityTest(unittest.TestCase):
    """The standalone copies of trusted_git constants stay equal."""

    def test_environment_and_context_sets_match(self):
        self.assertEqual(check_git_identity.STANDALONE_CHILD_ENVIRONMENT,
                         trusted_git.CHILD_ENVIRONMENT)
        self.assertEqual(check_git_identity.GIT_IDENTITY_CONFIG_KEYS,
                         trusted_git.GIT_CONTEXT_IDENTITY_KEYS)
        self.assertEqual(check_git_identity.GIT_CONTEXT_VARIABLES,
                         trusted_git.GIT_CONTEXT_NAMES)

    def test_proxy_constants_match(self):
        self.assertEqual(tuple(check_git_identity.STANDALONE_PROXY_VARIABLES),
                         tuple(trusted_git.PROXY_VARIABLES))
        self.assertEqual(check_git_identity.STANDALONE_PROXY_HOSTS,
                         trusted_git.MANAGED_PROXY_HOSTS)
        self.assertEqual(check_git_identity.STANDALONE_PROXY_PORT,
                         trusted_git.MANAGED_PROXY_PORT)


class ProxyParityTest(unittest.TestCase):
    """Both runners strip the same managed proxy placeholders."""

    def test_placeholder_proxies_removed(self):
        with patch.dict(os.environ, {**PLACEHOLDER_PROXIES, **REAL_PROXY}):
            standalone = capture_environment(
                check_git_identity._standalone_run_git, "status")["env"]
            trusted = capture_environment(trusted_git.run_git, "status")["env"]
        standalone_proxies = {name: standalone.get(name) for name in PROXY_NAMES}
        trusted_proxies = {name: trusted.get(name) for name in PROXY_NAMES}
        self.assertEqual(standalone_proxies, trusted_proxies)
        for name in PLACEHOLDER_PROXIES:
            with self.subTest(name=name):
                self.assertNotIn(name, standalone)
        self.assertEqual(standalone.get("ALL_PROXY"), REAL_PROXY["ALL_PROXY"])


class ContextValidationParityTest(unittest.TestCase):
    """Both runners drop empty, NUL-bearing, and non-identity context values."""

    def test_same_context_subset(self):
        context = {
            "GIT_DIR": "", "GIT_WORK_TREE": "/work\0tree", "GIT_COMMON_DIR": "/work/common",
            "GIT_CONFIG_COUNT": "3",
            "GIT_CONFIG_KEY_0": "user.name", "GIT_CONFIG_VALUE_0": "Ada\0Lovelace",
            "GIT_CONFIG_KEY_1": "user.email", "GIT_CONFIG_VALUE_1": "ada@example.com",
            "GIT_CONFIG_KEY_2": "core.fsmonitor", "GIT_CONFIG_VALUE_2": "evil",
        }
        context_names = trusted_git.GIT_CONTEXT_NAMES | {"GIT_CONFIG_COUNT"}
        standalone = capture_environment(
            check_git_identity._standalone_run_git, "status", git_context=context)["env"]
        trusted = capture_environment(trusted_git.run_git, "status", git_context=context)["env"]

        def context_view(environment):
            return {name: value for name, value in environment.items()
                    if name in context_names or name.startswith(("GIT_CONFIG_KEY_",
                                                                 "GIT_CONFIG_VALUE_"))}

        self.assertEqual(context_view(standalone), context_view(trusted))
        self.assertNotIn("GIT_DIR", standalone)
        self.assertNotIn("GIT_WORK_TREE", standalone)
        self.assertEqual(standalone.get("GIT_COMMON_DIR"), "/work/common")
        self.assertEqual(standalone.get("GIT_CONFIG_COUNT"), "1")
        self.assertEqual(standalone.get("GIT_CONFIG_KEY_0"), "user.email")


class GitTimeoutTest(unittest.TestCase):
    """Identity Git calls carry a timeout, and a timeout exits 1 with the command."""

    def test_git_passes_the_timeout(self):
        captured = {}

        def fake_run_git(repo, arguments, **options):
            captured.update(options)
            return subprocess.CompletedProcess(arguments, 0, "", "")

        with patch.object(check_git_identity, "run_git", side_effect=fake_run_git):
            check_git_identity._git(tempfile.gettempdir(), ["config", "user.name"])
        self.assertEqual(captured.get("timeout"), check_git_identity.GIT_TIMEOUT_SECONDS)
        self.assertEqual(check_git_identity.GIT_TIMEOUT_SECONDS, 120)

    def test_standalone_runner_forwards_the_timeout(self):
        captured = capture_environment(
            check_git_identity._standalone_run_git, "status", timeout=7)
        self.assertEqual(captured.get("timeout"), 7)

    def test_timeout_names_command(self):
        expired = subprocess.TimeoutExpired(["git", "config", "user.name"], 120)
        stderr = io.StringIO()
        with patch.object(check_git_identity, "run_git", side_effect=expired), \
                patch.object(sys, "argv", ["check_git_identity.py"]), \
                redirect_stdout(io.StringIO()), redirect_stderr(stderr):
            code = check_git_identity.main()
        self.assertEqual(code, 1)
        self.assertIn("timed out", stderr.getvalue())
        self.assertIn("git config user.name", stderr.getvalue())


class AllowPatternTest(unittest.TestCase):
    """A custom --allow pattern must match the whole address."""

    def test_suffix_is_rejected(self):
        pattern = re.compile(r"a@corp\.com")
        self.assertTrue(check_git_identity._allowed("a@corp.com", pattern))
        self.assertFalse(check_git_identity._allowed("a@corp.com.evil.net", pattern))


if __name__ == "__main__":
    unittest.main()
