# Quality Guidelines

## Current SQLite Buffer Contract

The active runtime uses the plugin-owned SQLite Buffer contract in
`persistence-guidelines.md`: only `group_message` rows in `pending`/`inflight` are
stored locally. Core conversation history owns assistant/tool/reasoning history and
successful external actions; no `agent_action` row is appended by the active runtime.
The JSONL/cursor/action-fact bullets and scenarios below are retained as legacy review
context only and must not override the current SQLite lifecycle.

## Required Patterns

- Keep snapshot isolation end to end. Context preparation, history search, message lookup,
  reply, and reaction targets must all enforce `seq <= snapshot_seq`.
- Route every externally visible autonomous action through the plugin-controlled send
  boundary. Only `send_message`, `reply_message`, `react_message`, and `poke_user` may reach QQ. Keep
  ordinary `LLM_RESULT` content, reasoning, Provider errors, and internal tool status behind
  the send guard.
- Keep renderer behavior deterministic. Every request uses the XML delta block renderer;
  Core multi-role history remains in its original form while only the next SQLite Buffer suffix is
  projected into one XML `user` block.
- Empty renderer input produces no user message. A stale or contentless snapshot stops in
  `OnLLMRequestEvent` before the Provider receives a request, and cursor updates remain
  monotonic across delayed runs.
- On a missing history cursor, replace AstrBot conversation contexts with the plugin
  token-bounded recent SQLite Buffer suffix selected from cursor `0` and reset conversation token
  usage. On an existing history cursor, preserve Core contexts, render only the waterline
  delta, and add its estimate to the persisted token baseline before Provider preparation.
- Preserve structured message metadata in storage even when a renderer projects it to text.
- Keep successful external actions in the current run event extras and Core conversation history;
  never append them as SQLite Buffer facts.
- Build one observation suffix from SQLite Buffer rows in increasing sequence order. Use Core
  `EstimateTokenCounter` and the configured hard token budget; trim oldest selected records
  when the suffix exceeds the budget. Keep earlier facts accessible through snapshot-bounded
  history tools.
- Validate run generation under the per-flow lock before gateway admission and terminal state
  writes. Clear persistent and coordinator flow state in the same critical
  section.
- Keep `.astrbot-plugin/i18n/zh-CN.json` and `en-US.json` aligned with
  `_conf_schema.json`: every section and field has a localized `description`, schema
  `hint` and `labels` entries have matching localized entries.

## Testing

Use pytest and `pytest.mark.asyncio` for async gateway/tool behavior. New behavior starts with
a focused failing test and then the smallest implementation. Tests should exercise real pure
modules and use fakes only at AstrBot/QQ network boundaries.

Run from the plugin root:

```powershell
$env:PYTHONPATH='D:\Project\2026\AstrBot'
D:\Project\2026\AstrBot\.venv\Scripts\python.exe -m pytest -q
D:\Project\2026\AstrBot\.venv\Scripts\python.exe -m ruff check .
D:\Project\2026\AstrBot\.venv\Scripts\python.exe -m compileall -q .
```

Generated Trellis and Codex assets are excluded through `pyproject.toml`; project source and
tests remain covered by `ruff check .`.

Also import `astrbot_plugin_group_agent_flow.main` with both the project parent and AstrBot on
`PYTHONPATH` after changing imports, decorators, or metadata.

## Review Checklist

- Messages arriving during reasoning are reserved for the next snapshot.
- Inbound `append_record()` and coordinator `enqueue()` execute under one per-flow lock, so every
  action ordering is covered by the next valid pending snapshot.
- A missing history cursor ignores stale ordinary cursors and legacy window data, then rebuilds
  from cursor `0`. An existing history cursor renders only `(history_cursor, snapshot_seq]`;
  an empty delta makes no Provider request.
- Overflow removes oldest selected records until the rendered suffix fits the hard token budget.
  A single oversized record retains identity metadata and a visible recovery marker.
- Multiple external actions from one Provider tool batch may execute in declared order, and an
  external action result may drive a later Provider call. The finalizer computes the run outcome
  from the complete accumulated action list.
- Unknown or future message IDs cannot be read or targeted.
- Ordinary provider content, reasoning, Provider errors, and internal tool status cannot reach
  QQ; an explicit action tool reaches QQ exactly once.
- External action handlers return compact structured results. Only
  `stay_silent` returns AstrBot's `None` terminal signal and advances the cursor.
