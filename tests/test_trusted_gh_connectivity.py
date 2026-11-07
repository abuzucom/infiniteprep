#!/usr/bin/env python3
"""Tests for trusted_gh connectivity diagnostics and multi-client retry policy."""
import io
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch


REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPOSITORY_ROOT / "scripts"))
sys.path.insert(0, str(REPOSITORY_ROOT / "hooks"))

import check_policy_size
import reinject_agents_policy
import trusted_gh


class ConnectivityClassificationTest(unittest.TestCase):
    """Classify network transport failures separately from auth failures."""

    def test_connectivity_error_subclasses_oserror(self) -> None:
        self.assertTrue(issubclass(trusted_gh.ConnectivityError, OSError))

    def test_network_stderr_messages_match_connectivity_failure(self) -> None:
        samples = (
            "error connecting to api.github.com\n",
            "Get \"https://api.github.com/user\": dial tcp: lookup api.github.com: no such host",
            "dial tcp 140.82.112.6:443: connectex: forbidden by its access permissions",
            "net/http: TLS handshake timeout",
            "proxyconnect tcp: dial tcp 127.0.0.1:9: connect: connection refused",
            "connect: network is unreachable",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertTrue(trusted_gh.is_connectivity_failure(sample))

    def test_auth_stderr_messages_do_not_match_connectivity_failure(self) -> None:
        samples = (
            "",
            "To get started with GitHub CLI, please run:  gh auth login",
            "HTTP 401: Bad credentials (https://api.github.com/user)",
            "authentication token is expired",
        )
        for sample in samples:
            with self.subTest(sample=sample):
                self.assertFalse(trusted_gh.is_connectivity_failure(sample))

    def test_authenticated_account_raises_connectivity_error_on_network_failure(self) -> None:
        failed_process = subprocess.CompletedProcess(
            ["gh", "api", "user"],
            1,
            "",
            "error connecting to api.github.com\n",
        )
        with patch.object(trusted_gh, "run_gh", return_value=failed_process):
            with self.assertRaises(trusted_gh.ConnectivityError):
                trusted_gh.authenticated_account(REPOSITORY_ROOT)

    def test_authenticated_account_raises_oserror_on_auth_failure(self) -> None:
        failed_process = subprocess.CompletedProcess(
            ["gh", "api", "user"],
            4,
            "",
            "HTTP 401: Bad credentials\n",
        )
        with patch.object(trusted_gh, "run_gh", return_value=failed_process):
            with self.assertRaises(OSError) as captured:
                trusted_gh.authenticated_account(REPOSITORY_ROOT)
        self.assertNotIsInstance(captured.exception, trusted_gh.ConnectivityError)


class ProxySanitizationTest(unittest.TestCase):
    """Strip loopback discard proxies and honor option delimiters."""

    def test_loopback_discard_proxies_are_removed_across_hosts(self) -> None:
        direct_environment = {
            "HTTP_PROXY": "http://localhost:9",
            "HTTPS_PROXY": "http://[::1]:9",
            "ALL_PROXY": "127.0.0.1:9",
            "http_proxy": "http://proxy.example.test:8080",
        }
        trusted_gh._sanitize_proxy_environment(direct_environment)
        self.assertNotIn("HTTP_PROXY", direct_environment)
        self.assertNotIn("HTTPS_PROXY", direct_environment)
        self.assertNotIn("ALL_PROXY", direct_environment)
        self.assertEqual(
            direct_environment["http_proxy"],
            "http://proxy.example.test:8080",
        )

        captured = {}

        def fake_runner(arguments, **kwargs):
            captured.update(kwargs)
            return subprocess.CompletedProcess(arguments, 0, "", "")

        proxy_env = {
            "HTTP_PROXY": "http://localhost:9",
            "HTTPS_PROXY": "http://[::1]:9",
            "ALL_PROXY": "http://proxy.example.test:8080",
        }
        with patch.dict(os.environ, proxy_env, clear=False):
            with patch.object(trusted_gh, "resolve_gh", return_value=sys.executable):
                with patch.object(
                    trusted_gh,
                    "_safe_directory",
                    return_value=Path(tempfile.gettempdir()),
                ):
                    trusted_gh.run_gh(
                        Path(tempfile.gettempdir()),
                        ["api", "user"],
                        runner=fake_runner,
                    )

        self.assertNotIn("HTTP_PROXY", captured["env"])
        self.assertNotIn("HTTPS_PROXY", captured["env"])
        self.assertEqual(
            captured["env"]["ALL_PROXY"],
            "http://proxy.example.test:8080",
        )

    def test_positional_tokens_after_delimiter_do_not_suppress_context(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            git_dir = root / ".git"
            git_dir.mkdir()
            (git_dir / "HEAD").write_text(
                "ref: refs/heads/fix/retry-test\n",
                encoding="utf-8",
            )
            (git_dir / "config").write_text(
                '[remote "origin"]\n\turl = https://github.com/owner/repo.git\n',
                encoding="utf-8",
            )
            arguments = trusted_gh.with_repository_context(
                root,
                ["pr", "create", "--", "--repo", "other/repo", "--head", "other:branch"],
            )
            self.assertEqual(
                arguments,
                [
                    "pr",
                    "create",
                    "--head",
                    "owner:fix/retry-test",
                    "--repo",
                    "owner/repo",
                    "--",
                    "--repo",
                    "other/repo",
                    "--head",
                    "other:branch",
                ],
            )


class CliDiagnosticOutputTest(unittest.TestCase):
    """Emit secret-safe connectivity diagnostics and recovery instructions."""

    def test_main_account_check_reports_connectivity_recovery(self) -> None:
        with patch.object(
            trusted_gh,
            "authenticated_account",
            side_effect=trusted_gh.ConnectivityError("secret-proxy-credential"),
        ):
            with patch.object(trusted_gh.sys, "argv", ["trusted_gh.py"]):
                stderr_buffer = io.StringIO()
                with redirect_stderr(stderr_buffer):
                    exit_code = trusted_gh.main()

        message = stderr_buffer.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn("cannot reach GitHub", message)
        self.assertIn("request approved external network access or retry", message)
        self.assertNotIn("authentication failed", message)
        self.assertNotIn("secret-proxy-credential", message)

    def test_run_command_reports_connectivity_recovery_on_account_lookup(self) -> None:
        with patch.object(
            trusted_gh,
            "with_repository_context",
            return_value=["pr", "view", "1", "--repo", "owner/repo"],
        ):
            with patch.object(
                trusted_gh,
                "authenticated_account",
                side_effect=trusted_gh.ConnectivityError("secret-token-value"),
            ):
                stderr_buffer = io.StringIO()
                with redirect_stderr(stderr_buffer):
                    exit_code = trusted_gh._run_requested_command(
                        REPOSITORY_ROOT,
                        ["pr", "view", "1"],
                    )

        message = stderr_buffer.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn("cannot reach GitHub", message)
        self.assertIn("request approved external network access or retry", message)
        self.assertNotIn("secret-token-value", message)

    def test_run_command_reports_connectivity_recovery_on_subcommand_drop(self) -> None:
        dropped = subprocess.CompletedProcess(
            ["gh", "pr", "view", "1"],
            1,
            "",
            "error connecting to api.github.com: secret-header-value\n",
        )
        with patch.object(
            trusted_gh,
            "with_repository_context",
            return_value=["pr", "view", "1", "--repo", "owner/repo"],
        ):
            with patch.object(
                trusted_gh,
                "authenticated_account",
                return_value={"id": 1, "login": "octocat"},
            ):
                with patch.object(trusted_gh, "run_gh", return_value=dropped):
                    stderr_buffer = io.StringIO()
                    with redirect_stderr(stderr_buffer):
                        exit_code = trusted_gh._run_requested_command(
                            REPOSITORY_ROOT,
                            ["pr", "view", "1"],
                        )

        message = stderr_buffer.getvalue()
        self.assertEqual(exit_code, 1)
        self.assertIn("cannot reach GitHub", message)
        self.assertIn("request approved external network access or retry", message)
        self.assertNotIn("secret-header-value", message)


class PolicyRetryGuidanceTest(unittest.TestCase):
    """Verify retry and network escalation guidance across all client policies."""

    def test_canonical_and_assembled_policy_include_retry_and_network_rules(self) -> None:
        violations = check_policy_size.find_violations(REPOSITORY_ROOT / "AGENTS.md")
        self.assertEqual(violations, [])
        policy_text, _digest = reinject_agents_policy.load_policy(REPOSITORY_ROOT)
        chunks = reinject_agents_policy.split_policy(
            policy_text,
            reinject_agents_policy.CLAUDE_CHUNK_COUNT,
        )
        self.assertEqual(len(chunks), reinject_agents_policy.CLAUDE_CHUNK_COUNT)
        self.assertIn("resets the retry count", policy_text)
        self.assertIn("external-network", policy_text)


if __name__ == "__main__":
    unittest.main()
