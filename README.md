# astrbot_plugin_group_agent_flow

面向 QQ 群聊的自主 Agent 插件。项目 fork 自
[`astrbot_plugin_group_context_flow`](https://github.com/Tsukumi233/astrbot_plugin_group_context_flow)，
保留其持久化增量上下文能力，并将触发和回复流程改为固定快照与工具调用。

## Phase 1 行为

消息按群持久化后进入 `WAITING -> DEBOUNCE -> SNAPSHOT -> REASONING -> TOOL ACTION`。
推理开始时冻结 `snapshot_seq`，推理期间到达的消息进入下一轮，也不会让当前轮重判。
每个群同时计算全局与定向启动资格：全局路径沿用 `debounce_seconds` / `direct_delay_seconds`
和 `min_cycle_interval_seconds`，定向路径使用 `direct_delay_seconds` 和
`direct_min_cycle_interval_seconds`（默认 20 秒），两者取较早时间后进入同一个 observation。
`@机器人`、`@全体`、引用机器人、戳机器人和 AstrBot `wake_prefix` 消息建立定向资格；任何
observation 实际启动都会刷新该群共享的全局最短周期间隔。同一 Provider 响应可以包含多个外部动作工具；
当前工具批次完成后，AstrBot Agent Loop 直接结束，不再追加一次模型请求。模型普通 `content`、
`No Action Needed` 等控制说明和角色扮演文本都属于内部输出，只有显式动作工具可以形成群消息。
没有动作工具时，本轮保持沉默。

可用工具：

- `send_message`
- `reply_message`
- `react_message`
- `poke_user`
- `get_message`
- `get_message_images`
- `get_image_captions`
- `search_chat_history`

当前聊天 Provider 的 `provider_config.modalities` 显式包含 `image` 时，工具集只包含
`get_message_images`，它按 `message_id` 返回冻结快照中的原图。其他 Provider 的工具集只包含
`get_image_captions`，它使用 AstrBot `provider_settings.default_image_caption_provider_id`
指定的视觉 Provider 返回图片转述，供文本模型以较低的上下文 token 消耗理解图片。
图片事件会保存 OneBot 原始段中的线上 `data.url`，两个工具调用时优先按需下载该地址；上下文
渲染继续使用原有图片引用，不会把线上地址传给聊天模型。线上地址失效时，原图工具返回
`image_unavailable`。原图工具会先调用 NapCat `get_msg` 刷新当前消息的图片地址；新事件保存
`message_seq` 后，`get_group_msg_history` 还能作为刷新兜底。未配置转述 Provider 时，工具返回
`image_caption_provider_unconfigured`。

发送闸门拦截普通 LLM `content`、推理、Provider 错误和内部工具状态。工具动作通过保存的原始
发送入口直接调用
AstrBot 消息链或 NapCat `set_msg_emoji_like` API。自主运行期间的 AstrBot 核心错误消息也会被发送闸门拦截，
Agent 开始前会重新固定工具集，避免全局 web、shell、cron 或主动发送工具绕过插件的动作边界。
运维指令仍由 AstrBot 命令处理。

## 上下文实验

`context.renderer` 支持三种投影：

- 聚合增量块（短线分隔，`legacy_delta`）：生成一个 User 块，使用 `<group_messages_delta>` 包装，消息之间以 `---` 分隔。
- 聚合增量块（换行分隔，`plain_lines`）：生成一个 User 块，每条群消息占一行，包含发送者、时间和消息 ID。
- 逐消息独占 User 块（`native_messages`）：每条群消息分别生成一条 `role=user` 消息，保留模型请求中的消息边界。
- XML 增量块（结构化组件，`xml_delta`）：生成一个 User 块，使用 `<group_messages_delta>` 在外层传递群号和群名，内部以 XML 表达消息、机器人动作、引用、提及、文本和媒体组件。

四个 option value 为兼容已保存配置保持不变。`---` 是人为分隔协议，提供比普通换行更明显的
记录边界；仓库中尚无模型评测能确定最优格式。修改 `context.renderer` 后，同一群聊的下一轮请求
立即采用新投影格式；已进入 AstrBot Core 会话的 `user`、`assistant`、`tool` 原始历史及 reasoning
保持原样，增量 JSONL 只投影尚未进入该会话的新记录。

首次观察会清空 AstrBot conversation contexts 并建立基线；后续请求保留 Core 多角色历史，附加本轮
增量投影，并把 observation 估算量追加到已持久化的 conversation token usage 基线。`max_context_tokens` 默认 8192，使用 AstrBot Core
`EstimateTokenCounter` 计数。缺少 `history_cursor` 时从 cursor `0` 重建最近 JSONL 后缀；
已有水位时只投影水位后的群事实。观察增量超限时会移除其中最旧记录；单条记录仍超限时保留元数据、内容尾部和 `get_message`
查询提示。更早的完整记录始终保留在 JSONL 中，可通过历史工具读取。

插件热重载或 `/gaf_clear` 会让旧调度 run 失效。旧 run 后续的 cursor、窗口和动作事实回写会被
拒绝；清理先于动作准入时，QQ 网关也不会执行该动作。无文本且无组件的适配器传输事件可以保留
用于诊断，进入模型前会被过滤。

`stay_silent` 返回 `None` 后，AstrBot Agent history 可能跳过该轮 tool call/result；插件 JSONL
承担完整群聊事实来源。入站事件保存为 `record_kind=group_message`，成功执行的
`send_message`、`reply_message`、`react_message`、`poke_user` 保存为
`record_kind=agent_action`。下一轮 renderer 会明确投影 `actor=bot`、动作、目标和成功状态。
动作事实与群消息共同进入持久化观察块；活动窗口内的已完成动作会随对应块稳定重建。
动作内部 ID 使用 `action_id=` 展示，并禁止作为 QQ reply/react 目标。

纯普通 `content` 会标记为不保存；与工具调用同响应的 `content` 仅作为本轮 Provider
协议上下文保留，始终不会发送到群内。群消息原始组件、`message_id`、`reply_to`、发送者和
时间戳存放在插件 JSONL 中，较早内容可通过历史工具按需读取。引用机器人消息会记录为直接
面向机器人；NapCat 的群戳一戳通知会记录发起者和目标 QQ，戳到机器人时同样写入定向标记。
`poke_user` 仅接受冻结快照中已经出现的 QQ 用户。

## 配置

安装后先设置：

```yaml
agent_settings:
  enabled: true
  authorized_group_ids:
    - "1"
scheduling:
  debounce_seconds: 10
  direct_delay_seconds: 1
  min_cycle_interval_seconds: 120
  direct_min_cycle_interval_seconds: 20
```

授权列表默认留空，因此初次加载只记录配置允许后的消息，不会在任何群自主运行。建议关闭
AstrBot 群聊 LTM，避免维护一份闲置的重复记录。插件会接管授权群的默认 LLM 唤醒，并在
事件层阻止内置 `active_reply` 的概率回复。

日常运行建议保持 `record_self_messages: false`。启用后，QQ 适配器回传的机器人消息也可能进入
后续快照并启动新周期。`record_empty_messages` 只控制诊断记录保留；完全空白的传输事件不会触发模型。

旧插件目录 `data/plugin_data/astrbot_plugin_group_context_flow` 会在首次启动时复制到新目录，
迁移标记保证该过程只执行一次。原目录保持不变。

## 指令

- `/gaf_status`：查看授权状态、消息记录数、快照游标和 conversation。
- `/gaf_clear`：管理员清空当前群的日志、游标、观察窗口、运行结果和内存调度状态。

## 本地测试

```powershell
$env:PYTHONPATH='D:\Project\2026\AstrBot'
D:\Project\2026\AstrBot\.venv\Scripts\python.exe -m pytest -q
```

## Trellis 开发流程

仓库通过 `.trellis/` 保存任务、项目规范和跨会话工作记录，通过 `AGENTS.md`、`.codex/`
和 `.agents/skills/` 接入 Codex。查看当前上下文：

```powershell
python -X utf8 .trellis/scripts/get_context.py
```

升级时先更新全局 CLI，再同步项目模板：

```powershell
trellis upgrade
trellis update
```

Codex 用户配置需要启用 `[features].hooks = true`，Codex 0.129+ 首次使用还需要在
`/hooks` 中批准项目的 `UserPromptSubmit` hook。
