# Directory Structure

## Runtime Modules

- `main.py` owns AstrBot registration, configuration lookup, event hooks, conversation
  acquisition, and lifecycle orchestration. Keep domain logic in the modules below.
- `event_codec.py` converts an `AstrMessageEvent` into the persisted schema. Add new QQ
  component mappings here and cover them in `tests/test_event_codec.py`.
- `coordinator.py` is the framework-independent per-group snapshot state machine. It uses
  monotonic time and contains no I/O or AstrBot imports.
- `observation.py` selects the cursor-to-snapshot range and delegates projection to
  `context_renderers.py`.
- `context_renderers.py` contains interchangeable LLM context projections. A renderer
  returns OpenAI-style message dictionaries and does not read storage or config.
- `agent_tools.py` defines the model-visible tool schemas and terminal-action policy.
  `qq_gateway.py` contains QQ/AstrBot side effects.
- `response_policy.py` owns the controlled platform boundary: it blocks ordinary model content,
  reasoning, Provider errors, and internal tool status while preserving the original sender
  exclusively for QQ action tools.
- `store.py` is the only module that reads and writes plugin persistence files.

## Dependency Direction

`main.py` composes all modules. Pure modules may depend on `store.py` and renderer protocols;
they should not import `main.py`. Platform side effects flow from `agent_tools.py` into
`qq_gateway.py`. Keep AstrBot-specific objects at the outer boundary whenever practical.

Tests mirror modules under `tests/test_<module>.py`. Small fake events and bots are preferred
over starting AstrBot for unit tests. Package modules that are also imported directly by tests
use the existing relative-import fallback pattern from `agent_tools.py` and `observation.py`.

## Adding Features

- Add a new persisted message field in `event_codec.py`, then update renderers or tools only
  when they consume it.
- Add a new external QQ action through `QQActionGateway`, expose it through `ToolRuntime`, and
  return the same terminal `None` contract after recording the gateway attempt.
- Add configuration in both `_conf_schema.json` and `CONFIG_PATHS`/`CONFIG_DEFAULTS` in
  `main.py`.

Avoid placing parsing, storage scans, or QQ API calls directly in hook methods.
