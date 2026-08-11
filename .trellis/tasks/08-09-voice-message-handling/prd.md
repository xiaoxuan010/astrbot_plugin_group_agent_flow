# 语音消息处理

## 目标

为群聊 Agent 提供安全、按模型能力区分的 QQ 语音处理：普通上下文不暴露语音 URL；音频模型在首轮收到预算内语音；文字模型可按需通过 AstrBot Core STT 获得转写文本。

## 已确认事实

- 上报的 QQ 纯语音消息形如 `<voice url="..."/>`。
- `event_codec._component_record()` 将 `Comp.Record` 持久化为包含 `url` 的 `voice` 组件；`context_renderers._xml_component()` 会将其渲染为带 URL 的 `<voice/>`，因而泄露到模型上下文。
- Core 的 `ProviderRequest.audio_urls` 支持远程 URL 或本地路径；仅当 Provider 明确声明 `modalities` 包含 `audio` 时，Core 才将其作为音频请求内容传给模型。
- Core 公开 `Context.get_using_stt_provider(umo)` 与 `STTProvider.get_text(audio_url)`；文字工具结果可进入下一轮 Agent，因此插件侧 STT 回退无需修改 Core。
- `Record` 不包含时长字段；Core `get_media_duration(local_path)` 会通过 `ffprobe` 返回毫秒时长。插件现有 `max_context_tokens` 尚未将语音计入选择预算。
- 当前未提交的 `event_codec.py` 与 `tests/test_event_codec.py` 修改属于过期 `agent_action` 清理，不属于本任务，必须原样保留。

## 需求

- 协调以下可独立验收的子任务：
  - `08-09-prevent-voice-context-leak`：从普通模型上下文和模型可见工具结果中移除语音 URL。
  - `08-09-add-get-message-audio-tool`：为音频模型的首轮请求附带冻结快照、且通过预算选择的语音。
  - `08-09-assess-core-stt-fallback`：为文字模型提供快照内语音的 STT 转写工具。
- 混合文字和语音的消息保留文字、消息 ID 与常规元数据，并保留无 URL 的语音标记；原始引用仅留在插件内部记录。
- 语音模型附带同一 `max_context_tokens` 预算所选记录的全部语音，按消息和组件顺序排列。每段使用 `ceil(duration_ms / 1000 × 6.25)` 计费；不得新增语音专用配置。
- 无 STT 配置或 STT 调用失败时，返回不含 URL 的 `stt_unavailable` 或 `stt_failed`；不得自动发送群消息。
- 语音下载、时长探测或格式识别失败时，跳过该条音频附件，保留 `<voice/>` 标记和文字上下文，并只记录不含 URL 的诊断；不得让该条语音绕过 token 封顶或中断整轮推理。
- 不修改 AstrBot Core。

## 验收标准

- [x] 所有活动子任务均有可测试的需求单及明确的依赖顺序。
- [x] 语音 URL 不会出现在渲染上下文、`get_message` 或历史查询的模型可见结果中，且既有非语音组件行为不回归。
- [x] 显式支持 `audio` 的模型只接收冻结快照且通过既有 token 预算选择的语音；其他模型不接收语音附件。
- [x] 文字模型仅能对快照内语音调用 STT，并能获得转写或规定的无 URL 错误。
