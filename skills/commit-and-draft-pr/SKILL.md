---
name: commit-and-draft-pr
description: Commit work and open a draft pull request.
---

# Commit and draft PR

## Before the first commit

Follow the `git-identity-recovery` skill.

## Stage

- Stage explicit repository-relative paths only.
- Do not use `git add -f`, `git add -A`, or `git add .`.
- Do not stage anything under `.scratch/` or `.worktrees/`.

## Scan

Run these on the staged files. On a finding, stop all commits and posts.
Report the file and line without the value. Recommend rotation for a
secret.

- `python scripts/check_secrets_heuristic.py <files>`
- `python scripts/check_local_paths.py <files>`

## Commit

1. Write the message to a file in `.scratch/<id>/`.
2. Subject: `type: description`, imperative, at most 50 characters, no
   trailing period. Types: feat, fix, chore, docs, test, ci.
3. Body: wrapped at 72 characters.
4. Trailers: `Co-authored-by: <tool>` and `Assisted-by: <model>` with no
   email. Name the true active model.
5. Run `git commit -F <message file>`.

Do not pass message text on the command line.

## Changelog

Advance the SemVer version in `CHANGELOG.md`. Keep one entry per UTC date.
Merge same-day changes into that entry under the new version.

## Push

Ask before each push. Name the branch explicitly:
`git push -u origin <branch>`.

## Draft PR

1. Read the PR template when present. Fill its headings.
2. Write the body to `.comment-drafts/<file>`.
3. Ask once. Then run
   `python scripts/trusted_gh.py run pr create --draft --title <title> --body-file .comment-drafts/<file>`.
4. Do not set labels.

## Client map

- Claude asks with the structured question tool.
- Codex, Gemini, and Antigravity ask in chat.

## Enforcement

Tier 3 guidance for staging and drafting. `check_commit_message.py`,
`check_commit_attribution.py`, `check_changelog.py`, and the agent prose
gate run in CI.

Tiers: `docs/enforcement-matrix.md`.