- A delayed snapshot already covered by cursor records `empty_snapshot_skipped`, assigns no
  tools, and makes no Provider request.
- `/gaf_clear` invalidates pending and active runs. Old runs perform no later state writes;
  actions rejected after clear make zero gateway calls.
- `_conf_schema.json`, runtime defaults/lookups, metadata, README, and state-writing behavior
  remain aligned.
- Both plugin locale files parse successfully and pass schema coverage tests after any
  metadata or configuration-schema text change.
- AstrBot core files and unrelated worktree changes remain untouched.

## Scenario: Directed observation scheduling

### 1. Scope / Trigger

Apply this contract when a message class needs a shorter reasoning-cycle interval while still
entering the normal per-flow observation pipeline. It prevents a fast-path change from delaying
the existing global candidate or creating a second snapshot lifecycle.

### 2. Signatures

```python
GroupRunCoordinator(
    *,
    debounce_seconds: float = 10,
    direct_delay_seconds: float = 1,
    min_cycle_interval_seconds: float = 10,
    direct_min_cycle_interval_seconds: float = 20,
)

GroupRunCoordinator.enqueue(
    flow_id: str,
    *,
    seq: int,
    received_at: float,
    directed: bool,
) -> None
```

Runtime configuration uses `scheduling.direct_min_cycle_interval_seconds`; `main.py` must keep
`CONFIG_PATHS`, `CONFIG_DEFAULTS`, and `_build_coordinator()` aligned with `_conf_schema.json`.

### 3. Contracts

- `_FlowState.global_due_at` preserves the original deadline behavior: the first message chooses
  `debounce_seconds` or `direct_delay_seconds`, later ordinary messages keep the deadline, and a
  later directed message may shorten it.
- `_FlowState.direct_due_at` exists only after a directed message and uses
  `received_at + direct_delay_seconds`.
- Global eligibility applies `min_cycle_interval_seconds`; directed eligibility applies
  `direct_min_cycle_interval_seconds`; the coordinator returns the earlier existing candidate.
- Both candidates own one `pending_seq` and allocate exactly one `RunSnapshot`.
- Every actual start writes the same per-flow `last_started_at` and clears both deadlines.
- `RunSnapshot` and the downstream observation/Provider/tool/cursor contracts contain no
  trigger-type branch.

### 4. Validation & Error Matrix

| Condition | Required behavior |
|---|---|
| Interval value below 0 | Clamp to `0.0` in `GroupRunCoordinator.__init__` |
| No prior observation start | Eligibility is the corresponding aggregation deadline |
| Pending batch has no directed message | `direct_due_at` remains `None`; use global candidate |
| Directed interval is longer than global interval | Preserve the earlier global candidate |
| A run is active | Keep the pending batch; `begin_if_due()` returns `None` |
| `clear_flow(flow_id)` | Remove both deadlines, pending state, active run, and frequency state |

### 5. Good / Base / Bad Cases

- Good: global interval `120`, directed interval `20`; a directed message creates an earlier
  eligibility candidate and the resulting snapshot includes all pending sequence records.
- Base: an ordinary-only batch follows the original debounce and global interval behavior.
- Bad configuration order: global interval `10`, directed interval `100`; the global candidate
  wins, so adding a directed message never delays the existing observation opportunity.

### 6. Tests Required

- `tests/test_coordinator.py`: assert both interval orderings, mixed-batch `snapshot_seq`, shared
  `last_started_at` refresh, active-run reservation, zero interval, and `clear_flow()` reset.
- `tests/test_event_codec.py`: assert bot Reply / other Reply / missing sender plus At and Poke
  classification.
- `tests/test_main_observation.py`: assert default, nested, and flat config reads and AstrBot wake
  flag propagation into `enqueue(..., directed=True)`.
- `tests/test_i18n.py`: assert schema type `float`, default `20.0`, and both locale descriptions.

### 7. Wrong vs Correct

Wrong — replacing the existing candidate can delay behavior when the new interval is longer:

```python
interval = direct_interval if pending_directed else global_interval
eligible_at = max(due_at, last_started_at + interval)
```

Correct — preserve the existing candidate and add one directed candidate:

```python
candidates = [global_eligible_at]
if direct_eligible_at is not None:
    candidates.append(direct_eligible_at)
eligible_at = min(candidates)
```

Avoid introducing a second scheduling path, direct `event.send()` outside `QQActionGateway`,
string-built persistence formats, unbounded history scans, or untested renderer changes.
