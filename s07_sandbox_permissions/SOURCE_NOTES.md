# Source Notes

## 研究快照

- `openai/codex`: `f297b9f07de10c7d8b9ed284b674d06cc5ff7723`
- 获取日期：2026-06-13
- 本章研究日期：2026-06-15

## 实际阅读

### 协议与权限模型

- `codex-rs/protocol/src/models.rs`
  - `SandboxPermissions`
  - `PermissionProfile`
- `codex-rs/protocol/src/permissions.rs`
  - `NetworkSandboxPolicy`
  - `FileSystemAccessMode`
  - `FileSystemSandboxEntry`
  - `FileSystemSandboxPolicy`
  - protected workspace metadata
- `codex-rs/protocol/src/protocol.rs`
  - legacy `SandboxPolicy`
  - `WritableRoot`

### Sandbox 选择与执行

- `codex-rs/core/src/sandboxing/mod.rs`
- `codex-rs/core/src/tools/sandboxing.rs`
- `codex-rs/core/src/tools/orchestrator.rs`
- `codex-rs/sandboxing/src/lib.rs`
- `codex-rs/sandboxing/src/manager.rs`
- `codex-rs/sandboxing/src/policy_transforms.rs`
- `codex-rs/sandboxing/src/bwrap.rs`
- `codex-rs/sandboxing/src/seatbelt.rs`
- `codex-rs/linux-sandbox/README.md`
- `codex-rs/windows-sandbox-rs/src/resolved_permissions.rs`
- `codex-rs/windows-sandbox-rs/src/workspace_acl.rs`

### 测试

- `codex-rs/core/src/tools/sandboxing_tests.rs`
- `codex-rs/sandboxing/src/manager_tests.rs`
- `codex-rs/sandboxing/src/policy_transforms_tests.rs`
- `codex-rs/sandboxing/src/seatbelt_tests.rs`
- `codex-rs/core/tests/suite/apply_patch_cli.rs`
- `codex-rs/core/tests/suite/unified_exec_zsh_fork_approvals.rs`
- `codex-rs/core/tests/suite/windows_sandbox.rs`

## 从源码确认的事实

- 当前快照中的 `PermissionProfile` 是会话、Turn 或命令的规范化运行权限，明确区分：
  - `Managed`：Codex 构造并执行 sandbox。
  - `Disabled`：不应用外层 sandbox。
  - `External`：文件系统隔离由外部调用方执行。
- `PermissionProfile` 将文件系统与网络权限分开表达。文件系统策略由带访问模式的 entries
  组成；网络策略至少区分 `Restricted` 与 `Enabled`。
- 文件系统访问模式包含 Read、Write 与 Deny。同等具体度冲突时，源码注释规定 deny 优先于
  write，write 优先于 read。
- built-in read-only profile 使用受管理的只读文件系统与受限网络；workspace-write profile
  增加项目根写权限，默认网络仍受限；disabled profile 对应无外层 sandbox 与启用网络。
- `PermissionProfile` 可以转换为运行时 `FileSystemSandboxPolicy` 和
  `NetworkSandboxPolicy`；legacy `SandboxPolicy` 仍有兼容转换路径。
- `ToolOrchestrator` 的稳定顺序是 approval、选择 sandbox、首次执行、必要时处理 sandbox
  denial 与升级重试。审批和 sandbox 选择是两个独立阶段。
- `SandboxManager::select_initial` 根据文件系统策略、网络策略、工具偏好和平台能力选择
  `None`、macOS Seatbelt、Linux sandbox 或 Windows restricted token。
- 即使文件系统允许完整写入，只要网络仍受限或存在 managed network 要求，运行时仍可能需要
  平台 sandbox。
- per-command `SandboxPermissions` 可以使用默认权限、请求无 sandbox 升级，或在 sandbox
  内请求附加权限。
- denied-read 规则不能因批准或显式升级而静默丢失。`sandbox_override_for_first_attempt` 会在
  denied-read 存在时拒绝直接绕过 sandbox；相关集成测试确认获批命令仍无法读取 secret。
- additional permissions 会与基础权限合并，但测试确认原有 deny entries 仍被保留。
- Linux 当前默认文件系统 sandbox 是 bubblewrap，受限文件系统默认只读挂载，再叠加 writable
  roots 与只读或 deny carve-outs；网络受限时还会隔离网络命名空间或使用受管理代理路径。
- macOS 通过固定 `/usr/bin/sandbox-exec` 与动态 Seatbelt policy 表达文件系统和网络规则。
- Windows 会把 managed permission profile 解析为 restricted-token 所需权限；不能强制执行的
  deny-read 场景会拒绝运行，而不是无 sandbox 执行。
- workspace 可写并不代表 `.git`、`.codex` 等元数据目录必然可写；协议层和平台实现包含额外
  保护。
- `apply_patch` 集成测试覆盖了路径穿越、软链接逃逸、不可读目标和验证失败无副作用。

## 教学实现的简化

- 教学版 `PermissionProfile` 只有 name、路径规则和一个网络开关，没有 Managed、Disabled、
  External enforcement 类型。
- 教学版只提供 read-only、workspace-write 与 danger-full-access 三个构造器。
- 教学版规则只接受绝对路径，不实现 special paths、glob deny、TMPDIR、多个 workspace roots
  或用户配置解析。
- 教学版用 Python 进程内的 `WorkspaceSandbox` 在文件工具边界检查权限，不启动 Seatbelt、
  bubblewrap、Landlock、restricted token 或真正的操作系统 sandbox。
- 因为没有 OS 强制边界，教学版不能安全包含任意 shell 子进程；本章只对受控教学工具成立。
- 教学版 `network_probe` 不访问真实网络，只用于展示文件系统权限与网络权限相互独立。
- 教学版不实现 sandbox failure 后的升级审批与第二次执行。
- 教学版 patch approval preview 验证结构和目标，保留所需读取权限检查，但将实际写权限检查留到
  执行阶段，以明确展示“获批仍可能被 sandbox 拒绝”。
- 教学版将 sandbox denial 映射为 `sandbox/denied` 事件和普通失败 ToolResult。

## 未确认与不写入正文的内容

- 不把某个平台后端当前的所有系统调用、挂载参数或 ACL 细节描述为跨平台稳定契约。
- 不声称所有批准都会保持 sandbox；真实 Codex 支持在特定策略和请求下批准无 sandbox 重试或
  per-command 权限扩展。
- 不声称所有平台对 hard link、symlink、deny glob 和不存在路径具有完全相同的行为。
- 不声称 legacy `SandboxPolicy` 已被移除；当前快照仍保留兼容路径。
- 不声称教学版路径检查能够替代生产级 OS sandbox。
