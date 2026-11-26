# Template drift

Source template: `abuzucom/agents` at commit `4797f14` (v2.46.0).

The previous template revision was `88f58b5`. A three-way apply of the
template changes between the two revisions carried the local differences
below forward.

## Taken

- `AGENTS.md` and the eight generated tool copies.
- `docs/agent-policy/` except `source-orientation.md`.
- Every file under `hooks/` and the client settings under `.claude/`,
  `.codex/`, `.gemini/`, and `.agents/`.
- Every checker under `scripts/`.
- The agents test suites under `tests/`.
- `shared-files.json`, `policy-digests.json`, `hook-coverage-baseline.json`,
  `tools/hook-trace/`, `Makefile`, `requirements-checkers.txt`,
  `.pre-commit-config.yaml`, `.claudeignore`, `.editorconfig`,
  `.local-path-allowlist`, and `plan/HANDOFF.md.example`.
- The skills under `skills/`, `.claude/skills/`, and `.agents/skills/`, and
  `docs/enforcement-matrix.md`.
- The workflows `agents-compliance.yml`, `agents-md-compliance.yml`,
  `sync-check.yml`, `immutable-conflict-check.yml`, `gate-integrity.yml`, and
  `policy-size-report.yml`.
- `CONTRIBUTING.md` and `SECURITY.md` from the template examples.
  - `SECURITY.md` diverges from the template. The file drops the agent policy
    security section, names the supported builds, and adds an application
    security section. `docs/template-drift.md` records the divergence.
  - `docs/agent-policy/security.md` diverges from the template. The
    vulnerability reporting reference names `SECURITY.md` instead of
    `SECURITY.md.example`.
- `.github/PULL_REQUEST_TEMPLATE.md` and `.github/ISSUE_TEMPLATE.md`.

## Declined

- `README.md`, `CHANGELOG.md`, `LICENSE`, `DRIFT.md`, and `adopters/`.
  These files belong to the source repository.
- `.github/FUNDING.yml`, `.github/workflows/release.yml`,
  `scripts/release.py`, and the `tests/test_release_*.py` modules. These
  files release the source repository. InfinitePrep releases through
  `build-packages.yml` and `packaging/release.py`.
- The template `quality-review-pr.yml` and
  `tests/test_quality_review_wiring.py`, plus
  `tests/test_review_adapter_portable.py` and
  `tests/test_review_response_narration.py`. InfinitePrep keeps its own review
  callers.
- `tests/test_policy_marker_names.py`. It reads
  `docs/agent-policy/source-orientation.md`. Only the source repository holds
  that file.
- `tests/test_scan_and_hash_wiring.py`. It counts the checker installs of the
  template workflow layout. `ci.yml` and `os-tests.yml` install the checkers
  without `--only-binary :all:` and `--require-hashes`.
- `SECURITY.md.example` and `CONTRIBUTING.md.example` changes. The
  InfinitePrep `SECURITY.md` and `CONTRIBUTING.md` diverge from the examples.
- `ci/`, the template `security-review-pr.yml`,
  `scripts/check_pr_review_response.py`,
  `tests/test_check_pr_review_response.py`, and
  `tests/test_security_review_wiring.py`. InfinitePrep keeps its own
  `abuzucom/agents-personas` callers. `tests/test_review_wiring.py` asserts
  the caller pins.
- `docs/pr-review.md` and `docs/gate-threat-model.md`. InfinitePrep keeps its
  own review document.

## Expected differences

- `Makefile` and `sync-check.yml` list InfinitePrep prose files. The
  `check_anti_delegation.py` step in `sync-check.yml` reads the same list.
- Every workflow keeps the InfinitePrep action pins. Dependabot moved those
  pins past the template pins.
- `tests/test_gate_pr_integrity_timeline.py` asserts the InfinitePrep
  `actions/github-script` pin.
- The application tests follow the template five-word test name rule.
- The `sync-check.yml` suite steps run in a shell that drops
  `GITHUB_EVENT_PATH`. A push payload otherwise fails the compliance scanner
  tests.
- `sync-check.yml` holds no `tests-windows` or `tests-macos` job. Both jobs
  run from `os-tests.yml`. That workflow omits the `edited` event.
  `sync-check.yml` probes the hook launchers in `check-sync` and skips
  AgentLint for a light change.
- `tests/test_push_event_workflow_wiring.py` and
  `tests/test_parallel_test_runner.py` read the suite steps from both
  workflows.
- `agents-md-compliance.yml` triggers on pull requests to `main` instead of
  pushes to `main`. The `check-sync` job of `sync-check.yml` runs on pull
  requests only. A push to `main` runs neither check.
- `.gitattributes` keeps `* text=auto eol=lf` and adds the policy entries.
- `pyproject.toml` excludes the copied gate files from Ruff. The agents lint
  in `ci.yml` covers those files.
- `docs/project-orientation.md` holds InfinitePrep facts.
- `hooks/workflow-scripts.txt` ends with eight InfinitePrep entries for
  `scripts/` and `packaging/` commands. It omits the four template
  `scripts/release.py` forms. InfinitePrep holds no `scripts/release.py`.
