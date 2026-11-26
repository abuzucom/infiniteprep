"""hooks/primary-branch.txt names one primary branch in place of main.

Each class stays small because the hook coverage runner gives every test class
one shard with a fixed time limit, and every case launches the hook.
"""
import subprocess
import unittest

from tests.read_only_branch_support import BranchFixture, decision

INVALID_OVERRIDES = (
    b"", b"\n", b"develop\nmain\n", b"master\n", b"HEAD\n", b"bad name\n",
    b"-develop\n", "d\xe9velop\n".encode("utf-8"), b"x" * 300 + b"\n",
)
SUBTEST_LABEL_LENGTH = 20


def read_readme(fixture: BranchFixture) -> subprocess.CompletedProcess:
    """Send one Read call for the fixture README to the hook."""
    return fixture.run("Read", {"file_path": str(fixture.root / "README.md")})


def assert_invalid_overrides_deny(test_case: unittest.TestCase, branch: str) -> None:
    """Assert that every invalid override denies Read and names the file at start.

    Each case rewrites the one override file before the hook reads it. The
    hook keeps no state between launches, so one fixture serves every case.
    """
    fixture = BranchFixture(test_case, branch)
    for content in INVALID_OVERRIDES:
        with test_case.subTest(content=content[:SUBTEST_LABEL_LENGTH], branch=branch):
            fixture.write_override(content)
            result = read_readme(fixture)
            test_case.assertEqual(decision(result), "deny")
            test_case.assertIn("hooks/primary-branch.txt", result.stderr)
            start = fixture.lifecycle("SessionStart")
            test_case.assertIn("hooks/primary-branch.txt", start.stdout)


class PrimaryBranchOverrideTest(unittest.TestCase):
    """The override moves the read-only state from main to the named branch."""

    def test_override_branch_is_read_only(self):
        fixture = BranchFixture(self, "develop")
        for content in (b"develop\n", b"develop\r\n", b"develop"):
            with self.subTest(content=content):
                fixture.write_override(content)
                self.assertEqual(decision(read_readme(fixture)), "allow")
                self.assertEqual(decision(fixture.shell("git status")), "allow")
                denied = fixture.shell("touch notes.txt")
                self.assertEqual(decision(denied), "deny")
                self.assertIn("`develop` permits read-only inspection", denied.stderr)

    def test_main_is_strict_under_override(self):
        fixture = BranchFixture(self, "main")
        fixture.write_override(b"develop\n")
        result = read_readme(fixture)
        self.assertEqual(decision(result), "deny")
        self.assertIn("git switch -c", result.stderr)
        self.assertEqual(decision(fixture.shell("git switch -c feat/new-work")), "ask")

    def test_develop_is_strict_without_override(self):
        # develop is a stable branch without the override, so it stays read-only.
        fixture = BranchFixture(self, "develop")
        self.assertEqual(decision(read_readme(fixture)), "allow")
        denied = fixture.shell("touch notes.txt")
        self.assertEqual(decision(denied), "deny")
        self.assertIn("`develop` permits read-only inspection", denied.stderr)


class UnusableOverrideFileTest(unittest.TestCase):
    """An override path that is a directory or names master fails closed."""

    def test_directory_override_fails_closed(self):
        fixture = BranchFixture(self, "main")
        (fixture.root / "hooks" / "primary-branch.txt").mkdir(parents=True)
        result = read_readme(fixture)
        self.assertEqual(decision(result), "deny")
        self.assertIn("hooks/primary-branch.txt", result.stderr)

    def test_invalid_override_blocks_lifecycle_completion(self):
        fixture = BranchFixture(self, "main")
        fixture.write_override(b"master\n")
        stop = fixture.lifecycle("Stop")
        self.assertIn("block", stop.stdout)
        self.assertIn("hooks/primary-branch.txt", stop.stdout)
        prompt = fixture.lifecycle("UserPromptSubmit")
        self.assertIn("hooks/primary-branch.txt", prompt.stdout)


class InvalidOverrideDevelopTest(unittest.TestCase):
    """Every invalid override denies every tool on a non-primary branch."""

    def test_invalid_override_denies_every_tool(self):
        assert_invalid_overrides_deny(self, "develop")


class InvalidOverrideMainTest(unittest.TestCase):
    """Every invalid override denies every tool on main."""

    def test_invalid_override_denies_every_tool(self):
        assert_invalid_overrides_deny(self, "main")


if __name__ == "__main__":
    unittest.main()
