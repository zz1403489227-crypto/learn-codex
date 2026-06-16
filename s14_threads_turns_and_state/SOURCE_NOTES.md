# Source Notes

## 研究快照

- `openai/codex`: `f297b9f07de10c7d8b9ed284b674d06cc5ff7723`
- 获取日期：2026-06-13
- 本章研究日期：2026-06-16

## 实际阅读

### Thread / Session 标识

- `codex-rs/protocol/src/thread_id.rs`
- `codex-rs/protocol/src/session_id.rs`

### Thread 管理与入口

- `codex-rs/core/src/codex_thread.rs`
- `codex-rs/core/src/thread_manager.rs`
- `codex-rs/core/src/thread_manager_tests.rs`

### Session / Turn 状态

- `codex-rs/core/src/state/session.rs`
- `codex-rs/core/src/state/turn.rs`
- `codex-rs/core/src/session/turn_context.rs`
- `codex-rs/core/src/session/mod.rs`

### Protocol 与持久化

- `codex-rs/protocol/src/protocol.rs`
- `codex-rs/thread-store/src/lib.rs`
- `codex-rs/thread-store/src/store.rs`
- `codex-rs/thread-store/src/types.rs`
- `codex-rs/state/src/model/thread_metadata.rs`
- `codex-rs/state/src/runtime/threads.rs`

### 通过搜索定位但未完整展开的辅助文件

- `codex-rs/core/src/session/inject.rs`
- `codex-rs/core/src/session/input_queue.rs`
- `codex-rs/core/src/session/rollout_reconstruction.rs`
- `codex-rs/core/src/session/rollout_reconstruction_tests.rs`
- `codex-rs/core/src/session/tests.rs`

## 从源码确认的事实

- `ThreadId` 是 `uuid::Uuid` 包装类型，`ThreadId::new()` 使用 `Uuid::now_v7()`，并实现字符串序列化、反序列化和 `Display`。
- `SessionId` 也是 UUID 包装类型，并且源码实现了 `From<ThreadId> for SessionId` 与 `From<SessionId> for ThreadId`。这说明真实 Codex 在兼容层保留 session/thread 标识互转，而不是把二者建模成聊天消息。
- `CodexThread` 的注释称其是组成 thread 的双向消息流 conduit，且“formerly called a conversation”。它包装 `Codex` session，并暴露 `submit`、`steer_input`、`next_event`、`load_history`、`read_thread`、`update_thread_metadata` 等线程级入口。
- `CodexThread::inject_if_running` 是 thread 层到 `Session::inject_if_running` 的桥；如果没有 active turn，会把 items 原样返回给调用方。
- `CodexThread::try_start_turn_if_idle` 只在没有排队的用户/client 触发 turn、没有 active task、并且不在 Plan mode 时启动自动 idle work；失败时返回稳定 reason 和原始 items。
- `ThreadManager` 内部维护 `HashMap<ThreadId, Arc<CodexThread>>`，并通过 broadcast channel 发布 thread created 事件。
- `ThreadManager` 负责 start、resume、fork、lookup、metadata update、shutdown 等线程生命周期入口，而不是由一次 model request 自己承担这些职责。
- `ThreadManager` 的测试确认：`shutdown_all_threads_bounded` 会向每个 live thread 提交 shutdown，并在完成后从 manager 中移除。
- `ThreadManager` 的测试确认：internal session source 创建的 thread 不会出现在普通 `list_thread_ids` / `get_thread` 结果中，但 shutdown 仍会覆盖它。
- `ThreadManager` 的测试确认：resume/fork 不从 rollout 盲目恢复 thread environments；环境选择来自启动/配置路径。
- `ForkSnapshot` 支持按用户消息边界截断 fork，也支持将当前持久 history 当作 interrupted snapshot。
- `SessionState` 保存 session-scoped mutable state，包括 session configuration、`ContextManager` history、rate limits、server reasoning 标记、additional context、previous turn settings、auto-compaction window、startup prewarm、connector selection、pending session start sources、按 environment 记录的 granted permissions、以及 next-turn-is-first 标记。
- `SessionState::replace_history` 会替换 `ContextManager` history，并设置 `reference_context_item`；同时清空 auto-compaction prefill。
- `SessionState` 暴露 `set_reference_context_item` / `reference_context_item`，说明上下文 diff baseline 是 session/thread 级状态，不是普通用户消息。
- `ActiveTurn` 保存正在运行的 task 与共享 `TurnState`。
- `RunningTask` 保存 task kind、实际 task、cancellation token、handle、`Arc<TurnContext>`、extension data 和执行 guard。
- `TurnState` 保存 turn-scoped runtime state，包括 pending approvals、pending request permissions、pending user input、pending elicitations、pending dynamic tools、pending input queue、mailbox delivery phase、turn-scoped granted permissions、strict auto review 标记、tool call 数、memory citation 标记和 turn start token usage。
- `TurnState::clear_pending_waiters` 会清空 pending approvals、request permissions、user input、elicitations 和 dynamic tools。
- `MailboxDeliveryPhase` 区分当前 turn 仍可消费 mailbox delivery，还是当前 turn 已经输出终端可见内容、应把 late child mail 留到下一 turn。
- `TurnContext` 是“single turn of the thread”需要的上下文，字段包含 sub_id、trace、realtime、config、auth、model info、provider、reasoning、session/thread source、parent thread id、environments、cwd、date/timezone、developer/user instructions、collaboration mode、multi-agent version、personality、approval policy、permission profile、network、features、dynamic tools、extension data 等。
- `TurnContext::to_turn_context_item` 会把本 turn 的 cwd、workspace roots、date/timezone、approval policy、sandbox/permission profile、network、model、comp hash、personality、collaboration mode、multi-agent version、realtime、effort 等转换成 `TurnContextItem`。
- `Session::record_context_updates_and_set_reference_context_item` 会在 baseline 缺失时构造完整初始上下文，否则只构造 settings/context diff；无论是否产生 model-visible diff，都会持久化一个 `RolloutItem::TurnContext`，并推进内存中的 reference context baseline。
- `protocol.rs` 中的 `RolloutItem` 包含 `SessionMeta`、`ResponseItem`、`InterAgentCommunication`、`Compacted`、`TurnContext` 和 `EventMsg`，说明 durable replay history 不只是模型消息。
- `TurnContextItem` 的注释说明：它在每个真实用户 turn 计算 model-visible context updates 后持久化一次；mid-turn compaction 后也会持久化，以便 resume/fork replay 恢复最新 durable baseline。
- `TurnStartedEvent` 包含 `turn_id`、可选 trace id、started_at、model context window 和 collaboration mode kind。
- `TurnAbortedEvent` 包含可选 `turn_id`、abort reason、completed_at 和 duration；abort reason 包含 interrupted、replaced、review_ended、budget_limited。
- `ThreadStore` 是 storage-neutral persistence boundary，使用 `ThreadId` 作为 durable thread handle；实现负责把 id 解析到本地 rollout、RPC 或其他后端。
- `ThreadStore` trait 包含 `create_thread`、`resume_thread`、`append_items`、`persist_thread`、`flush_thread`、`shutdown_thread`、`discard_thread`、`load_history`、`read_thread`、`list_threads`、`search_threads`、`list_turns`、`list_items`、`update_thread_metadata`、`archive_thread`、`unarchive_thread` 和 `delete_thread`。
- `CreateThreadParams` 包含 thread id、forked_from_id、parent_thread_id、source、thread_source、base instructions、dynamic tools、multi-agent version 和 `ThreadPersistenceMetadata`。
- `ThreadPersistenceMetadata` 保存 cwd、model provider 和 memory mode。
- `StoredThread` 包含 thread id、rollout path、fork/parent ids、preview/name、model provider、latest model、reasoning effort、created/updated/archived 时间、cwd、cli version、source、thread_source、agent metadata、git info、approval mode、permission profile、token usage、first user message 和可选 history。
- `StoredTurnStatus` 包含 completed、interrupted、failed、in_progress。
- `StoredTurn` 包含 turn id、items、metadata、created timestamp、items view、status、error、started/completed/duration 等字段。
- state runtime 的 `get_thread` 从 `threads` 表读取 id、rollout path、created/updated、source、thread_source、agent metadata、model provider、model、reasoning effort、cwd、cli version、title、preview、sandbox/approval、tokens、first user message、archive 和 git 字段。
- state runtime 支持 thread spawn edge：可 upsert parent/child edge、更新 edge status、按 parent 列出 children、按 root 递归列出 descendants，并按 agent path 查找 child/descendant。
- state runtime 的 `set_thread_preview_if_empty` 只在 preview 为空时写入非空 preview。

