# Adoption
Use the canonical `AGENTS.md` as the policy source.

## Validation paths
Match the validation path to the change:

- Executable behavior. Write a failing test. Run it. Implement the fix.
- Executable configuration. Add a behavioral test before changing behavior.
- Policy, documentation, or comments. Run static validation before and after
  editing. Do not create an artificial behavioral test.

Run the focused test after implementation. Run related tests. Run the full
suite. Run lint and static policy checks. Verify hook and CI wiring. Review the
complete diff.

Run:

- `python scripts/sync.py --print-adoptable`
- `python scripts/sync.py`
- `python scripts/sync.py --check`
- `python scripts/check_gate_adoption.py`

Copy the complete gate set. Include hooks, registrations, shared modules,
tests, cited checkers, synchronization metadata, and policy copies.

## Adopt the local path gate whole
`scripts/check_local_paths.py`, its test, the
`.local-path-allowlist` that a human approved, and their hook and CI registrations form one gate.
Adopt them in one transaction and one pull request. A checker without its
allowlist is partial adoption under Rule 18.

Never land the allowlist after the checker. The trusted CI scan reads both
from the base branch. A checker merged first fails every later pull request.

An empty allowlist records a disclosure with no approved entries. A missing
allowlist means incomplete adoption. `check_gate_adoption.py` and
`complete_gate_adoption.py` enforce the pair.

### Local path disclosure
Run `--report` locally. Show every finding to the active human. Get a separate
decision on each proposed `<file><TAB><name>` entry. Never allowlist a runtime
identity match. Keep findings out of committed text and CI logs.

## Atomic multi-client adoption
Rule 18 and `docs/agent-policy/enforcement.md` define atomic adoption.

Edit `AGENTS.md` only. Regenerate synchronized copies. Do not edit generated
copies directly.

Record the canonical revision in controlled adopters. Keep local policy changes
separate from generated copies. Use a draft pull request for outbound
changes.
Create `docs/project-orientation.md` for the adopter's own commands, protected
paths, architecture, entry points, and operational notes. Keep source repository
facts out of that file. The synchronization and reinjection tools append it to
the policy copies and adoptable output.

The source repository may provide
`docs/agent-policy/source-orientation.md`. Its content must use the
`source orientation:start` and `source orientation:end` markers. Content between those
markers reaches local policy copies but never adoptable output. An adopter may
lack that marked content. Missing adopter orientation content fails closed.

Supporting files must remain regular files within the repository. Keep them
ASCII and below the policy size limit. Synchronization rejects missing,
oversized, non-ASCII, or escaping supporting files.

The source repository uses `scripts/sync.py` for copies and digests of shared files. `scripts/check_*.py` supplies portable checks. `hooks/` supplies
client enforcement. `tests/` covers checks, hooks, distribution, and wiring.
Client settings live under `.agents/`, `.claude/`, `.codex/`, and `.gemini/`.
Preserve checker flags, hook payloads, reusable workflows, and copied policy
files.

## Scope decisions
Keep helper functions and imports required by the request in scope.

Public APIs include exported functions, exported classes, endpoints, CLI flags,
and response schemas.

Dependency proposals state the name, version, purpose, and alternatives.
Reusable workflows under `uses:` count as dependencies. Pin actions and
workflows to full commit SHAs. Record known release versions in nearby
comments. Reject tags and moving branch references.

Verify the current branch, remote URLs, and relevant file contents before
inferring workflow scope.

Branch examples include `fix/branch-name-validation`,
`chore/synchronize-policy-copies`, and `docs/clarify-agent-branch-rules`.
Avoid random or opaque names such as `chore/kind-thompson`.

Do not leave `TODO`, `FIXME`, `XXX`, `HACK`, `later`, stubbed bodies, bare
`pass`, `...`, or unexplained `NotImplementedError`.

Install `scripts/check_branch_name.py`. Register it in pre-push and supported
client hooks. Run its tests in CI and pre-commit. Dependabot receives its
documented exemption from branch and commit message rules through trusted metadata.
Copy `scripts/trusted_bot_identities.py` with it. That registry lists every
bot exempt from branch rules. A third-party bot also needs its account ID in the event.

Branch adoption copies `scripts/check_branch_name.py`,
`scripts/read_git_state.py`, `scripts/trusted_git.py`,
`hooks/enforce_branch_name.py`, `hooks/_command_execution_gate.py`,
`hooks/_gate_core.py`, and `hooks/_bash_parser.py`. Register pre-push and every observable supported
client event. Claude also registers `SessionStart`, `UserPromptSubmit`, `Stop`,
and `SubagentStop`. Run `tests/test_enforce_branch_name.py` in CI and
pre-commit. Agent hooks use `--strict-agent-preflight`.

Preserve license attribution and source metadata. Do not require
uncontrolled mirrors to report usage or divergence to this repository.

## Workflow consent and published history
Obtain consent before each workflow that the command execution gate routes to
consent. Workflows the gate allows run without consent. Existing tests,
hooks, and client settings require consent for each act.

Never rewrite pushed history on a shared branch. Never force-push, rebase,
amend, or reset published commits without explicit human consent. Add new
commits instead. `--force-with-lease` receives no exception. Branch age does
not create an exception.

## Branch recovery
Block ordinary tools until strict preflight
passes. Block `claude/` targets, aliases, and metadata writes.
Detect rebase metadata before detached HEAD recovery. Permit only `git rebase
--abort`, `git rebase --continue`, or `git rebase --skip`. Rerun strict
preflight after recovery. For an invalid branch, use the exact approved
`git branch -m <type>/<kebab-description>` command. On a stable branch or a
detached state, use `git switch -c <type>/<kebab-description>`. On `master`,
use `git switch main` when `main` exists. Otherwise tell the active human that
`master` is not allowed and the repository needs conversion to `main`. Run no
chained command.

## Git identity recovery
If either value remains unset, Git builds an identity from the machine account
name and hostname. Git prints this warning and commits anyway:

`Your name and email address were configured automatically based on your
username and hostname`

Verify `git config user.name` and `git config user.email` before the first
commit. If either is absent, resolve the authenticated account through the
trusted wrapper. A GitHub connector account request qualifies. Derive
`<id>+<login>@users.noreply.github.com`. Show the
values and obtain approval before setting them in the current repository.
Never set them globally. If no authenticated account source exists, show at most five
untrusted candidates from at most 50 commits. Never select one automatically.

Copy `scripts/check_git_identity.py` and `scripts/trusted_gh.py`. Register the
checker as a pre-commit hook. Claude Code also copies
`hooks/enforce_git_identity.py` and registers it for `SessionStart` and
`PreToolUse` on `Bash`. Required pull request CI runs the checker.

When a commit already carries the wrong identity, report the defect and stop.
Correcting the identity rewrites history. A wrong author field
cannot provide consent. Git permits amendment before the first push.

Agent-assisted commits and pull requests must name the true active model in an
`Assisted-by: <model>` trailer or PR description marker alongside
`Co-authored-by: <tool>`. Neither holds an email. Never hallucinate, disguise,
or fabricate a model name.

## Handoff
Use `scripts/read_git_state.py` after consent. Obtain
consent before builds and before each workflow that the command execution gate
routes to consent. Record only safe
identifiers, current status, and verification methods. Omit secrets,
credentials, tokens, PII, and private vulnerability details.
