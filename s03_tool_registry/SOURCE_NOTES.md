# Source Notes

## 研究快照

- 仓库：`openai/codex`
- Commit：`f297b9f07de10c7d8b9ed284b674d06cc5ff7723`
- 研究日期：2026-06-14

## 实际阅读

### 源码

- `codex-rs/tools/src/tool_executor.rs`
- `codex-rs/tools/src/tool_spec.rs`
- `codex-rs/protocol/src/tool_name.rs`
- `codex-rs/core/src/tools/registry.rs`
- `codex-rs/core/src/tools/router.rs`
- `codex-rs/core/src/tools/spec_plan.rs`
- `codex-rs/core/src/tools/orchestrator.rs`
- `codex-rs/core/src/tools/handlers/mod.rs`
- `codex-rs/core/src/tools/handlers/dynamic.rs`
- `codex-rs/core/src/tools/handlers/plan.rs`
- `codex-rs/core/src/tools/handlers/plan_spec.rs`
- `codex-rs/core/src/tools/handlers/request_user_input.rs`
- `codex-rs/core/src/tools/handlers/request_user_input_spec.rs`
- `codex-rs/core/src/session/turn.rs`

### 测试

- `codex-rs/core/src/tools/registry_tests.rs`
- `codex-rs/core/src/tools/router_tests.rs`
- `codex-rs/core/src/tools/spec_plan_tests.rs`
- `codex-rs/core/src/tools/handlers/request_user_input_tests.rs`
- `codex-rs/core/src/tools/handlers/request_user_input_spec_tests.rs`
- `codex-rs/core/src/tools/handlers/multi_agents_tests.rs`

### 模块文档或官方文档

- `codex-rs/tools/src/tool_executor.rs` 模块级契约注释
- `codex-rs/core/src/tools/orchestrator.rs` 模块级职责注释

## 从源码确认的事实

- `ToolExecutor` 将 `tool_name`、`spec`、`exposure`、可选搜索信息、并行能力和 `handle`
  绑定在同一个运行时契约中。
  - 证据路径：`codex-rs/tools/src/tool_executor.rs`
  - 如何用于本章：教学 Handler 同时提供 `spec` 与 `handle`，避免定义和实现分散。
- `ToolSpec` 包含 Function、Namespace、ToolSearch、ImageGeneration、WebSearch 和 Freeform
  等多种模型可见工具定义。
  - 证据路径：`codex-rs/tools/src/tool_spec.rs`
  - 如何用于本章：教学版只实现最小 Function ToolSpec，并明确省略其他种类。
- `ToolExposure` 区分 Direct、Deferred、DirectModelOnly 和 Hidden；已注册工具不一定出现在
  初始模型可见列表。
  - 证据路径：`codex-rs/tools/src/tool_executor.rs`、`codex-rs/core/src/tools/spec_plan.rs`
  - 如何用于本章：正文明确区分 registry 全集与模型可见集合，但教学版默认全部直接可见。
- `spec_plan` 从多种工具来源规划 runtimes，将直接暴露的 runtime specs 组成模型可见列表，并从
  runtimes 构造 `ToolRegistry`。
  - 证据路径：`codex-rs/core/src/tools/spec_plan.rs`
  - 如何用于本章：`default_router` 同时构造可执行 registry 与模型可见 specs。
- `ToolRegistry` 按 `ToolName` 保存 runtime；重复注册会报告错误；dispatch 未知工具时会返回面向
  模型的 unsupported-call 错误。
  - 证据路径：`codex-rs/core/src/tools/registry.rs`
  - 如何用于本章：教学 Registry 拒绝重复名字，Router 将未知工具转换为失败 ToolResult。
- `ToolName` 保留可选 namespace，Router 从 FunctionCall 的 namespace 与 name 构造注册表键；
  测试验证 plain 与 namespaced 名字不会被混淆。
  - 证据路径：`codex-rs/protocol/src/tool_name.rs`、`codex-rs/core/src/tools/router.rs`、
    `codex-rs/core/src/tools/router_tests.rs`、`codex-rs/core/src/tools/registry_tests.rs`
  - 如何用于本章：正文解释 namespace 的价值，教学实现明确只使用普通字符串名字。
