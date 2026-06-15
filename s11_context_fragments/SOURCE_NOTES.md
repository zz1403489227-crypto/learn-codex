# Source Notes

## 研究快照

- `openai/codex`: `f297b9f07de10c7d8b9ed284b674d06cc5ff7723`
- 获取日期：2026-06-13
- 本章研究日期：2026-06-15

## 实际阅读

### Context fragment 类型与识别

- `codex-rs/context-fragments/src/lib.rs`
- `codex-rs/context-fragments/src/fragment.rs`
- `codex-rs/context-fragments/src/additional_context.rs`
- `codex-rs/core/src/context/mod.rs`
- `codex-rs/core/src/context/contextual_user_message.rs`
- `codex-rs/core/src/context/contextual_user_message_tests.rs`
- `codex-rs/core/src/event_mapping.rs`
- `codex-rs/core/src/event_mapping_tests.rs`

### 环境、权限与项目指令片段

- `codex-rs/core/src/context/environment_context.rs`
- `codex-rs/core/src/context/environment_context_tests.rs`
- `codex-rs/core/src/context/permissions_instructions.rs`
- `codex-rs/core/src/context/collaboration_mode_instructions.rs`
- `codex-rs/core/src/context/model_switch_instructions.rs`
- `codex-rs/core/src/context/token_budget_context.rs`
- `codex-rs/core/src/context/user_instructions.rs`
- `codex-rs/core/src/agents_md.rs`

### 初始上下文、差异更新与历史管理

- `codex-rs/core/src/session/mod.rs`
- `codex-rs/core/src/session/turn_context.rs`
- `codex-rs/core/src/context_manager/updates.rs`
- `codex-rs/core/src/context_manager/history.rs`
- `codex-rs/core/src/context_manager/history_tests.rs`
- `codex-rs/core/src/session/tests.rs`

## 从源码确认的事实

- Codex 有独立的 `context-fragments` crate，`ContextualUserFragment` 要求片段提供 role、
  markers、body，并由 `render()` 组合成模型可见文本。
- 片段可以渲染为 `user` 或 `developer` role；名称虽叫 `ContextualUserFragment`，真实实现中
  collaboration mode、model switch 等片段返回的是 developer role。
- 有 marker 的片段可用起止 marker 识别，识别逻辑会忽略首尾空白且大小写不敏感；没有 marker 的
  fragment 不会被默认匹配成任意文本。
- `AdditionalContextUserFragment` 使用 `<external_{key}>...</external_{key}>` 形式，并对 value
  使用 token budget 截断；developer 侧 additional context 使用普通 `<key>...</key>` 形式。
- `contextual_user_message.rs` 注册了可识别的 user contextual fragments，包括 AGENTS.md、
  environment context、additional context、skills、user shell command、turn aborted、
  subagent notification、internal model context 和若干 legacy fragment。
- `event_mapping.rs` 会隐藏 contextual user message，不把它们当成普通用户消息展示；developer
  contextual prefixes 包括 permissions、model switch、collaboration mode、realtime、skills、
  personality spec 和 token budget。
- `EnvironmentContext` 渲染为 `<environment_context>`，可以包含 cwd、shell、current date、
  timezone、network、filesystem/workspace roots、permission profile 与 subagents。
- 环境上下文比较 turn 间差异时会忽略 shell；shell 主要属于初始环境描述，不作为普通 steady-state
  diff 的触发因素。
- `EnvironmentContext::diff_from_turn_context_item` 会根据已持久化的 `TurnContextItem` 与当前
  `TurnContext` 生成差异片段；单环境 cwd 未变化时不会重复 cwd，多个环境时会完整列出环境。
- `TurnContext::to_turn_context_item` 持久化 cwd、workspace roots、date、timezone、approval
  policy、sandbox policy、permission profile、network、model、personality、collaboration mode、
  realtime state、reasoning effort 等上下文基线字段。
- `Session::build_initial_context` 将多个 developer sections 聚合为 developer message，将
  AGENTS.md、环境上下文和 contextual user slot 聚合为 user message；特定 guardian developer
  prompt 会独立成单独 developer message。
- 初始 developer sections 可包含 model switch、permissions instructions、developer
  instructions、collaboration mode、realtime、personality、apps、available skills、plugins、
  extension contributors 和 token budget。
- 初始 contextual user sections 可包含 extension contextual user fragments、AGENTS.md user
  instructions 与 environment context。
- `context_manager::updates::build_settings_update_items` 在已有 reference context 时只发差异：
  developer message 包含 model switch、permissions、collaboration mode、realtime、personality；
  user message 目前主要包含 environment diff。
- model switch update 会放在 developer update sections 最前面，以便模型先看到新模型的指令。
- 权限 update 只在 permission profile 或 approval policy 变化时发；环境 update 受
  `include_environment_context` 开关控制。
- `record_context_updates_and_set_reference_context_item` 在 reference baseline 缺失时注入完整初始
  context，否则只注入 settings diffs；无论是否有可见 diff，都会持久化新的 `TurnContextItem`
  并更新 reference baseline。
- `ContextManager` 保存 `reference_context_item`，用于后续 turn 的差异更新；当 baseline 缺失时，
  下一次普通 turn 会完整重注入上下文。
- History rollback 会把待回滚 turn 之前连续的 contextual developer/user update 一起裁掉，避免
  保留只属于被回滚 turn 的上下文变化。
- 如果回滚裁掉的是混合 developer bundle（同时含 contextual fragment 与持久 developer text），
  `ContextManager` 会清空 `reference_context_item`，要求下一 turn 完整重注入上下文。
- `ContextManager` 的 token estimate 会把 base instructions 与 history items 一起估算；它是粗略
  token 估计，不是精确 tokenizer。

## 教学实现的简化

- 教学版只实现少数代表性 fragment：environment、permissions、model switch、collaboration、
  token budget、external user fragment 和 AGENTS.md user instructions。
- 教学版使用 Python dataclass 与字符串 marker 实现，不复刻 Rust trait object、registration proxy
  或 protocol item 类型。
- 教学版的 `ModelMessage` 只是 role + text sections，不实现完整 `ResponseItem`、phase、content
  item、images 或 tool call history。
- 教学版只实现单环境，且 environment diff 只覆盖 cwd、date、timezone、network、workspace roots
  与 permission profile。
- 教学版将 permissions 同时放入 developer fragment，并把 permission profile 名称放入 environment
  filesystem 摘要；真实 Codex 的 filesystem context 来自完整 `PermissionProfile` 与 workspace
  roots。
- 教学版没有实现 apps、plugins、skills 渲染预算、realtime、personality、guardian 分离消息、
  subagents、internal model context 和 legacy fragment。
- 教学版用字符长度截断 external context，不实现真实的 token-budget middle truncation。
- 教学版的 `ContextHistory` 只演示 baseline、diff 与 rollback，不实现 rollout JSONL、resume、
  compaction replacement history 或完整 normalization。

## 未确认与不写入正文的内容

- 不把具体 fragment 集合、开关名称和渲染文本写成永久公共协议；它们随 Codex 功能演进可能变化。
- 不声称所有 context updates 都能由 `TurnContextItem` 完全重建；源码中仍有 TODO 指出 settings
  update 尚未覆盖全部 model-visible initial context。
- 不声称教学版的 XML-like 文本是安全解析格式；真实实现也主要用于模型上下文边界与历史识别。
- 不声称 `AGENTS.md`、Skills、Apps、Plugins 属于同一加载机制；本章只说明它们最终都可能贡献
  model-visible context sections。
