# 向音频模型附带快照语音

## 目标

当所选 Provider 明确声明 `audio` 模态时，在首轮 Agent 请求中附带冻结快照、并由现有观察预算选出的语音；不得在普通聊天上下文暴露其 URL，也不得修改 Core。

## 已确认事实

- Core `ProviderRequest.audio_urls` 会在首轮请求中把远程 URL 或本地路径交给音频模型。
- 插件已经通过冻结快照和 `PreparedObservation.source_seqs` 绑定单次 Agent 运行的上下文。
- `Record` 没有时长字段；Core `get_media_duration(local_path)` 可通过 `ffprobe` 取得毫秒时长，而插件当前观察预算只计算渲染文字。

## 需求

- 仅当所选 Provider 的 `provider_config.modalities` 显式包含 `audio` 时附带语音。
- 语音实体化后以 `ceil(duration_ms / 1000 × 6.25)` 计入现有 `context.max_context_tokens`；仅附带同一预算选中的记录中的语音，且保持消息和组件顺序。
- 不新增语音专用配置；不向未声明 `audio` 的 Provider 附带语音。
- 无法下载、探测时长或识别格式的语音不附带；保留其普通上下文标记和文字，并只记录不含 URL 的诊断。

## 验收标准

- [ ] 附带的语音仅来自冻结快照及其预算选中的记录。
- [ ] 文字与语音合计超过 `max_context_tokens` 时，既有裁剪逻辑会排除较旧记录。
- [ ] `audio` Provider 的 `req.audio_urls` 收到有序的有效语音引用。
- [ ] 未声明 `audio` 的 Provider 不接收语音引用。
- [ ] 时长探测失败不会超出 token 预算或中断该轮推理。
