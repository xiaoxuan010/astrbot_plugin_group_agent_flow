# Error Handling

## Model-Visible Tool Errors

Read-only tools and validation failures return compact JSON strings. Expected failures use a
stable `error` code such as `message_not_found_in_snapshot`, so the model can choose a valid
follow-up. Keep these errors structured and free of tracebacks.

`ToolRuntime._external()` executes an external action, records gateway success or failure,
invokes the terminal state callback, and returns `None`. Both successful and failed gateway
attempts are terminal for that Agent Loop, preventing duplicate sends during automatic retries.

## Programming And Configuration Errors

Use exceptions for invalid internal state and unsupported capabilities:

- `GroupRunCoordinator.finish()` raises `KeyError` for an unknown run.
- `build_renderer()` raises `ValueError` for an unknown renderer.
- `QQActionGateway` raises `ValueError` for missing owner configuration and `RuntimeError`
  when the current platform lacks a required QQ API.
- Observation preparation raises `ValueError` when `max_context_tokens` cannot contain the
  minimum record metadata and `get_message` recovery marker.

Tests should assert the specific exception or error code. Do not convert programmer errors
into silent success.

## Scenario: Tool-only output crosses the AstrBot boundary

### 1. Scope / Trigger

AstrBot may yield ordinary `content` before tool execution or as the final assistant result.
Different Providers use that field for role-play replies or control statements such as
`No Action Needed`, so only explicit QQ action tools may cross the visible boundary.

### 2. Signatures

- `install_send_guard(event) -> None`
- `record_external_action(event, *, action_name: str, success: bool, detail: str = "") -> None`
- `suppress_direct_output(response, *, run_context=None) -> bool`
- `ToolRuntime._external(event, action_name, operation) -> None`
- `build_agent_action_record(event, *, run_id, action_index, action_name, action_result) -> dict`
- `stay_silent(event) -> None`
- `classify_run_outcome(external_actions, *, silence_selected, had_direct_output) -> str`

### 3. Contracts

- Ordinary Provider content and every core `event.send()` attempt are blocked.
- `AGENT_PROTOCOL_PROMPT` requires every group-visible action or message to use an available
  external action tool. It does not enumerate tool names; `ToolRuntime.build_tool_set()` owns
  the model-visible names, descriptions, and argument schemas.
- `OBSERVATION_CYCLE_PROMPT` repeats the immediate choice between an external action tool call
  and the no-argument `stay_silent` control tool.
- `_system_prompt()` creates the plugin-owned prompt with optional custom text before
  `AGENT_PROTOCOL_PROMPT`.
- AstrBot and other request hooks may append conversation persona, safety, context, and tool
  instructions. The lowest-priority `finalize_agent_protocol()` hook removes duplicate fixed
  protocols and places the protocol at the end of `req.system_prompt` before Provider execution.
- Explicit action tools call `tool_send()` through `QQActionGateway` and record the actual
  action name with `succeeded` or `failed` status.
- A successful gateway result is encoded and persisted as `agent_action` before the terminal
  callback. Encoding and write failures preserve the successful QQ result and attach only a
  safe `fact_encode_failed:<type>` or `fact_persist_failed:<type>` diagnostic.
- Multiple actions in one Provider tool batch update the same run outcome from the complete
  accumulated action list; success plus failure becomes `action_partial`.
- A response containing content `A` and `send_message("B")` sends only `B`.
- A pure final assistant response is cleared and its runtime message is marked `_no_save`.
- Content attached to a tool call remains internal Provider history and never reaches QQ.
- An attempted external action invokes the terminal callback, then returns `None` so AstrBot
  ends the Agent Loop without another Provider request.
- `stay_silent` sets `_group_agent_silence_selected=True`, invokes the same terminal callback,
  records `silence_selected`, advances the pending cursor, and returns terminal `None` without
  sending to QQ or appending `_group_agent_external_actions`.
- Outcome priority is external action status, `silence_selected`,
  `direct_output_suppressed`, then `no_action`.

### 4. Validation & Error Matrix

- Empty, whitespace, role-play, or control-statement content -> no send and no action record.
- `reasoning` chain -> blocked with no action record.
- General/provider error result -> blocked without hiding a previous persisted assistant message.
- Internal tool status chain -> blocked with no action record.
- QQ sender exception -> failed explicit action record followed by terminal `None`.
- Action fact encoding failure after a successful send -> `action_succeeded` plus safe encoding
  detail followed by terminal `None`.
