# 语音转写回退

## 目标

让未声明 `audio` 模态的所选 Provider 能通过显式工具请求当前会话中、冻结快照内语音的 AstrBot Core STT 转写。

## 已确认事实

- Core 公开 `Context.get_using_stt_provider(umo)` 和 `STTProvider.get_text(audio_url)`。
- 会话或全局 STT 配置禁用、缺失时，Core 返回空 STT Provider。
- Agent 工具循环支持文字工具结果，因此返回转写无需改 Core。

## 需求

- 仅为未声明 `audio` 的聊天 Provider 暴露显式转写工具。
- 工具只接受当前冻结快照中含语音的消息。
- 通过 `event.unified_msg_origin` 取得会话选中的 STT Provider。
- 成功返回转写文本；失败仅返回 `stt_unavailable`、`stt_failed`、`message_not_found_in_snapshot` 或 `message_contains_no_voice`。
- 任何工具结果均不得含原始语音 URL，也不得自动发送群消息。

## 验收标准

- [ ] 音频 Provider 不暴露转写工具；文字 Provider 暴露该工具。
- [ ] 工具拒绝快照外消息和不含语音的消息。
- [ ] 工具以内部语音引用调用会话选中的 STT Provider。
- [ ] 成功、各类无 URL 失败结果均有回归测试。
