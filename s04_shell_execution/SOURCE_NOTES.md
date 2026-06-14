# Source Notes

## 研究快照

- 仓库：`openai/codex`
- Commit：`f297b9f07de10c7d8b9ed284b674d06cc5ff7723`
- 研究日期：2026-06-14

## 实际阅读

### 源码

- `codex-rs/core/src/unified_exec/mod.rs`
- `codex-rs/core/src/unified_exec/process.rs`
- `codex-rs/core/src/unified_exec/process_state.rs`
- `codex-rs/core/src/unified_exec/process_manager.rs`
- `codex-rs/core/src/unified_exec/head_tail_buffer.rs`
- `codex-rs/core/src/unified_exec/async_watcher.rs`
- `codex-rs/core/src/tools/handlers/unified_exec.rs`
- `codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs`
- `codex-rs/core/src/tools/handlers/unified_exec/write_stdin.rs`
- `codex-rs/core/src/tools/handlers/shell_spec.rs`
- `codex-rs/core/src/tools/context.rs`
- `codex-rs/exec-server/src/process.rs`

### 测试

- `codex-rs/core/src/unified_exec/head_tail_buffer_tests.rs`
- `codex-rs/core/src/unified_exec/process_manager_tests.rs`
- `codex-rs/core/src/unified_exec/mod_tests.rs`
- `codex-rs/core/src/tools/handlers/unified_exec_tests.rs`
- `codex-rs/core/src/tools/handlers/shell_spec_tests.rs`
- `codex-rs/core/tests/suite/unified_exec.rs`
- `codex-rs/core/tests/suite/tool_harness.rs`
- `codex-rs/exec-server/tests/process.rs`

### 模块文档或官方文档

- `codex-rs/core/src/unified_exec/mod.rs` 模块级职责与流程注释
- `codex-rs/exec-server/README.md`

## 从源码确认的事实

- Unified Exec 负责管理交互式进程、复用进程、限制输出，并通过共享 ToolOrchestrator 接入 approval、
  sandbox 与 retry。
  - 证据路径：`codex-rs/core/src/unified_exec/mod.rs`
  - 如何用于本章：教学版只重建进程 session 与有界输出，并明确省略安全编排。
- `exec_command` 和 `write_stdin` 是两个 ToolExecutor Handler，共享 session 的
  `UnifiedExecProcessManager`。
  - 证据路径：`codex-rs/core/src/tools/handlers/unified_exec/exec_command.rs`、
    `codex-rs/core/src/tools/handlers/unified_exec/write_stdin.rs`
  - 如何用于本章：两个教学 Handler 共享同一 `ProcessManager`。
- `exec_command` spec 描述其返回 output 或 ongoing interaction session ID；`write_stdin` 的空
  chars 是轮询，非空 chars 是实际终端交互。
  - 证据路径：`codex-rs/core/src/tools/handlers/shell_spec.rs`、
    `codex-rs/core/src/tools/handlers/unified_exec/write_stdin.rs`
  - 如何用于本章：教学模型先启动命令，再使用空 `write_stdin` 轮询。
- `ExecCommandToolOutput` 分别携带 raw output、process ID、exit code、wall time、chunk ID 和原始
  token 数；面向模型的文本会附带进程状态并按 token 策略截断。
  - 证据路径：`codex-rs/core/src/tools/context.rs`
  - 如何用于本章：教学 `ExecResult` 把 output、session ID、exit code 和截断元数据分为独立字段。
- ProcessManager 在初次 yield deadline 内收集输出；活进程被保存并返回 process ID，已退出进程
  返回 exit code 并从 store 删除。
  - 证据路径：`codex-rs/core/src/unified_exec/process_manager.rs`
  - 如何用于本章：教学 `exec_command` 在有限 yield 后返回 session 或退出状态。
