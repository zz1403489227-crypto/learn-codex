# Progress

最后更新：2026-06-15

## 当前状态

- 当前里程碑：**M2 Safe Runtime**
- 当前分支：`main`
- 已完成章节：7 / 24
- 已完成里程碑：**M0 Foundation**、**M1 Runnable Core**
- 正在进行：准备编写第八章
- 下一章：`s08_config_and_trust`
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
- 完成 `s03_tool_registry`：
  - 基于 Codex ToolExecutor、ToolRegistry、ToolRouter 与 spec plan 建立工具运行时心智模型。
  - 把硬编码 `count_words` 升级为 ToolSpec、Handler、Registry 与 Router。
  - 在每次 sampling 时向模型暴露当前工具 specs。
  - 实现必需参数、类型和额外参数的受限教学验证器。
  - 将未知工具与非法参数转成可返回模型的失败 ToolResult。
  - 添加发现、路由、重复注册、执行前验证和完整 Turn 测试。
  - 明确模型可见工具与已注册工具、教学集中验证与真实 Handler 强类型解析的边界。
- 完成 `s04_shell_execution`：
  - 基于 Codex Unified Exec、ProcessManager、HeadTailBuffer 与相关集成测试建立长命令心智模型。
  - 在 s03 Tool Registry 上注册共享 ProcessManager 的 `exec_command` 与 `write_stdin`。
  - 实现有限 yield、运行中 session、空轮询、stdin 写入、退出状态和完成后 session 清理。
  - 同时限制未读 session 输出与单次模型响应，并保留截断和退出元数据。
  - 添加短命令、长命令、交互输入、未知 session、资源回收和完整 Turn 测试。
  - 明确教学版 `subprocess` 与真实 Codex approval、sandbox、PTY、exec-server 的边界。
- 完成 `s05_file_tools_apply_patch`：
  - 基于 Codex apply-patch parser、verification、handler、runtime 与行为测试建立结构化修改心智模型。
  - 在既有 Registry 上注册受工作区约束的 `read_file` 与 `apply_patch`。
  - 实现 add、update、delete 的最小补丁语法，并分离 parse、verify 与 commit。
  - 使用旧文本唯一匹配检测缺失或歧义上下文，拒绝绝对路径、工作区逃逸和重复目标。
  - 在任何写入前验证完整教学 PatchPlan，并明确区别于真实 Codex 可追踪部分成功的行为。
  - 添加补丁解析、读取上限、路径边界、多文件修改、失败无提交和完整 Turn 测试。
  - 明确教学 `read_file`、JSON patch 参数与真实 Codex freeform apply_patch 的边界。
- 完成 `s06_approval_pipeline`：
  - 基于 Codex ReviewDecision、ExecApprovalRequirement、ToolOrchestrator 与 pending approvals 建立审批状态机。
  - 在工具验证与执行之间加入 ApprovalOrchestrator，区分 Skip、NeedsApproval 与 Forbidden。
  - 新增结构化 ApprovalRequest、requested/resolved 事件和 Turn pending approval 投影。
  - 实现 Approved、ApprovedForSession、Denied 与 Abort，并区分拒绝后继续和中止当前 Turn。
  - 使用精确 action keys 缓存 session approval，只有全部 keys 命中时才跳过提示。
  - 在 patch 审批前预验证拟议变化，并在获批执行时再次验证。
  - 添加分类、事件顺序、拒绝、中止、session 缓存、无效请求和完整 Turn 测试。
  - 明确 approval 表达用户同意，但不替代 sandbox 与实际权限限制。
- 完成 `s07_sandbox_permissions`：
  - 基于 Codex PermissionProfile、SandboxManager、ToolOrchestrator 与平台 sandbox 测试建立权限强制心智模型。
  - 明确区分 Approval、Permission Profile 与 Sandbox 的职责。
  - 实现 read-only、workspace-write 与 danger-full-access 教学权限 profile。
  - 实现路径级 Read、Write、Deny 规则、默认拒绝与更具体规则优先。
  - 在获批工具执行时强制检查文件读写和网络权限。
  - 保护 workspace 内的 `.git` 与 `.codex` 元数据路径。
  - 新增结构化 SandboxDenial 与 `sandbox/denied` 事件。
  - 使用模拟 network probe 展示网络权限与文件权限相互独立。
  - 添加获批后仍被 read-only sandbox 拒绝、审批预览保留 deny-read、deny 规则优先和失败无副作用测试。
  - 明确教学进程内检查不能替代操作系统级 sandbox。

