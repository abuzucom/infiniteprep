# Dependency updates

Dependabot proposes the version bumps. A reviewer turns each proposal into a
compliant pull request and closes the proposal. The proposals never merge.

`.github/dependabot.yml` holds the configuration.

## What Dependabot covers

Two ecosystems run on a weekly schedule.

- The `pip` ecosystem covers `pyproject.toml`, `requirements-lock.txt`, and `requirements-checkers.txt`.
- The `github-actions` ecosystem covers the action pins in `.github/workflows/`.

The `github-actions` ecosystem also covers the two reusable review workflows.
Dependabot updates a reusable workflow reference when the referenced workflow
file changes. The `abuzucom/foucault` repository changed its security review
workflow. A proposal moves that reference to a newer commit. The
`abuzucom/euler` repository left its quality review workflow unchanged. That
reference has no proposal.

A proposal that moves a reusable workflow reference changes the `uses:` line
only. The `audit_ref:` input beside it keeps the old commit. A reviewer sets
both to the same commit, because the workflow comment asks for that.

## The groups

The configuration groups the patch updates and the minor updates separately for
each ecosystem. A major update matches no group. Dependabot opens an individual
pull request for it.

A breaking release therefore cannot block the safe updates that share the same
ecosystem. A reviewer sees the major bump on its own.

## Why a proposal arrives red

The repository policy requires a person to approve every dependency change.
Two checks turn a proposal red on purpose.

- `tests/test_security_scan_workflows.py` holds the approved commits for `actions/dependency-review-action`, `github/codeql-action/upload-sarif`, and `microsoft/security-devops-action`. A proposal that bumps one of those actions requires an update to the approved commit record.
- `tests/test_review_wiring.py` holds the pinned commits for the two reusable review workflows and the SHA-256 digests of the adapter files under `ci/`. A proposal that bumps one of those workflows requires an adapter re-sync from the upstream repository and an update to the digests.

Both tests run inside the `ci`, `sync-check`, and `agents-compliance` suites.
A proposal that fails one of those tests fails every job that runs that suite.

`scripts/check_gate_pr_integrity.py` adds a third condition. A pull request
that touches `.github/workflows/` or `requirements-checkers.txt` needs the
`gate-change-approved` label. An active human applies the label after the
latest push. No agent applies that label.

A proposal needs no changelog entry. The version advance check in
`sync-check.yml` skips the dependabot bot. The combined pull request needs the
entry, because its author is a person.

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

## The version comments

A pin carries a version comment beside it, such as `# v7.0.1`. Dependabot
updates the commit SHA only. The comment keeps the old version after a
proposal moves the SHA.

A reviewer sets the comment to the version that the new SHA names. The comment
is the only record of the version beside the SHA. A stale comment misleads the
next reviewer.

`scripts/check_action_versions.py` resolves every pin to the upstream release
that its commit names. A comment that names another release fails the check.
The `ci` workflow runs that check on every pull request and push.

## The limits

`open-pull-requests-limit` caps the open proposals at ten per ecosystem.
Dependabot raises no further proposals until a proposal merges or closes.
Closing the proposals after each review keeps the limit clear for the next week.

The first run of the version updates created six proposals for the action
pins. A limit of five held the sixth back. The repository holds more distinct
action references than that. The limit now sits above the count.

The `ignore` key excludes a dependency permanently. The repository relies on
that key when a package must stay at one version.
