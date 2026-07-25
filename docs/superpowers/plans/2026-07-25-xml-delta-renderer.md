# XML 增量文本块渲染格式 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `xml_delta` renderer，以一个带群元数据的 XML User 块表达 observation 内的消息、动作和结构化组件。

**Architecture:** `event_codec.py` 负责补齐 Reply 的持久化事实，`context_renderers.py` 负责纯 XML 投影；配置文件只注册新的稳定 option 和文案。旧 JSONL 缺失新增 Reply 键时保留已有引用 ID/发送者 ID。

**Tech Stack:** Python 3、AstrBot message components、pytest、ruff。

## Global Constraints

- 新 option 固定为 `xml_delta`；`legacy_delta`、`plain_lines`、`native_messages` 与默认值保持不变。
- renderer 为空输入时返回 `[]`，有输入时仅返回一个 `role=user` 字典。
- `group_id`、`group_name` 只置于 `<group_messages_delta>` 外层属性。
- XML 动态属性和文本使用标准转义；组件顺序与 JSONL `components` 顺序一致。
- JSONL 旧记录保持可读，Reply 新字段全部可选。
- 组件映射：`text`、`reply`、`at`/`mention`、`image`、`face`、`poke`、`voice`、`video`、`file`；`user_id="all"` 映射为 `<mention all="true"/>`，未知组件映射为 `<component type="…"/>`。
- 媒体 URL 或本地路径仅作为 XML 属性；本任务不向 Provider 注入图像、音频或视频二进制输入。

---

### Task 1: 持久化完整 Reply 组件事实

**Files:**
- Modify: `event_codec.py:16-52`
- Test: `tests/test_event_codec.py:189-207`

**Interfaces:**
- Consumes: `Comp.Reply.id`, `sender_id`, `sender_nickname`, `time`, `message_str`。
- Produces: `{"type": "reply", "message_id": str, "sender_id": str, "sender_name": str, "timestamp": str, "text": str}`。

- [ ] **Step 1: 写入失败测试**

```python
assert record["components"][0] == {
    "type": "reply", "message_id": "msg-199", "sender_id": "10000",
    "sender_name": "Quoted", "timestamp": "1710000000", "text": "被引用正文",
}
```

- [ ] **Step 2: 运行失败测试**

Run: `$env:PYTHONPATH='D:\Project\2026\AstrBot'; D:\Project\2026\AstrBot\.venv\Scripts\python.exe -m pytest tests/test_event_codec.py -q`

Expected: Reply 字典缺少 `sender_name`、`timestamp`、`text`。

- [ ] **Step 3: 实现最小 codec 变更**

在 `_component_record()` 的 `Comp.Reply` 分支通过 `_value()` 写入 `component.sender_nickname`、`component.time`、`component.message_str`。

- [ ] **Step 4: 运行 codec 测试**

Run: `$env:PYTHONPATH='D:\Project\2026\AstrBot'; D:\Project\2026\AstrBot\.venv\Scripts\python.exe -m pytest tests/test_event_codec.py -q`

Expected: PASS。

### Task 2: 实现 XML 组件聚合 renderer

**Files:**
- Modify: `context_renderers.py:12-147`
- Test: `tests/test_context_renderers.py:6-148`

**Interfaces:**
- Consumes: `list[dict[str, Any]]` JSONL records。
- Produces: `XmlDeltaRenderer.render(events) -> list[dict[str, str]]`，名称为 `xml_delta`。

- [ ] **Step 1: 写入失败测试**

```python
messages = build_renderer("xml_delta").render(events)
assert messages == [{"role": "user", "content": expected_xml}]
assert '<mention user_id="10001" name="Alice"/>' in messages[0]["content"]
assert '&lt;tag&gt;&amp;' in messages[0]["content"]
```

- [ ] **Step 2: 运行失败测试**

Run: `$env:PYTHONPATH='D:\Project\2026\AstrBot'; D:\Project\2026\AstrBot\.venv\Scripts\python.exe -m pytest tests/test_context_renderers.py -q`

Expected: `unknown context renderer: xml_delta`。

- [ ] **Step 3: 实现最小 renderer**

使用 `xml.sax.saxutils.escape` 和 `quoteattr` 构造 XML；按 `components` 顺序将 `at` 投影为 `mention`，将 Reply 投影为嵌套 `<reply><text>…</text></reply>`，将未知类型投影为 `<component type="…"/>`。为无 components 的旧记录创建 `<text>` 回退。

- [ ] **Step 4: 注册并通过测试**

在 `_RENDERERS` 注册 `XmlDeltaRenderer.name`，运行 renderer 测试。

Run: `$env:PYTHONPATH='D:\Project\2026\AstrBot'; D:\Project\2026\AstrBot\.venv\Scripts\python.exe -m pytest tests/test_context_renderers.py -q`

Expected: PASS。

### Task 3: 配置、文案与回归验证

**Files:**
- Modify: `_conf_schema.json:45-51`
- Modify: `.astrbot-plugin/i18n/zh-CN.json:31-34`
- Modify: `.astrbot-plugin/i18n/en-US.json:31-34`
- Modify: `README.md:31-41`
- Test: `tests/test_i18n.py:42-87`

**Interfaces:**
- Consumes: 配置值 `xml_delta`。
- Produces: schema 与双语 UI 中的第四个 renderer option，中文名为 `XML 增量块（结构化组件）`。

- [ ] **Step 1: 写入失败 i18n 测试**

```python
assert renderer["options"] == ["legacy_delta", "plain_lines", "native_messages", "xml_delta"]
assert zh["config"]["context"]["renderer"]["labels"][3] == "XML 增量块（结构化组件）"
```

- [ ] **Step 2: 更新 schema、locale 与 README**

追加 `xml_delta` 的 option、双语 label/hint，并在 README 说明一个 User 块、外层群元数据和结构化组件投影。

- [ ] **Step 3: 运行全量验证**

Run:

```powershell
$env:PYTHONPATH='D:\Project\2026\AstrBot'
D:\Project\2026\AstrBot\.venv\Scripts\python.exe -m pytest -q
D:\Project\2026\AstrBot\.venv\Scripts\python.exe -m ruff check .
D:\Project\2026\AstrBot\.venv\Scripts\python.exe -m compileall -q .
D:\Project\2026\AstrBot\.venv\Scripts\python.exe -c "import sys; sys.path.insert(0, 'D:\Project\2026'); import astrbot_plugin_group_agent_flow.main"
git diff --check
```

Expected: 所有命令成功。
