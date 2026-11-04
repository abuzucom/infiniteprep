# Dependency updates

Dependabot proposes the version bumps. A reviewer turns each proposal into a
compliant pull request and closes the proposal. The proposals never merge.

`.github/dependabot.yml` holds the configuration.

## What Dependabot covers

Two ecosystems run on a weekly schedule.

- The `pip` ecosystem covers `pyproject.toml`, `requirements-lock.txt`, and `requirements-checkers.txt`.
- The `github-actions` ecosystem covers the action pins in `.github/workflows/`.

The reusable review workflows stay outside the reach of the `github-actions`
ecosystem. The `abuzucom/foucault` repository publishes no tag newer than
`v3.3.14`, and the `abuzucom/euler` repository publishes no tag at all. Both
repositories sit behind the pinned commits that the workflows use. Dependabot
proposes a newer tag only. Neither reference can move.

## The groups

The configuration groups the patch updates and the minor updates separately for
each ecosystem. A major update matches no group. Dependabot opens an individual
pull request for it.

A breaking release therefore cannot block the safe updates that share the same
ecosystem. A reviewer sees the major bump on its own.

## Why a proposal arrives red

The repository policy requires a person to approve every dependency change.
Three checks turn a proposal red on purpose.

- `agents-compliance.yml` runs `check_changelog.py --base --head`. The head version must exceed the base version. The proposal needs a changelog entry.
- `tests/test_security_scan_workflows.py` holds the approved commits for `actions/dependency-review-action`, `github/codeql-action/upload-sarif`, and `microsoft/security-devops-action`. A proposal that bumps one of those actions requires an update to the approved commit record.
- `tests/test_review_wiring.py` holds the pinned commits for the two reusable review workflows and the SHA-256 digests of the adapter files under `ci/`. A proposal that bumps one of those workflows requires an adapter re-sync from the upstream repository and an update to the digests.

`scripts/check_gate_pr_integrity.py` adds a fourth condition. A pull request
that touches `.github/workflows/` or `requirements-checkers.txt` needs the
`gate-change-approved` label. An active human applies the label after the
latest push. No agent applies that label.

## The review

The reviewer takes the following steps.

1. Open a branch from `main` with a compliant name.
2. Apply the proposed version changes.
3. Add the changelog entry. The version must exceed the version on `main`.
4. Update the approval records for every pin the proposal touched.
5. Apply the `gate-change-approved` label when the change reaches a protected path.
6. Open the pull request, review it, and merge it.
7. Close the Dependabot proposal.

## The branch name

Dependabot names its branches `dependabot/ecosystem/group`. The bot exemption
in `scripts/trusted_bot_identities.py` recognizes that prefix and no other.
The configuration keeps the default prefix so the exemption holds.

## Limits

`open-pull-requests-limit` caps the open proposals at five per ecosystem.
Dependabot raises no further proposals until a proposal merges or closes.
Closing the proposals after each review keeps the limit clear for the next week.

The `ignore` key excludes a dependency permanently. The repository relies on
that key when a package must stay at one version.
