# Persistence Guidelines

## Current Storage Model: SQLite Pending Buffer

`GroupFlowStore` in `store.py` owns one SQLite file at
`data/plugin_data/astrbot_plugin_group_agent_flow/group_agent_flow.db`. The active runtime
stores only `group_message` rows whose lifecycle is `pending -> inflight -> ack/delete`.
AstrBot Core conversation history owns assistant/tool/reasoning history; NapCat owns already
sent QQ history. Existing `logs/*.jsonl` and `state.json` are legacy read-only material and
are neither imported nor written after cutover.

### Scenario: SQLite Buffer claim and Core checkpoint acknowledgement

#### 1. Scope / Trigger

An autonomous group event must survive restart without duplicating Core conversation history
or treating a completed batch as a permanent local archive.

#### 2. Signatures

- `GroupFlowStore.append_pending(flow_id, record) -> AppendResult(seq, inserted)`
- `GroupFlowStore.claim_batch(flow_id, snapshot_seq, *, checkpoint_id=None, checkpoint_baseline_count=0) -> BatchSnapshot | None`
- `GroupFlowStore.ack_batch(batch_id) -> bool`
- `GroupFlowStore.requeue_batch(batch_id) -> bool`
- `GroupFlowStore.reconcile_inflight(flow_id, checkpoint_counts, *, requeue_unconfirmed=False) -> list[str]`

#### 3. Contracts

- SQLite uses `PRAGMA user_version=1`, WAL, foreign keys, a five-second busy timeout, short-lived
  connections, and `BEGIN IMMEDIATE` for writes.
- `flows.next_seq` is the next monotonic sequence and is not reset by ack or `/gaf_clear`.
- `message_keys(flow_id,message_id)` retains only deduplication metadata after message bodies are deleted.
- `buffer_messages` stores the full JSON envelope in `payload_json`; unknown fields/components round-trip.
- `claim_batch` atomically marks `pending` rows with `seq <= snapshot_seq` as `inflight` and returns an
  immutable `BatchSnapshot`; later arrivals remain pending.
- The plugin sets `event.extra["llm_checkpoint_id"]` to a scoped `gaf:` ID only when no existing non-empty
  ID exists. Core persists `_checkpoint` internally and strips it before Provider-facing messages.
- A newly observed checkpoint count acknowledges and deletes the batch body. A missing checkpoint with
  `requeue_unconfirmed=True` returns the batch to pending without changing seq.
- `agent_action` is rejected by `append_pending`; successful action/tool history remains Core-owned.

#### 4. Validation & Error Matrix

- non-`group_message` record -> `ValueError`, no SQLite row;
- duplicate non-empty message ID -> existing seq, `inserted=False`, no new body;
- positive `max_log_records` at capacity -> `BufferCapacityError`, inflight rows preserved;
- active inflight batch -> `ActiveBatchError`, no second claim;
- malformed DB/schema -> `StoreInitializationError`, legacy files untouched;
- ack/requeue repeated -> idempotent state result, no second body mutation.

#### 5. Good/Base/Bad Cases

- Good: claim rows 8-10, receive seq 11 during reasoning, current batch contains only 8-10.
- Base: Core history gains a new matching `_checkpoint`; next event deletes 8-10 and keeps seq 11 pending.
- Bad: append successful tool actions as new user facts, or put `batch_id` in `ProviderRequest.contexts`.

#### 6. Tests Required

- SQLite schema/reopen/WAL and corrupt-database tests;
- duplicate, concurrent seq, capacity, unknown-field round-trip, claim boundary, ack/requeue and clear tests;
- checkpoint baseline-count test proving an old same-ID segment does not ack a new batch;
- main/tool tests proving batch ID is private and Core checkpoint is the only ack signal.

#### 7. Wrong vs Correct

Wrong: advance a local history cursor and delete JSONL immediately after `on_llm_response`.

Correct: hold bodies as `inflight` until Core raw history contains the newly appended checkpoint, then ack/delete.

`main.py` still serializes flow mutations with a per-flow `asyncio.Lock`; SQLite transactions remain the
final cross-thread boundary.

## Historical architecture notes (archival; not implementation requirements)

