# 语音消息处理设计

## 范围与边界

插件继续在 SQLite 事件记录中保留 QQ 原始语音引用，但将其用途分为普通观察上下文、首轮 Provider 请求和显式 STT 工具三条边界。任何语音 URL 都不得作为普通文本对模型可见。本次不修改 Core。

## 数据流

1. `event_codec.py` 继续将语音保存为带 URL 的内部 `voice` 组件。
2. `context_renderers.py` 输出 `<voice/>`，不输出属性；`agent_tools.py` 也会从 `get_message` 和历史查询的 `voice` 条目中移除 `url`。
3. `main.inject_snapshot()` 判断所选 Provider 是否显式具备 `audio` 模态，并将该能力传入观察准备流程。
4. 音频分支用 Core `MediaResolver.as_path()` 临时实体化语音，并以 `get_media_duration()` 得到毫秒时长；临时文件离开上下文即清理，原始引用不会进入文本上下文。每段按 `ceil(duration_ms / 1000 × 6.25)` 计入预算；“优先保留最新消息块”的裁剪逻辑据此选择最终记录集合及有序语音引用。
5. `main.inject_snapshot()` 将预算保留的原始引用赋给 `req.audio_urls`；Core 只在首轮、音频 Provider 请求中再次实体化它们。
6. 对未声明 `audio` 的 Provider，`agent_tools.py` 暴露 `get_voice_transcript(message_id)`：校验快照、取得内部引用、调用 `context.get_using_stt_provider(event.unified_msg_origin)`，只返回转写或紧凑的无 URL 错误。

## 接口契约

- `PreparedObservation` 新增 `audio_urls: tuple[str, ...]`。其顺序是消息序号、再组件顺序；只包含 `source_seqs` 所列记录的语音。
- `resolve_voice_attachments(records, max_context_tokens)` 返回已探测时长的内部附件；`prepare_observation_records(..., audio_attachments=())` 将其与现有文字上下文共同计费。默认空元组保证其他调用点兼容。
- `ToolRuntime` 新增私有语音引用查询与 `get_voice_transcript` 函数工具；模型可见记录投影会将 `type == "voice"` 的组件投影为 `{ "type": "voice" }`。

## 封顶与隐私规则

- 既有 Buffer 记录数封顶继续限制保留的输入记录。
- 既有插件观察 token 封顶会计入每段语音的 `ceil(duration_ms / 1000 × 6.25)`；渲染文字加语音估算超过预算的记录集合不会被选中。
- 组装 `req.audio_urls` 后，Core 既有 Provider 上下文封顶继续生效。不增加配置键，不记录 URL，也不在文字上下文保留 URL。

## 失败处理

音频下载、实体化、时长探测或格式识别失败时，插件不附带该条音频，继续使用其无 URL 标记和文字上下文，并记录不含 URL 的诊断；不得影响整轮推理或放宽预算。没有 STT Provider 时返回 `stt_unavailable`，`get_text` 抛异常时返回 `stt_failed`；两类错误均不含 URL、路径或异常细节。

## 兼容性与回滚

记录结构不变，仍使用既有 `voice.url`，因此无需 SQLite 迁移；变化仅发生在模型投影。移除 Provider 的 `audio` 模态即可恢复原有文字请求形态。回退插件提交即可移除语音附件和转写工具，无需回滚数据库。
