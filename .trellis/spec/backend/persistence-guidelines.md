# Persistence Guidelines

## Storage Model

The plugin has no ORM or application database. `GroupFlowStore` in `store.py` owns two forms
of local persistence under `data/plugin_data/astrbot_plugin_group_agent_flow/`:

- Per-flow JSONL logs contain normalized group events with monotonic `seq` values.
- `state.json` contains conversation cursors, renderer assignments, cursor metadata,
  observation block windows, and per-run outcomes.

Flow filenames are SHA-256-derived so platform and group identifiers never become paths.
Use `GroupFlowStore` methods for every read or write; callers should not know log filenames.

## Write Rules

- Rewrite JSON and JSONL through a sibling `.tmp` file followed by `os.replace`, matching
  `_write_json_file()` and `write_records()`.
- Deduplicate incoming events by non-empty `message_id` before assigning a new sequence.
- Keep sequence values increasing even when retention trims old records.
- Keep each conversation cursor monotonically increasing. A delayed or stale run may finish
  after a newer run and must never move the cursor backwards.
- Persist a conversation's cursor and observation block window only after a successful run.
  Both writes occur under the per-flow lock after validating the run generation.
- Bound user-facing search limits to 1-100 records.
- Apply `max_seq` before the result limit so an observation cannot see messages newer than
  its frozen snapshot.

`main.py` serializes flow mutations with a per-flow `asyncio.Lock`. Store methods remain
synchronous and perform no `await`, which keeps each individual state update atomic within
the event-loop thread.

## Compatibility And Migration

Persisted records carry `schema_version`. Preserve readable old fields when evolving the
schema. `import_legacy_data()` copies the old plugin's logs/state once and writes
`legacy_migration.json`; never delete or rewrite the legacy source directory.

Malformed JSON state falls back to an empty object. Invalid JSONL lines are skipped while
valid records remain readable. Add migration and reload tests in `tests/test_store.py` for
every persistence contract change.

Avoid ad hoc file writes, path names derived directly from QQ IDs, and cursor advancement
beyond the run's `snapshot_seq`.

## Scenario: A terminal action enters the group fact stream

### 1. Scope / Trigger

An autonomous QQ action succeeds and returns AstrBot's terminal `None`. The action must remain
visible to later observation cycles even when AstrBot conversation history skips that terminal
tool call/result.

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
- The plugin appends the action under the per-flow lock before the terminal callback commits
  the original `snapshot_seq` cursor.
- The plugin validates `(flow_id, run_id, generation)` under the per-flow lock before gateway
  admission and again before appending the action fact.
- `include_pending_seq()` raises an existing pending upper bound and never creates a due time.
  A later inbound event naturally covers an action written while no pending snapshot exists.
- Persist and enqueue an inbound group message while holding the same per-flow lock. This closes
  the interval where an action could be appended after the message but before its pending state.
- `agent_action` and `group_message` records enter observation blocks together in increasing
  `seq` order. Earlier actions remain visible only while their blocks remain active.
- A repeated outcome write may update only the same `(run_id, flow_id, snapshot_seq)`. It keeps
  the initial `recorded_at` and recomputes outcome/detail from the complete action batch.

### 4. Validation & Error Matrix

- Gateway failure -> failed run action; no `agent_action` record.
- Gateway success plus action encoding failure -> successful run action with
  `fact_encode_failed:<ExceptionType>`; terminal `None`; no QQ retry.
- Gateway success plus JSONL write failure -> successful run action with
  `fact_persist_failed:<ExceptionType>`; terminal `None`; no QQ retry.
- Synthetic action ID passed to reply/react -> `message_not_found_in_snapshot`; zero gateway calls.
- Action bot sender passed to poke validation -> excluded from the eligible user set.
- Existing run ID with a different flow or snapshot -> outcome update rejected.
- Run invalidated by `/gaf_clear` before gateway admission -> zero gateway calls and zero writes.
- Gateway admitted before `/gaf_clear` -> the side effect may complete; stale fact, outcome,
  cursor, and window writes are rejected.

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
- Tool tests assert successful persistence, encode/write failures, terminal `None`, one Provider
  call, readable history, and synthetic target rejection.
