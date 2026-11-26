# Pull request review wiring

Two model reviewers from `abuzucom/agents-personas` run on every infiniteprep
pull request:

- foucault applies its security policy.
- euler applies its code quality policy.

Each reviewer posts a pull request comment and a check run. The
`foucault / security` check covers foucault. The `euler / quality` check
covers euler.

## Pins

| Repository | Release | Commit |
|---|---|---|
| `abuzucom/agents-personas` | 0.22.0 | `c43f4592191b42b9f66072622b082fc2f4dc3217` |

Each caller pins the wrapper `uses:` reference and the `personas_ref` input
to the same full commit SHA. Upgrade both values together.

## Event flow

1. `.github/workflows/ci.yml` runs the wiring tests on the pull request.
2. `security-review-pr.yml` and `quality-review-pr.yml` trigger on completion
   of `ci`.
3. Each caller resolves the pull request from the workflow run head.
4. A docs-only pull request gets a skipped check and no model call.
5. Each caller calls its pinned wrapper.

Every completed `ci` run gets a review. No existing check run skips it. Any
workflow with `checks: write` can publish a check with any name and title.

`workflow_run` runs only from the default branch. Both reviews start after
this wiring reaches `main`. Renaming the `ci` workflow stops both reviews.

## Files

- `.github/workflows/security-review-pr.yml` calls
  `foucault-security-review.yml`.
- `.github/workflows/quality-review-pr.yml` calls
  `euler-quality-review.yml`. `exclude_paths` stays empty.
- The wrappers load the engine, the persona policy, and the model adapter
  from the pinned commit. This repository holds no adapter copies.
- `tests/test_review_wiring.py` checks the triggers, the pins, the
  permissions, the secret mapping, the check names, and the action pins.

## Large diffs

The engine splits a large diff into chunks under the mode caps. A file past
the caps counts as unreviewed and forces a `NEEDS-HUMAN` verdict.
`NEEDS-HUMAN` warns and concludes neutral. A human reviewer must assess the
complete diff.

## Forks

The engine publishes a skipped check for a fork pull request. It withholds
the provider key from the fork. The callers set no `fork_review` input.

## Repository settings

Configure these settings in the GitHub repository:

- An `OLLAMA_API_KEY` repository secret. Both callers map it to
  `MODEL_API_KEY`.
- Branch protection that requires a human approval. Never configure a model
  review check as the only required status. Prompt injection in the pull
  request can steer a model verdict.
- A branch protection rule that required `security-review` or
  `quality-review` needs the new names `foucault / security` and
  `euler / quality`.

## Upgrade procedure

1. Pick the new `abuzucom/agents-personas` commit on its `main` branch.
2. Update both `uses:` pins, both `personas_ref` values, and both release
   comments together.
3. Update `PERSONAS_COMMIT` in `tests/test_review_wiring.py`.
4. Update the pin table above and `CHANGELOG.md`.
