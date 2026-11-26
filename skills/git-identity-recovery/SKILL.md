---
name: git-identity-recovery
description: Verify the Git author identity before the first commit and set the operator noreply address after approval.
---

# Git identity recovery

## Steps

1. Run `git config user.name` and `git config user.email`. Two values that
   match the operator noreply form end the skill.
2. Resolve the authenticated account with
   `python scripts/trusted_gh.py run api user --jq '{id, login}'`.
3. Derive `<id>+<login>@users.noreply.github.com`.
4. Show the name and email. Ask once.
5. After approval, set both in the current repository only:
   - `git -C <repository> config user.name <login>`
   - `git -C <repository> config user.email <id>+<login>@users.noreply.github.com`
6. Verify with `git config --local --get` for both keys.

Do not set the identity globally. Do not infer it from the environment,
hostname, task text, or history. Do not write the operator email into any
file. When no authenticated source exists, show at most five untrusted
candidates from at most 50 commits. Do not pick one.

Report a commit that already carries the wrong identity. Correcting it
rewrites history. That stays a human decision.

## Client map

- In a Claude single-repository session, the identity hook checks before
  `git commit`.
- Other clients run step 1 before the first commit.

## Enforcement

Tier 3 guidance. `scripts/check_git_identity.py` runs in pre-commit and CI.

Tiers: `docs/enforcement-matrix.md`.
