# Source Notes

## 研究快照

- 仓库：`openai/codex`
- Commit：`f297b9f07de10c7d8b9ed284b674d06cc5ff7723`
- 研究日期：2026-06-13

## 实际阅读

### 源码

- `codex-rs/protocol/src/items.rs`
- `codex-rs/protocol/src/models.rs`
- `codex-rs/protocol/src/protocol.rs`
- `codex-rs/core/src/event_mapping.rs`
- `codex-rs/core/src/stream_events_utils.rs`
- `codex-rs/core/src/session/mod.rs`
- `codex-rs/core/src/session/turn.rs`
- `sdk/python/src/openai_codex/_message_router.py`

### 测试

- `codex-rs/core/src/event_mapping_tests.rs`
- `codex-rs/core/tests/suite/items.rs`
- `codex-rs/app-server/tests/suite/v2/turn_start.rs`
- `sdk/python/tests/test_client_rpc_methods.py`

### 模块文档或官方示例

- `codex-rs/protocol/README.md`
- `sdk/python/examples/03_turn_stream_events/sync.py`
- `sdk/python/examples/03_turn_stream_events/async.py`

## 从源码确认的事实

- Codex 区分模型侧 `ResponseItem` 与面向客户端展示的 `TurnItem`，并通过
  `parse_turn_item` 将部分模型输出映射为客户端 Item。
  - 证据路径：`codex-rs/protocol/src/models.rs`、`codex-rs/protocol/src/items.rs`、
    `codex-rs/core/src/event_mapping.rs`
  - 如何用于本章：教学版显式定义 `ResponseItem` 与 `TurnItem` 两个视角，并使用映射函数连接。
- 真实 `parse_turn_item` 不直接把普通 `ResponseItem::FunctionCall` 映射为 `TurnItem`；
  工具调用会由工具 router、handler 和各类工具 Item 生命周期处理。
  - 证据路径：`codex-rs/core/src/event_mapping.rs`、`codex-rs/core/src/session/turn.rs`、
    `codex-rs/core/src/tools/router.rs`
  - 如何用于本章：教学版 `to_client_item` 为保持 s01 的最小工具闭环，也转换 FunctionCall；
    正文明确这是合并后的教学边界，不是对真实函数的逐项复刻。
- 并非每个模型侧 Item 都会成为客户端可见 Item；例如 system message 不会被
  `parse_turn_item` 转换，部分上下文消息也会被过滤。
  - 证据路径：`codex-rs/core/src/event_mapping.rs`、`codex-rs/core/src/event_mapping_tests.rs`
  - 如何用于本章：映射函数允许返回 `None`，正文强调协议视图不是模型原始流的镜像。
- 模型流中的 `OutputItemAdded`、`OutputTextDelta`、`OutputItemDone` 与 `Completed`
  被 Turn 处理代码分别消费；客户端侧会收到 Item 生命周期事件和文本 delta。
  - 证据路径：`codex-rs/core/src/session/turn.rs`
  - 如何用于本章：教学模型先产生模型流事件，运行时再转换为客户端事件。
- `ItemStartedEvent`、`ItemCompletedEvent` 和 `AgentMessageContentDeltaEvent` 都包含
  Turn 或 Item 的关联标识；delta 使用 `item_id` 指向所属 Item。
  - 证据路径：`codex-rs/protocol/src/protocol.rs`
  - 如何用于本章：reducer 使用 `turn_id` 路由事件，并使用 `item_id` 累积文本。
- Core 集成测试验证同一用户消息、assistant message 和 reasoning Item 的 started 与
  completed 事件共享 Item ID。
  - 证据路径：`codex-rs/core/tests/suite/items.rs`
  - 如何用于本章：教学 reducer 把 started Item 放入 `in_progress`，完成时以相同 ID 替换。
- Python SDK 的消息路由测试验证不同 Turn 的事件可以交错到达，但必须被投递到各自 Turn；
  早到事件还可能在注册前被暂存。
  - 证据路径：`sdk/python/tests/test_client_rpc_methods.py`、
    `sdk/python/src/openai_codex/_message_router.py`
  - 如何用于本章：教学 reducer 维护 `turn_id -> TurnView`，允许交错消费多个 Turn。
- App Server 测试会等待 `item/started`、`item/completed` 和 `turn/completed`，并检查
  FileChange 等 Item 从进行中状态演进到完成状态。
  - 证据路径：`codex-rs/app-server/tests/suite/v2/turn_start.rs`
  - 如何用于本章：正文把事件描述为状态变化事实，而非日志字符串。
- Python SDK 流式示例消费 `item/agentMessage/delta` 来即时显示文本，同时仍从
  `item/completed` 获取完成 Item，并要求最终看到 `turn/completed`。
  - 证据路径：`sdk/python/examples/03_turn_stream_events/sync.py`
  - 如何用于本章：教学 reducer 将 delta 用于临时展示，将 completed Item 作为最终事实。

## 教学实现的简化

- 教学版只实现 `UserMessage`、`AgentMessage`、`FunctionCall` 和 `FunctionCallOutput`，
  没有覆盖真实 Codex 的 Reasoning、FileChange、MCP、WebSearch 等 Item。
- 教学版 `to_client_item` 同时映射 assistant message 与 FunctionCall；真实 Codex 的
  `event_mapping.rs::parse_turn_item` 不负责普通 FunctionCall 的工具生命周期。
- 教学版使用同步生成器模拟模型流，没有实现 SSE、异步 channel、背压、取消与重试。
- 教学版把事件统一放入一个轻量 `Event` 数据类；真实 Codex 使用多个具体协议事件类型，
  App Server 还会将其转换为外部协议通知。
- 教学版只过滤 system message；真实 `event_mapping.rs` 还处理上下文片段、图片标签、
  Hook Prompt 和更多兼容逻辑。
- 教学版对未知 Item delta 直接报错。真实客户端路由可能暂存早到事件，并处理更复杂的并发与恢复。
- 工具仍是硬编码函数；registry、参数验证和工具生命周期留到 s03 以后。

## 未确认与不写入正文的内容

- 不声称所有真实 `TurnItem` 都必然经历完全相同的 started、delta、completed 序列。
- 不声称 App Server 通知与 Core 内部 `EventMsg` 在所有字段上完全一一对应。
- 不描述事件跨进程传输时的精确投递保证、重放保证或持久化语义。
- 不推断 OpenAI 服务端如何产生底层流事件。
