# Source Notes

## 研究快照

- 仓库：`openai/codex`
- Commit：`f297b9f07de10c7d8b9ed284b674d06cc5ff7723`
- 研究日期：2026-06-14

## 实际阅读

### 源码

- `codex-rs/protocol/src/approvals.rs`
- `codex-rs/protocol/src/protocol.rs`
- `codex-rs/core/src/guardian/mod.rs`
- `codex-rs/core/src/guardian/approval_request.rs`
- `codex-rs/core/src/guardian/review.rs`
- `codex-rs/core/src/state/turn.rs`
- `codex-rs/core/src/session/mod.rs`
- `codex-rs/core/src/session/handlers.rs`
- `codex-rs/core/src/tools/orchestrator.rs`
- `codex-rs/core/src/tools/sandboxing.rs`
- `codex-rs/core/src/tools/lifecycle.rs`
- `codex-rs/core/src/tools/runtimes/shell.rs`
- `codex-rs/core/src/tools/runtimes/unified_exec.rs`
- `codex-rs/core/src/tools/runtimes/apply_patch.rs`

### 测试

- `codex-rs/core/src/tools/sandboxing_tests.rs`
- `codex-rs/core/src/tools/runtimes/apply_patch_tests.rs`
- `codex-rs/core/src/guardian/tests.rs`
- `codex-rs/core/tests/suite/approvals.rs`

### 模块文档或提示模板

- `codex-rs/core/src/tools/orchestrator.rs` 模块注释
- `codex-rs/core/src/guardian/mod.rs` 模块注释
- `codex-rs/prompts/templates/permissions/approval_policy/on_request.md`
- `codex-rs/prompts/templates/permissions/approval_policy/on_failure.md`
- `codex-rs/prompts/templates/permissions/approval_policy/unless_trusted.md`
- `codex-rs/prompts/templates/permissions/approval_policy/never.md`

## 从源码确认的事实

- ToolOrchestrator 是 approvals、sandbox selection 与 retry semantics 的统一编排点；运行顺序先处理
  approval，再选择 sandbox 并执行第一次尝试。
  - 证据路径：`codex-rs/core/src/tools/orchestrator.rs`
  - 如何用于本章：教学版只重建 approval gate，并把 sandbox/retry 留到 s07。
- `ExecApprovalRequirement` 明确区分 Skip、NeedsApproval 和 Forbidden。
  - 证据路径：`codex-rs/core/src/tools/sandboxing.rs`
  - 如何用于本章：教学 `ApprovalRequirement` 保留同样的三分状态。
- 默认 approval requirement 取决于 approval policy 与文件系统 sandbox policy；granular policy
  禁止某类 prompt 时可直接得到 Forbidden。
  - 证据路径：`codex-rs/core/src/tools/sandboxing.rs`、
    `codex-rs/core/src/tools/sandboxing_tests.rs`
  - 如何用于本章：正文强调 Forbidden 不应询问用户，但不实现完整 policy 组合。
- `ReviewDecision` 包含 Approved、ApprovedForSession、Denied、TimedOut、Abort，以及 execpolicy 和
  network policy amendment 决策。
  - 证据路径：`codex-rs/protocol/src/protocol.rs`
  - 如何用于本章：教学版实现四个核心决策，并列出省略的扩展决策。
- `ReviewDecision::Denied` 表示不执行当前命令但继续 session；`Abort` 表示不执行并等待用户下一条
  命令。
  - 证据路径：`codex-rs/protocol/src/protocol.rs` 的 variant 注释、
    `codex-rs/core/src/session/handlers.rs`
  - 如何用于本章：教学 Denied 返回工具失败给模型，Abort 发出 turn/aborted。
- exec approval request 包含 call ID、可选独立 approval ID、turn ID、command、cwd、reason、
  available decisions、解析命令及可选策略/权限上下文。
  - 证据路径：`codex-rs/protocol/src/approvals.rs`
  - 如何用于本章：教学请求保留 approval ID、tool、summary、keys、reason 和 available decisions。
- apply-patch approval request 包含 call ID、turn ID、changes、reason 与可选 grant root。
  - 证据路径：`codex-rs/protocol/src/approvals.rs`
  - 如何用于本章：教学 patch 请求在执行前展示目标路径。
- GuardianApprovalRequest 能表示 shell、unified exec、execve、apply patch、network access、MCP tool
  call 和 request permissions，并可序列化为结构化审查 action。
  - 证据路径：`codex-rs/core/src/guardian/approval_request.rs`
  - 如何用于本章：正文说明真实审批不是通用文本确认框。
- `TurnState` 保存 approval ID 到 oneshot sender 的 `pending_approvals` 映射。
  - 证据路径：`codex-rs/core/src/state/turn.rs`
  - 如何用于本章：教学 EventReducer 也维护 pending approval map。
