# Model Content Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Execute inline for this repository.

**Goal:** Send normal model `content` as a QQ message while keeping reasoning, errors, and internal tool output private.

**Architecture:** Make the existing send guard selectively forward ordinary AstrBot `LLM_RESULT` chains and record them as `model_content` actions. Send the final plain response inside `finalize_observation()` before outcome persistence, then clear only the pending transport response while preserving assistant history.

**Tech Stack:** Python 3.12, AstrBot 4.26.5 hooks and message chains, pytest, Ruff.

## Global Constraints

- Keep AstrBot core unchanged.
- Send `content A` before a tool-generated message `B` and keep them separate.
- Keep `reasoning_content`, Provider errors, tool status, and empty content private.
- Record every attempted visible model send in `EXTERNAL_ACTIONS_EXTRA`.
- Preserve visible assistant history and Provider replay fields.

---

### Task 1: Selective model-content send boundary

**Files:**
- Modify: `tests/test_response_policy.py`
- Modify: `response_policy.py`
- Modify: `agent_tools.py`

**Interfaces:**
- Produces: `record_external_action(event, *, action_name, success, detail="")`.
- Produces: `clear_response_output(response) -> str` returning the removed plain content.
- Changes: `install_send_guard(event)` forwards eligible `LLM_RESULT` content and records `model_content`.

- [ ] **Step 1: Write failing policy tests**

Add tests proving an eligible LLM result reaches the original sender, reasoning/general output remains blocked, success/failure creates a `model_content` action, and clearing transport output leaves the assistant history saveable.

- [ ] **Step 2: Run policy tests and verify RED**

Run:

```powershell
$env:PYTHONPATH='D:\Project\2026\AstrBot'
D:\Project\2026\AstrBot\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\test_response_policy.py -q
```

Expected: failures because the guard blocks eligible LLM content and `clear_response_output` does not exist.

- [ ] **Step 3: Implement the minimal boundary**

Move external-action bookkeeping into `response_policy.record_external_action()`. In `guarded_send`, require `event.get_result().is_llm_result()`, reject reasoning/tool-status chains and empty visible text, invoke the saved original sender, and record success or failure. Add `clear_response_output()` without touching `run_context.messages`.

- [ ] **Step 4: Run policy tests and verify GREEN**

Run the Task 1 command. Expected: all policy tests pass.

### Task 2: Final content and content-plus-tool ordering

**Files:**
- Modify: `tests/test_tools.py`
- Modify: `agent_tools.py`
- Modify: `main.py`

**Interfaces:**
- Produces: `ToolRuntime.emit_model_content(event, content) -> str` using action name `model_content`.
- Consumes: `clear_response_output()` and the shared external-action record.

- [ ] **Step 1: Write failing ordering tests**

Add an async test that installs the guard, sends eligible content `A`, invokes the `send_message` tool with `B`, and asserts original platform sends are exactly `A`, `B` with action names `model_content`, `send_message`. Add a pure-content test for `emit_model_content()`.

- [ ] **Step 2: Run focused tests and verify RED**

Run:

```powershell
$env:PYTHONPATH='D:\Project\2026\AstrBot'
D:\Project\2026\AstrBot\.venv\Scripts\python.exe -m pytest -p no:cacheprovider tests\test_response_policy.py tests\test_tools.py -q
```

Expected: failures because `emit_model_content()` and selective pipeline retention are missing.

- [ ] **Step 3: Implement final delivery**

Add `ToolRuntime.emit_model_content()` through `_external()` and `QQActionGateway.send_message()`. In `finalize_observation()`, send non-empty final content before classifying the run, then clear its transport response. Remove history `_no_save` suppression. In `on_decorating_result`, retain eligible LLM results and clear every other autonomous pipeline result.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run the Task 2 command. Expected: all focused tests pass.

### Task 3: Verify and document the contract

**Files:**
- Modify: `README.md`
- Modify: `.trellis/spec/backend/quality-guidelines.md`
- Modify: `.trellis/spec/backend/error-handling.md`
- Modify: `.trellis/spec/backend/logging-guidelines.md`

- [ ] **Step 1: Update behavior documentation**

Document normal content delivery, separate content/tool messages, private reasoning/errors, `model_content` action records, and the unbuffered intermediate-result compatibility requirement.

- [ ] **Step 2: Run the complete quality gate**

Run pytest with a fresh writable `--basetemp`, Ruff, compileall, plugin import, and `git diff --check`. Expected: 0 failures and 0 lint errors.

- [ ] **Step 3: Inspect the final diff**

Confirm the diff contains plugin, test, README, spec, task, and plan changes only; scan for group IDs, ports, tokens, credentials, and AstrBot core changes.
