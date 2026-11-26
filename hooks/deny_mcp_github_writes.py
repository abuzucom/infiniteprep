#!/usr/bin/env python3
"""Gate GitHub MCP write tools that bypass the shell gates and the trusted wrapper.

Comment and review tools deny. The only comment path is
`scripts/trusted_gh.py comment`. Merges, auto-merge, a pull request that is
not a draft, and any label field deny under Rules 5 and 23. Every other
known write asks. `core.decide` turns that ask into a deny in unattended
modes. Read tools pass.

Claude Code registers this hook on PreToolUse for every MCP tool. The
action is the tool name after the last `__`. Every server prefix
therefore matches. Other clients expose no MCP tool calls to repository hooks.
"""
import sys

import _gate_core as core

GATE = "deny_mcp_github_writes.py"
MCP_PREFIX = "mcp__"
MCP_SEPARATOR = "__"
LABELS_FIELD = "labels"
DRAFT_FIELD = "draft"
COMMENT_ACTIONS = frozenset({"add_issue_comment", "add_reply_to_pull_request_comment",
                             "pull_request_review_write", "add_comment_to_pending_review",
                             "update_issue_comment"})
DENIED_ACTIONS = frozenset({"merge_pull_request", "enable_pr_auto_merge",
                            "create_pull_request_with_copilot"})
ASKED_ACTIONS = frozenset({"push_files", "create_or_update_file", "delete_file",
                           "fork_repository", "create_repository", "create_branch",
                           "issue_write", "sub_issue_write", "resolve_review_thread",
                           "unresolve_review_thread", "request_copilot_review",
                           "assign_copilot_to_issue", "actions_run_trigger",
                           "update_pull_request_branch", "disable_pr_auto_merge"})
CREATE_PULL_REQUEST = "create_pull_request"
UPDATE_PULL_REQUEST = "update_pull_request"
MALFORMED_REASON = "the gate cannot read the tool call or check it"
COMMENT_REASON = ("GitHub MCP comment and review tools bypass the wrapper. Post only on an "
                  "active human request through scripts/trusted_gh.py comment")
DENIED_REASON = "agents never merge, enable auto-merge, or open a pull request that is not a draft"
LABELS_REASON = "agents never add, remove, or change labels (Rule 23)"
DRAFT_REASON = "open every pull request as a draft (Rule 5)"
READY_REASON = "agents never mark a pull request ready without consent (Rule 5)"
ASK_REASON = "Repository workflow requires execution consent. The GitHub MCP tool writes to GitHub."


def _tool_action(tool_name: str) -> str:
    """Return the tool name after the MCP server prefix, or an empty string."""
    if not tool_name.startswith(MCP_PREFIX):
        return ""
    return tool_name.rsplit(MCP_SEPARATOR, 1)[-1]


def _pull_request_decision(action: str, tool_input: dict) -> tuple:
    """Return (decision, reason) for the pull request create and update tools."""
    if action == CREATE_PULL_REQUEST and tool_input.get(DRAFT_FIELD) is not True:
        return "deny", DRAFT_REASON
    if action == UPDATE_PULL_REQUEST and tool_input.get(DRAFT_FIELD) is False:
        return "deny", READY_REASON
    return "ask", ASK_REASON


def write_decision(payload: dict) -> tuple:
    """Return (decision, reason) for one PreToolUse payload."""
    tool_name = payload.get("tool_name")
    tool_input = payload.get("tool_input")
    if not isinstance(tool_name, str):
        return "deny", MALFORMED_REASON
    action = _tool_action(tool_name)
    if not action:
        return "", ""
    if not isinstance(tool_input, dict):
        return "deny", MALFORMED_REASON
    # Any present labels field writes labels. An empty list clears every label.
    if LABELS_FIELD in tool_input:
        return "deny", LABELS_REASON
    if action in COMMENT_ACTIONS:
        return "deny", COMMENT_REASON
    if action in DENIED_ACTIONS:
        return "deny", DENIED_REASON
    if action in (CREATE_PULL_REQUEST, UPDATE_PULL_REQUEST):
        return _pull_request_decision(action, tool_input)
    if action in ASKED_ACTIONS:
        return "ask", ASK_REASON
    return "", ""


def main() -> int:
    """Read one PreToolUse payload and emit a deny, an ask, or nothing."""
    try:
        payload = core.read_payload()
        if payload is None:
            return core.emit(GATE, "deny", MALFORMED_REASON)
        decision, reason = write_decision(payload)
        if decision:
            return core.decide(GATE, payload, decision, reason)
        return 0
    except Exception:
        # Claude Code treats a non-2 exit as a non-blocking error. An
        # unhandled exception would let the call through unchecked.
        return core.emit(GATE, "deny", "the gate raised an unexpected error. The gate "
                                       "cannot clear this call")


if __name__ == "__main__":
    sys.exit(main())