The following records preserve prior JSONL/cursor/action-fact design discussions. They may explain old
commits, but new runtime code must follow the SQLite Buffer contract above and must not restore a renderer
configuration, JSONL persistence, or local `agent_action` facts.

### Core multi-role history paired with JSONL deltas

### 1. Scope / Trigger

An autonomous group run needs prior `assistant` tool calls, `tool` results, and reasoning without rendering an already-completed `agent_action` as a second user message.

### 2. Signatures

- `GroupFlowStore.get_history_cursor(flow_id, conversation_id) -> int | None`
- `GroupFlowStore.commit_observation(..., history_cursor: int) -> None`
- `prepare_observation(..., history_cursor: int | None = None) -> PreparedObservation`
- `suppress_direct_output(response, *, run_context=None) -> bool`

### 3. Contracts

- AstrBot `Conversation.history` owns raw `user`/`assistant`/`tool` messages and assistant `ThinkPart` reasoning.
- `state.json.history_cursors` records the greatest JSONL sequence already represented in Core history.
- Missing history cursor starts a clean bootstrap with `req.contexts=[]` and renders a
  token-bounded recent suffix selected from `(0, snapshot_seq]`; later runs retain Core
  contexts and append only records in `(history_cursor, snapshot_seq]`.
- Missing history cursor also resets `Conversation.token_usage=0`. Later runs retain the
  persisted Core token baseline and add `PreparedObservation.estimated_tokens` before the first
  Core context-limit check.
- `context.renderer` is read for every request. Its new value projects the next JSONL suffix immediately; existing Core multi-role history retains the original provider serialization.
- Incremental renderer input excludes `record_kind="agent_action"`; JSONL keeps it for audit, tools, and scheduling.
- A suppressed plain assistant is removed from Core history. Tool-call assistants and matching tool results remain in their original sequence.

### 4. Validation & Error Matrix

- Invalid/missing history cursor -> bootstrap; legacy Core history is not mixed in.
- Existing history cursor plus missing Provider usage -> retain the best available token
  baseline and add the next observation estimate; never replace it with zero.
- Stale run -> cursor and history cursor remain unchanged.
- `/gaf_clear` -> clear current Core conversation history before deleting plugin flow state.
- Provider lacking a reasoning representation -> Core adapter performs its native safe conversion; plugin never injects `think` blocks directly.

### 5. Good/Base/Bad Cases

- Good: `assistant(tool_calls)`, matching `tool(tool_call_id)`, then only new JSONL user delta reach the Provider.
- Base: a plain assistant response produces `direct_output_suppressed` and contributes no Core history entry.
- Bad: render the same `agent_action` as user after its tool result, causing the model to repeat the QQ reply.

### 6. Tests Required

- Store test commits and clears `history_cursor` with the ordinary observation cursor.
- Request test retains Core contexts only after bootstrap and excludes incremental action facts.
- Request test asserts bootstrap token usage is zero and incremental token usage equals the
  persisted baseline plus `PreparedObservation.estimated_tokens`.
- Response-policy test removes the terminal plain assistant while retaining prior tool-call and tool-result messages.
- Core-compatible test preserves `ThinkPart(think, encrypted)` through reload and request serialization.

### 7. Wrong vs Correct

Wrong: copy raw assistant/tool messages into JSONL and render every fact as `role=user`.

Correct: keep Core history as the raw multi-role source, keep JSONL as the ordered group-fact source, and join them only at the committed sequence waterline.

### Core persists a terminal tool chain without a final assistant response

### 1. Scope / Trigger

AstrBot Core's agent runner finishes through a terminal tool such as `stay_silent`. The tool
returns `None`, so `final_llm_resp` is also `None`, while `ProviderRequest.tool_calls_result`
contains the assistant tool call and matching tool result that must survive into the next
autonomous observation cycle.

### 2. Signatures

- `AgentSubStage._save_to_history(event, req, llm_response, all_messages, user_aborted)`
- `ProviderRequest.tool_calls_result: list[ToolCallsResult]`
- `ConversationManager.update_conversation(..., history, token_usage=<latest baseline>)`

### 3. Contracts

- When `llm_response is None` and `req.tool_calls_result` is non-empty, Core saves
  `all_messages` after checkpoint serialization even when `llm_checkpoint_id` is absent.
- The saved sequence retains the current user observation, assistant tool-call message, and
  matching tool-result message with the same `tool_call_id`.
