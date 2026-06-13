# Progress

最后更新：2026-06-13

## 当前状态

- 当前里程碑：**M1 Runnable Core**
- 当前分支：`main`
- 已完成章节：2 / 24
- 已完成里程碑：**M0 Foundation**
- 正在进行：准备编写第三章
- 下一章：`s03_tool_registry`
- GitHub：`https://github.com/zz1403489227-crypto/learn-codex`

## 本次会话完成

- 确认根目录 `/Users/air/Documents/codex开源仓库学习` 作为教程仓库。
- 克隆并阅读 `learn-claude-code`，提炼其渐进式教学编排。
- 克隆并初步研究公开 `openai/codex` 源码。
- 确定 Python 为主要实现语言，Java 只在有明确教学价值时使用。
- 确定 24 章课程路线。
- 建立 `AGENTS.md`、`Plan.md`、`Progress.md` 与公开 README。
- 建立源码映射、决策记录、章节模板、目录骨架和结构检查脚本。
- 完成 `s01_turn_loop`：
  - 建立 Thread、Turn、Item 的初步心智模型。
  - 实现零依赖、可离线运行的 Python Turn Loop。
  - 使用粗粒度 Event 暴露 Turn 生命周期。
  - 添加工具调用、跨 Turn 历史、直接回答和循环上限测试。
- 增加硬性源码研究规则：
  - 每章必须先阅读对应 Codex 公开源码与测试。
  - 每个完成章节必须包含可审计的 `SOURCE_NOTES.md`。
  - 结构检查会拒绝缺少源码阅读记录的完成章节。
- 完成 `s02_streaming_items`：
  - 基于 Codex 公开源码区分模型侧 `ResponseItem` 与客户端侧 `TurnItem`。
  - 实现 Added、Delta、Done、Completed 模型流与客户端 Item 生命周期事件。
  - 实现按 `turn_id` 与 `item_id` 归并事件的 `EventReducer`。
  - 添加完成 Item 覆盖临时 delta、交错 Turn 路由和非法 delta 测试。
  - 明确教学版函数调用映射与真实 Codex 工具处理路径的边界。

## 章节进度

| 阶段 | 章节 | 状态 |
|---|---|---|
| 从循环到运行时 | s01-s02 完成；s03-s05 未开始 | 进行中 |
| 安全运行时 | s06-s09 | 未开始 |
| 上下文架构 | s10-s14 | 未开始 |
| 长期会话 | s15-s18 | 未开始 |
| 协作与扩展 | s19-s22 | 未开始 |
| 工程化与综合 | s23-s24 | 未开始 |

## 研究快照

- `openai/codex`: `f297b9f07de10c7d8b9ed284b674d06cc5ff7723`
- `shareAI-lab/learn-claude-code`: `20e7cbb72c66ab01967299ad3eac6c7bda242136`

详情与章节映射见 `docs/SourceMap.md`。

## 验证记录

- `python3 scripts/check_course.py`
  - 结果：通过，确认 24 个连续章节目录、必需交接文件和章节开篇 Mermaid 图。
- `git diff --check`
  - 结果：通过。
- `git status --short --branch --ignored`
  - 结果：确认 `learn-claude-code/` 与 `references/` 处于 ignored 状态。
- `/Users/air/.local/bin/python3.11 s01_turn_loop/code.py "Codex turns model requests into agent actions"`
  - 结果：一次 Turn 完成两次 sampling、一次工具调用，最终输出 `7 words`。
- `/Users/air/.local/bin/python3.11 -m unittest discover -s s01_turn_loop -p 'test_*.py' -v`
  - 结果：4 个测试通过。
- `/Users/air/.local/bin/python3.11 -m compileall -q s01_turn_loop`
  - 结果：通过。
- `/Users/air/.local/bin/python3.11 s02_streaming_items/code.py "Codex streams structured events to clients"`
  - 结果：一次 Turn 完成两次 sampling；assistant 文本先按 delta 展示，再由完成 Item 确认。
- `/Users/air/.local/bin/python3.11 -m unittest discover -s s02_streaming_items -p 'test_*.py' -v`
  - 结果：5 个测试通过。
- `/Users/air/.local/bin/python3.11 -m compileall -q s02_streaming_items`
  - 结果：通过。

## 已知问题与风险

- 公开 Codex 源码变化较快。章节写作前需要核对当前快照，避免把易漂移细节写成稳定事实。
- 当前系统默认 `python3` 是 3.9；课程代码目标为 Python 3.11+，本机可使用
  `/Users/air/.local/bin/python3.11` 或 `uv run --python 3.11`。
- 尚未确定教程最终许可证；正式发布前需要由用户确认。
- 章节目录当前仅为骨架，不代表正文完成。

## 下一步

1. 编写 `s03_tool_registry`：
   - 阅读 Codex Tool Registry、Router、Orchestrator 与相关测试。
   - 把 s02 中硬编码的 `count_words` 工具升级为可注册、可发现、可验证的工具。
   - 保持 s02 的事件流与 reducer 主干稳定。
2. 完成 s03 后更新本文件、单独 commit 并 push。
