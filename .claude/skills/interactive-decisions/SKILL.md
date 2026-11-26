---
name: interactive-decisions
description: Ask the active human one open choice at a time with one recommendation.
---

# Interactive decisions

AGENTS.md requires one recommendation with its reason for each open choice,
asked once. The agent makes no independent decision on an open choice.

## Steps

1. List the open choices that block the work. Settle facts by reading the
   code. A fact is not an open choice.
2. Ask one choice per prompt. Wait for the answer before the next prompt.
3. Give two to four options. Put the recommended option first. End its label
   with `(Recommended)`. State the reason in its description.
4. Make each question stand alone. Name the files, commands, and findings it
   depends on.
5. Never present options in prose. Use the client's structured question tool
   when one exists.
6. Apply the answer as given. A new open choice starts a new prompt.

## Client map

- Claude Code asks through `AskUserQuestion`. A hook denies a call with more than
  one question, fewer than two or more than four options, or no option
  marked `(Recommended)`. An advisory Stop hook blocks one stop when the
  final message offers choices in prose without a question call.
- Codex, Gemini, and Antigravity expose no question tool to hooks. Ask in
  chat with the same shape. Number the options and mark the recommended one.

## Enforcement

Tier 2 for the Claude Code question shape. The prose Stop hook is advisory.
Tier 3 guidance for every other client.

Tiers: `docs/enforcement-matrix.md`.