- The same update persists `req.conversation.token_usage`, which the runner refreshes from the
  latest Provider `LLMResponse.usage.total` before tool execution.
- A valid `llm_checkpoint_id` continues to append `CHECKPOINT_FINISHED_ABNORMAL` for provider
  failures and other abnormal termination paths.
- `user_aborted`, `_no_save`, conversation absence, and conversation mismatch continue to
  suppress history writes.
- The plugin advances `history_cursor` only after the successful Core-backed run completes, so
  the cursor never claims that JSONL events exist in missing Core history.

### 4. Validation & Error Matrix

- `llm_response=None`, non-empty `tool_calls_result`, no checkpoint -> save the complete message
  chain with the latest `req.conversation.token_usage`.
- `llm_response=None`, empty `tool_calls_result`, no checkpoint -> skip the history write.
- `llm_response=None`, valid checkpoint -> save the complete message chain plus abnormal
  checkpoint marker with `token_usage=None`.
- `user_aborted=True` -> skip the history write regardless of tool results.
- Assistant `llm_response` -> use the normal assistant-response persistence path and preserve
  token usage.

### 5. Good/Base/Bad Cases

- Good: `stay_silent` ends the loop and the next cycle receives the prior user observation,
  assistant tool call, and successful tool result.
- Base: an empty response with no tool execution and no checkpoint leaves conversation history
  unchanged.
- Bad: advance the plugin's JSONL history cursor after a successful terminal tool while Core
  drops the tool chain, causing the next request to jump across unrepresented group events.

### 6. Tests Required

- Core regression test constructs real `ToolCall`, assistant tool-call, and tool-result message
  objects, calls `_save_to_history()` with `llm_response=None` and no checkpoint, and asserts the
  exact history and token baseline passed to `update_conversation()`.
- Core safety test calls the same path without `tool_calls_result` and asserts that
  `update_conversation()` is not awaited.
- Related runner and checkpoint suites must preserve existing abnormal-checkpoint, abort,
  no-save, and normal assistant behavior.

### 7. Wrong vs Correct

Wrong: treat every `llm_response=None` outcome without a checkpoint as empty and return before
persisting the already-executed tool chain.

Correct: use non-empty `ProviderRequest.tool_calls_result` as evidence of a completed terminal
tool chain and persist `all_messages` before returning.

### 1. Scope / Trigger

An autonomous QQ action succeeds and returns a structured tool result. The action must remain
visible to later observation cycles even when AstrBot conversation history is compacted or a
later Provider step ends the run.

### 2. Signatures

- `build_agent_action_record(event, *, run_id, action_index, action_name, action_result) -> dict`
- `GroupAgentFlowPlugin._persist_agent_action(flow_id, record, *, run_id, generation) -> int`
- `GroupRunCoordinator.include_pending_seq(flow_id, seq) -> bool`
- `GroupFlowStore.record_run_outcome(run_id, *, flow_id, snapshot_seq, outcome, detail="") -> bool`

### 3. Contracts

- New inbound records carry `record_kind="group_message"`; old records without the field use
  the same semantic default.
- Successful visible actions carry `record_kind="agent_action"`, synthetic
  `message_id="agent-action:{run_id}:{action_index}"`, `targetable=false`, bot sender identity,
  structured components, action target, `action_status="succeeded"`, and the originating run.
- `schema_version=2` identifies the shared envelope; `record_kind` identifies record semantics.
- The plugin appends the action under the per-flow lock before the unified run finalizer commits
  the original `snapshot_seq` cursor.
- The plugin validates `(flow_id, run_id, generation)` under the per-flow lock before gateway
  admission and again before appending the action fact.
- `include_pending_seq()` raises an existing pending upper bound and never creates a due time.
  A later inbound event naturally covers an action written while no pending snapshot exists.
- Persist and enqueue an inbound group message while holding the same per-flow lock. This closes
  the interval where an action could be appended after the message but before its pending state.
- Bootstrap selection admits `agent_action` and `group_message` records together in increasing
  `seq` order. Incremental selection excludes `agent_action` because Core history already owns
  the completed assistant/tool chain.
- A repeated outcome write may update only the same `(run_id, flow_id, snapshot_seq)`. It keeps
  the initial `recorded_at` and recomputes outcome/detail from the complete action batch.