- `request_command_approval` 先注册 pending sender，再发出 approval event 并等待 receiver；通道被清除
  时返回 Abort。`notify_approval` 按 approval ID 移除并唤醒对应请求。
  - 证据路径：`codex-rs/core/src/session/mod.rs`
  - 如何用于本章：教学版用同步 callback 模拟同一 request/wait/resume 顺序。
- exec 与 patch approval handler 收到 Abort 时会 interrupt 当前 task；其他决策通知待决请求。
  - 证据路径：`codex-rs/core/src/session/handlers.rs`
  - 如何用于本章：教学 Thread 单独处理 ApprovalAborted。
- `ApprovalStore` 序列化通用 approval keys；`with_cached_approval` 只有在所有 keys 均为
  ApprovedForSession 时跳过提示，并把 session 决策分别存到每个 key。
  - 证据路径：`codex-rs/core/src/tools/sandboxing.rs`
  - 如何用于本章：教学 ApprovalStore 使用“所有 keys 命中”规则并逐 key 缓存。
- apply-patch 的 approval keys 按 environment ID 与每个目标路径构造；多文件请求可让之后触及其子集
  的请求跳过提示。
  - 证据路径：`codex-rs/core/src/tools/runtimes/apply_patch.rs`、
    `codex-rs/core/tests/suite/approvals.rs`
  - 如何用于本章：教学 patch key 使用每个目标路径，测试验证 subset 命中、新 key 重新询问。
- shell/unified-exec 的 approval keys 包含规范化 command、cwd、sandbox permissions 等上下文。
  - 证据路径：`codex-rs/core/src/tools/runtimes/shell.rs`、
    `codex-rs/core/src/tools/runtimes/unified_exec.rs`
  - 如何用于本章：正文强调 session approval 不应只按工具名缓存。
- Orchestrator 对 NeedsApproval 请求决策；Denied/Abort 被转为 rejected tool error，TimedOut 使用超时
  消息，允许类决策继续执行。
  - 证据路径：`codex-rs/core/src/tools/orchestrator.rs`
  - 如何用于本章：教学 Orchestrator 只允许 Approved/ApprovedForSession 执行。
- permission request hooks 的 allow/deny 在 Guardian 或用户审批路径之前生效。
  - 证据路径：`codex-rs/core/src/tools/orchestrator.rs`
  - 如何用于本章：列为生产边界，延后到 s09。
- approval 集成测试会等待 ExecApprovalRequest 或 ApplyPatchApprovalRequest，并断言请求必须早于
  TurnComplete；拒绝路径向模型返回 rejected-by-user 信息。
  - 证据路径：`codex-rs/core/tests/suite/approvals.rs`
  - 如何用于本章：教学测试验证 request/resolved 顺序、拒绝不执行和 Turn 继续。
- ApprovedForSession 的 apply-patch 集成测试确认后续触及已批准目标的 patch 不再产生提示。
  - 证据路径：`codex-rs/core/tests/suite/approvals.rs`
  - 如何用于本章：教学测试覆盖 session key cache。
- approval 与 sandbox 是独立阶段；即使 approval 已处理，Orchestrator 后续仍会选择 sandbox，并可能
  因 sandbox denial 决定是否请求升级与重试。
  - 证据路径：`codex-rs/core/src/tools/orchestrator.rs`
  - 如何用于本章：正文明确 approval 不等于实际权限，s07 再实现 sandbox。

## 教学实现的简化

- 教学 decider 是同步 Python callback；真实 Codex 发事件并通过异步 channel 等待客户端或 Guardian。
- 教学请求只包含 tool、summary、keys、reason 和固定 available decisions。
- 教学只实现 Approved、ApprovedForSession、Denied、Abort。
- 教学 approval requirement 由 Handler 固定返回，不实现完整 approval policy、exec policy 或 trust。
- 教学 session cache 只存字符串 key，不包含环境、cwd、command 规范化和权限上下文。
- 教学 patch key 只按路径，不包含 environment ID。
- 教学版没有 Guardian、permission request hooks、network/MCP approval、subcommand approval 或超时。
- 教学版没有真正并行待决请求、跨进程客户端和取消竞态。
- 教学版尚未实现 sandbox、permission profile 和失败后升级。
- 教学 EventReducer 只展示 pending 和 resolved 决策，不复刻全部真实协议事件。

## 未确认与不写入正文的内容

- 不声称用户批准必然使工具成功；后续 sandbox、权限和运行时错误仍可能阻止执行。
- 不声称真实 Codex 对所有工具使用相同 approval request shape。
- 不声称真实 Codex 的 session approval 只按路径或命令字符串缓存。
- 不声称 Denied 与 Abort 在所有外部客户端和 MCP 协议中具有完全相同映射。
- 不描述公开源码无法确认的服务端自动审批行为。
