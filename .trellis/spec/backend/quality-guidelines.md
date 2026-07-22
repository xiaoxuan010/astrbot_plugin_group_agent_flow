# Quality Guidelines

## Required Patterns

- Keep snapshot isolation end to end. Context preparation, history search, message lookup,
  reply, and reaction targets must all enforce `seq <= snapshot_seq`.
- Route every externally visible autonomous action through the plugin-controlled send
  boundary. Only `send_message`, `reply_message`, `react_message`, and `poke_user` may reach QQ. Keep
  ordinary `LLM_RESULT` content, reasoning, Provider errors, and internal tool status behind
  the send guard.
- Keep renderer behavior deterministic. Renderer selection is assigned once per
  flow/conversation in `state.json` so experiments do not mix formats mid-conversation.
- Keep renderer labels aligned with request structure: `legacy_delta` is
  `聚合增量块（短线分隔）`, `plain_lines` is `聚合增量块（换行分隔）`, and
  `native_messages` is `逐消息独占 User 块`. Labels may change; option values and defaults
  remain stable for saved configuration compatibility.
- Empty renderer input produces no user message. A stale or contentless snapshot stops in
  `OnLLMRequestEvent` before the Provider receives a request, and cursor updates remain
  monotonic across delayed runs.
- Preserve structured message metadata in storage even when a renderer projects it to text.
- Persist each successful external action as a typed, non-targetable fact before committing the
  terminal cursor. Extend an existing pending snapshot with the action seq without creating a
  second scheduling path.
- Replay recent consumed action facts from JSONL whenever a new delta exists, bounded by the
  existing cycle message limit, so consecutive terminal cycles retain completed-action context.
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
- Two consecutive observations after cursor advancement both include the prior action fact; an
  empty delta remains empty and does not schedule work from action memory alone.
- Multiple external actions from one Provider tool batch may execute in declared order; the
  batch ends without a follow-up Provider request. Each callback recomputes the same run outcome
  from the full accumulated action list. No tool call ends as a persisted no-action outcome.
- Unknown or future message IDs cannot be read or targeted.
- Synthetic `agent-action:*` IDs remain readable through history tools and are rejected by
  reply/react target validation.
- Ordinary provider content, reasoning, Provider errors, and internal tool status cannot reach
  QQ; an explicit action tool reaches QQ exactly once.
- External action handlers return AstrBot's `None` terminal signal after persisting the run
  outcome and cursor, preventing a follow-up Provider request.
- A delayed snapshot already covered by cursor records `empty_snapshot_skipped`, assigns no
  tools, and makes no Provider request.
- `_conf_schema.json`, defaults, metadata, README, and migration behavior remain aligned.
- Both plugin locale files parse successfully and pass schema coverage tests after any
  metadata or configuration-schema text change.
- AstrBot core files and unrelated worktree changes remain untouched.

Avoid introducing a second scheduling path, direct `event.send()` outside `QQActionGateway`,
string-built persistence formats, unbounded history scans, or untested renderer changes.
