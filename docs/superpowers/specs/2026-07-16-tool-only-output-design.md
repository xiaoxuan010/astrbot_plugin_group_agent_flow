# Tool-only output design

Date: 2026-07-16
Status: Approved in conversation

## Context

Provider `content` has no portable semantic marker that distinguishes a group reply from
an internal control statement. Some models return an in-character reply while others return
text such as `No Action Needed`. Forwarding every non-empty `content` therefore exposes
model-dependent control text and can duplicate an explicit message tool.

The existing protocol already says ordinary assistant text is internal. The transport must
enforce the same contract.

## Decision

Only `send_message`, `reply_message`, and `react_message` may create visible QQ actions.
Ordinary Provider `content`, reasoning, errors, and internal tool status remain private.

- A response with content and no tool ends as `direct_output_suppressed` and sends nothing.
- A response with an external action tool sends only that tool action.
- A response with content plus an external action sends only the tool action.
- Read-only tools return structured results and may continue the Agent Loop.
- External action tools return AstrBot's `None` terminal signal after recording their result,
  so the current tool batch completes without another Provider request.

The system protocol states the invariant without listing concrete tool names: every action or
message intended to be visible in the group must use an available external action tool, while
ordinary assistant content remains internal. The current-cycle prompt repeats the choice between
an external action tool call and silent completion. Tool names and argument schemas remain owned
by the injected `ToolSet`. AstrBot decorates requests with conversation persona, safety, and tool
instructions after the plugin creates its initial prompt, so a dedicated lowest-priority request
hook deduplicates and moves the fixed protocol to the end of the fully decorated System Prompt
immediately before Provider execution.

## History contract

Suppressed final assistant content is cleared from the transport response. A pure assistant
message is marked `_no_save`, preventing later turns from assuming that an unsent message
reached the group. Content attached to a tool-call assistant message stays internal in the
Provider protocol history so tool names, IDs, arguments, reasoning signatures, and tool
results remain replay-compatible; it never crosses the QQ send boundary.

## Error handling

Gateway success and failure are both recorded in `_group_agent_external_actions`. An external
action attempt is terminal for the current cycle, which prevents automatic retries from
duplicating an uncertain QQ side effect. Read-only tool errors keep their structured result
contract and may be followed by another model step.

## Tests

Focused tests prove that direct content never reaches the original sender, suppressed pure
content is excluded from persisted history, explicit tool actions remain visible, and every
external action handler produces AstrBot's terminal `None` signal. A request-order test simulates
AstrBot-added persona and tool instructions and proves the fixed protocol appears once at the end.
Full validation covers pytest, Ruff, compileall, plugin import, and `git diff --check`.

## Superseded decision

This design supersedes `2026-07-15-model-content-delivery-design.md` and commit `73edaf5`'s
visible-content behavior.
