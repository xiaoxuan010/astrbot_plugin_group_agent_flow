# `stay_silent` 主动沉默工具设计

日期：2026-07-21
状态：已在对话中批准

## 背景

自主群聊 Agent 当前要求模型在无需群内动作时直接结束，并且不生成普通
assistant 内容。实机记录表明，部分模型即使判断“保持安静”，仍会生成诸如
“派蒙就不打扰啦”之类的普通内容；这些内容随后被工具专用发送边界抑制，最终
记录为 `direct_output_suppressed`。这种行为同时造成真实回复漏发、沉默意图不清
和运行状态难以统计。

## 目标

- 为模型提供明确、可调用、无群副作用的主动沉默出口。
- 区分“模型明确选择沉默”和“模型没有遵守工具协议”。
- 保持群内可见动作只能由现有外部动作工具产生。
- 保持 cursor 提交、快照边界和 Agent Loop 终止语义一致。

## 决策

新增无参数工具 `stay_silent`。它属于内部控制工具，QQ 副作用为零；调用后立即
提交当前观察轮次、推进已消费快照的 cursor，并以 AstrBot 的 `None` 终止信号结束
Agent Loop。

`stay_silent` 在事件上设置 `_group_agent_silence_selected=True`，并将本轮结果记录为
独立 outcome：`silence_selected`。结果分类函数新增 `silence_selected: bool` 输入。
现有结果继续保持原义：

- `action_succeeded`：至少一个群外部动作成功完成。
- `action_failed`、`action_partial`、`action_incomplete`：群外部动作对应的失败状态。
- `silence_selected`：模型明确调用 `stay_silent`。
- `direct_output_suppressed`：模型未调用终止工具或群外部动作，并生成普通内容。
- `no_action`：模型未调用任何终止工具或群外部动作，并以空输出结束。
- `empty_snapshot_skipped`：本轮没有待处理快照消息。

结果分类优先级为：群外部动作结果、`silence_selected`、普通内容抑制、空输出。
`stay_silent` 自身不写入 `_group_agent_external_actions`，避免被分类为
`action_succeeded`。

## 工具与提示词协议

`stay_silent` 的参数 schema 为空对象，不接受原因、文本或其他可选字段。沉默理由
保留在模型内部推理中，不进入群消息，也不进入 outcome detail。

固定系统协议使用与具体工具列表解耦的措辞，要求模型通过工具调用完成群内动作或
主动沉默。工具 schema 负责向模型说明 `stay_silent` 的名称、参数和用途。模型在
完成必要的只读查询后，必须选择以下一种终止方式：

1. 调用一个或多个群外部动作工具；
2. 调用 `stay_silent`。

`get_message` 和 `search_chat_history` 继续作为只读工具返回结构化结果，并允许 Agent
Loop 继续。模型可以先读取上下文，再选择群外部动作或 `stay_silent`。普通 assistant
内容继续保持内部且不可见，用于暴露模型违反工具协议的情况。

## 执行流程

1. 模型读取冻结的群消息快照。
2. 模型可以调用只读工具补充上下文。
3. 模型决定参与群聊时，调用现有群外部动作工具。
4. 模型决定保持安静时，调用 `stay_silent`。
5. `stay_silent` 设置 `_group_agent_silence_selected=True`，调用现有 terminal
   callback，持久化 `silence_selected` 并推进 cursor。
6. 工具返回 `None`，当前 Agent Loop 结束。

终止工具执行后，本轮不再接受后续动作。现有群外部动作的终止行为保持不变。

## 错误与兼容性

`stay_silent` 在自主群聊轮次之外调用时，沿用现有元数据校验并抛出
`RuntimeError("tool called outside an autonomous group run")`。持久化失败沿用当前
terminal callback 的异常传播方式，避免在 cursor 尚未提交时伪造成功沉默。

该设计不增加配置项，不改变现有群动作工具参数，不改变 JSONL 消息格式，也不改变
已保存 outcome 的读取方式。旧状态文件无需迁移；新版本只会开始写入新的
`silence_selected` 字符串值。

## 测试

- 工具集合包含无参数 `stay_silent`。
- 调用 `stay_silent` 时不调用 QQ gateway，也不发送 MessageChain。
- 调用后执行 terminal callback，并返回 AstrBot 的 `None` 终止信号。
- `stay_silent` 记录 `silence_selected` 并推进 cursor。
- `stay_silent` 不写入 `_group_agent_external_actions`。
- 只读工具之后仍可调用 `stay_silent` 完成轮次。
- 群外部动作、`direct_output_suppressed`、`no_action` 和
  `empty_snapshot_skipped` 的既有分类保持通过。
- 固定提示词明确要求群外部动作与主动沉默都通过工具完成，同时不列出具体工具名。
- 每轮提示词仅提醒模型查看近期群聊并通过工具调用完成本轮。
- LLM 可见文案使用“群聊”和“聊天历史”，快照术语只保留在内部边界实现中。

完整验证继续覆盖 pytest、Ruff、compileall、插件导入和 `git diff --check`。
