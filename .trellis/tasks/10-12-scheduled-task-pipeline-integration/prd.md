# 定时任务无缝接入 GAF 观察循环（消除前缀失效丢缓存）

## 背景

AstrBot 的定时任务（`future_task` / active_agent cron job）触发后走一条**绕过完整
pipeline 的专用路径**：`cron/manager.py` 的 `_woke_main_agent` 自己构造
`ProviderRequest`（换掉 system prompt、直连 `build_main_agent`、手动
`step_until_done`），从不把事件放入事件队列。因此：

1. **插件钩子全部不触发**：GAF 的 `observe_group_message`、`inject_snapshot`、
   `finalize_agent_protocol` 等只在 pipeline 内被触发（触发点
   `pipeline/process_stage/method/agent_sub_stages/internal.py` 的
   `call_event_hook(OnLLMRequestEvent)`），cron 路径完全绕开。
2. **system prompt 被替换且逐次变化**：cron 路径把
   `PROACTIVE_AGENT_CRON_WOKE_SYSTEM_PROMPT.format(cron_job=json.dumps(cron_job))`
   用作 system prompt，其中内嵌的 JSON 含 `run_started_at` —— 每次运行都是新的
   system prompt 变体。
3. **与 GAF 共用同一个 conversation**：job payload 的 `session` 就是群会话 umo，
   cron 请求加载的是 GAF 正在用的同一份群历史。

### 已实测的代价（relay 日志分析，2026-10-08 ~ 10-11，1970 请求）

- SCHEDULED 类请求 89 条，累计 miss **2,431,506** tokens（占该窗口总账单 25.4%）。
- 其中 35 条"每次 run 的第一条请求"（前缀基本全冷）占该类 miss 的 **99.4%**
  （2,417,302 tokens）—— 丢失 100% 来自前缀不一致，与容量无关。
- 对照组：GAF 自身 1836 条请求 system prompt 只有 **1 个变体**（fd8da72d28），
  run 内第 2、3 条 cron 请求（system prompt 相同）miss 只有 200-300 tokens。
- cron JSON 逐次变化 → run 之间也无法互相复用；同一时刻多个任务触发 = 同一会话
  连续多次全量冷 prefill。
- 附带污染：cron 结束后 `persist_agent_history` 向群会话追加两条
  （`"Output your last task result below."` + assistant 摘要），混入 GAF 后续历史。
- 附带并发风险：cron 直连路径不抢 session lock，可与真实消息驱动的 GAF 运行
  并发竞争同一 conversation。

### 根因定性

丢缓存不是"容量大"或"图片/渲染格式"问题（均已排除并撤回过错误结论），而是
**调用方前缀不一致**：prefix cache 从 token 0 逐块匹配，两条链路在 system prompt
第一个 token 就分叉，其后 5 万-16 万 token 的共享历史全部作废。

## 用户诉求

> 定时任务触发后不要动 system prompt，把任务要求追加在末尾，无缝融入 GAF 流程
> —— 相当于往群里插入一条消息然后发给 LLM，地位相当于现在的一次 observation。

进一步演进为（见对话记录）：

> 方案要足够通用，不只 GAF 能用，甚至可以提交给 AstrBot 上游。

## 已确认事实（代码调研，2026-10-11/12）

### 为什么"合成事件入队"这条路能走通

1. **核心自带先例**：`builtin_stars/astrbot/main.py` 的空 @ 唤醒场景直接
   `context.get_event_queue().put_nowait(new_event)`，事件重新走完整 pipeline。
   `Context.get_event_queue()`（`core/star/context.py`）对插件可用。
2. **`CronMessageEvent` 本就按"伪装真实消息"设计**：`is_wake=True`、
   `is_at_or_wake_command=True`、session 保留原始群 umo——只差最后一步没入队。
3. **入队后全链路验证结论**（逐阶段读码）：
   - EventBus.dispatch：按 umo 解析配置 → 正确的 PipelineScheduler。
   - WakingCheckStage：插件 handler 的 `event_message_type(GROUP_MESSAGE)`
     filter 对合成事件通过 → **无需 @、无需唤醒词，GAF handler 天然被激活**。
   - GAF `observe_group_message`：note 作为定向记录写入 Buffer →
     directed=True 走 1s direct_delay → coordinator 快照 →
     `request_llm(prompt=OBSERVATION_CYCLE_PROMPT, tool_set=GAF 工具,
     conversation=群会话, system_prompt=self._system_prompt())`。
   - internal 子阶段：`OnLLMRequestEvent` 钩子正常触发 →
     `inject_snapshot` + `finalize_agent_protocol` 正常执行。
   - `observe_group_message` 开头 `should_call_llm(True)` 阻止默认 LLM 兜底分支。