## 教学实现的简化

- 教学版 `ThreadId` 使用 Python 3.11 的 `uuid.uuid4()`，没有复刻真实 Codex 的 UUIDv7 时间排序属性。
- 教学版把 `ThreadManager`、`InMemoryThreadStore`、`ManagedThread` 放在单个 `code.py` 中，真实 Codex 分布在 core、protocol、thread-store、state 和 app-server 等多个 crate。
- 教学版只实现内存 store，不实现本地 JSONL rollout、SQLite projection、compression worker、RPC 后端或 app-server pagination。
- 教学版 `TurnContextSnapshot` 只保留 cwd、model、collaboration mode、permission/approval、context update count、plan summary 和 active goal；真实 `TurnContextItem` 字段更多，且兼容旧 schema。
- 教学版 `TurnRuntimeState` 只演示 pending input、pending approvals、cancellation 和 tool call 数；真实 `TurnState` 还有 request permissions、user input、elicitations、dynamic tools、mailbox delivery phase、turn-scoped granted permissions 和 token usage baseline。
- 教学版用 `ThreadBusy` 直接拒绝第二个 active turn；真实 Codex 对用户输入、steer input、idle lifecycle、review/compact 等路径有更细的队列与状态机。
- 教学版 fork 复制教学 history/context/plan/goal 快照，但不实现按 user-message boundary 截断、interrupted marker、rollout reconstruction 或 active sampling boundary。
- 教学版 archive 只把 metadata 标记为 archived 并从 loaded threads 中移除；真实 store 还要处理 durable metadata、history 可见性和 delete/unarchive 行为。
- 教学版仍复用前序章节的离线 deterministic model，不连接真实 Responses API、MCP、exec server、extension lifecycle 或 telemetry。

## 未确认与不写入正文的内容

- 不声称 `SessionId` 与 `ThreadId` 在所有外部 API 中完全等价；源码只确认当前公开实现提供互转。
- 不声称真实 Codex 永远只允许一个用户 turn 排队；本章只讲 active turn runtime state，不覆盖 input queue 的完整调度策略。
- 不声称 fork/resume 的所有历史修剪细节已经复刻；教学版只保留“新 thread id + parent/fork metadata + history snapshot”心智模型。
- 不把 `ThreadStore` 的具体本地文件布局、SQLite schema 或 app-server API 字段当作稳定公共 API。
- 不把教学版 `context_update_count` 当作真实 token/rollout 计量；它只是证明每个 turn 会记录 context baseline/diff。
- 不讨论私有实现或未公开服务端状态。
