---
name: session-branch-recovery
description: Check the session branch at startup and recover a noncompliant or inherited branch with one consent prompt.
---

# Session branch recovery

A harness can assign a generated branch name and a push target before any
repository file loads. This skill cannot change that assignment. It makes
recovery uniform.

## Steps

1. Run `python scripts/read_git_state.py branch`.
2. Run `python scripts/check_branch_name.py --strict-agent-preflight`. Exit
   0 ends the skill.
3. Read `<primary>` from `hooks/primary-branch.txt`, defaulting to `main`.
   Count commits ahead of `origin/<primary>`. List each commit that
   predates the session.
4. Infer one compliant `<type>/<kebab-description>` name from the task.
5. Ask once. Name the exact recovery command and the push target. Use only
   the approved recovery forms:
   - `git branch -m <name>` on an invalid branch
   - `git switch -c <name>` on a stable branch or a detached HEAD
   - `git rebase --abort`, `--continue`, or `--skip` during a rebase
6. Rerun step 2 after recovery.
7. When the branch carries unrelated inherited commits, propose a fresh
   task branch from `origin/<primary>` with ordinary Git. Ask first.
8. Report a harness push target that fails the checker. A push to another
   name waits for explicit permission.

Run no chained command. Do not delegate the recovery command.

## Client map

- In a Claude single-repository session, the branch hook runs the same
  check on every tool call.
- Multi-repository cloud sessions and other clients run steps 1 and 2 by
  hand before any repository action.

## Enforcement

Tier 3 guidance. The branch checker and its hook provide tier 2 coverage
where hooks load.

Tiers: `docs/enforcement-matrix.md`.