4. **缓存效果**：请求前缀 = GAF system prompt（字节不变）+ 群会话历史 + 观察块，
   定时任务 note 只是 Buffer 里多一条记录（几百 tokens 的 miss 增量）。

### 需要处理的坑

1. `CronMessageEvent` 不设 `msg_obj.group_id` → `get_group_id()` 返回空 →
   GAF `_is_authorized` 拒绝、`flow_id` 断流。**入队前必须补
   `msg_obj.group_id = group_id`**（有 setter，自动建 Group）。
2. `platform_meta.name="cron"` 会影响 persona 解析与 LTM 判断；合成事件应使用
   记录里存的真实 platform name/id。
3. unique_session 若开启，WakingCheckStage 会重写 session_id → umo 变化。
   需确认部署未开启（GAF 依赖群会话粒度）。
4. RateLimitStage 按会话计数，紧限流可能 stall 合成事件（可接受，需知情）。
5. 其他 `ALL` 类型插件 handler 也会看到该事件（如 LTM 群历史持久化）——
   属合理副作用，sender 应设为可识别名字（如 "Scheduler"）。
6. 走 pipeline 后不再需要 core 的 `persist_agent_history`——由 GAF 的
   checkpoint 机制落历史，顺带消除历史污染。

### 通用性依据

- cron 唤醒与后台任务唤醒（`astr_agent_tool_exec.py` 的 background task wake，
  与 cron 同构：同样自己 build req + 直连 `build_main_agent`）是 core **仅有的
  两处绕过 pipeline 的路径**。
- 上游维护热度高：`_run_active_agent_job` 近两个月有 #9987 / #9992 / #10028
  三个修复，历史共 7 次函数体改动。
- pipeline 化后可删掉 manager.py 里 ~80 行手工 `MainAgentBuildConfig` 组装
  （复用 pipeline 的配置构建），即"dedup 叙事"。

### 规模数据

- 日志窗口内 distinct cron job = **9 个**（逐月节提醒系列、催配角B、提醒清日常、
  提醒睡觉等），均通过 WebUI future task 或 `future_task` 工具创建。
- 方案选型结论（已比较三种路线）：
  - **B（通用化 core 补丁 + 上游化）与 A′（插件猴子补丁）成本最低**；
  - A（GAF 自维护任务表）代价最大：迁移一次性工作小，但源头拦截缺口导致
    "双真相源"长期存在，且需在插件里复制一份脱离上游修复的调度逻辑。

## 目标

1. **缓存目标**：定时任务触发的 LLM 请求与普通 GAF 观察请求共享前缀，
   SCHEDULED 类的全量冷 prefill（2.43M tokens / 3 天）降为观察块增量级别
   （预计降幅 >95%）。
2. **语义目标**：定时任务要求以"追加内容"形式进入观察块（地位 = 一次
   observation 中的一条记录），system prompt 字节不变。
3. **通用目标**：core 改造以"通用唤醒改道"形式设计（配置驱动、extras 契约），
   不绑定 GAF；可提交上游。
4. **兼容目标**：默认配置下行为与现状完全一致（零破坏）；现有 9 个任务、
   WebUI 管理、`run_once` 删除、失败记录、时区处理全部原样保留。

## 非目标

- 不迁移任务表（排除路线 A）。
- 不改动 GAF 的观察协议/工具集语义（本次不动 `enforce_tool_set`、渲染器）。
- 不追求上游 PR 一定被合并（上游化是加分项，不是硬依赖）。

## 阶段划分

### Phase 0：上游 bugfix —— CronMessageEvent 群字段补全

- **背景**：`CronMessageEvent` 对群 session 不设置 `msg_obj.group_id`，
  任何消费方拿不到群 ID（对插件、对 core 自身的群上下文逻辑都是缺陷）。
- **目标**：独立的小 PR：构造时按 session 的 message_type 补全
  `group_id`（及必要的 Group 字段）。不引入任何新概念。
- **验收**：合成事件的 `get_group_id()` 返回真实群 ID；现有
  `tests/unit/test_cron_manager.py` 不回归。

### Phase 1：Core feature —— 唤醒改道 pipeline（配置驱动，默认关）

- **背景**：两处绕过 pipeline 的唤醒路径（cron、background task）导致插件
  无法参与、system prompt 前缀失效、绕过 session lock、污染群历史。
- **目标**：
  - 新增配置 `provider_settings.proactive_capability` 下的投递模式开关
    （`direct` | `pipeline`，默认 `direct` = 现行为）；具体命名与粒度
    （全局 / per-job / 工具参数）留到 design 阶段收敛。
  - `pipeline` 模式下：唤醒事件补全字段后 `event_queue.put_nowait()`，
    不再直连 `build_main_agent`。
  - **extras 契约**：事件 extras 携带任务元数据（`cron_job` /
    `background_task_result`，JSON 结构与今日 system prompt 内嵌的完全一致），
    作为插件消费的通用接口。
  - 插件未接管时（fresh req 分支）：唤醒指令作为**尾部**
    extra_user_content_parts 追加，不再 format 进 system prompt
    （对无插件用户语义等价，且修掉 run_started_at 变体化根因）。