- `ToolRouter::build_tool_call` 从模型 `ResponseItem` 构造内部 ToolCall，并将 dispatch 交给
  Registry；不同 ResponseItem 种类可以形成不同 payload。
  - 证据路径：`codex-rs/core/src/tools/router.rs`
  - 如何用于本章：教学 Router 接收已经映射的 FunctionCall 并路由到 Registry。
- `build_prompt` 使用 Router 的 `model_visible_specs()` 构造发给模型的 Prompt。
  - 证据路径：`codex-rs/core/src/session/turn.rs`
  - 如何用于本章：教学 Thread 在每次 sampling 时把 Router specs 传给模型。
- 具体 Handler 会检查 payload 类型，并通过 `serde_json` 或共享 `parse_arguments` 将参数解析为
  强类型结构；解析失败会形成可返回模型的 FunctionCallError。
  - 证据路径：`codex-rs/core/src/tools/handlers/mod.rs`、
    `codex-rs/core/src/tools/handlers/plan.rs`、
    `codex-rs/core/src/tools/handlers/request_user_input.rs`
  - 如何用于本章：教学版在 Registry 前置实现受限 schema 验证，同时明确这不是生产实现复刻。
- Router 测试验证扩展工具可以同时模型可见并可 dispatch；spec plan 测试分别检查模型可见工具和
  已注册工具集合，并验证 deferred、hidden、无效 MCP schema 等边界。
  - 证据路径：`codex-rs/core/src/tools/router_tests.rs`、
    `codex-rs/core/src/tools/spec_plan_tests.rs`
  - 如何用于本章：正文强调“可见”与“已注册”是不同问题。
- `ToolOrchestrator` 集中负责 approval、sandbox 选择和拒绝后的 retry 语义，不是 Tool Registry
  的同义词。
  - 证据路径：`codex-rs/core/src/tools/orchestrator.rs`
  - 如何用于本章：本章只讲发现、验证与路由，将 approval 和 sandbox 留到后续章节。

## 教学实现的简化

- 教学版 ToolSpec 只支持 function 名称、描述、字段类型、必需字段和额外字段开关；真实 Codex
  使用更完整的 JSON Schema 风格类型，并支持多种 ToolSpec。
- 教学版所有已注册工具都模型可见；真实 Codex 区分 Direct、Deferred、DirectModelOnly、Hidden，
  并根据 Turn 上下文、模型能力、功能开关和工具来源规划暴露集合。
- 教学版使用普通字符串名字；真实 Codex 使用保留 namespace 的 `ToolName`。
- 教学版 Registry 集中执行受限参数验证；真实 Codex 的具体 Handler 常通过强类型反序列化与业务
  逻辑完成解析和校验。
- 教学版 Handler 同步执行并只返回字符串；真实工具输出具有更丰富类型，执行涉及异步、取消、
  lifecycle、hooks、遥测和外部上下文。
- 教学版把所有 ToolError 转为可恢复 ToolResult；真实 Codex 区分 RespondToModel、Fatal、审批
  拒绝、sandbox denial 和其他错误路径。
- 教学版没有实现 approval、sandbox、policy 与权限升级 retry；真实相关职责主要位于
  `ToolOrchestrator` 和 sandboxing 模块。

## 未确认与不写入正文的内容

- 不声称模型服务端一定依据 ToolSpec 自动拒绝所有非法参数。
- 不声称真实 Codex 在 Registry 中统一执行完整 JSON Schema 验证。
- 不声称所有已注册真实工具都必然直接暴露给模型。
- 不声称重复注册在所有构建模式下都以完全相同方式终止进程。
- 不描述私有服务端如何选择工具或生成 FunctionCall。
