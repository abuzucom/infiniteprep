---
name: policy-change-proposal
description: Propose and apply an agent policy edit with an itemized proposal.
---

# Policy change proposal

## Steps

1. Read all of `AGENTS.md` and every affected file in `docs/agent-policy/`.
2. Run static validation before the edit:
   - `python scripts/sync.py --check`
   - `python scripts/check_policy_size.py`
   - `python scripts/lint_style.py`
3. For each moved or changed rule, list:
   - exact source text
   - destination
   - retained requirement
   - semantic impact
   - enforcement impact
4. Get approval for each item.
5. Edit `AGENTS.md` and the linked documents only. Do not edit generated
   copies.
6. Run `python scripts/sync.py` to regenerate copies.
7. Run the step 2 checks again.

Keep every mandatory, security, authorization, and enforcement rule in
`AGENTS.md`. Do not weaken a critical rule to meet a size limit.

## Client map

- Every client presents each item in its own prompt.

## Enforcement

Tier 3 guidance. `sync.py --check` and the policy size check run in CI.

Tiers: `docs/enforcement-matrix.md`.
