# Source Notes

## 研究快照

- `openai/codex`: `f297b9f07de10c7d8b9ed284b674d06cc5ff7723`
- 获取日期：2026-06-13
- 本章研究日期：2026-06-15

## 实际阅读

### Hooks 引擎与事件

- `codex-rs/hooks/src/engine/mod.rs`
- `codex-rs/hooks/src/engine/dispatcher.rs`
- `codex-rs/hooks/src/engine/output_parser.rs`
- `codex-rs/hooks/src/engine/mod_tests.rs`
- `codex-rs/hooks/src/events/pre_tool_use.rs`
- `codex-rs/hooks/src/events/post_tool_use.rs`
- `codex-rs/hooks/src/events/permission_request.rs`
- `codex-rs/hooks/src/config_rules.rs`
- `codex-rs/hooks/src/types.rs`

### Core 集成

- `codex-rs/core/src/hook_runtime.rs`
- `codex-rs/core/src/tools/registry.rs`
- `codex-rs/core/src/tools/orchestrator.rs`
- `codex-rs/core/src/tools/lifecycle.rs`
- `codex-rs/core/src/tools/network_approval.rs`
- `codex-rs/core/src/tools/runtimes/shell/unix_escalation.rs`

### Exec Policy

- `codex-rs/execpolicy/README.md`
- `codex-rs/execpolicy/src/decision.rs`
- `codex-rs/execpolicy/src/policy.rs`
- `codex-rs/execpolicy/src/rule.rs`
- `codex-rs/execpolicy/src/parser.rs`
- `codex-rs/execpolicy/tests/basic.rs`
- `codex-rs/core/src/exec_policy.rs`
- `codex-rs/core/src/exec_policy_tests.rs`

## 从源码确认的事实

- 当前 hooks engine 支持多种事件，包括 PreToolUse、PermissionRequest、PostToolUse、
  SessionStart、UserPromptSubmit、Stop、compact 和 subagent 事件。
- hook handler 可以按事件和 matcher 选择；同一个 handler 即使命中多个兼容 alias，也只为一次
  工具调用运行一次。
- hooks engine 会先 preview 匹配 handler，使客户端能显示运行中的 hook，再发出完成状态。
- 匹配 handlers 可并发执行，但完成报告保持配置顺序；PreToolUse 的多个输入重写以实际最后完成的
  rewrite 为准。
- PreToolUse 在工具 handler 之前运行。它可以阻断工具调用、提供 additional context，或在允许
  决策中返回 updated input。
- Core registry 收到 PreToolUse updated input 后，会让具体 tool handler 重新构造 invocation；
  重写失败会在 handler 执行前结束调用。
- PreToolUse block 会作为模型可见工具错误返回，并把 extension lifecycle outcome 标记为
  blocked。
- PermissionRequest hook 只在 approval path 中运行。它可以返回 allow、deny，或不做决定并回退到
  Guardian / 用户审批。
- 多个 PermissionRequest hook 决策采用保守折叠：任意 deny 胜出；否则保留 allow；没有决定时
  继续正常审批。
- PermissionRequest hook 在 Guardian 或用户审批 UI 之前生效，但不会取代后续 sandbox 强制。
- PostToolUse 只在工具产生成功输出且 handler 提供稳定 hook payload 时运行。它接收规范化
  tool name、input 与 response，而不是任意内部结构。
- PostToolUse 可以提供 additional context、反馈文本或停止后续处理。Core 可用 hook feedback
  替换模型可见工具输出，同时保留原始结果用于日志。
- hook 的失败、无效 JSON、超时和非零退出状态会被记录为失败状态；许多解析或执行失败路径采取
  fail-open，工具操作继续，明确 block/stop 决策除外。
- Hook 配置发现与启用状态受来源和 trust 控制；user/session 可以覆盖 hook state，requirements
  可以只允许 managed hooks。
- `codex-execpolicy` 当前公开决策为 Allow、Prompt、Forbidden，并按该顺序定义严格度。
- prefix rule 按有序 token 前缀匹配，可带 alternatives、justification、match/not_match 示例。
- 一个命令可能同时命中多条规则；effective decision 取所有匹配中的最严格结果：
  Forbidden > Prompt > Allow。
- 多命令检查同样聚合所有命令匹配并取最严格结果。
- 没有规则命中时，execpolicy core 可以调用 heuristics fallback；heuristics match 与显式规则
  match 可被区分。
- Core `ExecPolicyManager` 将 policy evaluation 转成 `ExecApprovalRequirement`：
  Allow → Skip，Prompt → NeedsApproval 或因 approval policy 冲突变为 Forbidden，
  Forbidden → Forbidden。
- 显式 allow 规则只有在所有解析出的命令段都被规则允许时，才可能允许首轮绕过 sandbox；
  policy allow 不普遍等于无 sandbox 执行。
- exec policy 可以从启用的 config layer 对应 rules 目录加载，并叠加 requirements policy；
  untrusted project layer 的 rules 不会生效。
- Exec policy amendment 可以在用户批准后追加 allow prefix rule，并同步更新内存 policy。
- Tool lifecycle extensions 的 start/finish 通知与用户配置的 command hooks 是相邻但不同的扩展
  机制。

## 教学实现的简化

- 教学版只实现 PreToolUse、PermissionRequest 和 PostToolUse 三种工具生命周期 hook。
- 教学版 hooks 是同步 Python callbacks，不发现或运行外部命令，不实现 timeout、并发、
  stdout JSON schema、output spill、plugin 环境或 trust hash。
- 教学版按注册顺序串行运行 hooks，并让后一个 PreToolUse hook 看到前一个 hook 的 rewrite；
  不模拟真实 handlers 基于同一请求并发执行、再按完成顺序选择 rewrite 的语义。
- 教学版 PermissionRequest 遇到首个 deny 就短路，不继续执行后续 hook；真实引擎执行匹配
  handlers 后再保守折叠决定。
- 教学版 matcher 只支持精确 tool name 或匹配全部，不实现 regex 和兼容 alias。
- 教学版 hook 失败发出 started/completed 事件并 fail-open，不实现所有事件各自不同的失败语义。
- 教学版 PostToolUse feedback 直接替换返回模型的字符串，不单独保留日志可见原始输出包装器。
- 教学版 ExecPolicy 只支持精确 token prefix、justification 和固定 fallback。
- 教学版不解析 shell 字符串、复合命令、heredoc、PowerShell、host executable 或 Starlark
  `.rules` 文件。
- 教学版没有 exec policy amendment、network rules、requirements overlay 或 rules 文件加载。
- 教学 `exec_command` 是离线模拟 handler，不启动子进程，因此不声称 policy 可以替代 OS
  sandbox。
- 教学版保留 s06-s08 的 approval、sandbox 和 config 行为，Exec Policy 只负责产生审批需求。

## 未确认与不写入正文的内容

- 不把当前 hooks 事件全集描述为永久稳定协议。
- 不声称所有 hook 失败都必然 fail-open；不同 hook 类型和明确 block/stop 输出具有不同语义。
- 不声称 PostToolUse 会在失败工具调用后运行。
- 不声称 hooks 与 extension lifecycle contributors 是同一个 API。
- 不声称 Exec Policy 是通用工具策略引擎；当前重点是 shell/exec 与 network policy 规则。
- 不声称 policy Allow 等于用户批准、sandbox disabled 或命令一定成功。
- 不声称 PermissionRequest hook 的 allow 可以绕过 sandbox 或其他强制边界。
- 不声称教学版 prefix matcher 能安全解析真实 shell 命令。
