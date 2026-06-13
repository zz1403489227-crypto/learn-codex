# Progress

最后更新：2026-06-13

## 当前状态

- 当前里程碑：**M0 Foundation**
- 当前分支：`main`
- 已完成章节：0 / 24
- 正在进行：项目基础设施与课程路线
- 下一章：`s01_turn_loop`

## 本次会话完成

- 确认根目录 `/Users/air/Documents/codex开源仓库学习` 作为教程仓库。
- 克隆并阅读 `learn-claude-code`，提炼其渐进式教学编排。
- 克隆并初步研究公开 `openai/codex` 源码。
- 确定 Python 为主要实现语言，Java 只在有明确教学价值时使用。
- 确定 24 章课程路线。
- 建立 `AGENTS.md`、`Plan.md`、`Progress.md` 与公开 README。
- 建立源码映射、决策记录、章节模板、目录骨架和结构检查脚本。

## 章节进度

| 阶段 | 章节 | 状态 |
|---|---|---|
| 从循环到运行时 | s01-s05 | 未开始 |
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

## 已知问题与风险

- 公开 Codex 源码变化较快。章节写作前需要核对当前快照，避免把易漂移细节写成稳定事实。
- 当前系统默认 `python3` 是 3.9；课程代码目标为 Python 3.11+，本机可使用
  `/Users/air/.local/bin/python3.11` 或 `uv run --python 3.11`。
- Foundation 推送前仍需创建 GitHub 远程仓库。
- 章节目录当前仅为骨架，不代表正文完成。

## 下一步

1. 完成并验证 Foundation commit。
2. 创建 GitHub 公开仓库并推送 Foundation。
3. 编写 `s01_turn_loop`：
   - 图：User Input → Turn → Model → Tool Call → Tool Result → Turn Complete
   - Python 最小事件化 Turn Loop
   - 解释 Codex 的 Thread / Turn / Item 术语边界，但不提前展开持久化
   - 添加本章测试与运行说明
4. 完成 s01 后更新本文件、单独 commit 并 push。
