# Error Handling

## Model-Visible Tool Errors

Read-only tools and validation failures return compact JSON strings. Expected failures use a
stable `error` code such as `message_not_found_in_snapshot`, so the model can choose a valid
follow-up. Keep these errors structured and free of tracebacks.

`ToolRuntime._external()` executes an external action, records gateway success or failure, and
returns a compact structured result. The Agent Loop may continue; `stay_silent` is the explicit
tool that invokes the terminal callback and returns `None`.

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
- `ToolRuntime._external(event, action_name, operation) -> str`
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
- A successful gateway result is encoded and persisted as `agent_action` before its structured
  tool result. Encoding and write failures preserve the successful QQ result and attach only a
  safe `fact_encode_failed:<type>` or `fact_persist_failed:<type>` diagnostic.
- Multiple actions in one Provider tool batch update the same run outcome from the complete
  accumulated action list; success plus failure becomes `action_partial`.
- A response containing content `A` and `send_message("B")` sends only `B`.
- A pure final assistant response is cleared and removed from `run_context.messages`; prior tool-call and tool-result messages remain.
- Content attached to a tool call remains internal Provider history and never reaches QQ.
- An attempted external action returns `{success, action}` or
  `{success:false, action, error, detail?}`. A non-empty `str(exc)` is the default `detail`
  and is recorded on the failed action; an empty diagnostic omits the optional field. For
  `send_message` and `reply_message`, a QQNT `sendMsg` failure queries the
  current bot through `get_group_member_info(no_cache=true)`. A future
  `shut_up_timestamp` produces
  `你已被禁言，无法发送消息；解禁时间：<ISO time>` using the same
  `Asia/Shanghai` ISO 8601 format as injected group-event timestamps and without the raw platform error;
  query failure preserves the original platform diagnostic. A safe `fact_error` reports action-fact encoding or
  persistence failure. It leaves cursor persistence to the unified response finalizer.
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
- QQ sender exception -> failed explicit action record plus structured error; a non-empty
  platform diagnostic is returned to the model as `detail`.
- QQNT `sendMsg` failure plus current bot `shut_up_timestamp > now` -> failed action with
  a concise Chinese mute detail and no raw platform error.
- Mute-status query failure, missing/non-numeric/out-of-range `shut_up_timestamp`, or expired
  timestamp -> failed action with the original platform error. Validate and format a future
  timestamp in one guarded block because the context renderer intentionally maps invalid event
  timestamps to the Unix epoch.
- Action fact encoding failure after a successful send -> `action_succeeded` plus safe encoding
  detail in a structured result.
- Action fact write failure after a successful send -> `action_succeeded` plus safe persistence
  detail in a structured result.
- Invalid snapshot target before a gateway attempt -> structured JSON error; the model may
  choose a valid target in a later step.
- Stale run before gateway admission -> failed action with `stale_run`; zero QQ calls.
- Run cleared after gateway admission -> completed QQ side effect plus
  `fact_persist_failed:RuntimeError`; stale terminal state writes are ignored.
- `stay_silent` outside an autonomous run ->
  `RuntimeError("tool called outside an autonomous group run")`.
- `stay_silent` after read-only tools -> terminal `silence_selected`; no QQ send.

### 5. Good/Base/Bad Cases

- Good: `content="A"` plus `send_message("B")` sends only `B` and terminates the loop.
- Good: a QQNT `sendMsg` failure while the current bot has a future
  `shut_up_timestamp` returns an explicit Chinese mute detail and `+08:00` ISO release time.
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
- History test asserts a suppressed pure assistant message is removed while the preceding tool chain remains.
- Core-executor test asserts a valid external handler produces a structured result while
  `stay_silent` produces AstrBot's terminal `[None]` signal.
- External-action failure tests assert ordinary exception text and `ActionFailed` platform
  diagnostics reach the model as `detail`, while an empty diagnostic omits the field.
- Mute-diagnostic tests assert `get_group_member_info` uses the current group and bot with
  `no_cache=true`, a future `shut_up_timestamp` returns only the concise Chinese mute
  detail with the shared `Asia/Shanghai` ISO time format, and query failure or an out-of-range
  timestamp preserves the original `ActionFailed` diagnostic.
- Runner integration asserts an action produces a QQ gateway call and `agent_action`, then a
  follow-up Provider call can select `stay_silent`.
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

Wrong: translate QQNT `sendMsg result=120` directly to `bot_muted`.

Correct: verify the current bot's live `shut_up_timestamp`, then replace the model-visible
platform error with the concise mute reason while that timestamp is in the future.

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
- Model tool: `poke_user(user_id: str) -> str`

### 3. Contracts

- Inbound Poke component: `{"type":"poke","target_id":"<QQ>"}`.
- Inbound semantic text: `[戳一戳 target=<QQ>]`; the record's `sender_id` is the actor.
- `is_directed_at_bot` is true when the Poke target equals the event `self_id`.
- A valid tool target appears at or before `snapshot_seq` as a record `sender_id`, an `at.user_id`,
  or a `poke.target_id`.
- The gateway calls NapCat `group_poke` with the current `group_id` and target `user_id`,
  then records `poke_user` as the external action name. AstrBot's `Comp.Poke` remains the
  inbound notice representation.
- The action returns a structured result after the gateway attempt.

### 4. Validation & Error Matrix

- Empty `user_id` -> `user_not_found_in_snapshot`; no gateway call.
- User appears only after `snapshot_seq` -> `user_not_found_in_snapshot`; no gateway call.
- User appears in the frozen snapshot -> call `group_poke` once.
- Missing `bot.call_action` or current group -> failed `poke_user` action plus structured
  `RuntimeError`.
- NapCat `group_poke` exception -> failed `poke_user` action plus structured error.

### 5. Good/Base/Bad Cases

- Good: sender `10001` appears at sequence 8 and snapshot is 10; `poke_user("10001")` sends once.
- Base: an inbound poke from `10001` to the bot stores actor `10001`, target `self_id`, and marks
  the event directed.
- Bad: user `10002` first appears at sequence 11 while snapshot is 10; the tool still reaches QQ instead of returning `user_not_found_in_snapshot`.

### 6. Tests Required

- Event-codec test asserts actor, target, semantic text, and directed detection.
- Gateway test asserts one `group_poke` Action receives the current group and requested user,
  while the normal message sender remains unused.
- Tool-schema test asserts `poke_user` requires only `user_id`.
- Snapshot test covers sender, mention, poke-target, and post-snapshot rejection.
- Prompt test asserts `poke_user` stays out of the generic System Prompt.

### 7. Wrong vs Correct

Wrong: persist only `[ComponentType.Poke]` and expose a tool that accepts any arbitrary QQ ID.

Correct: persist actor and target explicitly, then validate the target against the frozen snapshot
before crossing the QQ gateway.

Wrong: send `Comp.Poke` through the normal QQ message chain; NapCat produces no send element
and rejects the empty message body.

Correct: keep `Comp.Poke` for inbound notice decoding and use NapCat `group_poke` for the
outbound action.
