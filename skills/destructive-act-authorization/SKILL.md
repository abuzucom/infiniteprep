---
name: destructive-act-authorization
description: Ask for and record authorization before any Rule 2 destructive act.
---

# Destructive act authorization

Rule 2 gates every target. That includes scratch folders and clones from
the current session.

## Steps

1. Restate the exact command.
2. List every target path or ref.
3. Ask once for that command and those targets.
4. After approval, run only that command.
5. Record the authorization, the command, and the UTC execution time in
   the session report.

## Client map

- In a Claude single-repository session, the destructive gates route the
  act to a prompt.
- Other clients ask in chat.

## Enforcement

Tier 3 guidance for the record. The destructive gates give tier 2
coverage where hooks load.

Tiers: `docs/enforcement-matrix.md`.