### 4. Validation & Error Matrix

- Gateway failure -> failed run action; no `agent_action` record.
- Gateway success plus action encoding failure -> successful run action with
  `fact_encode_failed:<ExceptionType>` in its structured result; no QQ retry.
- Gateway success plus JSONL write failure -> successful run action with
  `fact_persist_failed:<ExceptionType>` in its structured result; no QQ retry.
- Synthetic action ID passed to reply/react -> `message_not_found_in_snapshot`; zero gateway calls.
- Action bot sender passed to poke validation -> excluded from the eligible user set.
- Existing run ID with a different flow or snapshot -> outcome update rejected.
- Run invalidated by `/gaf_clear` before gateway admission -> zero gateway calls and zero writes.
- Gateway admitted before `/gaf_clear` -> the side effect may complete; stale fact, outcome,
  cursor, and history-cursor writes are rejected.

### 5. Good/Base/Bad Cases

- Good: message seq 11 arrives during reasoning, reply action becomes seq 12, and pending seq
  advances from 11 to 12 before the next snapshot starts.
- Base: action seq 11 is written with no pending message; a later message seq 12 schedules a
  snapshot that includes both records.
- Bad: the action remains outside JSONL or outside the next pending upper bound, so the model
  sees the old user message without the completed reply fact.

### 6. Tests Required

- Codec tests cover all four action payloads, targets, components, synthetic ID, and bot identity.
- Store tests reload mixed legacy/group/action records and verify same-run outcome updates plus
  cross-route rejection.
- Coordinator tests cover message-before-action and action-before-message orderings.
- Tool tests assert successful persistence, encode/write failures, structured results, a
  follow-up Provider call ending in `stay_silent`, readable history, and synthetic target rejection.
- Renderer tests cover all four actions in all three renderer formats without exposing
  `msg=agent-action:...`.
- Request-hook tests assert the next Provider context contains the completed action.
- Observation tests advance the cursor through two terminal cycles and assert both later contexts
  still contain the same completed action ID and target.

### 7. Wrong vs Correct

Wrong: save only `action_succeeded` in `state.json` and rely on AstrBot conversation history or
adapter self-echoes to reconstruct the visible reply.

Correct: admit the current run under the flow lock, append a typed action fact to the same
ordered JSONL source after the side effect, extend only an existing pending snapshot, then let
the unified finalizer commit state for the same generation.

## Current XML observation projection

### 1. Scope / Trigger

The fixed XML delta renderer projects one frozen observation into one XML `role=user` block. The projection
consumes persisted SQLite Buffer `components`, so component fields are a storage-to-provider contract.

### 2. Signatures

- `_component_record(component) -> dict[str, Any]`
- `XmlDeltaRenderer.render(events: list[dict[str, Any]]) -> list[dict[str, Any]]`

### 3. Contracts

- The outer element is `<group_messages_delta group_id="…" group_name="…">`; `group_id` and `group_name` each use the first non-empty value in the batch.
- Inbound `Reply` components persist `message_id`, `sender_id`, `sender_name`, `timestamp`, and `text`. The three latter fields are optional for old JSONL records.
- `at` maps to `<mention>`; `user_id="all"` maps to `<mention all="true"/>`.
- `text`, `reply`, `image`, `face`, `poke`, `voice`, `video`, and `file` retain their component boundary and Buffer order. Unknown types map to `<component type="…"/>`.
- Every dynamic XML attribute and text node is escaped. Existing media URLs or local paths remain XML attributes; renderer projection does not attach binary media to the Provider request. Internal image `source_url` values are never rendered or returned by model-visible history tools.

### 4. Validation & Error Matrix

- Empty event list -> `[]`.
- Empty optional component field -> omit its XML attribute.
- Old Reply without new fields -> emit available `message_id` and `sender_id`; omit the missing attributes and nested text.
- Replayed action with empty `group_name` before a later group message -> use the later non-empty group name on the outer element.
- Unknown component -> `<component type="…"/>`; no renderer exception.

### 5. Good/Base/Bad Cases

- Good: a reply, mention, image, and text retain their order under one `<message>` element.
- Base: a historic reply with only ID fields remains model-visible as a self-closing `<reply>` element.
- Bad: deriving group metadata only from the first record causes empty `group_name` when replayed actions precede the current message.

