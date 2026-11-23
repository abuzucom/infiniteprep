# Pull request review wiring

Two model reviewers run on every infiniteprep pull request:

- `abuzucom/foucault` applies `AUDIT.md` for security review.
- `abuzucom/euler` applies `QUALITY.md` for code quality review.

Each reviewer posts a pull request comment and a check run. The
`security-review` check covers foucault. The `quality-review` check covers
euler.

## Pins

| Reviewer | Release | Commit |
|---|---|---|
| foucault | 3.8.9 | `9ec9681131841bae8bd80bdffdfcb673b36a6289` |
| euler | 0.9.0 | `940c53a39b7cc7345b1f6232d43bec7c4d7623df` |

Each caller pins the reusable workflow `uses:` reference and the policy ref
(`audit_ref` or `quality_ref`) to the same full commit SHA. Upgrade both
values together.

## Event flow

1. `.github/workflows/ci.yml` runs the wiring tests on the pull request.
2. `security-review-pr.yml` and `quality-review-pr.yml` trigger on completion
   of `ci`.
3. Each caller resolves the pull request from the workflow run head.
4. Each caller calls its pinned reusable workflow.

`workflow_run` runs only from the default branch. Both reviews start after
this wiring reaches `main`. Renaming the `ci` workflow stops both reviews.

## Files

- `.github/workflows/security-review-pr.yml` copies the foucault caller. It
  triggers on `ci` instead of `Immutable Compliance` and calls the pinned
  remote workflow.
- `.github/workflows/quality-review-pr.yml` copies the euler caller. It calls
  the pinned remote workflow. It grants `actions: read` because the reusable
  review job requests that permission. `exclude_paths` stays empty.
- `ci/build_pr_case.py`, `ci/build_retry_case.py`, `ci/run_model_command.py`,
  `ci/call_model.py`, `ci/model_providers.json`, and
  `scripts/check_pr_review_response.py` match the pinned foucault commit byte
  for byte. The foucault reusable workflow runs those files from this
  repository's checkout. The upstream preflight names three of the six files.
  Copy all six. A missing `ci/build_retry_case.py` fails the retry step.
- Euler loads its adapter from its own checkout at `quality_ref`. This
  repository holds no euler file copies.
- `tests/test_review_wiring.py` checks the triggers, the pins, the secret
  mapping, the fork handling, the action pins, and the SHA-256 digest of
  each copied adapter file.

## Large diffs

`ci/model_providers.json` carries one active profile, `ollama-large`, with
`minimax-m3.1-flash`. Input above that profile's 900000 token budget exits
with code 3 and sends no request. The foucault workflow then records a fixed
`NEEDS-HUMAN` verdict. With `fail_on_block: true`, that verdict fails the
`security-review` check. A human reviewer must assess the complete diff. A
diff above 3 MB takes the same path.

The adapter keeps its `large_input_provider` routing for registries that set
the key. The shipped registry sets no such key.

## Repository settings

Configure these settings in the GitHub repository:

- An `OLLAMA_API_KEY` repository secret. Both callers map it to
  `MODEL_API_KEY`.
- A `fork-review` environment with maintainer approval rules and a
  `MODEL_API_KEY` environment secret. Foucault fork reviews also need the
  `safe-to-review` label from a maintainer.
- Branch protection that requires a human approval. Never configure a model
  review check as the only required status. Prompt injection in the pull
  request can steer a model verdict.

The euler caller skips fork pull requests with a `quality-review` check
conclusion of `skipped`. Fork pull requests receive no repository secret.

## Upgrade procedure

1. Pick the new foucault or euler commit on its `main` branch.
2. Update the `uses:` pin, the policy ref, and the release comment together.
3. For foucault, copy the six adapter files from the new commit.
4. Update the commit and digest constants in `tests/test_review_wiring.py`.
5. Update the pin table above and `CHANGELOG.md`.
