---
name: gate-adoption
description: Atomic adoption procedure for the shared gate set.
---

# Gate adoption

Rule 18 makes adoption atomic across every client.

## Steps

1. Pin the source to one full commit SHA. Do not adopt from a branch.
2. Verify each shared file digest against the source `policy-digests.json`
   entry for that release. Stop on any mismatch.
3. Show the human the full diff of every shared skill and hook.
4. Generate adoptable policy with `python scripts/sync.py --print-adoptable`.
5. Stage the candidate under `.gate-staging/<candidate>`.
6. Run `python scripts/complete_gate_adoption.py --candidate .gate-staging/<candidate>`.
7. Fill adopter files:
   - `docs/project-orientation.md` with the canonical revision
   - `hooks/primary-branch.txt` when the primary branch is not `main`
   - `hooks/trusted-owners.txt`, asking for each owner in its own prompt.
     An empty or absent list keeps every test runner at ask.
   - `hooks/workflow-scripts.local.txt` for adopter scripts, confirming each
     entry in its own prompt. An entry may not repeat a shared entry.
8. Run `python scripts/check_local_paths.py --report`. Show each finding.
   Get a separate decision per proposed allowlist entry.
9. Run the verification list:
   - `python scripts/check_gate_adoption.py`
   - `python scripts/check_hook_launchers.py`
   - `python scripts/check_hook_coverage.py`
   - `python scripts/sync.py --check`
   - `python scripts/sync.py --check-shared`

A failed step keeps the last verified set.

## Client map

- In every client, the transaction hook allows only fixed checks, reads,
  and `.gate-staging/` writes while adoption stays incomplete.

## Enforcement

Tier 1 for the required files through `check_gate_adoption.py` in CI.
Tier 3 for the digest and diff review steps.

Tiers: `docs/enforcement-matrix.md`.
