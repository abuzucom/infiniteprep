---
name: gate-denial-report
description: Report a gate denial without a variant retry or a gate change offer.
---

# Gate denial report

A denial is a policy outcome. It is not a defect.

## Steps

1. Report the gate name, the exact command or tool call, and the message.
2. Do not retry a variant. Do not offer a gate change.
3. Ask the human for the next step.

## Suspected defect

Report a defect in a designed denial only with these parts:
- the exact input
- the contradictory policy text
- a reproduction

A blocking gate does not authorize another act.

## Client map

- Every client applies the steps after any denial.

## Enforcement

Tier 3 guidance. No repository check observes retries.

Tiers: `docs/enforcement-matrix.md`.
