# Implement: 让 GAF 循环复用 AstrBot 工具并维护黑名单机制

## Ordered Checklist

1. **新建 `tool_filter.py`**：定义 `DEFAULT_BLOCKLIST`（`send_message_to_user`、`get_group_message_history`）与 `filter_astrbot_tools(tool_set, blocklist=None)`。
2. **修改 `main.py` `enforce_autonomous_tools`**：把 `enforce_tool_set(request, build_tool_set(event))` 替换为「合并 AstrBot 工具 + 黑名单过滤」逻辑。
3. **`main.py` 配置表**：`CONFIG_PATHS` / `CONFIG_DEFAULTS` 新增 `astrbot_tool_blocklist`。
4. **`_conf_schema.json`**：`context` 下新增 `astrbot_tool_blocklist`（list，默认 `["send_message_to_user", "get_group_message_history"]`）。
5. **i18n**：`zh-CN.json` / `en-US.json` 新增该配置的 `description` / `hint`。
6. **测试**：覆盖合并 + 过滤、黑名单剔除冲突工具、GAF 自身工具保底、默认黑名单最小化。
7. **验证**：pytest、ruff check、ruff format、compileall。

## Validation Commands

```powershell
$env:PYTHONPATH='D:\Project\2026\AstrBot'
D:\Project\2026\AstrBot\.venv\Scripts\python.exe -m pytest -q
D:\Project\2026\AstrBot\.venv\Scripts\python.exe -m ruff check .
D:\Project\2026\AstrBot\.venv\Scripts\python.exe -m ruff format .
D:\Project\2026\AstrBot\.venv\Scripts\python.exe -m compileall -q .
```

## Review Gates

- 合并逻辑：`request.func_tool` 非空时保留 AstrBot 工具；为空时退化为仅 GAF 工具集。
- GAF 自身工具（`send_message` 等）始终存在。
- 黑名单默认仅含两个冲突工具，剔除后不破坏其余 AstrBot 工具。
- i18n 与 `_conf_schema.json` 对齐。

## Rollback Points

- 单个提交；如合并逻辑问题，恢复 `enforce_autonomous_tools` 为原 `enforce_tool_set` 调用即可。
