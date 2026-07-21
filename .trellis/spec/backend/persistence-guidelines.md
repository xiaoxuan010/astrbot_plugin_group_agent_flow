# Persistence Guidelines

## Storage Model

The plugin has no ORM or application database. `GroupFlowStore` in `store.py` owns two forms
of local persistence under `data/plugin_data/astrbot_plugin_group_agent_flow/`:

- Per-flow JSONL logs contain normalized group events with monotonic `seq` values.
- `state.json` contains conversation cursors, renderer assignments, cursor metadata,
  terminal-action claims, and per-run outcomes.

Flow filenames are SHA-256-derived so platform and group identifiers never become paths.
Use `GroupFlowStore` methods for every read or write; callers should not know log filenames.

## Write Rules

- Rewrite JSON and JSONL through a sibling `.tmp` file followed by `os.replace`, matching
  `_write_json_file()` and `write_records()`.
- Deduplicate incoming events by non-empty `message_id` before assigning a new sequence.
- Keep sequence values increasing even when retention trims old records.
- Keep each conversation cursor monotonically increasing. A delayed or stale run may finish
  after a newer run and must never move the cursor backwards.
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