### 6. Tests Required

- Codec test asserts new Reply fields serialize from AstrBot's `Reply` component.
- Renderer test asserts outer metadata, XML escaping, component ordering, all supported media types, all-mention, unknown fallback, and action metadata.
- Renderer regression test puts an empty-name action before a named group message and asserts the outer group name comes from the latter.

### 7. Wrong vs Correct

Wrong: parse `[引用消息(...)]` or `[At:...]` from the flattened AstrBot outline to reconstruct XML.

Correct: persist the structured component fields at ingestion, then render them directly in XML.

## Scenario: A token-bounded observation delta is committed

### 1. Scope / Trigger

A frozen snapshot contains at least one model-visible record. The Provider receives one
token-bounded SQLite Buffer suffix selected from the committed Core-history waterline.

### 2. Signatures

- `prepare_observation(..., max_context_tokens, history_cursor: int | None = None) -> PreparedObservation`
- `GroupFlowStore.commit_observation(flow_id, conversation_id, seq, *, unified_msg_origin, history_cursor: int) -> None`
- `GroupRunCoordinator.clear_flow(flow_id) -> int`
- `GroupRunCoordinator.is_run_current(run_id, *, flow_id, generation) -> bool`

### 3. Contracts

- `context.max_context_tokens` is the single hard observation budget and defaults to 8192.
- Count renderer output through AstrBot Core `EstimateTokenCounter` after validating contexts
  as Core `Message` values.
- A missing `history_cursor` selects records from `(0, snapshot_seq]`, drops the oldest records
  until the rendered suffix fits, replaces AstrBot conversation contexts, and resets
  conversation `token_usage`.
- An existing `history_cursor` selects records from `(history_cursor, snapshot_seq]`, excludes
  `record_kind="agent_action"`, preserves Core contexts, and adds the selected suffix estimate
  to the persisted token baseline.
- A selected suffix above the hard limit loses oldest records first. A single oversized record
  retains identity metadata, a text tail, and a visible `get_message` recovery marker.
- A successful run commits the ordinary cursor and `history_cursor=snapshot_seq` together.
  The state schema contains no `observation_windows` or renderer-specific block boundaries.
- `/gaf_clear` removes Buffer rows and flow metadata, clears the Core conversation, and resets the
  coordinator flow state inside one per-flow critical section.

### 4. Validation & Error Matrix

- Missing history cursor plus stale ordinary cursor or legacy window data -> ignore both and
  bootstrap from cursor `0`.
- Existing history cursor greater than or equal to `snapshot_seq` -> empty observation; Provider
  request does not proceed.
- Selected record exceeds budget -> deterministic metadata-preserving tail truncation.
- Minimum metadata and recovery marker exceed budget -> `ValueError` mentioning
  `max_context_tokens`; Provider request does not proceed.
- Old generation attempts outcome, cursor, history-cursor, or fact write -> write ignored or
  rejected.

### 5. Good/Base/Bad Cases

- Good: an existing history cursor at seq 20 and snapshot seq 24 renders only group messages
  from seq 21-24, while Core retains its prior assistant/tool chain.
- Base: a missing history cursor renders the newest suffix within 8192 tokens from all readable
  records through the frozen snapshot.
- Bad: restore legacy block boundaries or render incremental `agent_action` facts after Core
  already persisted their assistant/tool results.

### 6. Tests Required

- Store atomic cursor/history-cursor commit, fresh-state schema, reload, and flow clear coverage.
- Bootstrap from cursor `0`, incremental selection, legacy-window input ignored, oldest-record
  trimming, oversized-record recovery, and exact Core counter coverage.
- Request bootstrap/incremental token accounting, cursor/history-cursor commit, failed-run retry,
  and stale-generation rejection.

### 7. Wrong vs Correct

Wrong: restore persisted block windows, apply a second retention ratio, replay incremental
actions separately, or clear a trusted Core token baseline while retaining its contexts.

Correct: bootstrap a token-bounded recent suffix from cursor `0`, continue from the committed
history cursor with one SQLite Buffer delta and an adjusted Core token baseline, commit both
cursors through the store, and query older facts through snapshot-bounded tools.
