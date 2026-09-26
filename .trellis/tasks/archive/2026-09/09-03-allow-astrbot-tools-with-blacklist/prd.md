# 让 GAF 循环复用 AstrBot 工具并维护黑名单机制

## 背景

当前 GAF（Group Agent Flow）在自主观察循环里**完全替换**了 AstrBot 注入的全局工具集：
`enforce_tool_set()` 把 `req.func_tool` 直接赋值为 `ToolRuntime.build_tool_set(event)`，
因此 AstrBot 自带的联网搜索、本地 shell/python、知识库、主动发消息、文件读写等工具
在 GAF 循环中**全部不可用**。

本任务的目标是：**不再清空所有 AstrBot 工具**，而是保留一部分与 GAF 流程兼容的 AstrBot
工具，并用一个**黑名单机制**去掉与 GAF 快照/仅工具动作模型**冲突**的工具。

### 「冲突」的定义

「冲突」只指会**破坏 GAF 边界模型**的工具，而非能力强弱。GAF 的边界模型有两条硬约束：

1. **仅工具动作**：群可见动作只能通过 GAF 自己的工具（`send_message` / `reply_message` /
   `react_message` / `poke_user`），普通模型输出被 `suppress_direct_output` 丢弃。
2. **快照边界**：动作与历史读取目标必须出现在**冻结快照**里（`_message_in_snapshot` /
   `_user_in_snapshot` 校验）；历史检索只能走 `search_chat_history`（冻结 Buffer）或
   `get_message`（快照内）。

因此，默认黑名单**只包含会绕过以上任一条约束的工具**，其余 AstrBot 工具（含只读能力强的
shell/python/浏览器/知识库/联网搜索等）**默认保留**。

## 用户诉求

> 允许在 Group Agent Flow 循环中调用 Astrbot 的其他工具（也就是不清空所有工具），
> 我们之前研究过，需要维护一个黑名单机制，把部分 AstrBot 核心提供的但是与我们的 flow 不兼容的工具去掉。

## 已确认事实（来自本次代码调研）

### GAF 侧现状

1. **`enforce_tool_set(request, tool_set)`**（`response_policy.py` L84-86）：
   ```python
   def enforce_tool_set(request: Any, tool_set: Any) -> None:
       """替换 AstrBot 在请求装饰阶段可能追加的全局工具。"""
       request.func_tool = tool_set
   ```
   这是**纯赋值替换**，会丢弃 AstrBot 在请求装饰阶段追加的所有全局工具。

2. **注入时机**：`enforce_autonomous_tools` 在 `on_agent_begin(priority=maxsize-20)` 触发，
   **晚于** AstrBot 的 `_plugin_tool_fix` / `_apply_web_search_tools` / `_apply_local_env_tools`
   等工具注入阶段（它们在 `_decorate_llm_request` 管线里、Agent Runner 构建**之前**完成）。
   因此 GAF 有机会在 AstrBot 注入完成后**再加工** `req.func_tool`。

3. **GAF 自身工具集**由 `ToolRuntime.build_tool_set(event)` 构造（`agent_tools.py` L288-830）：
   - 基础工具（始终存在）：`send_message`、`reply_message`、`react_message`、`poke_user`、
     `stay_silent`、`get_message`、`search_chat_history`
   - 按模型能力条件新增：
     - 支持 image → 加 `get_message_images`；否则加 `get_image_captions`
     - 不支持 audio → 加 `get_voice_transcript`（STT 回退）
   - 该方法**已经具备**条件包含机制（按模态），但没有配置驱动的排除机制。

4. **`_conf_schema.json` 目前没有任何工具过滤配置键**。

5. GAF 的 `AGENT_PROTOCOL_PROMPT` 不枚举工具名——工具名/描述/参数 schema 由
   `ToolRuntime.build_tool_set()` 全权提供。

### AstrBot Core 侧现状

1. **内置工具**（`@builtin_tool` 装饰的 `FunctionTool` 子类，约 32 个），分类：
   - 文件系统：`astrbot_file_read_tool`、`astrbot_file_write_tool`、`astrbot_file_edit_tool`、
     `astrbot_grep_tool`、`astrbot_upload_file`、`astrbot_download_file`
   - Shell/执行：`astrbot_execute_shell`、`astrbot_shell_session`（+Local 变体）
   - Python：`astrbot_execute_ipython`、`astrbot_execute_python`
   - 浏览器（Shipyard Neo）：`astrbot_execute_browser`、`astrbot_execute_browser_batch`、`astrbot_run_browser_skill`
   - Neo 技能生命周期（11 个）：`astrbot_get_execution_history` 等
   - CUA 桌面：`astrbot_cua_screenshot`、`astrbot_cua_mouse_click`、`astrbot_cua_keyboard_type`
   - 定时任务：`future_task`
   - 知识库：`astr_kb_search`
   - 消息：`send_message_to_user`、`get_group_message_history`
   - 联网搜索（12 个）：`web_search_tavily`、`tavily_extract_web_page`、`web_search_bocha`、
     `web_search_brave`、`web_search_firecrawl`、`web_search_baidu`、`web_search_exa`、`web_search_anysearch` 等

