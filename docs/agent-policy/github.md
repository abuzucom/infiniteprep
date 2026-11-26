# GitHub operations
Run hosted GitHub operations through:

`python scripts/trusted_gh.py run <gh arguments>`

The wrapper resolves `gh` outside the repository and verifies the authenticated
account through a fixed account request. Direct `gh` lookup remains denied
because shell lookup can select an executable inside the repository.

Commands bound to a repository receive a validated `--repo OWNER/REPOSITORY` target.
The wrapper resolves `origin` from the local checkout or worktree metadata.
The wrapper fails closed when that context is missing or unsafe. The wrapper
keeps `gh` execution in an external safe directory.

PR creation also receives a validated `--head OWNER:BRANCH` target
when no head option exists. Global options may precede the GitHub command.

Executable changes require a behavioral test. Required CI checks the changed
range and fails when an executable change lacks a changed test.

Read-only repo inspection, checks, workflow reads, and pull request
diffs remain available through the wrapper. GitHub clone and fetch use the
fixed commands `python scripts/trusted_git.py clone <github-url> <directory>`
and `python scripts/trusted_git.py fetch <repository> [refspec...]`. The
transport CLI rejects arbitrary Git options, shell expansion, and paths outside
the current workspace. A fetch remote must be a GitHub URL or a remote name.
Git receives only an allowlisted environment.

PR creation, issue creation, comments, reviews, reactions, forks,
stars, watches, releases, and hosted state changes require active human
consent when the operation is outbound or mutating.

Repository, release, run, secret, variable, and hosted resource deletions are
denied. Administrative merges, visibility changes, authentication changes,
token output, GraphQL mutations, and mutating API methods require the
applicable denial or consent path.

Agents never create, push, move, or delete Git tags. Agents never create,
edit, publish, or delete releases.

Managed sandboxes may set `127.0.0.1:9`, `localhost:9`, or `[::1]:9` as a
closed loopback proxy placeholder or block outbound sockets by default.
`scripts/trusted_gh.py` strips loopback discard proxies on port 9 and reports
connectivity failures with exit code 1. Exit code 2 marks a policy denial.
Exit code 1 marks a runtime, timeout, proxy placeholder, or network failure.
Exit code 1 never proves an authentication failure. On a network or
unclassified failure, report proxy variable names and retry the wrapper once
before the Git fallback. Do not change user proxy settings to bypass policy.

## Comments
Read and post issue and pull request comments over REST through these
fixed wrapper forms:

- `python scripts/trusted_gh.py comments <issue|pr> <number> [--page <n>]`
- `python scripts/trusted_gh.py comment <issue|pr> <number> --body-file
  .comment-drafts/<file>`

A read fetches one page of 100 comments. `--page` selects a page from 1 to
1000 and defaults to 1. A full page prints a note naming the next page.

Both forms target the `origin` repository only. The command gate allows the
read form on every branch state, a detached HEAD included. The post form asks
for active human consent and stays denied on a detached HEAD. The wrapper
accepts only a regular ASCII draft file directly in the git-ignored
`.comment-drafts/` directory. Real newlines in the draft carry the line
breaks. The wrapper opens the draft once through a descriptor that follows no
link and reads only that descriptor. It sends the validated bytes to `gh` on
standard input. A later change or link swap cannot alter the posted body.

Posting needs `os.open` support for `dir_fd` and `O_NOFOLLOW`. Windows lacks
both. The post form exits 2 there and the read form still works.

Comments have no Git equivalent. A failed comment operation has no fallback.
Report the blocker to the active human and stop.

GitHub MCP comment tools such as `add_issue_comment` bypass the wrapper.
Claude Code's `deny_mcp_github_writes.py` hook denies them. Other clients
lack that gate. Rule 17 still limits every comment to an
active human request. Read-only GitHub MCP tools may fetch issues, PRs, and
comments when the wrapper reports no authenticated account.

## REST write forms
Sessions without GraphQL run these wrapper forms over REST. Each asks:
- `run issue edit <n> --body-file <draft> [--title <text>]`
- `run issue create --title <text> --body-file <draft>`
- `run issue close <n> [--reason completed|not_planned]`
- `run issue reopen <n>`
- `run pr create --draft --title <text> --body-file <draft> [--base <b>]`
- `run pr edit <n> --body-file <draft> [--title <text>]`
`<draft>` names `.comment-drafts/<file>`. Other options reach `gh`
unchanged. A refused draft exits 2. The PR base defaults to the primary
branch.

## Pull request base
CI workflows run on every pull request base. Gate integrity runs the base
branch copy of `scripts/check_pr_base.py`. It fails a base outside the
stable branches. `forge_verdict` in
`hooks/_gate_core.py` denies `gh pr create` and `gh pr edit` with any other
`--base` or `-B` value. It reads `hooks/primary-branch.txt` and fails
closed on an unusable file. Coverage:
- `scripts/trusted_gh.py` runs the check on every call in every client.
  It exits 2. A `pr create` without a base gets `--base <primary>`, since
  gh otherwise targets the GitHub default branch.
- The Bash, PowerShell, and CMD gates run it for each client that
  registers them.
- `hooks/enforce_pr_base.py` checks the `base` of GitHub MCP
  `create_pull_request` and `update_pull_request` calls in Claude Code.
  Other clients expose no MCP tool calls to repository hooks.

## Git fallback
After a failed wrapper operation, one semantically equivalent Git fallback may
run after active human confirmation. Mark it with
`-c agents.githubFallback=confirmed`. The shell gate routes the marked command
to consent. The gate does not retain cross-process state. Human review enforces
the limit of one use.

## Checkout credentials
The four allowed exceptions permit persistence when the job:

- Pushes commits or tags.
- Pushes to another repository.
- Calls `gh` or a tool that uses the Git credential helper.
- Fetches private submodules or LFS objects.

The default `true` writes `GITHUB_TOKEN` to the runner Git configuration. Any
later step or third-party action can read it.

Check this rule before creating or modifying checkout steps. Do not refactor
unrelated workflows. For an allowed exception, retain `true` or omit the
setting. Add:

`# persist-credentials: true: this job <reason> (Rule 11 exception).`

Flag unrelated violations instead of fixing them under Rule 4.
`scripts/check_persist_credentials.py` checks the rule.

For external repositories, mentions of external accounts also require consent.

`scripts/check_external_pr_refs.py` and the pre-push hook block external
autolinks. The GitHub gate routes outbound commands to consent. Unreadable
origin ownership asks and does not pass. Other client APIs may not observe
every hosted surface.

## Labels
Rule 23 covers these surfaces:

- `--add-label` and `--remove-label` on `gh pr edit` and `gh issue edit`
- `--label` on `gh pr create` and `gh issue create`
- `gh label` commands
- GitHub MCP tools that carry a `labels` field
- REST and GraphQL label writes

The GitHub gate denies the `gh` label options, `gh label`, and `gh api`
mutating requests. In Claude Code `hooks/deny_mcp_github_writes.py` denies
MCP label fields. The rule binds every client without that coverage.

The wrapper refuses deny verdicts. It runs ask verdicts without confirming
consent. Clients without shell hooks receive no consent prompt.
