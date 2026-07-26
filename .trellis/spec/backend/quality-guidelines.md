# Quality Guidelines

## Required Patterns

- Keep snapshot isolation end to end. Context preparation, history search, message lookup,
  reply, and reaction targets must all enforce `seq <= snapshot_seq`.
- Route every externally visible autonomous action through the plugin-controlled send
  boundary. Only `send_message`, `reply_message`, `react_message`, and `poke_user` may reach QQ. Keep
  ordinary `LLM_RESULT` content, reasoning, Provider errors, and internal tool status behind
  the send guard.
- Keep renderer behavior deterministic. Read `context.renderer` for each request and record
  the renderer used for that cycle; Core multi-role history remains in its original form while
  only the next JSONL suffix uses the current renderer.
- Keep renderer labels aligned with request structure: `legacy_delta` is
  `聚合增量块（短线分隔）`, `plain_lines` is `聚合增量块（换行分隔）`, and
  `native_messages` is `逐消息独占 User 块`. Labels may change; option values and defaults
  remain stable for saved configuration compatibility.
- Empty renderer input produces no user message. A stale or contentless snapshot stops in
  `OnLLMRequestEvent` before the Provider receives a request, and cursor updates remain
  monotonic across delayed runs.
- On a missing history cursor, replace AstrBot conversation contexts with the plugin
  token-bounded recent JSONL suffix selected from cursor `0` and reset conversation token
  usage. On an existing history cursor, preserve Core contexts, render only the waterline
  delta, and add its estimate to the persisted token baseline before Provider preparation.
- Preserve structured message metadata in storage even when a renderer projects it to text.
- Persist each successful external action as a typed, non-targetable fact before the run's final
  cursor commit. Extend an existing pending snapshot with the action seq without creating a
  second scheduling path.
- Build one observation suffix from JSONL in increasing sequence order. Use Core
  `EstimateTokenCounter` and the configured hard token budget; trim oldest selected records
  when the suffix exceeds the budget. Keep earlier facts accessible through snapshot-bounded
  history tools.
- Validate run generation under the per-flow lock before gateway admission, fact persistence,
  and terminal state writes. Clear persistent and coordinator flow state in the same critical
  section.
- Keep `.astrbot-plugin/i18n/zh-CN.json` and `en-US.json` aligned with
  `_conf_schema.json`: every section and field has a localized `description`, schema
  `hint` and `labels` entries have matching localized entries, and translated labels
  never change configuration keys, option values, or defaults.

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
- A message reserved during reasoning followed by a later action record advances the existing
  pending upper bound to the action seq; an action with no pending message remains unscheduled.
- Inbound `append_record()` and coordinator `enqueue()` execute under one per-flow lock, so every
  action ordering is covered by the next valid pending snapshot.
- A missing history cursor ignores stale ordinary cursors and legacy window data, then rebuilds
  from cursor `0`. An existing history cursor renders only `(history_cursor, snapshot_seq]`;
  an empty delta makes no Provider request.
- Overflow removes oldest selected records until the rendered suffix fits the hard token budget.
  A single oversized record retains identity metadata and a visible recovery marker.
- Multiple external actions from one Provider tool batch may execute in declared order, and an
  external action result may drive a later Provider call. The finalizer computes the run outcome
  from the complete accumulated action list. No tool call ends as a persisted no-action outcome.
- Unknown or future message IDs cannot be read or targeted.
- Synthetic `agent-action:*` IDs remain readable through history tools and are rejected by
  reply/react target validation.
- Ordinary provider content, reasoning, Provider errors, and internal tool status cannot reach
  QQ; an explicit action tool reaches QQ exactly once.
- External action handlers return compact structured results after fact persistence. Only
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

Avoid introducing a second scheduling path, direct `event.send()` outside `QQActionGateway`,
string-built persistence formats, unbounded history scans, or untested renderer changes.