- `write_stdin` 可写输入或轮询，随后刷新进程状态；活进程继续返回相同 process ID，退出进程被移除。
  - 证据路径：`codex-rs/core/src/unified_exec/process_manager.rs`
  - 如何用于本章：教学 `write_stdin` 复用相同 session，并在完成时清除它。
- `HeadTailBuffer` 对 retained output 设置字节上限，保留稳定 prefix 与 suffix，并丢弃中间内容。
  - 证据路径：`codex-rs/core/src/unified_exec/head_tail_buffer.rs`、
    `codex-rs/core/src/unified_exec/head_tail_buffer_tests.rs`
  - 如何用于本章：教学版实现字符级 head-tail buffer，同时限制未读 session 输出和单次响应。
- Unified Exec 使用后台任务持续读取进程输出、发出 output delta，并在退出后发出 end 事件；单条
  delta 也有大小上限。
  - 证据路径：`codex-rs/core/src/unified_exec/process.rs`、
    `codex-rs/core/src/unified_exec/async_watcher.rs`
  - 如何用于本章：教学版使用后台 reader thread，但省略 shell 专属 delta 事件。
- 集成测试验证活进程返回 process ID 且不返回 exit code；`write_stdin` 复用 process ID；退出后
  process ID 消失、exit code 出现并清除 session。
  - 证据路径：`codex-rs/core/tests/suite/unified_exec.rs`
  - 如何用于本章：教学测试覆盖相同的核心状态转换。
- 集成测试验证输出包含 chunk/exit 元数据、模型请求的输出预算会受策略上限约束，并且长进程可在
  Turn 完成后继续运行。
  - 证据路径：`codex-rs/core/tests/suite/unified_exec.rs`
  - 如何用于本章：正文强调输出预算与进程生命周期不应绑定到单次 Turn。
- ProcessManager 对保存的进程数量设上限，并优先清理已退出或较久未使用的进程。
  - 证据路径：`codex-rs/core/src/unified_exec/mod.rs`、
    `codex-rs/core/src/unified_exec/process_manager.rs`、
    `codex-rs/core/src/unified_exec/process_manager_tests.rs`
  - 如何用于本章：列为生产边界，教学版暂未实现 process-count/LRU 策略。
- exec-server 提供进程 start/read/write/terminate、保留输出读取和 pushed events；输出历史也有
  event-count 与 byte-count 上限。
  - 证据路径：`codex-rs/exec-server/README.md`、`codex-rs/exec-server/src/process.rs`
  - 如何用于本章：正文说明真实系统还支持远程环境和更完整的传输边界。

## 教学实现的简化

- 教学版直接使用 `subprocess.Popen(shell=True)`；真实 Codex 会解析 shell 模式、环境、cwd，并通过
  ToolOrchestrator 处理 approval、sandbox 和 retry。
- 教学版只实现本地子进程；真实 Codex 还支持 exec-server-backed remote process。
- 教学版 stdout/stderr 合并，未实现 PTY、终端尺寸、信号和控制字符。
- 教学版使用字符预算；真实 Codex 同时存在字节上限、token 截断策略、单 delta 大小限制和事件数限制。
- 教学版通过后台 thread 读取输出，但不发出 shell 专属 output delta、begin/end 或 terminal
  interaction 事件。
- 教学版不保留完整 transcript，只保存有界未读输出；真实 Codex 还维护 transcript 并用于结束事件。
- 教学版没有进程总数上限、LRU、取消、超时、跨 Turn 管理和关闭时终止策略。
- 教学版所有命令都可执行；没有 approval、sandbox、exec policy 或可信项目边界。

## 未确认与不写入正文的内容

- 不声称真实 Codex 的本地与远程进程在所有时序和错误条件下完全一致。
- 不声称每次 `write_stdin` 都一定产生输出或终止进程。
- 不声称所有 shell 输出都会无损传递给客户端或模型。
- 不声称真实 Codex 使用 Python subprocess、thread 或本章的字符级截断算法。
- 不描述公开源码无法确认的服务端进程执行行为。
