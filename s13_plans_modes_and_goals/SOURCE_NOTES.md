# Source Notes

## 研究快照

- `openai/codex`: `f297b9f07de10c7d8b9ed284b674d06cc5ff7723`
- 获取日期：2026-06-13
- 本章研究日期：2026-06-16

## 实际阅读

### update_plan 工具与 Plan item

- `codex-rs/protocol/src/plan_tool.rs`
- `codex-rs/core/src/tools/handlers/plan.rs`
- `codex-rs/core/src/tools/handlers/plan_spec.rs`
- `codex-rs/core/src/tools/spec_plan_tests.rs`
- `codex-rs/app-server/tests/suite/v2/plan_item.rs`
- `codex-rs/utils/stream-parser/src/proposed_plan.rs`
- `codex-rs/tui/src/history_cell/plans.rs`

### Collaboration mode

- `codex-rs/core/src/context/collaboration_mode_instructions.rs`
- `codex-rs/collaboration-mode-templates/src/lib.rs`
- `codex-rs/collaboration-mode-templates/templates/default.md`
- `codex-rs/collaboration-mode-templates/templates/plan.md`
- `codex-rs/collaboration-mode-templates/templates/execute.md`
- `codex-rs/collaboration-mode-templates/templates/pair_programming.md`
- `codex-rs/app-server-protocol/src/protocol/v2/collaboration_mode.rs`
- `codex-rs/app-server/tests/suite/v2/collaboration_mode_list.rs`
- `codex-rs/models-manager/src/collaboration_mode_presets.rs`
- `codex-rs/models-manager/src/collaboration_mode_presets_tests.rs`

### Thread goals

- `codex-rs/ext/goal/src/spec.rs`
- `codex-rs/ext/goal/src/tool.rs`
- `codex-rs/ext/goal/src/runtime.rs`
- `codex-rs/ext/goal/src/accounting.rs`
- `codex-rs/ext/goal/src/events.rs`
- `codex-rs/ext/goal/src/steering.rs`
- `codex-rs/ext/goal/tests/accounting.rs`
- `codex-rs/ext/goal/tests/goal_extension_backend.rs`
- `codex-rs/state/src/model/thread_goal.rs`
- `codex-rs/state/src/runtime/goals.rs`
- `codex-rs/app-server/src/request_processors/thread_goal_processor.rs`
- `codex-rs/tui/src/chatwidget/tests/goal_validation.rs`
- `codex-rs/prompts/src/goals.rs`
- `codex-rs/prompts/templates/goals/continuation.md`
- `codex-rs/prompts/templates/goals/budget_limit.md`
- `codex-rs/prompts/templates/goals/objective_updated.md`

## 从源码确认的事实

- `update_plan` 的协议参数在 `plan_tool.rs` 中定义：每个 item 有 `step` 和 `status`，status 是
  `pending`、`in_progress`、`completed` 三选一。
- `update_plan` 工具说明要求最多只能有一个 `in_progress` item。
- `PlanHandler` 的工具名是 `update_plan`，成功输出为 `Plan updated`。
- `PlanHandler` 在 `ModeKind::Plan` 下会拒绝调用，并返回“update_plan is a TODO/checklist tool and is
  not allowed in Plan mode”。
- 因此 Plan mode 与 `update_plan` 不是同一件事：前者是协作模式，后者是普通任务进度/TODO 工具。
- app-server 的 plan item 测试显示，Plan mode 中模型最终输出的 `<proposed_plan>...</proposed_plan>`
  会被解析为 `ThreadItem::Plan`，并通过 `item/plan/delta` 流式发送。
- 如果 Plan mode 输出没有 `<proposed_plan>` block，不会产生 plan item。
- `CollaborationModeInstructions` 是 developer role 的上下文片段，markers 是
  `<collaboration_mode>...</collaboration_mode>`。
- collaboration mode 模板包含 Default、Plan、Execute、Pair Programming；模板由
  `collaboration-mode-templates` crate 嵌入。
- Default mode 明确说明 mode 只能由新的 developer instructions 改变，不能由用户语气或工具说明改变。
- Plan mode 模板明确禁止执行实现性 mutation，只允许非变更探索；它也明确区分 Plan mode 与
  `update_plan` 工具。