- Action fact write failure after a successful send -> `action_succeeded` plus safe persistence
  detail followed by terminal `None`.
- Invalid snapshot target before a gateway attempt -> structured JSON error; the model may
  choose a valid target in a later step.
- Stale run before gateway admission -> terminal failed action with `stale_run`; zero QQ calls.
- Run cleared after gateway admission -> completed QQ side effect plus
  `fact_persist_failed:RuntimeError`; stale terminal state writes are ignored.
- `stay_silent` outside an autonomous run ->
  `RuntimeError("tool called outside an autonomous group run")`.
- `stay_silent` after read-only tools -> terminal `silence_selected`; no QQ send.

### 5. Good/Base/Bad Cases

- Good: `content="A"` plus `send_message("B")` sends only `B` and terminates the loop.
- Good: a read-only lookup followed by `stay_silent()` sends nothing, records
  `silence_selected`, and advances the cursor.
- Base: `No Action Needed` with no tool sends nothing and records `direct_output_suppressed`.
- Bad: a Provider error reaches the guard and is discarded before the original QQ sender.

### 6. Tests Required

- Prompt contract tests assert generic mandatory tool wording, absence of concrete tool names,
  fixed-protocol ordering after custom persona text and AstrBot-added instructions, one protocol
  copy in the final request, and runtime use of the cycle prompt.
- Policy test asserts ordinary LLM content creates zero original-sender calls.
- Tool boundary test asserts only `B` reaches the original sender for content `A` plus tool `B`.
- Isolation tests assert reasoning and general results create zero sends.
- History test asserts a suppressed pure assistant message is marked `_no_save`.
- Core-executor test asserts a valid external handler produces `[None]`, the AstrBot terminal
  signal, while read-only handlers still return structured JSON.
- Runner integration asserts a terminal action produces one Provider call, one QQ gateway call,
  one `agent_action`, and no follow-up Provider request.
- Batch tests assert later actions update the same run to the final succeeded/failed/partial
  classification and keep safe fact diagnostics.
- Silence-tool tests assert an empty object schema, marker-before-callback ordering, `[None]`,
  zero QQ effects, zero external action records, `silence_selected`, and cursor advancement.
- Full suite asserts existing tool, snapshot, persistence, renderer, and i18n behavior.

### 7. Wrong vs Correct

Wrong: infer visible-message intent from non-empty Provider `content` and forward it to QQ.

Correct: block every core send, expose visible behavior through explicit QQ action tools, and
return AstrBot's terminal `None` after an external action attempt.

Wrong: duplicate the current action/read tool names inside the system prompt, allowing prompt
text and `ToolSet` definitions to drift independently.

Correct: state the generic external-action-tool invariant in the prompt and keep concrete tool
metadata in `ToolRuntime.build_tool_set()`.

Wrong: rely on empty assistant content as the only silence protocol for Providers that always
emit text.

Correct: require `stay_silent()` as the explicit zero-side-effect terminal choice and keep
ordinary text classified as `direct_output_suppressed`.

Avoid broad exception handling around pure logic. At external QQ boundaries, record enough
detail for diagnostics while keeping credentials and full message content out of logs.

## Scenario: A stale scheduler produces an empty snapshot

### 1. Scope / Trigger

Plugin hot reload can leave an older waiting coroutine alive while a new plugin instance
consumes a higher snapshot. The same empty projection also occurs when a persisted adapter
event contains neither text nor components.

### 2. Signatures

- `prepare_observation(...) -> PreparedObservation`
- `GroupAgentFlowPlugin.inject_snapshot(event, req) -> None`
- `GroupFlowStore.set_cursor(flow_id, conversation_id, seq, *, unified_msg_origin) -> None`
- `ContextRenderer.render([]) -> []`

### 3. Contracts

- `PreparedObservation.source_seqs == ()` means the Provider has no model-visible input.
- `inject_snapshot()` records run outcome `empty_snapshot_skipped`, commits the target cursor,
  calls `event.stop_event()`, and returns before assigning `req.func_tool`.
- `set_cursor()` stores `max(current_cursor, requested_seq)` so a delayed run cannot regress
  state.