- Renderer tests cover all four actions in all three renderer formats without exposing
  `msg=agent-action:...`.
- Request-hook tests assert the next Provider context contains the completed action.
- Observation tests advance the cursor through two terminal cycles and assert both later contexts
  still contain the same completed action ID and target.

### 7. Wrong vs Correct

Wrong: save only `action_succeeded` in `state.json` and rely on AstrBot conversation history or
adapter self-echoes to reconstruct the visible reply.

Correct: admit the current run under the flow lock, append a typed action fact to the same
ordered JSONL source after the side effect, extend only an existing pending snapshot, then
commit terminal state for the same generation.

## Scenario: A token-bounded observation window is committed

### 1. Scope / Trigger

A frozen snapshot contains at least one new model-visible record. The Provider receives a
recent JSONL suffix assembled from stable observation blocks.

### 2. Signatures

- `prepare_observation(..., max_context_tokens, rotation_retention_ratio) -> PreparedObservation`
- `GroupFlowStore.commit_observation(flow_id, conversation_id, seq, *, unified_msg_origin, renderer_name, blocks) -> None`
- `GroupRunCoordinator.clear_flow(flow_id) -> int`
- `GroupRunCoordinator.is_run_current(run_id, *, flow_id, generation) -> bool`

### 3. Contracts

- `context.max_context_tokens` is the single hard observation budget and defaults to 8192.
- Count renderer output through AstrBot Core `EstimateTokenCounter` after validating contexts
  as Core `Message` values.
- Each successful observation appends one latest block and persists its `start_seq` / `end_seq`
  with the renderer assignment.
- Rebuild historical blocks from JSONL and persisted boundaries. Missing retained boundaries
  invalidate the affected prefix and trigger a recent-history bootstrap.
- On overflow, remove oldest complete blocks until usage reaches
  `max_context_tokens * rotation_retention_ratio`, or only the latest block remains.
- A latest block above the hard limit loses oldest records first. A single oversized record
  retains identity metadata, a text tail, and a visible `get_message` recovery marker.
- Replace AstrBot conversation contexts with the selected blocks and reset conversation
  `token_usage` before Core prepares the Provider request.
- `/gaf_clear` removes logs, cursors, renderer assignments, windows, run outcomes, and the
  coordinator flow state inside one per-flow critical section.

### 4. Validation & Error Matrix

- Malformed, overlapping, or non-increasing block boundaries -> window rejected.
- Persisted block cannot be reconstructed after JSONL retention -> invalid prefix discarded;
  readable recent suffix remains eligible.
- Latest record exceeds budget -> deterministic metadata-preserving tail truncation.
- Minimum metadata and recovery marker exceed budget -> `ValueError` mentioning
  `max_context_tokens`; Provider request does not proceed.
- Old generation attempts outcome, cursor, window, or fact write -> write ignored or rejected.

### 5. Good/Base/Bad Cases

- Good: a 7800-token history plus a 900-token latest block rotates oldest complete blocks
  toward 4096 at the default settings, then persists the remaining stable prefix.
- Base: an under-budget run reuses every previous context byte and appends one latest block.
- Bad: remove one oldest message every cycle or append the plugin window to AstrBot's old
  assistant/tool history, causing repeated prefix-cache invalidation and duplicate context.

### 6. Tests Required

- Store reload, malformed window, retention loss, and flow clear coverage.
- Bootstrap, stable-prefix append, multi-block rotation, retention-ratio boundaries, latest
  block trimming, oversized-record recovery, and exact Core counter coverage.
- Request replacement, cursor/window commit, failed-run retry, and stale-generation rejection.

### 7. Wrong vs Correct

Wrong: cap each run by a message count, replay cursor-before actions separately, and let
AstrBot conversation history determine the remaining visible messages.

Correct: rebuild a token-bounded active block suffix from JSONL, replace request contexts,
commit cursor and block boundaries in one state-file replacement, and query older facts through
snapshot-bounded tools.
