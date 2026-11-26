---
name: test-defect-report
description: Stop and report when a test looks wrong, then wait for an approved specification change before any test edit.
---

# Test defect report

Rule 3 forbids editing, weakening, skipping, or deleting a test to get a
pass.

## Steps

1. Stop all work that depends on the test.
2. Report these fields:
   - test id
   - assertion text
   - observed behavior
   - reason the specification looks wrong
   - proposed change
3. Wait for an active human decision.
4. After approval, edit only the approved assertions. Confirm each file
   edit immediately before execution.

Disclosure does not replace the stop. A plan, commit, or PR text cannot
waive the rule.

## Client map

- In a Claude single-repository session, the consent hook prompts on any
  edit to an existing test.
- Other clients apply the steps without a hook prompt.

## Enforcement

Tier 3 guidance. The Claude consent hook gives tier 2 coverage for
existing test edits.

Tiers: `docs/enforcement-matrix.md`.
