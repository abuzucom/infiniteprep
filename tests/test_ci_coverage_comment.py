#!/usr/bin/env python3
"""Run the coverage comment script from the ci workflow against a stubbed API."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CI_WORKFLOW = ROOT / ".github" / "workflows" / "ci.yml"

HARNESS = """
const fs = require('fs');
const callsPath = process.env.STUB_CALLS;
function record(entry) {
  fs.appendFileSync(callsPath, JSON.stringify(entry) + '\\n');
}
process.on('unhandledRejection', (error) => {
  record({ action: 'error', message: error.message });
  process.exit(1);
});
const comments = JSON.parse(fs.readFileSync(process.env.STUB_COMMENTS, 'utf8'));
const github = {
  paginate: async () => comments,
  rest: {
    issues: {
      listComments: async () => ({ data: comments }),
      createComment: async (params) => {
        record({ action: 'create', body: params.body });
        return { data: { id: 1 } };
      },
      updateComment: async (params) => {
        record({ action: 'update', comment_id: params.comment_id, body: params.body });
        return { data: { id: params.comment_id } };
      },
      deleteComment: async (params) => {
        record({ action: 'delete', comment_id: params.comment_id });
        return { data: {} };
      },
    },
  },
};
const context = JSON.parse(fs.readFileSync(process.env.STUB_CONTEXT, 'utf8'));
const core = {
  info: (message) => record({ action: 'info', message }),
  setFailed: (message) => record({ action: 'failed', message }),
};
// actions/github-script runs the script inside an async function. A rejection
// reaches the unhandledRejection handler above.
(async () => {
%(script)s
})();
"""

BOT = "github-actions[bot]"
MARKER = "<!-- infiniteprep-coverage -->"
OTHER = "someone-else"
NODE = shutil.which("node")


def extract_script() -> str:
    """Return the script of the coverage comment step from the ci workflow."""
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    start = text.index("      - name: Comment the coverage on the pull request")
    block = text[start:]
    script_start = block.index("          script: |\n") + len("          script: |\n")
    lines = []
    for line in block[script_start:].splitlines():
        if line.strip() and not line.startswith("            "):
            break
        lines.append(line[12:])
    return "\n".join(lines)


def bot_comment(comment_id: int, run_id: int, body: str = "") -> dict:
    """Return one bot comment that carries the marker."""
    return {
        "id": comment_id,
        "user": {"login": BOT},
        "body": f"{MARKER}\n<!-- infiniteprep-coverage-run {run_id} -->\n{body}",
    }


def run_script(
    tmp_path: Path,
    summary: object,
    comments: list[dict],
    run_id: int = 100,
    raw_summary: str | None = None,
) -> list[dict]:
    """Run the workflow script and return the recorded API calls."""
    summary_path = tmp_path / "coverage-summary.json"
    if raw_summary is None:
        summary_path.write_text(json.dumps(summary), encoding="utf-8")
    else:
        summary_path.write_text(raw_summary, encoding="utf-8")
    comments_path = tmp_path / "comments.json"
    comments_path.write_text(json.dumps(comments), encoding="utf-8")
    context_path = tmp_path / "context.json"
    context_path.write_text(
        json.dumps(
            {
                "repo": {"owner": "abuzucom", "repo": "infiniteprep"},
                "issue": {"number": 1},
                "runId": run_id,
            }
        ),
        encoding="utf-8",
    )
    calls_path = tmp_path / "calls.json"
    calls_path.write_text("", encoding="utf-8")
    harness_path = tmp_path / "harness.js"
    harness_path.write_text(HARNESS % {"script": extract_script()}, encoding="utf-8")
    environment = dict(os.environ)
    environment.update(
        {
            "STUB_COMMENTS": str(comments_path),
            "STUB_CONTEXT": str(context_path),
            "STUB_CALLS": str(calls_path),
        }
    )
    completed = subprocess.run(
        [str(NODE), str(harness_path)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    lines = [line for line in calls_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    calls = [json.loads(line) for line in lines]
    # A recorded error exits 1 through the harness. Any other exit code means node
    # itself failed, so the failure names its stderr instead of an empty call list.
    expected_exit = 1 if any(call["action"] == "error" for call in calls) else 0
    if completed.returncode != expected_exit:
        raise AssertionError(
            f"node exited {completed.returncode}, expected {expected_exit}: {completed.stderr}"
        )
    return calls


@unittest.skipIf(NODE is None, "node is not installed")
class CoverageCommentScriptTest(unittest.TestCase):
    """Exercise the comment script of the ci workflow."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def test_a_valid_summary_creates_the_comment(self) -> None:
        """A valid summary becomes a comment with the total and the file rows."""
        calls = run_script(
            self.tmp,
            {"total": 87.9, "files": [{"path": "src/infiniteprep/ui/theme.py", "percent": 42.4}]},
            [],
        )

        actions = [call["action"] for call in calls]
        self.assertEqual(actions, ["create"])
        self.assertIn("Total coverage: 87.9%", calls[0]["body"])
        self.assertIn("42.4%  src/infiniteprep/ui/theme.py", calls[0]["body"])
        self.assertTrue(calls[0]["body"].startswith(MARKER))

    def test_a_foreign_comment_never_becomes_the_target(self) -> None:
        """A comment from another author that carries the marker is left alone."""
        calls = run_script(
            self.tmp,
            {"total": 87.9, "files": []},
            [
                {
                    "id": 7,
                    "user": {"login": OTHER},
                    "body": f"{MARKER}\n<!-- infiniteprep-coverage-run 5 -->\nold",
                }
            ],
        )

        actions = [call["action"] for call in calls]
        self.assertEqual(actions, ["create"])

    def test_a_bot_comment_with_the_marker_inside_the_body_is_left_alone(self) -> None:
        """A bot comment that only mentions the marker is not the target."""
        calls = run_script(
            self.tmp,
            {"total": 87.9, "files": []},
            [
                {
                    "id": 8,
                    "user": {"login": BOT},
                    "body": "text before the marker " + MARKER,
                }
            ],
        )

        actions = [call["action"] for call in calls]
        self.assertEqual(actions, ["create"])

    def test_a_second_bot_comment_is_removed(self) -> None:
        """A second coverage comment from the bot does not survive."""
        calls = run_script(
            self.tmp,
            {"total": 87.9, "files": []},
            [bot_comment(11, 90), bot_comment(12, 90)],
        )

        actions = [call["action"] for call in calls]
        self.assertEqual(actions, ["update", "delete"])
        self.assertEqual(calls[1]["comment_id"], 12)

    def test_an_older_run_never_replaces_a_newer_result(self) -> None:
        """A run older than the posted run writes nothing."""
        calls = run_script(
            self.tmp,
            {"total": 87.9, "files": []},
            [bot_comment(13, 500)],
            run_id=100,
        )

        actions = [call["action"] for call in calls]
        self.assertEqual(actions, ["info"])

    def test_a_newer_run_updates_the_comment(self) -> None:
        """A run newer than the posted run updates the comment."""
        calls = run_script(
            self.tmp,
            {"total": 87.9, "files": []},
            [bot_comment(14, 50)],
            run_id=100,
        )

        actions = [call["action"] for call in calls]
        self.assertEqual(actions, ["update"])
        self.assertEqual(calls[0]["comment_id"], 14)

    def test_a_path_with_a_fence_character_is_rejected(self) -> None:
        """A crafted file path never reaches the comment."""
        calls = run_script(
            self.tmp,
            {"total": 87.9, "files": [{"path": "src/a.py`\n@someone", "percent": 1.0}]},
            [],
        )

        self.assertEqual([call["action"] for call in calls], ["error"])
        self.assertIn("unsupported character", calls[0]["message"])

    def test_an_unexpected_field_is_rejected(self) -> None:
        """A summary with an extra field never reaches the comment."""
        calls = run_script(
            self.tmp,
            {"total": 87.9, "files": [], "body": "injected"},
            [],
        )

        self.assertEqual([call["action"] for call in calls], ["error"])
        self.assertIn("unexpected fields", calls[0]["message"])

    def test_a_total_out_of_range_is_rejected(self) -> None:
        """A total outside the range never reaches the comment."""
        calls = run_script(self.tmp, {"total": 1000, "files": []}, [])

        self.assertEqual([call["action"] for call in calls], ["error"])
        self.assertIn("out of range", calls[0]["message"])

    def test_too_many_files_are_rejected(self) -> None:
        """A file list longer than the cap never reaches the comment."""
        files = [{"path": f"src/file{index}.py", "percent": 1.0} for index in range(21)]
        calls = run_script(self.tmp, {"total": 87.9, "files": files}, [])

        self.assertEqual([call["action"] for call in calls], ["error"])
        self.assertIn("out of range", calls[0]["message"])

    def test_a_file_record_with_an_extra_field_is_rejected(self) -> None:
        """A file record with an extra field never reaches the comment."""
        calls = run_script(
            self.tmp,
            {"total": 87.9, "files": [{"path": "src/a.py", "percent": 1.0, "note": "x"}]},
            [],
        )

        self.assertEqual([call["action"] for call in calls], ["error"])
        self.assertIn("unexpected fields", calls[0]["message"])

    def test_a_summary_that_is_not_json_is_rejected(self) -> None:
        """A summary that is not valid JSON never reaches the comment."""
        calls = run_script(self.tmp, None, [], raw_summary="not json")

        self.assertEqual([call["action"] for call in calls], ["error"])


if __name__ == "__main__":
    unittest.main()
