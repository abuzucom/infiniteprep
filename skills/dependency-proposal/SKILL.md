---
name: dependency-proposal
description: Propose a dependency change with fixed facts and wait for approval.
---

# Dependency proposal

Rule 9 requires approval before any dependency change.

## Proposal fields

- name
- exact version, or full commit SHA for an action or reusable workflow
- purpose
- alternatives, including the standard library and existing dependencies
- lockfile impact

## Steps

1. Present the fields.
2. Wait for approval.
3. Update the manifest and its lockfile together.
4. Record a known release version in a comment beside a SHA pin.

Reject tags and moving branch references.

## Client map

- Every client presents the proposal in one prompt.

## Enforcement

Tier 3 guidance. `scripts/check_action_pins.py` checks action pins in CI.

Tiers: `docs/enforcement-matrix.md`.
