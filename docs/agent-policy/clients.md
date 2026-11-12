# Client lifecycle
Inject complete policy context before every Gemini and Antigravity model
request. Keep client output within the client limit.

Claude loads `CLAUDE.md` natively. Built-in Explore and Plan agents skip that
file. Preserve both agents. Inject numbered chunks through `SubagentStart`.

Codex loads `AGENTS.md` natively. Set `project_doc_max_bytes` above the
canonical limit. Set `additionalContextLimit` to zero when supported.

Gemini uses `SessionStart` and `BeforeModel`. Gemini project hooks require
fingerprint trust and permit disablement.

Antigravity uses an ephemeral `PreInvocation` message. Its `PreToolUse`
payload must remain schema-safe. Do not emit unsupported `injectSteps` fields
from `PreToolUse`.

Default sandboxes isolate outbound network access. Request the client's
approved `external-network` path when `scripts/trusted_gh.py` reports a
connectivity failure. Execute a fresh attempt on active-human retry
instruction.

Client APIs differ. Do not claim coverage that the client cannot observe.
Repository hooks remain defense in depth only.

A multi-repository Claude Code cloud session starts above the clones. It loads
no repository hooks. Agents there run the branch checker manually. A setup
script running `pre-commit install --hook-type pre-push` restores the pre-push
check.

## Compaction events
Claude and Codex report compaction through the session payload `source`
field. The reinjection hook prepends a compaction directive to policy
context for those events.

Claude and Codex deliver policy through hook context. Gemini prepends a
system message. Antigravity uses an ephemeral message.

Gemini and Antigravity expose no compaction event to repository hooks. No
repository hook can verify disclosure, the stop, plan mode, or continuation
claims. An external harness must enforce them.