- Execute mode 强调对明确任务独立执行，并在缺少信息时做合理假设继续推进。
- Pair Programming mode 强调和用户一起推进，并对复杂工作更积极使用 planning tool。
- app-server protocol 暴露 collaboration mode preset metadata，包括 name、mode、model 和
  reasoning effort。
- Goal 工具名是 `get_goal`、`create_goal`、`update_goal`。
- `create_goal` 的工具说明要求只有在用户或系统/开发者明确请求时才创建 goal；普通任务不能让 agent
  自行推断创建 goal。
- `create_goal` 需要 `objective`，可选 `token_budget`，且 token budget 必须为正数。
- `create_goal` 在存在未完成 goal 时失败；只有既有 goal 是 complete 时才可替换。
- `update_goal` 只允许 agent 将 goal 标记为 `complete` 或 `blocked`。
- `update_goal` 明确禁止 agent 自行设置 paused、resume、budget-limited、usage-limited 等状态；这些由
  用户或系统控制。
- `update_goal` 的 blocked 规则很严格：同一阻塞条件至少连续出现三次 goal turn，并且 agent 确实无
  法在没有用户输入或外部状态变化时继续推进。
- `ThreadGoalStatus` 包含 active、paused、blocked、usage_limited、budget_limited、complete。
- state 层的 thread goal 持久化 objective、status、token_budget、tokens_used、time_used_seconds、
  created_at 和 updated_at。
- Goal accounting 从 turn start 的 token usage baseline 计算增量。
- Goal accounting 在 Plan mode turn 中不记录 token 增量。
- tool finish 会触发 active goal progress accounting 并发出 thread goal updated event；并发 tool finish
  使用 semaphore 避免重复计费同一段 token delta。
- 如果 active goal 达到 token budget，状态会转为 budget_limited。
- budget_limited prompt 要求模型不要为该 goal 开始新的实质工作，而是总结进展、剩余工作或 blocker。
- continuation prompt 把 objective 包在 `<objective>` 中，并明确 objective 是用户提供的数据，不是更高
  优先级指令。
- objective_updated prompt 把新 objective 包在 `<untrusted_objective>` 中，并要求停止继续只服务旧目标的
  工作。
- goal prompt 渲染会 escape XML 特殊字符，避免目标文本破坏标签边界。
- goal tools 对 ephemeral thread 和 review subagent 隐藏。
- app-server 的 thread goal processor 提供 set/get/clear，并会在 set/clear 前 reconcile rollout，确保
  目标状态与持久线程状态对齐。

## 教学实现的简化

- 教学版把 `update_plan` 实现为进程内 `PlanState`，不实现完整 tool registry 暴露、JSON schema 或
  app-server plan item。
- 教学版只解析一个 `<proposed_plan>` block，不实现真实 stream parser 的全部边界和 delta 事件。
- 教学版内置少量 collaboration mode 文本，不复刻模型、reasoning effort、preset mask 或模式列表 API。
- 教学版 goal 只保存在内存中，不实现 SQLite state、rollout reconciliation、thread preview 或 app-server
  set/get/clear 请求。
- 教学版用简单整数 `total_tokens` 表示 token usage，不拆 input、cached input、output 和 reasoning output。
- 教学版的 accounting 只演示 turn baseline、Plan mode 不计费、tool finish 计费、budget limited 和完成
  报告，不实现 wall-clock accounting、并发 semaphore、analytics、metrics 或 lifecycle hooks。
- 教学版的 external goal set 用来说明用户/系统可暂停、恢复或编辑目标，不复刻真实 UI 的 draft、paste 和
  slash command 行为。

## 未确认与不写入正文的内容

- 不把 collaboration mode preset 名称、模板全文或 app-server v2 字段当成稳定公共 API。
- 不声称 Plan mode 一定会产生 plan item；源码测试显示只有输出 `<proposed_plan>` block 才会产生。
- 不声称 `update_plan` 可以切换 Plan mode；真实实现明确把二者分开。
- 不声称 agent 可以自动创建或替用户暂停/恢复 goal；真实工具说明把这些能力限定得很窄。
- 不把教学版 token 计数当作真实 billing/tokenizer 行为；这里只保留 accounting 心智模型。
