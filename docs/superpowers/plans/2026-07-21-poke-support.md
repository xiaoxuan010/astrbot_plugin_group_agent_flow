# QQ Poke Support Implementation Plan

> **For agentic workers:** Execute inline in the main Codex session. Follow test-driven development and the existing Trellis task workflow.

**Goal:** Give the autonomous group Agent explicit inbound poke context and a controlled tool for poking an observed QQ user.

**Architecture:** `event_codec.py` owns stable inbound poke records, `agent_tools.py` owns snapshot-bounded model exposure, and `qq_gateway.py` owns AstrBot/OneBot delivery. Existing response-policy terminal semantics remain unchanged.

**Tech Stack:** Python 3.12, AstrBot 4.26.6, NapCat OneBot v11, pytest, Ruff.

## Global Constraints

- Keep AstrBot core unchanged.
- Keep the System Prompt decoupled from concrete tool names.
- Require the target QQ to appear at or before the frozen snapshot.
- Route the visible action through the existing external-action terminal boundary.

---

### Task 1: Preserve inbound poke semantics

**Files:**
- Modify: `tests/test_event_codec.py`
- Modify: `event_codec.py`

**Interfaces:**
- Consumes: AstrBot `Comp.Poke(id=target_id)` on a group notice event.
- Produces: component record `{"type":"poke","target_id":str}` and text `[戳一戳 target=<id>]`.

- [x] Add a failing event-codec test for target serialization and bot-directed detection.
- [x] Run the focused test and confirm the missing `target_id` failure.
- [x] Add the minimal Poke branch and semantic outline fallback.
- [x] Re-run the focused test until green.

### Task 2: Add the controlled poke action

**Files:**
- Modify: `tests/test_actions.py`
- Modify: `tests/test_tools.py`
- Modify: `qq_gateway.py`
- Modify: `agent_tools.py`

**Interfaces:**
- Produces: `QQActionGateway.poke_user(event, *, user_id: str) -> dict[str, Any]`.
- Produces: model tool `poke_user(user_id: str) -> None | str`.
- Error: `{"error":"user_not_found_in_snapshot","user_id":user_id}`.

- [x] Add failing gateway and tool-runtime tests.
- [x] Run focused tests and confirm the missing method/tool failures.
- [x] Build a `MessageChain` containing `Comp.Poke(id=user_id)` in the gateway.
- [x] Validate target membership across sender, mention, and poke-target fields in the frozen snapshot.
- [x] Register `poke_user` as an external tool and verify success remains terminal.
- [x] Re-run focused tests until green.

### Task 3: Synchronize contracts and verify

**Files:**
- Modify: `.trellis/spec/backend/error-handling.md`
- Modify: `README.md`

- [x] Document the inbound record, tool signature, snapshot error, and NapCat boundary.
- [x] Run pytest, Ruff, compileall, plugin import, and `git diff --check`.
- [x] Review the diff for concrete tool names accidentally added to the System Prompt.
