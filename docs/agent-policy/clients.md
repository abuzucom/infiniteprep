# Client lifecycle
Inject complete policy context before every Gemini and Antigravity model
request. Keep client output within the client limit.

Every client follows the matching skill in `skills/`.

Claude loads `CLAUDE.md` natively. Built-in Explore and Plan agents skip that
file. Preserve both agents. Inject numbered chunks through `SubagentStart`.

Codex loads `AGENTS.md` natively. Set `project_doc_max_bytes` above the
canonical limit. Set `additionalContextLimit` to zero when supported.

Gemini uses `SessionStart` and `BeforeModel`. Gemini project hooks require
fingerprint trust and permit disablement.

Antigravity uses an ephemeral `PreInvocation` message. Its `PreToolUse`
payload must match its schema. Do not emit unsupported `injectSteps` fields
from `PreToolUse`.

Client APIs differ. Do not claim coverage that the client cannot observe.
Repository hooks remain defense in depth only.

The reinjection hook states external hook precedence on every lifecycle
event. No repository check verifies model compliance.

A multi-repository Claude Code cloud session starts above the clones. It loads
no repository hooks. The rule reaches it only through the synchronized policy
copies. Agents there run the branch checker manually. A setup script running
`pre-commit install --hook-type pre-push` restores the pre-push check. Start
each cloud session inside one repository. Its hooks then load natively.

Claude Code, Codex, and Gemini pass `session_id` to hooks. Their
`SessionStart` creates `.scratch/<id>/` and `.worktrees/<id>/`. Codex has
no native consent prompt. Its zone deletes still deny. Antigravity
passes no session id. The zone helper stays denied there. Clients without hooks
create the folders with `mkdir -p`.

Ask each open choice through the client's question tool. Claude Code hooks
check the `AskUserQuestion` shape and gate GitHub MCP writes. A Stop hook
flags prose choices as advice. Other clients expose neither to hooks.

## Compaction events
Claude and Codex report compaction through the session payload `source`
field. The reinjection hook prepends a compaction directive to policy
context for those events.

Claude and Codex deliver policy through hook context. Gemini prepends a
system message. Antigravity uses an ephemeral message.

Gemini and Antigravity expose no compaction event to repository hooks. No
repository hook can verify disclosure, the stop, plan mode, or continuation
claims. An external harness must enforce them.