2. **工具注入管线**（`astr_main_agent.py` `_decorate_llm_request`）：
   ```python
   await _decorate_llm_request(event, req, plugin_context, config, provider=provider)
   await _apply_kb(...)
   _plugin_tool_fix(event, req)            # 插件工具过滤
   await _apply_web_search_tools(...)
   if config.computer_use_runtime == "sandbox":
       _apply_sandbox_tools(...)
   elif config.computer_use_runtime == "local":
       _apply_local_env_tools(...)
   agent_runner = AgentRunner()            # 之后才创建 runner
   ```

3. **`_plugin_tool_fix`**：只在 `event.plugins_name is not None`（会话限定插件列表）时过滤；
   保留 MCP 工具、`handler_module_path` 为空或查不到 star_map 的工具、以及 `plugin.reserved` 为
   True 的插件工具。**这是 AstrBot 侧现成的「不可过滤」机制，但属于会话插件白名单范畴，
   与 GAF 的黑名单需求不同。**

4. **`ToolSet` API**：`add_tool(tool)`（同名去重，新的激活会覆盖未激活）、`remove_tool(name)`、
   `get_tool(name)`、`merge(other)`、`names()`。`FunctionTool` 有 `name` / `handler` /
   `handler_module_path` / `active` 字段，`active` 可用于临时代谢某个工具。

5. **工具命名约定**：AstrBot 内置工具名多以 `astrbot_` 开头（除 `future_task`、`astr_kb_search`、
   `send_message_to_user`、`get_group_message_history`、`web_search_*`）。

## 目标

1. 在 GAF 自主循环中，**保留**一部分与 GAF 快照/仅工具动作模型兼容的 AstrBot 工具。
2. 用**黑名单机制**剔除不兼容的 AstrBot 工具。
3. 保留 GAF 自身工具集（`send_message` 等动作工具不可被黑名单移除，需保护）。

## 关键设计问题（待收敛）

### Q1：注入策略——合并 vs 替换？

- **方案 A（合并后过滤）**：保留 AstrBot 注入好的 `req.func_tool`，再按黑名单移除不兼容工具，
  最后并入 GAF 自身工具集。GAF 工具与 AstrBot 工具同名时以 GAF 为准。
- **方案 B（GAF 工具集 + 白名单追加）**：以 GAF 自身工具集为基，按一个白名单把部分 AstrBot
  工具追加进来（白名单 + 黑名单双机制）。
- **方案 C（纯黑名单）**：从 AstrBot 完整工具集里剔除黑名单，再并入 GAF 工具。

倾向 **方案 A**，因为「不清空所有工具，只去掉不兼容的」最贴合用户原始诉求，且实现上只需在
`enforce_autonomous_tools` / `build_tool_set` 后追加一个「按黑名单过滤」步骤。

### Q2：黑名单内容如何维护？

- **默认黑名单**（静态内置）：只放真正与 GAF 边界模型**冲突**的工具。
  目前确认冲突的只有：
  - `send_message_to_user`：绕过快照边界主动发消息/私聊（支持任意组件、`mention_user`），
    且不受 GAF 的「仅 GAF 工具发送 + 快照内目标」约束。
  - `get_group_message_history`：直接读取 AstrBot 的持久化全局历史
    （`message_history_manager`），绕过 GAF 的「`search_chat_history` 只查冻结 Buffer」模型。
- **可扩展**：黑名单应暴露成一个可配置集合（`_conf_schema.json` 新增配置项），支持用户
  追加/删除。
- **不做默认禁用**：`future_task` / 浏览器 / CUA / shell / python / 知识库 / 联网搜索等
  **默认保留**，它们不破坏 GAF 边界——用户确认无需禁用。

### Q3：GAF 自身工具的保底

无论黑名单怎么配，GAF 的 `send_message` / `reply_message` / `react_message` / `poke_user` /
`stay_silent` 等核心工具**必须有保底，不能被黑名单移除**（否则 Agent 失去对外动作能力）。
黑名单应只作用于「AstrBot 注入的工具」，GAF 自身工具集独立于黑名单过滤。

### Q4：与 AstrBot 工具重名冲突

若 GAF 工具与 AstrBot 工具同名（例如 `send_message` 与 AstrBot 的某个发送工具），需确认
`ToolSet.add_tool` 的同名覆盖逻辑是否符合预期——GAF 工具应覆盖 AstrBot 同名工具。

## 验收标准（初版）

- [ ] GAF 自主循环中，黑名单外的 AstrBot 工具变得可用。
- [ ] 黑名单内的 AstrBot 工具被移除，不进入模型可见 schema。
- [ ] GAF 自身动作工具（`send_message` 等）始终存在，不受黑名单影响。
- [ ] 黑名单可通过配置自定义（增删）。
- [ ] 现有 GAF 行为（仅工具动作、快照边界）在默认黑名单下不回归。

## 调研记录

- 首次调研日期：2026-09-03
- 调研范围：GAF `main.py` / `response_policy.py` / `agent_tools.py`；
  AstrBot `astr_main_agent.py` / `agent/tool.py` / `func_tool_manager.py` / `computer_tools/*` /
  `web_search_tools.py` / `knowledge_base_tools.py` / `message_tools.py` / `cron_tools.py` /
  `shipyard_neo/*`
- 结论：当前 GAF 用 `enforce_tool_set` 完全替换工具集；AstrBot 内置工具约 32 个；
  黑名单机制是全新特性，无既有实现。
