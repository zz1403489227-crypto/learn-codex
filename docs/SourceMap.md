# Source Map

本文件把课程章节映射到公开 Codex 源码。它不是完整源码索引，而是帮助写作者快速找到每章最相关的证据。

## 当前快照

| 仓库 | 本地路径 | Commit | 获取日期 |
|---|---|---|---|
| `openai/codex` | `references/codex/` | `f297b9f07de10c7d8b9ed284b674d06cc5ff7723` | 2026-06-13 |
| `shareAI-lab/learn-claude-code` | `learn-claude-code/` | `20e7cbb72c66ab01967299ad3eac6c7bda242136` | 2026-06-13 |

更新 `references/codex/` 后，必须同步更新本表和 `Progress.md`。

## 章节到源码的初始映射

| 章节 | Codex 公开源码入口 |
|---|---|
| s01 Turn Loop | `codex-rs/core/src/session/turn.rs`, `tasks/regular.rs`, `codex_thread.rs` |
| s02 Streaming Items | `codex-rs/protocol/src/items.rs`, `protocol.rs`, `core/src/event_mapping.rs`, `stream_events_utils.rs` |
| s03 Tool Registry | `codex-rs/core/src/tools/registry.rs`, `router.rs`, `orchestrator.rs`, `spec_plan.rs` |
| s04 Shell Execution | `codex-rs/core/src/unified_exec/`, `exec.rs`, `shell.rs`, `codex-rs/exec-server/` |
| s05 File Tools | `codex-rs/core/src/apply_patch.rs`, `codex-rs/apply-patch/`, `core/src/tools/` |
| s06 Approval | `codex-rs/protocol/src/approvals.rs`, `core/src/guardian/approval_request.rs`, `tools/lifecycle.rs` |
| s07 Sandbox | `codex-rs/core/src/sandboxing/`, `codex-rs/sandboxing/`, `linux-sandbox/`, `windows-sandbox-rs/` |
| s08 Config & Trust | `codex-rs/core/src/config/`, `config_lock.rs`, `codex-rs/config/` |
| s09 Hooks & Policy | `codex-rs/hooks/`, `core/src/hook_runtime.rs`, `core/src/exec_policy.rs`, `codex-rs/execpolicy/` |
| s10 AGENTS.md | `codex-rs/core/src/agents_md.rs`, `agents_md_tests.rs` |
| s11 Context Fragments | `codex-rs/core/src/context/`, `context_manager/` |
| s12 Skills | `codex-rs/core-skills/`, `codex-rs/core/src/skills.rs` |
| s13 Plans & Goals | `codex-rs/protocol/src/plan_tool.rs`, `collaboration-mode-templates/`, goal-related app-server protocol |
| s14 Threads & Turns | `codex-rs/core/src/session/`, `state/`, `thread_manager.rs`, `protocol/src/thread_id.rs` |
| s15 Rollouts | `codex-rs/rollout/`, `thread-store/`, `core/src/rollout.rs`, `session/rollout_reconstruction.rs` |
| s16 Compaction | `codex-rs/core/src/compact.rs`, `compact_remote*.rs`, `tasks/compact.rs`, `state/auto_compact_window.rs` |
| s17 Memory | `codex-rs/memories/`, `protocol/src/memory_citation.rs` |
| s18 Error Recovery | `codex-rs/core/src/responses_retry.rs`, `client.rs`, `session/turn.rs` |
| s19 Subagents | `codex-rs/core/src/agent/`, `session/multi_agents.rs`, `thread_manager.rs` |
| s20 Worktree | `codex-rs/core/src/git_info_tests.rs`, `git-utils/`, app and thread worktree behavior |
| s21 MCP & Plugins | `codex-rs/core/src/mcp*.rs`, `codex-mcp/`, `rmcp-client/`, `plugin/`, `core-plugins/`, `connectors/` |
| s22 App Server | `codex-rs/app-server/`, `app-server-protocol/`, `app-server-client/`, `sdk/python/` |
| s23 Observability | `codex-rs/otel/`, `rollout-trace/`, `core/suite/`, SDK tests |
| s24 Comprehensive | 综合前述路径，重点回到 `core/src/session/turn.rs` 与 `tools/orchestrator.rs` |

## 使用规则

- 写章前先阅读对应入口的模块级注释、README 和测试。
- 初始映射只用于定位，不代表已经完成源码研究。
- 每章必须在自己的 `SOURCE_NOTES.md` 中记录实际阅读过的路径和得到的结论。
- 优先从测试理解稳定行为，再阅读实现细节。
- README 中只引用能帮助读者建立心智模型的源码路径。
- 对无法从公开源码确认的行为，明确标注为推断或不写。
- 不要求教学实现与 Codex 的类型结构一一对应。
