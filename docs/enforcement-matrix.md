# Enforcement matrix

Each skill names its tier. A rule claims enforcement only in tier 1 or
tier 2. Tier 3 states its limitation.

## Tier 1 (blocking in CI and pre-commit)

| Rule | Check |
| --- | --- |
| Skill frontmatter allows only `name` and `description` | `scripts/check_gate_adoption.py` full mode |
| Each skill copy matches the canonical file | `scripts/sync.py --check`, `scripts/check_gate_adoption.py` |
| Every skill and copy exists | `scripts/check_gate_adoption.py` |
| Skills use file-based command forms | `tests/test_skill_command_forms.py` through `scripts/run_tests.py` |
| No `.scratch/` path enters a commit or PR diff | `scripts/check_forbidden_paths.py` |
| Shared skills and policy name no specific owner | `scripts/check_shared_neutrality.py` |
| Skill edits need the `gate-change-approved` label | `scripts/check_gate_pr_integrity.py` |

`tests/test_new_checker_wiring.py` fails when CI or pre-commit stops
calling a tier 1 checker.

## Tier 2 (Claude hooks in single-repository sessions)

| Rule | Hook |
| --- | --- |
| Writes below `skills/` route to consent | `hooks/require_consent.py`, `hooks/_gate_core.py` |
| Adoption stays complete on every tool call | `hooks/enforce_gate_adoption.py` with `--fast` |
| One question with one recommendation per call | `hooks/enforce_single_question.py` |
| GitHub MCP comments, merges, labels, and non-draft PRs deny | `hooks/deny_mcp_github_writes.py` |
| Prose choices without a question call block one stop (advisory) | `hooks/detect_prose_options.py` |

Multi-repository cloud sessions load no repository hooks.

## Tier 3 (guidance only)

No repository check observes these. Each skill states the limitation.

- Steward acts outside the hooks above
- Explicit `git add` paths
- No raw command or CI output in drafts or handoffs
- Branch recovery, compaction replan, test defect, dependency, and policy
  change procedures
- Handoff content. `scripts/check_handoff_content.py` runs locally only.
- The `mkdir` fallback for session folders