## 章节进度

| 阶段 | 章节 | 状态 |
|---|---|---|
| 从循环到运行时 | s01-s05 | 完成 |
| 安全运行时 | s06-s07 完成；s08-s09 未开始 | 进行中 |
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
- `/Users/air/.local/bin/python3.11 s03_tool_registry/code.py "tools need discoverable contracts"`
  - 结果：模型看到 `count_words` 与 `repeat_text`，一次 Turn 完成两次 sampling 和一次注册工具调用。
- `/Users/air/.local/bin/python3.11 -m unittest discover -s s03_tool_registry -p 'test_*.py' -v`
  - 结果：7 个测试通过。
- `/Users/air/.local/bin/python3.11 -m compileall -q s03_tool_registry`
  - 结果：通过。
- `/Users/air/.local/bin/python3.11 s04_shell_execution/code.py`
  - 结果：一次 Turn 完成三次 sampling，依次执行 `exec_command`、空 `write_stdin` 轮询和最终回答。
- `/Users/air/.local/bin/python3.11 -W error::ResourceWarning -m unittest discover -s s04_shell_execution -p 'test_*.py' -v`
  - 结果：11 个测试通过，完成路径无 ResourceWarning。
- `/Users/air/.local/bin/python3.11 -m compileall -q s04_shell_execution`
  - 结果：通过。
- `/Users/air/.local/bin/python3.11 s05_file_tools_apply_patch/code.py`
  - 结果：一次 Turn 完成三次 sampling，依次读取文件、提交多文件 patch 并生成最终回答。
- `/Users/air/.local/bin/python3.11 -m unittest discover -s s05_file_tools_apply_patch -p 'test_*.py' -v`
  - 结果：10 个测试通过。
- `/Users/air/.local/bin/python3.11 -m compileall -q s05_file_tools_apply_patch`
  - 结果：通过。
- `/Users/air/.local/bin/python3.11 s06_approval_pipeline/code.py`
  - 结果：读取自动执行，patch 在 requested/resolved 审批事件后执行并完成 Turn。
- `/Users/air/.local/bin/python3.11 -m unittest discover -s s06_approval_pipeline -p 'test_*.py' -v`
  - 结果：12 个测试通过。
- `/Users/air/.local/bin/python3.11 -m compileall -q s06_approval_pipeline`
  - 结果：通过。
- `/Users/air/.local/bin/python3.11 s07_sandbox_permissions/code.py`
  - 结果：patch 获批后被 read-only sandbox 拒绝，产生 `sandbox/denied`，文件保持不变且 Turn 正常完成。
- `/Users/air/.local/bin/python3.11 -m unittest discover -s s07_sandbox_permissions -p 'test_*.py' -v`
  - 结果：20 个测试通过。
- `/Users/air/.local/bin/python3.11 -m compileall -q s07_sandbox_permissions`
  - 结果：通过。

## 已知问题与风险

- 公开 Codex 源码变化较快。章节写作前需要核对当前快照，避免把易漂移细节写成稳定事实。
- 当前系统默认 `python3` 是 3.9；课程代码目标为 Python 3.11+，本机可使用
  `/Users/air/.local/bin/python3.11` 或 `uv run --python 3.11`。
- 尚未确定教程最终许可证；正式发布前需要由用户确认。
- 章节目录当前仅为骨架，不代表正文完成。

## 下一步

1. 编写 `s08_config_and_trust`：
   - 阅读 Codex config layer、permission profile resolution、config lock、project trust 与相关测试。
   - 实现用户配置、项目配置、默认值和可信状态的分层合并。
   - 解释配置来源、优先级、锁定值与可信项目边界。
2. 完成 s08 后更新本文件、单独 commit 并 push。
