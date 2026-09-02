# Design: 让 GAF 循环复用 AstrBot 工具并维护黑名单机制

## Summary

把 GAF 自主循环中「完全替换 AstrBot 工具集」的行为，改为「**保留 AstrBot 注入的工具，
用黑名单剔除冲突工具，再并入 GAF 自身工具集**」，从而让 AstrBot 能力（联网搜索、知识库、
只读文件、shell/python 等）在 GAF 循环中可用，同时守住 GAF 的仅工具动作 + 快照边界。

## Background / Constraints

- 当前 `enforce_autonomous_tools`（`on_agent_begin`, priority=maxsize-20）调用
  `enforce_tool_set()`，后者是纯赋值替换 `request.func_tool = build_tool_set(event)`，
  丢弃 AstrBot 在 `_decorate_llm_request` 阶段注入的所有全局工具。
- 注入时机：AstrBot 工具注入发生在 `_decorate_llm_request` 管线（Agent Runner 构建**之前**），
  `on_agent_begin` 在其后触发。因此在 `enforce_autonomous_tools` 里拿到的
  `request.func_tool` **已经包含 AstrBot 工具**，可在此处做「合并 + 过滤」而非替换。
- 「冲突」定义：只指会破坏 GAF 边界模型的工具。两条硬约束：
  1. 仅工具动作——群可见动作只能经 GAF 的 `send_message`/`reply_message`/`react_message`/`poke_user`。
  2. 快照边界——动作与历史读取目标必须落在冻结快照里；历史检索只走 `search_chat_history`（冻结 Buffer）或 `get_message`（快照内）。

## Design

### 1. 新增黑名单工具模块 `tool_filter.py`

```python
"""按黑名单过滤 AstrBot 注入的工具，保留 GAF 自身工具集的独立过滤。"""

DEFAULT_BLOCKLIST: frozenset[str] = frozenset({
    "send_message_to_user",       # 绕过快照边界主动发消息/私聊
    "get_group_message_history",  # 读取 AstrBot 持久化全局历史，绕过冻结 Buffer 模型
})


def filter_astrbot_tools(tool_set, blocklist=None) -> None:
    """就地移除 tool_set 中命中黑名单的工具；纯读取工具与 GAF 自身工具不受影响。"""
    blocklist = set(blocklist) if blocklist else set(DEFAULT_BLOCKLIST)
    for name in list(tool_set.names()):
        if name in blocklist:
            tool_set.remove_tool(name)
```

说明：
- `tool_set.names()` 来自 AstrBot `ToolSet` API（已有）。
- `DEFAULT_BLOCKLIST` 只含真正冲突的两个工具，符合用户「只禁用冲突工具」的意愿。
- 黑名单只对** AstrBot 注入的工具**生效；GAF 自身工具在合并时由 `ToolSet.add_tool`
  同名覆盖保护，且 `filter_astrbot_tools` 与 GAF 工具集构建解耦。

### 2. 修改 `enforce_autonomous_tools`（main.py）

把「替换」改为「合并 + 过滤」：

```python
@filter.on_agent_begin(priority=maxsize - 20)
async def enforce_autonomous_tools(self, event, run_context) -> None:
    if not event.get_extra(AUTONOMOUS_EXTRA, False):
        return
    event.set_extra(RUN_CONTEXT_EXTRA, run_context)
    request = event.get_extra("provider_request")
    if not isinstance(request, ProviderRequest):
        return
    # 1) 合并 AstrBot 工具与 GAF 自身工具（同名时 add_tool 覆盖，GAF 优先）
    merged = request.func_tool if request.func_tool is not None else ToolSet()
    gaf_set = self.tool_runtime.build_tool_set(event)
    for tool in gaf_set.tools:
        merged.add_tool(tool)
    # 2) 按黑名单剔除冲突的 AstrBot 工具（不作用于 GAF 自身工具）
    filter_astrbot_tools(merged, self._cfg("astrbot_tool_blocklist", []))
    request.func_tool = merged
```

要点：
- 若 `request.func_tool` 为 `None`，退化为仅 GAF 工具集（与旧行为一致）。
- GAF 工具通过 `add_tool` 合并，同名时按 `ToolSet` 规则覆盖 AstrBot 工具。
- 黑名单过滤在合并后执行，只剔除命中黑名单名的工具。

### 3. 新增配置项 `context.astrbot_tool_blocklist`

- `_conf_schema.json` 在 `context` 下新增：
  ```json
  "astrbot_tool_blocklist": {
    "type": "list",
    "description": "AstrBot tools to exclude from the autonomous agent",
    "hint": "Tools that conflict with the snapshot/send boundary are excluded. Entries are AstrBot tool names; extra names may be added to block more AstrBot tools.",
    "items": {"type": "string"},
    "default": ["send_message_to_user", "get_group_message_history"]
  }
  ```
- `i18n/zh-CN.json` 与 `en-US.json` 对齐新增该配置的 `description` / `hint`。
- `main.py` `CONFIG_PATHS` / `CONFIG_DEFAULTS` 新增：
  ```python
  "astrbot_tool_blocklist": ("context", "astrbot_tool_blocklist"),
  ...
  "astrbot_tool_blocklist": ["send_message_to_user", "get_group_message_history"],
  ```

## Tradeoffs

- **合并 vs 替换**：合并保留了 AstrBot 能力，更贴合用户诉求；代价是要处理同名工具覆盖
  和黑名单过滤。选择合并，因为「不清空所有工具」是硬需求。
- **默认黑名单最小化**：只禁 `send_message_to_user` / `get_group_message_history` 两个
  真正冲突的工具；其余（shell/python/浏览器/CUA/kb/联网搜索/定时任务）默认保留。
- **配置可扩展**：用户可在配置里追加黑名单。默认值体现「仅禁冲突」，追加项体现「用户自主」。
- **GAF 工具保底**：GAF 自身工具不经过 `filter_astrbot_tools`，用 `add_tool` 合并天然覆盖同名，
  不会因黑名单丢失对外动作能力。

## Compatibility / Rollback

- 若默认黑名单为空或用户删空，行为退化为「保留全部 AstrBot 工具 + GAF 工具」，不报错。
- 旧行为（完全替换）可通过清空合并逻辑回退；此处用单个 `enforce_autonomous_tools` 方法修改，
  回退点清晰。
- 不改 AstrBot 侧代码，只消费其 `ToolSet` API 与已注入的 `request.func_tool`。

## Tests

- `test_merge_and_filter_keeps_astrbot_tools`
- `test_blocklist_removes_conflicting_tools`
- `test_gaf_own_tools_are_never_removed`
- `test_default_blocklist_minimal`
