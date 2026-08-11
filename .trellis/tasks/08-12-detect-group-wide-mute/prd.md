# 识别 QQ 全员禁言

## Goal

当 QQ 群开启全员禁言时，让 `send_message` 与 `reply_message` 的失败结果向模型明确反馈
群级禁言，使模型能够选择 `stay_silent`，并避免返回冗长、无语义的 `ActionFailed` 平台文本。

## Confirmed Facts

- 当前 `QQActionGateway.failure_detail()` 只查询当前机器人在
  `get_group_member_info(...).shut_up_timestamp` 中的个人禁言状态。
- NapCat 的 `get_group_info` 返回 `group_all_shut`，该字段表达全员禁言；全员禁言时个人
  `shut_up_timestamp` 可以为 `0`。
- 现场工具结果包含 `NodeIKernelMsgService/sendMsg` 失败与 `result=120`，随后回传原始
  `ActionFailed`，说明现有个人禁言核验无法识别群级状态。
- 外部动作的工具结果会由 AstrBot Core 保存为跨轮工具历史；明确的失败详情可以供后续模型
  决策使用。

## Requirements

- 保留现有针对未来个人 `shut_up_timestamp` 的详情：
  `你已被禁言，无法发送消息；解禁时间：<Asia/Shanghai ISO 时间>`。
- 对 `send_message` 和 `reply_message` 的 QQNT `sendMsg` 失败，个人禁言未确认时查询
  `get_group_info(group_id, self_id)`。
- `group_all_shut` 为真时，工具结果和失败动作记录使用同一详情：
  `群已开启全员禁言，无法发送消息`，并省略原始 `ActionFailed`。
- 任一查询返回异常、无效结构或未确认禁言时，保留原始 `str(exc)`。
- 保持 `success`、`action`、`error` JSON 字段，以及 `react_message`、`poke_user`、成功动作、
  `stale_run` 的现有语义。
- 保持 Agent Loop 的既有执行策略；本任务通过准确工具结果让模型和跨轮历史感知全员禁言。

## Acceptance Criteria

- [ ] `group_all_shut=1` 的真实 `ActionFailed` 结果包含 `群已开启全员禁言，无法发送消息`，且不包含原始平台错误。
- [ ] 全员禁言失败动作记录与模型工具结果使用同一详情。
- [ ] 个人禁言详情继续优先输出解禁时间。
- [ ] 群信息查询异常、无效或未开启全员禁言时保留原始平台错误。
- [ ] 全量插件测试、Ruff、编译和导入检查通过。

## Out of Scope

- 改变 QQ 群禁言设置或自动解除禁言。
- 根据 `result=120` 推断禁言。
- 修改 AstrBot Core 的 Agent Loop 策略。
