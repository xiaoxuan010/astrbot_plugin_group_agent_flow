# 修复语音 URL 混入聊天记录上下文

## 目标

让群聊 Agent 能感知语音存在，但不让 QQ 语音 URL 出现在渲染上下文或普通 `get_message` 输出中。

## 已确认事实

- `event_codec._component_record()` 将 `Comp.Record` 持久化为含 `url` 的 `voice` 组件。
- `context_renderers._xml_component()` 当前会生成 `<voice url="..."/>`。
- `ToolRuntime._model_visible_record()` 当前只移除图片 `source_url`，`get_message` 仍会泄露语音 URL。

## 需求

- 普通上下文将语音渲染为无 URL 的 `<voice/>` 标记。
- 混合消息保留文字、消息 ID 与发送者元数据。
- 原始语音引用仅保留在插件 SQLite 记录中，供语音附件和 STT 子任务使用；不得从渲染上下文或模型可见工具中暴露。

## 验收标准

- [ ] 纯语音和混合消息的渲染均不含原始语音 URL。
- [ ] 混合消息保留文字和无 URL 的语音标记。
- [ ] `get_message` 不返回语音 URL，而内部存储仍有后续子任务所需的引用。
- [ ] 图片、引用、提及、戳一戳、文件和视频渲染保持原行为。
