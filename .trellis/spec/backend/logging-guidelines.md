# Logging Guidelines

Use `astrbot.api.logger` for runtime logs. Prefix plugin-owned messages with
`[astrbot_plugin_group_agent_flow]` through the `PLUGIN_NAME` constant so mixed AstrBot logs
remain searchable.

## Levels

- `info`: plugin initialization, data directory, and one-time legacy migration outcome.
- `debug`: high-volume snapshot metadata such as flow, conversation, cursor, renderer,
  injected count, and skipped count. Gate these messages with `debug.debug_log`.
- `warning`: a recoverable protocol violation with operational value, such as a rejected
  Provider error or internal output attempting to cross the send boundary.
- `error`: inability to create a required conversation or another failure that prevents a
  reasoning cycle.

Prefer identifiers (`flow_id`, `run_id`, `snapshot_seq`) and counts over full group message
text. Tool operation errors are already persisted in terminal-action state and returned to
the model; log them only when an operator needs additional context.

For suppressed model content, log only `run_id`; never log the full content. Explicit action
records carry success or failure without message text.

Never log API credentials, owner private-message content, raw provider requests, complete
conversation history, or full QQ component URLs. Avoid unconditional per-message info logs in
active groups.
