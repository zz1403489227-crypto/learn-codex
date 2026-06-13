# AGENTS.md

本文件是这个多会话项目的最高优先级协作说明。每个接手本项目的会话都必须先完整阅读：

1. `AGENTS.md`
2. `Progress.md`
3. `Plan.md`
4. `docs/Decisions.md`
5. 当前要处理章节的 `README.md`

## 项目目标

创建一套可公开发布的 Codex 风格 Coding Agent 教程，暂定名称：

> **Learn Codex: 从 Agent Loop 到工程化 Coding Agent**

教程借鉴 `learn-claude-code` 的渐进式编排，但需要更进一步解释 Codex 中更成熟的工程机制：

- 事件流和结构化 Item，而不只依赖一次请求的 `stop_reason`
- Thread、Turn、Rollout 与恢复
- Approval、Sandbox、Policy 与可信项目边界
- 配置、`AGENTS.md`、上下文片段和 Skills 的分层加载
- Context compaction、Memory 与错误恢复
- Subagent、Worktree、MCP、Plugin 与 App Server
- 可观测性、协议测试和 Agent 行为测试

## 目标读者

目标读者是已经学完 `learn-claude-code`、理解基本 Agent Loop 和工具调用，但尚未形成深厚
Agent 工程经验的开发者。

写作必须满足：

- 比 `learn-claude-code` 更深，但不能变成 Rust 源码逐行导读。
- 先讲问题、心智模型与设计取舍，再展示教学实现。
- 对真实 Codex 的描述必须有源码或官方文档依据。
- 明确区分“教学实现”“真实 Codex”“合理推断”，禁止把简化实现描述为生产事实。
- 默认使用中文写作；重要术语首次出现时保留英文。

## 技术与语言策略

- 教学代码默认使用 **Python 3.11+**。
- 不要求每章同时提供 Python 和 Java。
- Java 仅在它能显著帮助理解时引入，例如：
  - 强类型事件模型
  - JSON-RPC / App Server 客户端
  - 企业系统中的 Agent 集成边界
- 不使用 Rust 重写章节代码，但可以引用 Rust 文件路径解释真实 Codex 的设计。
- 每章代码以“最小但完整”为目标，不追求生产框架的大而全。

## 课程结构

- 当前课程规划为 24 章，详见 `Plan.md`。
- 每章只引入一个主要机制，并继承必要的前置概念。
- 每章开头必须有一幅总结本章机制的图。
- 默认使用 Mermaid 保存图的可维护源文件；发布前可导出 SVG。
- 每章应包含：
  1. 本章图
  2. 本章要解决的问题
  3. 心智模型
  4. 最小教学实现
  5. 工作原理
  6. 相对上一章的变化
  7. 与真实 Codex 的对应关系
  8. 教学简化与生产边界
  9. 可运行实验
  10. 小结与下一章

## 章节完成标准

章节只有同时满足下列条件，才能在 `Progress.md` 标记为完成：

- 写作前已实际阅读该章对应的 Codex 公开源码、相关测试与模块文档；禁止只凭既有印象写作。
- 章节目录包含 `SOURCE_NOTES.md`，记录源码快照、实际阅读文件、源码事实、教学简化和未确认项。
- `README.md` 不再是占位稿，且开头包含本章图。
- 核心概念能由目标读者理解，不依赖提前阅读 Rust 源码。
- 至少有一个可运行的 Python 示例；纯概念章需在 `Plan.md` 中提前说明例外。
- 示例代码通过本章测试或最小运行验证。
- 写明真实 Codex 的对应源码路径，且基于当前记录的源码快照核对。
- 写明教学版主动省略了什么。
- `python scripts/check_course.py` 通过。
- 更新 `Progress.md`，记录完成内容、验证命令和下一步。
- 一章一个独立 Git commit；有远程仓库时完成后立即 push。

## 多会话交接协议

开始工作时：

1. 阅读规定的交接文件。
2. 运行 `git status --short --branch`，不得覆盖其他会话留下的修改。
3. 查看 `Progress.md` 的“下一步”与“已知问题”。
4. 核对 `docs/SourceMap.md` 中记录的 Codex 源码快照。
5. 只领取一个明确章节或一个基础设施任务。

工作过程中：

- 保持改动集中，不顺手重写无关章节。
- 每章必须先研究源码，再设计教学实现；不得先写结论、再寻找源码为结论背书。
- 阅读源码时同时查看相关测试。测试通常比实现细节更能说明稳定行为。
- 将实际阅读记录写入该章节的 `SOURCE_NOTES.md`，不要只依赖 `docs/SourceMap.md` 的初始入口。
- 新的重要决策写入 `docs/Decisions.md`。
- 发现章节路线需要调整时，先更新 `Plan.md`，并在 `Progress.md` 说明原因。
- 涉及真实 Codex 行为时，优先阅读 `references/codex/`；该目录是本地参考，不提交。
- `learn-claude-code/` 只用于学习教学编排，不复制其文本或实现。

结束工作时：

1. 完成验证。
2. 更新 `Progress.md`，包括本次完成、验证结果、未解决问题和明确下一步。
3. 检查 `git diff`。
4. 按任务边界提交；完成章节时使用类似 `feat(s01): add turn loop lesson` 的提交信息。
5. 如果远程已配置，完成章节后立即 push。

## 事实与引用规则

- 开源 Codex 主参考仓库：`https://github.com/openai/codex`
- 本地参考路径：`references/codex/`
- 教学结构参考路径：`learn-claude-code/`
- 当前研究快照记录在 `docs/SourceMap.md`，后续会话更新源码前必须同时更新快照。
- `docs/SourceMap.md` 只是研究入口；每章的 `SOURCE_NOTES.md` 才是该章实际使用的证据清单。
- 每个关于真实 Codex 行为的陈述，都必须能够追溯到本章 `SOURCE_NOTES.md` 中列出的公开源码、测试或官方文档。
- 如果源码无法确认某项行为，必须删除该陈述，或明确标注为“教学设计”或“推断”。
- 源码发生变化时，优先保持教程心智模型稳定，再更新易漂移的实现细节。
- 不声称教程复刻了 Codex 内部全部行为。
- 不引用或泄露私有实现；只使用公开源码与官方公开文档。

## Git 与发布规则

- 根目录就是教程仓库，不要再创建嵌套的 `learn-codex/`。
- `learn-claude-code/` 和 `references/` 必须保持忽略状态。
- 不提交 API Key、`.env`、本地线程、rollout 或 memory 数据。
- 每完成一个章节，单独 commit 并 push。
- 基础设施可以按逻辑单元单独提交。
- 未经用户明确要求，不发布未完成章节为正式版本。
