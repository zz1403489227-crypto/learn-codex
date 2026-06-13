# Plan

## 总体策略

课程采用“概念先行、源码校准、Python 重建”的方法：

1. 先用最小实现建立可运行心智模型。
2. 每章增加一个 Codex 风格的工程机制。
3. 用公开 Codex 源码解释该机制为何存在、位于哪里、承担什么边界。
4. 不把目标读者拖进 Rust 类型和异步实现细节。
5. 最终在 `s24` 把机制组合成一个完整但仍可读的教学 Agent。

## 深度边界

课程要比 `learn-claude-code` 更进一步的地方：

- 从 `messages[]` 推进到 Thread / Turn / Item / Event。
- 从简单工具表推进到 registry / router / lifecycle。
- 从字符串拒绝列表推进到 approval / sandbox / policy 的分层边界。
- 从对话历史推进到 rollout、恢复、压缩和记忆。
- 从单进程 CLI 推进到 App Server 协议与外部客户端。
- 从“能运行”推进到可观测、可测试和可演进。

课程暂不深入：

- Rust 所有权、Tokio 调度和 crate 级实现细节。
- 各操作系统沙箱底层系统调用。
- OpenAI 服务端私有实现。
- Codex TUI 的完整 UI 状态机。
- 企业管理策略的全部组合。

## 章节路线

### 阶段一：从循环到运行时

| 章 | 目录 | 核心问题 | 主要实现 |
|---|---|---|---|
| s01 | `s01_turn_loop` | 一次用户任务如何变成连续行动？ | Python 最小 Turn Loop |
| s02 | `s02_streaming_items` | 为什么成熟 Agent 需要 Item 和 Event 流？ | Python 事件流与 reducer |
| s03 | `s03_tool_registry` | 工具增多后如何发现、路由和验证？ | registry + router |
| s04 | `s04_shell_execution` | Shell 工具如何处理长任务、输出和进程状态？ | exec session + bounded output |
| s05 | `s05_file_tools_apply_patch` | 文件修改为什么不应只靠 shell 字符串？ | read/write/edit/apply-patch |

### 阶段二：先建立边界，再给予能力

| 章 | 目录 | 核心问题 | 主要实现 |
|---|---|---|---|
| s06 | `s06_approval_pipeline` | 哪些行动必须停下来请求确认？ | approval request/decision |
| s07 | `s07_sandbox_permissions` | 审批与实际权限限制有什么区别？ | workspace sandbox model |
| s08 | `s08_config_and_trust` | 用户配置、项目配置和可信状态如何合并？ | layered config |
| s09 | `s09_hooks_and_policy` | 如何扩展生命周期而不污染主循环？ | hooks + policy checks |

### 阶段三：构造模型真正看到的世界

| 章 | 目录 | 核心问题 | 主要实现 |
|---|---|---|---|
| s10 | `s10_agents_md_instructions` | 仓库级指导如何按目录作用？ | `AGENTS.md` hierarchy loader |
| s11 | `s11_context_fragments` | System prompt 为什么应由有界片段组装？ | contextual fragments |
| s12 | `s12_skills_progressive_loading` | 如何按需加载知识而不塞满上下文？ | skill discovery/injection |
| s13 | `s13_plans_modes_and_goals` | 计划、协作模式和目标各解决什么问题？ | plan state + goal contract |
| s14 | `s14_threads_turns_and_state` | Thread、Turn 和运行状态如何分层？ | explicit state machine |

### 阶段四：让会话可以长期工作

| 章 | 目录 | 核心问题 | 主要实现 |
|---|---|---|---|
| s15 | `s15_rollouts_resume_and_fork` | 会话如何持久化、恢复与分叉？ | JSONL rollout + replay |
| s16 | `s16_compaction_and_token_budget` | 如何在不破坏事件关系的前提下压缩？ | budget + compaction |
| s17 | `s17_memory_system` | Thread 历史与跨 Thread 记忆有何区别？ | select/extract/consolidate |
| s18 | `s18_error_recovery` | 网络、模型和工具失败后如何恢复？ | retry + continuation + fallback |

### 阶段五：协作、隔离与扩展

| 章 | 目录 | 核心问题 | 主要实现 |
|---|---|---|---|
| s19 | `s19_subagents_and_thread_manager` | 子 Agent 为什么应该是受管理的 Thread？ | thread manager + subagent |
| s20 | `s20_worktree_and_git_isolation` | 并行 Agent 如何避免文件冲突？ | worktree lifecycle |
| s21 | `s21_mcp_plugins_and_connectors` | 外部能力如何进入统一工具系统？ | dynamic tools + MCP mock |
| s22 | `s22_app_server_protocol` | UI、IDE 或其他进程如何控制 Agent？ | JSON-RPC App Server；可选 Java 客户端 |

### 阶段六：走向可维护的 Agent 产品

| 章 | 目录 | 核心问题 | 主要实现 |
|---|---|---|---|
| s23 | `s23_observability_testing_evals` | 如何知道 Agent 为什么成功或失败？ | trace + protocol/integration evals |
| s24 | `s24_comprehensive_agent` | 所有机制如何回到一个可理解的系统？ | 综合 Python Agent |

## Java 使用计划

Java 不作为每章硬性要求。当前只预留：

- `s02`：可选，用 sealed interface 对比事件类型建模。
- `s22`：建议加入 Java JSON-RPC 客户端，展示企业系统集成。
- `s23`：可选，展示跨语言协议契约测试。

是否真正加入，以“是否显著帮助理解”为判断标准。

## 每章交付流程

1. 阅读上一章、当前章节骨架和 `docs/SourceMap.md`。
2. 阅读对应 Codex 公开源码，记录事实与教学简化。
3. 先完成本章图和 README 叙事。
4. 再写最小可运行代码与测试。
5. 运行章节实验和结构检查。
6. 更新 `Progress.md`。
7. 独立 commit；配置远程后 push。

## 里程碑

- **M0 Foundation**：协作文档、路线、目录、模板和检查脚本。
- **M1 Runnable Core**：完成 s01-s05。
- **M2 Safe Runtime**：完成 s06-s09。
- **M3 Context Architecture**：完成 s10-s14。
- **M4 Durable Agent**：完成 s15-s18。
- **M5 Extensible Agent**：完成 s19-s22。
- **M6 Publishable Course**：完成 s23-s24，统一校对、图形导出与发布说明。