- **验收**：
  - 默认 `direct`：行为与现状逐字节一致（回归测试保护）。
  - `pipeline`：无插件场景下任务可正常执行并送达；有插件场景下插件钩子
    完整触发；同一 conversation 的请求前缀与普通消息请求一致。
- **配套**：cron 与 background task 两条路径的测试；manager.py 手工 config
  组装的去重评估（若安全则一并删除，否则留待后续）。

### Phase 2：GAF 侧接入与实测验证

- **背景**：core 改道后，GAF 需要确认端到端行为并量化收益。
- **目标**：
  - 确认合成事件在真实部署下走通 GAF 全链路（授权检查、Buffer 记录、
    快照合并、工具执行、checkpoint 落历史）。
  - GAF 可选增强：读取 extras 中的任务元数据，在观察块内对任务记录做
    更精细的渲染/标记（如 `[定时任务]` 前缀或独立块）。是否做、怎么做
    留到 design 阶段决定。
  - 实测：开启 pipeline 模式后采集新窗口 relay 日志，对比 SCHEDULED 类
    miss 与账单占比，产出前后对比报告。
- **验收**：实测 miss 降幅达到目标（>95%）；GAF 观察循环行为无回归
  （定时任务与真实消息混批、并发触发等边界场景）。

### Phase 3：上游 PR 与收尾

- **背景**：通用化改造的价值最大化路径是回馈上游，降低长期 fork 维护成本。
- **目标**：
  - 以 patch series 形态组织本地分支（bugfix + feature 两个自解释 commit），
    策略为"提 PR → 本地先用 → 上游合并后删分支"。
  - PR 描述携带实测数据（3 天日志基线 + 改造后对比）。
  - 上游无回应/被拒时的兜底：保留 patch series 本地维护，extras 契约与
    配置命名不受影响。
  - WebUI cron 编辑页的 per-job 覆盖项、`future_task` 工具参数等扩展，
    视上游反馈再决定是否做（避免过度设计）。

## 验收标准（总）

- [ ] 定时任务触发的请求与 GAF 普通观察请求共享 system prompt 前缀（日志验证）。
- [ ] SCHEDULED 类全量冷 prefill 消失（新窗口日志实测 miss 降幅 >95%）。
- [ ] 默认配置下 core 行为与现状一致（direct 模式回归测试通过）。
- [ ] 群会话历史不再被 cron 路径追加污染 turn。
- [ ] GAF 观察循环端到端无回归（含定时任务与真实消息混批场景）。
- [ ] core 改造不引用任何 GAF 专有概念（代码审查项）。

## 待收敛的设计问题（design 阶段展开）

1. 配置粒度：全局投递模式 vs per-job 字段 vs `future_task` 工具参数？
2. extras 契约的字段命名与版本化策略。
3. 插件未接管时尾部指令的具体内容与多轮 cron 会话的 prompt 组织。
4. GAF 侧任务记录的渲染形态（普通记录 vs 独立块 vs 仅 sender 标记）。
5. session lock 在 pipeline 模式下的获取方（pipeline 自动 vs 唤醒方显式）。
6. run_once 删除、失败记录等语义在 pipeline 模式下的归属。

## 调研记录

- 2026-10-08~11：relay 日志（908MB / 1970 请求）分析，四轮独立复核，
  修正了 4 处早期错误结论（工具 schema 动态、图片触发切模型等均已撤回）；
  产出分类账（GAF 1836 / SCHEDULED 89 / SUMMARY 13 / OTHER 32）与
  跨类相邻请求 miss 放大 20 倍的量化结论。
- 2026-10-11：读通 cron 全链路（`cron/manager.py`、`cron/events.py`、
  `cron_tools.py`、`astr_main_agent.py`、pipeline 各 stage、
  `waking_check`、`event_bus`、`builtin_stars` 事件入队先例），确认
  "合成事件入队 → GAF handler 天然激活"可行。
- 2026-10-12：确认 background task 唤醒与 cron 同构（两处绕过点）；
  统计 distinct job = 9；统计 `cron/manager.py` 上游改动频率
  （近 5 个月 14 次提交，函数体 7 次）；比较三条路线
  （迁移 / 猴子补丁 / fork 补丁），确定"通用化 + 上游化"方向。
- 分析产物位置：`c:\Users\25920\桌面\group_budget_analysis\`
  （scripts + data/verify/ + docs/）。
