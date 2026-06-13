# Source Notes

## 研究快照

- 仓库：`openai/codex`
- Commit：`f297b9f07de10c7d8b9ed284b674d06cc5ff7723`
- 研究日期：2026-06-13

## 实际阅读

### 源码

- `codex-rs/core/src/session/turn.rs`
- `codex-rs/core/src/tasks/regular.rs`
- `codex-rs/core/src/tasks/mod.rs`
- `codex-rs/core/src/codex_thread.rs`
- `codex-rs/protocol/src/items.rs`
- `codex-rs/protocol/src/protocol.rs`
- `codex-rs/protocol/src/models.rs`

### 测试

- 本章主要核对上述源码内联测试与类型序列化测试入口。
- 更完整的 Turn 行为集成测试将在涉及 Thread 状态和 App Server 时继续阅读。

### 模块文档或官方文档

- `codex-rs/core/README.md`
- `codex-rs/protocol/README.md`
- `sdk/python/examples/02_turn_run/sync.py`
- `sdk/python/examples/03_turn_stream_events/sync.py`
- GitHub 公开源码页：`openai/codex/codex-rs/core/src/session/turn.rs`

## 从源码确认的事实

- `run_turn` 会在模型要求函数调用时执行调用，并在下一次 sampling 将输出返回模型；模型只返回 assistant message 时，Turn 可以完成。
  - 证据路径：`codex-rs/core/src/session/turn.rs`
  - 如何用于本章：构成教学版 Turn Loop 的核心循环。
- 一次 sampling 是否需要继续，由 `SamplingRequestResult.needs_follow_up` 表达；工具输出和待处理输入等都可能要求继续。
  - 证据路径：`codex-rs/core/src/session/turn.rs`
  - 如何用于本章：教学版用出现 `FunctionCall` 作为最小 follow-up 条件。
- 普通任务在运行 Turn 前发出 `TurnStarted`，任务完成阶段发出 `TurnComplete`。
  - 证据路径：`codex-rs/core/src/tasks/regular.rs`、`codex-rs/core/src/tasks/mod.rs`
  - 如何用于本章：教学版发出 `turn/started` 与 `turn/completed`。
- `CodexThread` 是组成 Thread 的双向消息流通道，并关联配置、rollout 等 Thread 级状态。
  - 证据路径：`codex-rs/core/src/codex_thread.rs`
  - 如何用于本章：教学版用 `Thread` 保存跨 Turn 历史。
- Codex 同时存在模型侧 `ResponseItem` 与客户端侧 `TurnItem` 等相邻视角。
  - 证据路径：`codex-rs/protocol/src/models.rs`、`codex-rs/protocol/src/items.rs`
  - 如何用于本章：本章主动合并为单一教学 `Item`，将映射问题留给 s02。

## 教学实现的简化

- 教学版省略：真实模型流、pending input、hooks、skills、plugins、compaction、retry、telemetry 和取消。
  - 真实 Codex：这些机制都围绕 `run_turn` 和 sampling 请求生命周期存在。
  - 简化原因：s01 只建立 Thread、Turn、Item 和 follow-up sampling 的骨架。
- 教学版省略：`ResponseItem` 到 `TurnItem` 的映射与流式 delta。
  - 真实 Codex：协议与事件层维护多种 Item 和生命周期事件。
  - 简化原因：留给 s02 Streaming Items。
- 教学版使用：`ScriptedModel` 与一个硬编码 `count_words` 工具。
  - 真实 Codex：使用模型客户端、工具 registry/router 和完整工具运行时。
  - 简化原因：保证离线可运行，并让读者聚焦循环。

## 未确认与不写入正文的内容

- 本章不推断 Codex 服务端模型如何决定 `end_turn`。
- 本章不声称教学 `Item` 类型与真实 Codex 类型一一对应。
- 本章不描述完整的 Turn 错误、取消和恢复语义；这些留到对应章节核对源码后再写。

