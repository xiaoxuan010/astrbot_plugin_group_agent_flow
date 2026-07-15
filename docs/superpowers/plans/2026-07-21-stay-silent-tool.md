# stay_silent 主动沉默工具 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增显式的 `stay_silent` 内部终止工具，并将主动沉默持久化为 `silence_selected`。

**Architecture:** `ToolRuntime` 负责注册和执行无副作用的终止工具，通过 event extra 将主动沉默意图传给插件终止回调。`GroupAgentFlowPlugin` 在现有 outcome 分类和 cursor 提交流程中读取该标记；`response_policy` 保持纯函数分类边界。

**Tech Stack:** Python 3.10+、AstrBot `FunctionTool`/`ToolSet`、pytest、Ruff。

## Global Constraints

- `stay_silent` 无参数，QQ 副作用为零。
- `stay_silent` 不写入 `_group_agent_external_actions`。
- outcome 使用 `silence_selected`，群外部动作结果具有更高分类优先级。
- 普通 assistant 内容继续保持内部并记录为 `direct_output_suppressed`。
- 不增加配置项，不改变 JSONL 消息格式和现有群动作工具参数。
- 所有新增注释和日志使用 English；复杂接口使用 Google-style docstring。
- 保留工作区已有改动，只编辑本计划列出的代码区域。

---

### Task 1: 定义主动沉默状态与 outcome 分类

**Files:**
- Modify: `response_policy.py:36-51`
- Test: `tests/test_response_policy.py:50-70`

**Interfaces:**
- Consumes: `external_actions: list[dict[str, Any]]`、`had_direct_output: bool`。
- Produces: `classify_run_outcome(..., silence_selected: bool, had_direct_output: bool) -> str`。

- [ ] **Step 1: 编写失败测试**

在 `test_classify_run_outcome_distinguishes_silence_protocol_output_and_actions` 中显式传入
`silence_selected`，并增加主动沉默及优先级断言：

```python
assert (
    classify_run_outcome(
        [],
        silence_selected=True,
        had_direct_output=False,
    )
    == "silence_selected"
)
assert (
    classify_run_outcome(
        [{"status": "succeeded", "action_name": "send_message"}],
        silence_selected=True,
        had_direct_output=False,
    )
    == "action_succeeded"
)
```

同步为既有断言增加 `silence_selected=False`。

- [ ] **Step 2: 运行测试并确认失败**

Run: `pytest tests/test_response_policy.py::test_classify_run_outcome_distinguishes_silence_protocol_output_and_actions -v`

Expected: FAIL，错误指出 `classify_run_outcome()` 不接受 `silence_selected`。

- [ ] **Step 3: 实现最小分类逻辑**

将分类函数改为：

```python
def classify_run_outcome(
    external_actions: list[dict[str, Any]],
    *,
    silence_selected: bool,
    had_direct_output: bool,
) -> str:
    """Classify how an autonomous observation cycle terminated."""
    if external_actions:
        statuses = {str(action.get("status") or "") for action in external_actions}
        if statuses == {"succeeded"}:
            return "action_succeeded"
        if statuses == {"failed"}:
            return "action_failed"
        if "succeeded" in statuses and "failed" in statuses:
            return "action_partial"
        return "action_incomplete"
    if silence_selected:
        return "silence_selected"
    return "direct_output_suppressed" if had_direct_output else "no_action"
```

- [ ] **Step 4: 运行 focused test**

Run: `pytest tests/test_response_policy.py::test_classify_run_outcome_distinguishes_silence_protocol_output_and_actions -v`

Expected: PASS。

### Task 2: 注册并执行 `stay_silent`

**Files:**
- Modify: `agent_tools.py:18-22,116-320`
- Test: `tests/test_tools.py:45-75,120-190`

**Interfaces:**
- Produces: `SILENCE_SELECTED_EXTRA = "_group_agent_silence_selected"`。
- Produces: 无参数 FunctionTool `stay_silent`，handler 返回 `None`。
- Consumes: 现有 `terminal_callback: Callable[[Any], Awaitable[None]] | None`。

- [ ] **Step 1: 编写工具 schema 失败测试**

更新工具名称断言，并校验空参数 schema：

```python
assert [tool.name for tool in tool_set.tools] == [
    "send_message",
    "reply_message",
    "react_message",
    "poke_user",
    "stay_silent",
    "get_message",
    "search_chat_history",
]
silent_tool = tool_set.get_tool("stay_silent")
assert silent_tool.parameters == {"type": "object", "properties": {}}
```

- [ ] **Step 2: 编写终止行为失败测试**

新增异步测试：

```python
@pytest.mark.asyncio
async def test_stay_silent_marks_cycle_and_returns_terminal_signal(tmp_path):
    finalized = []

    async def finalize(event):
        finalized.append(event)

    runtime = ToolRuntime(
        GroupFlowStore(tmp_path),
        QQActionGateway(),
        terminal_callback=finalize,
    )
    event = FakeEvent()
    tool = runtime.build_tool_set().get_tool("stay_silent")
    run_context = ContextWrapper(
        context=SimpleNamespace(event=event, context=SimpleNamespace())
    )

    results = [
        result
        async for result in FunctionToolExecutor._execute_local(tool, run_context)
    ]

    assert results == [None]
    assert finalized == [event]
    assert event.sent == []
    assert event.bot.actions == []
    assert event.get_extra(SILENCE_SELECTED_EXTRA) is True
    assert event.get_extra("_group_agent_external_actions", []) == []
```

