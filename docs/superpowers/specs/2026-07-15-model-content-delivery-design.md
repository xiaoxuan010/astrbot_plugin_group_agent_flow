# Model content delivery design

Date: 2026-07-15
Status: Superseded by `2026-07-16-tool-only-output-design.md`

## Context

The autonomous group Agent currently installs a send guard that blocks every AstrBot direct send. Visible messages therefore come only from explicit plugin tools. Some providers return normal `content` together with tool calls, and AstrBot exposes that content as an `llm_result` before executing the tool. The required behavior is to deliver that normal content as its own QQ message while preserving the separate tool action.

For a model response containing `content="A"` and `send_message(content="B")`, the group receives two messages in this order:

1. `A`
2. `B`

The plugin continues to suppress reasoning, Provider errors, and internal tool status output.

## Decision

Use AstrBot's native `llm_result` yield order and make the existing send guard selective. A normal LLM result may pass through the plugin-controlled boundary. Tool actions continue through `QQActionGateway`. This preserves the runner's content-before-tool ordering without introducing a second Agent loop or buffering all actions for later replay.

Two alternatives were rejected:

- End-of-run history replay sends tool output before earlier companion content.
- A plugin-owned action queue guarantees ordering but replaces a large part of AstrBot's runner behavior and delays tool side effects.

## Message semantics

| Model result | QQ behavior | History behavior | Run action |
|---|---|---|---|
| Normal `content` only | Send one message | Keep visible assistant text | `model_content` |
| Normal `content` plus tool call | Send content, then execute tool | Keep content on assistant tool-call message | `model_content`, then tool action |
| Multiple normal content results | Send each result in runner order | Keep each visible result | One `model_content` per send |
| `reasoning_content` only | Send nothing | Preserve Provider-required reasoning data | None |
| Provider or runner error | Send nothing | Preserve diagnostic state according to AstrBot | None |
| Empty content | Send nothing | Preserve protocol structure as needed | None |

Normal content and tool message content remain separate. The plugin performs no merge and no semantic deduplication.

## Architecture

### Selective send guard

`response_policy.install_send_guard()` retains the original platform sender and classifies every core send attempt. It forwards a message only when the current event result is an ordinary `LLM_RESULT` with non-empty visible content. It rejects reasoning chains, error/general results, tool status chains, and sends without an eligible event result.

The guard records each attempted visible model send through a callback supplied by `ToolRuntime`. Successful sends append a succeeded `model_content` entry to `EXTERNAL_ACTIONS_EXTRA`; failures append a failed entry with bounded diagnostic detail. A failed model-content send does not expose AstrBot's generated error text to QQ.

### Final plain response

AstrBot invokes the final response hook before the final non-streaming result is sent. `finalize_observation()` therefore emits a non-empty final `completion_text` through a dedicated `ToolRuntime.emit_model_content()` operation before classifying and persisting the run outcome. It then clears only the pending transport response to prevent the later native pipeline from sending the same content again.

The assistant history message remains saveable because the user has seen the content. The plugin no longer marks visible final content with `_no_save`.

### Companion content before a tool

For a response containing both content and tool calls, AstrBot's runner yields the `llm_result` before it enters tool execution. The selective guard forwards that result immediately and records `model_content`. When the runner resumes, the tool handler executes through `QQActionGateway`, producing the second message and its own action record.

This design uses AstrBot's non-streaming, unbuffered intermediate-result order used by the current deployment. The integration regression must exercise the yield-before-tool sequence. Any future use of buffered intermediate results requires an explicit compatibility test before enabling it for autonomous runs.

## Data flow

```text
Provider response
  -> AstrBot LLMResponse
  -> llm_result(content A)
  -> selective send guard
  -> original QQ sender
  -> record model_content
  -> tool call(send_message B)
  -> QQActionGateway
  -> original QQ sender
  -> record send_message
  -> persist run outcome and cursor
```

Reasoning follows a separate path and never reaches the original QQ sender.

## Error handling

- Empty or whitespace-only content is ignored and creates no action record.
- A QQ send exception records a failed `model_content` action with a bounded error string.
- Core Provider errors remain blocked by the guard and retain their existing logs.
- Tool failures continue to use structured JSON tool results and the existing action record contract.
- Run outcome classification consumes both `model_content` and tool action entries, so mixed success and failure produces `action_partial`.

## Persistence and history

The feature adds no storage schema. `EXTERNAL_ACTIONS_EXTRA` receives a new action name, `model_content`, using the existing `{action_name, status, detail}` shape. Persisted run outcomes remain unchanged.

Visible assistant content stays in AstrBot conversation history. Reasoning signatures and tool-call fields remain intact for Provider replay. Transport suppression must not mutate the already-appended visible assistant history message.

## Tests

Focused tests must prove:

1. An eligible LLM result passes through the guard and records `model_content`.
2. Reasoning, general errors, tool status, empty content, and sends without an eligible result remain blocked.
3. `content A` plus `send_message B` produces two sends in `A`, `B` order.
4. A pure final content response is sent once before run outcome persistence.
5. Visible final assistant content remains saveable in history.
6. A model-content send failure records `action_failed` and exposes no generated error message.
7. Existing tool-only sends, snapshot isolation, i18n, and natural silence continue to pass.

Full verification runs pytest, Ruff, compileall, plugin import, and `git diff --check`, followed by a NapCat real-device observation and log inspection.

## Scope boundary

This change covers normal model content delivery. Deterministically ending the Agent Loop after an external action remains the next response-lifecycle task. AstrBot core source and Provider adapters remain unchanged.
