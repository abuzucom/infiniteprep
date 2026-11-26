#!/usr/bin/env python3
"""Exercise the largest-file ranking for the assembled policy."""
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import sync

from scripts import check_policy_size

AGENTS_LIMIT = 32 * 1024
COMBINED_LIMIT = 64 * 1024
RANK_LIMIT = 10
AGENTS_FIXTURE_BYTES = 2500
# Distinct sizes give each supporting file a unique rank.
SUPPORTING_FIXTURE_SIZES = (300, 1200, 50, 900, 700, 100)
# Two files at this size push the assembled policy over 65536 bytes.
OVERSIZED_SUPPORTING_BYTES = 32000
SYNTHETIC_FILE_COUNT = 12


def _write_fixture(root: Path, supporting_sizes: tuple) -> None:
    """Create a synthetic repository with one size per supporting file."""
    (root / "docs" / "agent-policy").mkdir(parents=True)
    body = "\n".join(check_policy_size.REQUIRED_TEXT).encode("ascii") + b"\n"
    padding = b"x" * (AGENTS_FIXTURE_BYTES - len(body) - 1)
    (root / "AGENTS.md").write_bytes(body + padding + b"\n")
    for relative_name, size in zip(sync.ADOPTER_POLICY_FILES, supporting_sizes):
        (root / relative_name).write_bytes(b"y" * (size - 1) + b"\n")


def _run_main(argv: list) -> tuple:
    """Run the checker entry point and capture its exit code and streams."""
    stdout = StringIO()
    stderr = StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        exit_code = check_policy_size.main(argv)
    return exit_code, stdout.getvalue(), stderr.getvalue()


class AssembledPartsTests(unittest.TestCase):
    def test_parts_join_to_policy(self):
        parts = sync.read_assembled_policy_parts(ROOT)
        self.assertEqual(parts[0][0], sync.SOURCE)
        self.assertEqual(
            b"".join(content for _, content in parts),
            sync.assemble_policy_bytes(ROOT))

    def test_ranking_order_and_cap(self):
        sizes = [(f"file{index}.md", index * 10)
                 for index in range(SYNTHETIC_FILE_COUNT)]
        ranked = check_policy_size.rank_largest_files(sizes)
        self.assertEqual(len(ranked), RANK_LIMIT)
        self.assertEqual(ranked[0], {"path": "file11.md", "bytes": 110})
        self.assertEqual(
            [entry["bytes"] for entry in ranked],
            sorted((entry["bytes"] for entry in ranked), reverse=True))

    def test_ranking_breaks_ties_by_path(self):
        ranked = check_policy_size.rank_largest_files(
            [("b.md", 5), ("a.md", 5), ("c.md", 9)])
        self.assertEqual([entry["path"] for entry in ranked],
                         ["c.md", "a.md", "b.md"])


class ReportFilesTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.root = Path(self.temporary_directory.name)

    def test_json_report_file_order(self):
        _write_fixture(self.root, SUPPORTING_FIXTURE_SIZES)
        exit_code, stdout, _ = _run_main([str(self.root / "AGENTS.md"), "--json"])
        self.assertEqual(exit_code, 0)
        report = json.loads(stdout)
        expected = [{"path": "AGENTS.md", "bytes": AGENTS_FIXTURE_BYTES}]
        expected += [{"path": name, "bytes": size} for name, size in zip(
            sync.ADOPTER_POLICY_FILES, SUPPORTING_FIXTURE_SIZES)]
        expected.sort(key=lambda entry: (-entry["bytes"], entry["path"]))
        self.assertEqual(report["files"], expected)
        self.assertEqual(sum(entry["bytes"] for entry in report["files"]),
                         report["targets"][1]["bytes"])

    def test_combined_failure_lists_largest_files(self):
        sizes = (OVERSIZED_SUPPORTING_BYTES, OVERSIZED_SUPPORTING_BYTES,
                 *SUPPORTING_FIXTURE_SIZES[2:])
        _write_fixture(self.root, sizes)
        exit_code, _, stderr = _run_main([str(self.root / "AGENTS.md")])
        self.assertEqual(exit_code, 1)
        self.assertIn("Largest files in the assembled policy:", stderr)
        first = stderr.index(f"{OVERSIZED_SUPPORTING_BYTES} bytes")
        self.assertLess(first, stderr.index(f"{AGENTS_FIXTURE_BYTES} bytes"))
        self.assertIn("AGENT DIRECTIVE", stderr)


class RenderFilesTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.report_path = Path(self.temporary_directory.name) / "report.json"

    def _build_report(self, files: object) -> dict:
        """Return a valid size report that carries the given file ranking."""
        return {"schema": 1, "targets": [
            {"name": "AGENTS.md", "bytes": 32602, "limit": AGENTS_LIMIT},
            {"name": "assembled policy", "bytes": 65532,
             "limit": COMBINED_LIMIT},
        ], "files": files}

    def _render(self, report: object) -> tuple:
        """Write the report as JSON and return the renderer exit code and streams."""
        self.report_path.write_text(json.dumps(report), encoding="ascii")
        return _run_main(["--render-markdown", str(self.report_path)])

    def test_render_file_shares(self):
        files = [
            {"path": "AGENTS.md", "bytes": 32602},
            {"path": "docs/agent-policy/enforcement.md", "bytes": 10095},
        ]
        exit_code, stdout, _ = self._render(self._build_report(files))
        self.assertEqual(exit_code, 0)
        self.assertIn("### Largest files in the assembled policy", stdout)
        self.assertIn("| 1 | `AGENTS.md` | 32,602 | 49.7% |", stdout)
        self.assertIn(
            "| 2 | `docs/agent-policy/enforcement.md` | 10,095 | 15.4% |", stdout)
        self.assertLess(stdout.index("Largest files"),
                        stdout.index("AGENT DIRECTIVE"))

    def test_render_rejects_untrusted_file_lists(self):
        too_many = [{"path": "AGENTS.md", "bytes": 1}] * (RANK_LIMIT + 1)
        bad_lists = [
            "AGENTS.md",
            too_many,
            [{"path": "`injected`.md", "bytes": 1}],
            [{"path": "AGENTS.md", "bytes": True}],
            [{"path": "AGENTS.md", "bytes": 1, "extra": 1}],
            [{"path": "docs/agent-policy/github.md", "bytes": 1},
             {"path": "AGENTS.md", "bytes": 2}],
            [{"path": "AGENTS.md", "bytes": 2}, {"path": "AGENTS.md", "bytes": 1}],
        ]
        for files in bad_lists:
            with self.subTest(files=files):
                exit_code, stdout, stderr = self._render(self._build_report(files))
                self.assertEqual(exit_code, 1)
                self.assertEqual(stdout, "")
                self.assertIn("invalid size report", stderr)


if __name__ == "__main__":
    unittest.main()