- [ ] **Step 3: 运行工具测试并确认失败**

Run: `pytest tests/test_tools.py -k "tool_set_exposes or stay_silent" -v`

Expected: FAIL，工具集合中缺少 `stay_silent`。

- [ ] **Step 4: 实现无副作用终止工具**

在 `agent_tools.py` 定义常量并在 `build_tool_set()` 内直接实现 handler：

```python
SILENCE_SELECTED_EXTRA = "_group_agent_silence_selected"

async def stay_silent(event: Any) -> None:
    """End the current observation cycle without a group-visible action."""
    self._run_metadata(event)
    event.set_extra(SILENCE_SELECTED_EXTRA, True)
    if self.terminal_callback is not None:
        await self.terminal_callback(event)
    return None
```

注册工具：

```python
FunctionTool(
    name="stay_silent",
    description="End the current observation cycle without any group-visible action.",
    parameters={"type": "object", "properties": {}},
    handler=stay_silent,
),
```

- [ ] **Step 5: 运行工具测试**

Run: `pytest tests/test_tools.py -k "tool_set_exposes or stay_silent" -v`

Expected: PASS。

### Task 3: 接入持久化和提示词协议

**Files:**
- Modify: `main.py:18-67,375-413`
- Test: `tests/test_main_observation.py:35-90`
- Test: `tests/test_main_observation.py`（新增持久化测试）

**Interfaces:**
- Consumes: `SILENCE_SELECTED_EXTRA`。
- Produces: `silence_selected` outcome 和已推进 cursor。
- Preserves: 外部动作、直接输出、空输出及空快照的现有结果语义。

- [ ] **Step 1: 编写提示词失败测试**

更新系统提示词和观察轮次提示词断言：

```python
assert "call stay_silent" in prompt
assert "call stay_silent" in cycle_prompt
```

现有“不重复列出工具名称”检查仅覆盖群动作和只读工具，`stay_silent` 作为协议终止词
单独断言恰好出现一次。

- [ ] **Step 2: 编写持久化失败测试**

构造带 pending cursor 和沉默标记的事件，直接执行持久化边界：

```python
event = FakeEvent(
    {
        AUTONOMOUS_EXTRA: True,
        FLOW_ID_EXTRA: flow_id,
        RUN_ID_EXTRA: "silent-run",
        SNAPSHOT_SEQ_EXTRA: 1,
        SILENCE_SELECTED_EXTRA: True,
        "_group_agent_pending_cursor": {
            "conversation_id": "conv",
            "target_seq": 1,
        },
    }
)

await plugin._persist_observation_state(event, had_direct_output=False)

assert store.get_run_outcome("silent-run")["outcome"] == "silence_selected"
assert store.get_cursor(flow_id, "conv") == 1
```

- [ ] **Step 3: 运行集成测试并确认失败**

Run: `pytest tests/test_main_observation.py -k "prompt or silent" -v`

Expected: FAIL，提示词缺少 `stay_silent`，持久化仍分类为 `no_action`。

- [ ] **Step 4: 更新协议与持久化输入**

从 `agent_tools` 导入 `SILENCE_SELECTED_EXTRA`。将两个提示词的沉默分支改为显式调用：

```text
When no external action improves the conversation, call stay_silent with no arguments. Do not produce ordinary assistant content.
```

在 `_persist_observation_state()` 调用分类函数时传入：

```python
silence_selected=bool(event.get_extra(SILENCE_SELECTED_EXTRA, False)),
```

- [ ] **Step 5: 运行 focused tests**

Run: `pytest tests/test_main_observation.py tests/test_response_policy.py tests/test_tools.py -v`

Expected: PASS。

### Task 4: 格式化和完整验证

**Files:**
- Modify: only files changed by Tasks 1-3 through formatter output

**Interfaces:**
- Consumes: Tasks 1-3 的完整实现。
- Produces: 可由运行时加载且通过质量门槛的插件代码。

- [ ] **Step 1: 格式化代码**

Run: `ruff format agent_tools.py response_policy.py main.py tests/test_tools.py tests/test_response_policy.py tests/test_main_observation.py`

Expected: exit 0。

- [ ] **Step 2: 运行 Ruff**

Run: `ruff check agent_tools.py response_policy.py main.py tests/test_tools.py tests/test_response_policy.py tests/test_main_observation.py`

Expected: `All checks passed!`

- [ ] **Step 3: 运行完整测试**

Run: `pytest -q`

Expected: 全部测试通过。

- [ ] **Step 4: 编译和导入验证**

Run: `python -m compileall -q agent_tools.py response_policy.py main.py tests`

Expected: exit 0。

Run: `python -c "import sys; from pathlib import Path; sys.path.insert(0, str(Path.cwd().parent)); import astrbot_plugin_group_agent_flow.main"`

Expected: exit 0。

- [ ] **Step 5: 检查 diff**

Run: `git diff --check -- agent_tools.py response_policy.py main.py tests/test_tools.py tests/test_response_policy.py tests/test_main_observation.py`

Expected: 无输出，exit 0。

- [ ] **Step 6: 审阅范围**

确认 diff 仅包含 `stay_silent` 工具、`silence_selected` 分类、提示词更新和对应测试；
现有未提交改动保持完整。
