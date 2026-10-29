# Template drift

Source template: `abuzucom/agents` at commit `b452348`.

## Taken

- `AGENTS.md` and the eight generated tool copies.
- `docs/agent-policy/` except `source-orientation.md`.
- Every file under `hooks/` and the client settings under `.claude/`,
  `.codex/`, `.gemini/`, and `.agents/`.
- Every checker under `scripts/` except `check_pr_review_response.py`.
- The agents test suites under `tests/`.
- `shared-files.json`, `hook-coverage-baseline.json`, `tools/hook-trace/`,
  `Makefile`, `requirements-checkers.txt`, `.pre-commit-config.yaml`,
  `.claudeignore`, `.editorconfig`, `.local-path-allowlist`, and
  `plan/HANDOFF.md.example`.
- The workflows `agents-compliance.yml`, `agents-md-compliance.yml`,
  `sync-check.yml`, `immutable-conflict-check.yml`, and `gate-integrity.yml`.
- `CONTRIBUTING.md` and `SECURITY.md` from the template examples.

## Declined

- `README.md`, `CHANGELOG.md`, `LICENSE`, `DRIFT.md`, and `adopters/`.
  These files belong to the source repository.
- `ci/`, `security-review-pr.yml`, `scripts/check_pr_review_response.py`,
  `tests/test_check_pr_review_response.py`, and
  `tests/test_security_review_wiring.py`. InfinitePrep pins its review
  adapters to `abuzucom/foucault`. `tests/test_review_wiring.py` asserts those
  digests.
- `docs/pr-review.md` and `docs/gate-threat-model.md`. InfinitePrep keeps its
  own review document.
- `.github/PULL_REQUEST_TEMPLATE.md` and `.github/ISSUE_TEMPLATE.md`.

## Expected differences

- `Makefile` and `sync-check.yml` list InfinitePrep prose files.
- `.gitattributes` keeps `* text=auto eol=lf` and adds the policy entries.
- `pyproject.toml` excludes the copied gate files from Ruff. The agents lint
  in `ci.yml` covers those files.
- `docs/project-orientation.md` holds InfinitePrep facts.