- Records with empty `text` and empty `components` stay in JSONL for diagnostics and are
  removed from the model projection.

### 4. Validation & Error Matrix

- `cursor >= snapshot_seq` -> stop before Provider; cursor unchanged.
- Snapshot contains only contentless transport records -> stop before Provider; cursor advances
  through the diagnostic records.
- Renderer receives `[]` -> return `[]`, never an empty user message or tag block.
- Snapshot contains at least one model-visible record -> inject contexts and the fixed tool set.

### 5. Good/Base/Bad Cases

- Good: hot-reload run for snapshot 43 arrives after cursor 44 and records a skipped run with
  cursor 44.
- Base: one normal group message produces one or more non-empty user contexts.
- Bad: `<group_messages_delta>\n\n</group_messages_delta>` reaches the Provider and spends a
  reasoning cycle without an observed message.

### 6. Tests Required

- Renderer test asserts all three renderers return `[]` for empty input.
- Observation test asserts a contentless transport record produces no contexts while retaining
  `target_cursor`.
- Hook test asserts the event is stopped, tools remain unassigned, and the skipped outcome is
  persisted.
- Store test writes cursor 44 followed by 43 and asserts the result remains 44.

### 7. Wrong vs Correct

Wrong: inject an empty tag block and overwrite cursor 44 with snapshot 43 when the stale run
finishes.

Correct: stop the request before Provider execution, persist `empty_snapshot_skipped`, and keep
cursor 44.

## Scenario: QQ poke crosses ingestion and action boundaries

### 1. Scope / Trigger

NapCat emits group pokes as OneBot notice events. AstrBot exposes the actor as the event sender
and the target through `Comp.Poke.target_id()`. The plugin must preserve both identities and only
allow the Agent to poke a QQ identity already visible in its frozen snapshot.

### 2. Signatures

- `extract_group_event(event, *, max_text_chars=4000) -> dict[str, Any]`
- `ToolRuntime._user_in_snapshot(flow_id, user_id, snapshot_seq) -> bool`
- `QQActionGateway.poke_user(event, *, user_id: str) -> dict[str, Any]`
- Model tool: `poke_user(user_id: str) -> None | str`

### 3. Contracts

- Inbound Poke component: `{"type":"poke","target_id":"<QQ>"}`.
- Inbound semantic text: `[戳一戳 target=<QQ>]`; the record's `sender_id` is the actor.
- `is_directed_at_bot` is true when the Poke target equals the event `self_id`.
- A valid tool target appears at or before `snapshot_seq` as a record `sender_id`, an `at.user_id`,
  or a `poke.target_id`.
- The gateway sends `MessageChain([Comp.Poke(id=user_id)])` through `tool_send()` and records
  `poke_user` as the external action name.
- The action returns AstrBot's terminal `None` after the gateway attempt.

### 4. Validation & Error Matrix

- Empty `user_id` -> `user_not_found_in_snapshot`; no gateway call.
- User appears only after `snapshot_seq` -> `user_not_found_in_snapshot`; no gateway call.
- User appears in the frozen snapshot -> send one native Poke component.
- QQ sender exception -> failed `poke_user` action followed by terminal `None`.

### 5. Good/Base/Bad Cases

- Good: sender `10001` appears at sequence 8 and snapshot is 10; `poke_user("10001")` sends once.
- Base: an inbound poke from `10001` to the bot stores actor `10001`, target `self_id`, and marks
  the event directed.
- Bad: user `10002` first appears at sequence 11 while snapshot is 10; the tool still reaches QQ instead of returning `user_not_found_in_snapshot`.

### 6. Tests Required

- Event-codec test asserts actor, target, semantic text, and directed detection.
- Gateway test asserts one `Comp.Poke` with the requested `target_id` reaches the original sender.
- Tool-schema test asserts `poke_user` requires only `user_id`.
- Snapshot test covers sender, mention, poke-target, and post-snapshot rejection.
- Prompt test asserts `poke_user` stays out of the generic System Prompt.

### 7. Wrong vs Correct

Wrong: persist only `[ComponentType.Poke]` and expose a tool that accepts any arbitrary QQ ID.

Correct: persist actor and target explicitly, then validate the target against the frozen snapshot
before crossing the QQ gateway.
