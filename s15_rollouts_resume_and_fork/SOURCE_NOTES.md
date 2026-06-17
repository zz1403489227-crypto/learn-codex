# s15 Source Notes

## 研究快照

- `openai/codex`: `f297b9f07de10c7d8b9ed284b674d06cc5ff7723`
- 本章研究日期：2026-06-16
- 本章主题：rollout JSONL 持久化、resume、fork、turn 边界截断与中断快照。

## 实际阅读

- `references/codex/codex-rs/rollout/src/lib.rs`
- `references/codex/codex-rs/rollout/src/recorder.rs`
- `references/codex/codex-rs/rollout/src/policy.rs`
- `references/codex/codex-rs/thread-store/README.md`
- `references/codex/codex-rs/thread-store/src/local/live_writer.rs`
- `references/codex/codex-rs/core/src/session/rollout_reconstruction.rs`
- `references/codex/codex-rs/core/src/session/rollout_reconstruction_tests.rs`
- `references/codex/codex-rs/core/src/thread_rollout_truncation.rs`
- `references/codex/codex-rs/core/src/thread_rollout_truncation_tests.rs`
- `references/codex/codex-rs/core/src/thread_manager.rs`
- `references/codex/codex-rs/core/src/thread_manager_tests.rs`

## 从源码确认的事实

- `codex-rollout` 是独立 crate，负责 Codex session rollout 文件的持久化、发现、搜索、metadata 提取和压缩等能力；`lib.rs` 对外导出 `RolloutRecorder`、`RolloutRecorderParams`、`persisted_rollout_items`、thread listing/search 相关函数。
- `RolloutRecorder` 将 session rollout 写成 JSONL；源码注释说明这些文件可用 `jq`、`fx` 等工具检查。
- 新建 rollout 时，`RolloutRecorderParams::Create` 会预计算路径和 `SessionMeta`；源码注释说明新会话会延迟打开/创建文件，直到显式 `persist()`。
- 恢复 rollout 时，`RolloutRecorderParams::Resume` 会 materialize 目标路径并以 append 模式打开已有 rollout 文件。
- `record_canonical_items` 将 canonical `RolloutItem` 送入后台 writer；`persist()`、`flush()`、`shutdown()` 作为写入屏障。writer 遇到 I/O 失败会保留 pending items，并在后续屏障中尝试重新打开和重试。
- `load_rollout_items` 逐行读取 JSONL，跳过空行，统计 JSON/结构解析错误，并使用文件中第一个 `SessionMeta` 作为 canonical thread id 来源。
- `get_rollout_history` 将已加载 items 包装成 `InitialHistory::Resumed(ResumedHistory { conversation_id, history, rollout_path })`。
- `policy.rs` 明确只有一部分 `RolloutItem` / `ResponseItem` / `EventMsg` 会持久化；例如完成类事件、turn started/complete/aborted、thread rollback、计划完成等会保留，而大量 streaming delta、begin 事件和审批请求不会进入 canonical rollout。
- `thread-store/README.md` 将 `ThreadStore` 定义为 Codex thread 的存储边界；local store 用 `codex-rollout` JSONL 保存历史，用 SQLite state database 保存可查询 metadata。
- `thread-store/src/local/live_writer.rs` 中，local live thread create/resume 会创建或恢复 `RolloutRecorder`；`append_items` 先通过 `persisted_rollout_items` 过滤 canonical items，然后调用 `record_canonical_items` 并 `flush()`，避免 SQLite metadata 领先于 JSONL。
- `ThreadManager::resume_thread_from_rollout` 通过 rollout path 读取初始历史，再调用 `resume_thread_with_history`；相关测试验证 resume/fork 都通过 `ThreadStore::read_thread_by_rollout_path` 读取历史。
- `ThreadManager::fork_thread` 读取 rollout path 得到 `InitialHistory`，再根据 `ForkSnapshot` 生成 fork history，并以新 thread id 启动线程。
- `ForkSnapshot::TruncateBeforeNthUserMessage(n)` 表示在第 n 个用户消息边界之前截断；当源快照处于 mid-turn 且 n 越界时，源码会尝试切到 active turn 起点，避免把未完成 turn 后缀带入 fork。
- `ForkSnapshot::Interrupted` 表示按“此刻中断源线程”的语义 fork。若持久化快照停在 mid-turn，源码会追加与真实 interrupt 路径一致的 interrupted marker 和 `TurnAborted` 事件；测试覆盖了显式 turn id、无 live source、重复 fork 不重复追加 marker 等场景。
- `thread_rollout_truncation.rs` 中的用户 turn 边界不是简单行数，而是从 `ResponseItem::Message` 解析为 `TurnItem::UserMessage`；fork turn 边界还会考虑 inter-agent communication 的 `trigger_turn`。
- `rollout_reconstruction.rs` 从 rollout items 重建模型历史，同时提取 resume/fork hydration metadata，例如 previous turn settings、reference context item、window id；它会处理 replacement history compaction 和 rollback marker。

## 教学实现的简化

- 教学版使用同步 Python 文件写入，不实现 Tokio 后台 writer、bounded channel、I/O 重试、压缩 worker 或 SQLite state database。
- 教学版 JSONL line 使用 `{"timestamp", "kind", "payload"}` 的小型格式，不复刻真实 `RolloutLine` / `RolloutItem` / `ResponseItem` 的完整 serde 结构。
- 教学版只持久化本课程已有的 `session_meta`、`turn_started`、`item_completed`、`turn_completed`、`turn_aborted`、`turn_failed` 和 `thread_rolled_back` 子集。
- 教学版把 `ThreadMetadata.rollout_path` 直接放进内存 metadata，真实 Codex 的 local thread store 还会维护 JSONL、SQLite 和 name index 的兼容关系。
- 教学版 resume 只恢复 `_loop.history`、turn records 和一个简化的 `ContextHistory` baseline；真实 Codex 会从 rollout 中恢复更多 hydration metadata，并处理 compaction window。
- 教学版 fork 截断使用 `turn_started.user_text` 作为用户 turn 边界，真实 Codex 通过 protocol `ResponseItem` 到 `TurnItem` 的解析判断用户边界，并额外处理 inter-agent trigger turn。
- 教学版 interrupted fork 追加固定文本 marker 和 `turn_aborted` 记录；真实 Codex 的 marker 会随 multi-agent 版本和配置变化，可能是 contextual user marker 或 developer input guidance。
- 教学版没有实现 archived rollout、compressed rollout、rollout 搜索、latest-thread discovery、memory 持久化过滤或 remote/custom thread store。

## 未确认与不写入正文的内容

- 不声称真实 Codex 的 rollout 文件格式稳定或对外兼容；本章只使用当前公开源码快照中的行为作为教学依据。
- 不描述 OpenAI 服务端如何保存或恢复内部状态；本章只讨论公开本地源码中的 rollout/thread-store/session 行为。
- 不声称教学版 fork 与真实 Codex 在 compaction、multi-agent、rollback、context hydration 上完全一致。
- 不解释各平台文件锁、崩溃恢复、压缩调度和 SQLite reconciliation 的完整实现细节。
