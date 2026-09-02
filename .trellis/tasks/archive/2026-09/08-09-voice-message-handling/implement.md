# 语音消息处理实施计划

## 任务 1：无 URL 的语音投影

**文件：** `context_renderers.py`、`agent_tools.py`、`tests/test_context_renderers.py`、`tests/test_tools.py`。

- [x] 编写失败测试：纯语音和混合组件渲染出 `<voice/>`，但不包含源 URL。
- [x] 编写失败测试：`get_message` 和历史查询工具返回的存储语音不含 URL。
- [x] 将 `voice` 渲染为无属性标记；将模型可见 `voice` 组件投影为 `{ "type": "voice" }`。
- [x] 使用 `D:\Project\2026\AstrBot\.venv\Scripts\python.exe -m pytest` 和工作区可写的 `--basetemp` 运行相关渲染器、工具测试。

## 任务 2：受预算约束的首轮语音附件

**文件：** `observation.py`、`main.py`、`tests/test_observation.py`、`tests/test_main_observation.py`。

- [x] 编写失败测试：语音按 `ceil(duration_ms / 1000 × 6.25)` 计费；裁剪保留最新且能放入预算的记录；返回的语音引用遵循消息/组件顺序；探测失败的语音不附带且不终止推理。
- [x] 扩展 `PreparedObservation` 和观察准备函数，新增探测后的 `audio_attachments` 输入与有序 `audio_urls` 输出。
- [x] 在 `inject_snapshot` 中解析所选 Provider 能力；仅在显式音频能力下，将 `PreparedObservation.audio_urls` 写入 `req.audio_urls`。
- [x] 编写音频/文字 Provider 测试，并断言 `source_seqs` 之外的 URL 绝不进入 `req.audio_urls`。

## 任务 3：文字模型 STT 工具

**文件：** `agent_tools.py`、`tests/test_tools.py`。

- [x] 编写失败测试：工具可见性、快照外拒绝、无语音、未配置 STT、STT 异常和会话级转写成功。
- [x] 新增无 URL 的语音引用查询和 `get_voice_transcript` 处理器。
- [x] 仅在快照和组件校验通过后调用 `self.context.get_using_stt_provider(event.unified_msg_origin)`；通过 `_json` 序列化成功结果和规定错误。
- [x] 运行工具测试模块及完整插件测试集，均使用项目虚拟环境与可写 `--basetemp`。

## 任务 4：集成检查与交付门禁

**文件：** 所有生产和测试改动；不修改配置 schema。

- [x] 运行项目已有 Ruff/格式检查、`compileall`、定向 pytest、完整 pytest、`python .\.trellis\scripts\task.py validate` 和 `git diff --check`。
- [x] 确认既有、与本任务无关的 `agent_action` 清理改动逐字未受影响。
- [x] 按最终代码重新核对 `prd.md`、`design.md` 和 `implement.md`；已记录 Core 运行时限制。
