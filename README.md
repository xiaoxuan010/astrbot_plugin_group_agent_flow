# astrbot_plugin_group_agent_flow

面向 QQ 群聊的自主 Agent 插件。项目 fork 自
[`astrbot_plugin_group_context_flow`](https://github.com/Tsukumi233/astrbot_plugin_group_context_flow)，
保留其持久化增量上下文能力，并将触发和回复流程改为固定快照与工具调用。

## Phase 1 行为

消息按群持久化后进入 `WAITING -> DEBOUNCE -> SNAPSHOT -> REASONING -> TOOL ACTION`。
推理开始时冻结 `snapshot_seq`，推理期间到达的消息进入下一轮，也不会让当前轮重判。
活跃群通过 `min_cycle_interval_seconds` 限制推理频率。同一 Provider 响应可以包含多个外部动作工具；
当前工具批次完成后，AstrBot Agent Loop 直接结束，不再追加一次模型请求。模型普通 `content`、
`No Action Needed` 等控制说明和角色扮演文本都属于内部输出，只有显式动作工具可以形成群消息。
没有动作工具时，本轮保持沉默。

可用工具：

- `send_message`
- `reply_message`
- `react_message`
- `poke_user`
- `get_message`
- `search_chat_history`

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

三个 option value 为兼容已保存配置保持不变。`---` 是人为分隔协议，提供比普通换行更明显的
记录边界；仓库中尚无模型评测能确定最优格式。renderer 会在某个 conversation 首次运行时固定，
修改默认值只影响新 conversation，方便对三种格式进行独立对照。

插件热重载可能留下正在等待的旧调度协程。旧协程的 `snapshot_seq` 已被新实例消费时，插件会在
Provider 调用前记录 `empty_snapshot_skipped` 并终止该轮；cursor 始终单调递增。无文本且无组件的
适配器传输事件可以保留用于诊断，进入模型前会被过滤。

完整工具调用会继续由 AstrBot 原生 Agent history 保存。纯普通 `content` 会标记为不保存；
与工具调用同响应的 `content` 仅作为 Provider 协议上下文保留，始终不会发送到群内。群消息原始组件、`message_id`、
`reply_to`、发送者和时间戳存放在插件 JSONL 中，较早内容可通过历史工具按需读取。NapCat 的群戳一戳通知会记录
发起者和目标 QQ；戳到机器人时，该事件会标记为直接面向机器人。`poke_user` 仅接受冻结快照中已经出现的 QQ 用户。

## 配置

安装后先设置：

```yaml
agent_settings:
  enabled: true
  authorized_group_ids:
    - "1"
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
- `/gaf_clear`：管理员清空当前群的日志、游标和 renderer 分配。

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
