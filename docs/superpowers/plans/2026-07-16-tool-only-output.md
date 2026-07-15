# Tool-only output implementation plan

> **For agentic workers:** Execute inline in the main Codex session. Do not dispatch subagents.

**Goal:** Make explicit QQ action tools the only visible autonomous output and end the Agent
Loop after an external action.

**Architecture:** `response_policy.py` blocks every core send and marks suppressed final
assistant content as unsaved. `agent_tools.py` records gateway outcomes and returns AstrBot's
terminal `None` signal for external actions. `main.py` composes both contracts at response and
agent-done hooks.

**Tech Stack:** Python 3.12, AstrBot 4.26.5, pytest, Ruff.

## Global Constraints

- Keep AstrBot core unchanged.
- Preserve snapshot isolation and tool-call history.
- Route every visible autonomous action through `QQActionGateway`.
- Keep Provider reasoning and errors private.

### Task 1: Suppress Provider content

**Files:** `tests/test_response_policy.py`, `response_policy.py`, `main.py`

- [ ] Replace visible-content tests with failing assertions that core sends are blocked.
- [ ] Add a failing history test for `_no_save` on suppressed assistant output.
- [ ] Restore `suppress_direct_output(response, run_context=None) -> bool`.
- [ ] Restore the `on_agent_done` history hook and unconditional autonomous result clearing.
- [ ] Run `tests/test_response_policy.py` until green.

### Task 2: Terminate after external actions

**Files:** `tests/test_tools.py`, `agent_tools.py`

- [ ] Add failing tests that each external action handler returns `None` after one gateway
      attempt while read-only tools still return JSON strings.
- [ ] Change `_external(...) -> None` to record success/failure and consume the core terminal
      tool protocol.
- [ ] Remove the `emit_model_content` path and its tests.
- [ ] Run `tests/test_tools.py` until green.

### Task 3: Synchronize contracts and verify

**Files:** `README.md`, task artifacts, `.trellis/spec/backend/*.md`

- [ ] Replace visible-content documentation with the tool-only contract.
- [ ] Run the full pytest suite, Ruff, compileall, plugin import, and `git diff --check`.
- [ ] Review the final diff for AstrBot core changes, secrets, and stale `model_content`
      references.

### Task 4: Make tool use explicit without naming tools in the prompt

**Files:** `tests/test_main_observation.py`, `main.py`, `.trellis/spec/backend/error-handling.md`

- [x] Add failing assertions that the system protocol requires every group-visible action to
      use an available external action tool, contains no concrete tool names, and remains after
      `custom_system_prompt`.
- [x] Add a failing assertion that the cycle prompt repeats the tool-or-silence decision.
- [x] Strengthen `AGENT_PROTOCOL_PROMPT`, replace the placeholder cycle prompt, and compose the
      fixed protocol after the optional persona text.
- [x] Run focused tests, the complete quality gate, and synchronize the executable contract.
- [x] Reproduce later AstrBot request-hook additions, then move and deduplicate the fixed protocol
      in a dedicated lowest-priority request hook.
- [x] Reload the plugin and verify a natural post-reload group snapshot completes through
      `reply_message` with `action_succeeded`.
