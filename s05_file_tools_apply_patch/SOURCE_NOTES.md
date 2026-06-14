# Source Notes

## 研究快照

- 仓库：`openai/codex`
- Commit：`f297b9f07de10c7d8b9ed284b674d06cc5ff7723`
- 研究日期：2026-06-14

## 实际阅读

### 源码

- `codex-rs/apply-patch/src/lib.rs`
- `codex-rs/apply-patch/src/parser.rs`
- `codex-rs/apply-patch/src/invocation.rs`
- `codex-rs/apply-patch/src/seek_sequence.rs`
- `codex-rs/core/src/apply_patch.rs`
- `codex-rs/core/src/tools/handlers/apply_patch.rs`
- `codex-rs/core/src/tools/handlers/apply_patch_spec.rs`
- `codex-rs/core/src/tools/handlers/apply_patch.lark`
- `codex-rs/core/src/tools/runtimes/apply_patch.rs`
- `codex-rs/core/src/tools/context.rs`

### 测试

- `codex-rs/apply-patch/tests/suite/tool.rs`
- `codex-rs/apply-patch/tests/suite/scenarios.rs`
- `codex-rs/apply-patch/tests/fixtures/scenarios/README.md`
- `codex-rs/apply-patch/tests/fixtures/scenarios/001_add_file/patch.txt`
- `codex-rs/apply-patch/tests/fixtures/scenarios/002_multiple_operations/patch.txt`
- `codex-rs/apply-patch/tests/fixtures/scenarios/004_move_to_new_directory/patch.txt`
- `codex-rs/apply-patch/tests/fixtures/scenarios/006_rejects_missing_context/patch.txt`
- `codex-rs/apply-patch/tests/fixtures/scenarios/009_requires_existing_file_for_update/patch.txt`
- `codex-rs/apply-patch/tests/fixtures/scenarios/015_failure_after_partial_success_leaves_changes/patch.txt`
- `codex-rs/apply-patch/tests/fixtures/scenarios/020_delete_file_success/patch.txt`
- `codex-rs/core/src/apply_patch_tests.rs`
- `codex-rs/core/src/tools/handlers/apply_patch_tests.rs`
- `codex-rs/core/src/tools/handlers/apply_patch_spec_tests.rs`
- `codex-rs/core/src/tools/runtimes/apply_patch_tests.rs`
- `codex-rs/core/tests/suite/tool_harness.rs`
- `codex-rs/core/tests/suite/unified_exec.rs`

## 从源码确认的事实

- `apply_patch` 使用带 begin/end marker 的专用补丁格式，hunk 包含 add、delete 和 update；update
  可包含 move、change context、change lines 与 end-of-file marker。
  - 证据路径：`codex-rs/apply-patch/src/parser.rs`、`core/src/tools/handlers/apply_patch.lark`
  - 如何用于本章：教学版实现 add/delete/update 和最小 change lines 子集。
- 核心 Handler 将 `apply_patch` 暴露为带 Lark grammar 的 freeform tool，并明确要求不要把补丁正文
  包在 JSON 中。
  - 证据路径：`codex-rs/core/src/tools/handlers/apply_patch_spec.rs`
  - 如何用于本章：正文说明教学 Registry 使用 JSON 参数承载补丁是协议简化。
- parser 负责语法与 hunk 结构，不负责确认补丁能否应用到文件系统。
  - 证据路径：`codex-rs/apply-patch/src/parser.rs` 模块注释
  - 如何用于本章：教学实现明确分开 `parse_patch` 与 `Workspace._verify`。
- `verify_apply_patch_args` 会解析相对路径、读取 delete 目标内容，并为 update 计算 unified diff 和
  new content；失败时返回 correctness error。
  - 证据路径：`codex-rs/apply-patch/src/invocation.rs`
  - 如何用于本章：教学版在提交前检查目标、读取旧内容并计算新内容。
- `ApplyPatchAction` 保存解析和验证后的 changes、原始 patch 与 cwd；按构造约定其路径应为绝对路径。
  - 证据路径：`codex-rs/apply-patch/src/lib.rs`
  - 如何用于本章：教学 `PatchPlan` 与 `Workspace.resolve` 分别表达拟议变化与解析后的工作区路径。
- 实际应用按 hunk 顺序处理 add、delete、update，成功后输出 added/modified/deleted 路径摘要。
  - 证据路径：`codex-rs/apply-patch/src/lib.rs`、`apply-patch/tests/suite/tool.rs`
  - 如何用于本章：教学工具返回 operation/path 结构化摘要。
- update 会从现有文件推导新内容；找不到 expected lines 时失败。匹配从精确逐步放宽到忽略尾部空白、
  忽略首尾空白以及归一化常见 Unicode 标点。
  - 证据路径：`codex-rs/apply-patch/src/lib.rs`、`codex-rs/apply-patch/src/seek_sequence.rs`
  - 如何用于本章：教学版只允许旧文本唯一精确匹配，并把宽松匹配列为省略项。
- apply-patch 场景测试使用 input、patch、expected 三部分，并比较最终文件系统状态。
  - 证据路径：`codex-rs/apply-patch/tests/fixtures/scenarios/README.md`、
    `codex-rs/apply-patch/tests/suite/scenarios.rs`
  - 如何用于本章：教学测试也在临时工作区验证最终文件状态。
- add、update、delete、多操作、多 chunk、move、缺失上下文、缺失文件与非法 hunk 都有行为测试。
  - 证据路径：`codex-rs/apply-patch/tests/suite/tool.rs` 与 fixtures
  - 如何用于本章：选择其中适合最小实现的核心成功与失败路径。
- 真实 apply-patch 不是全量回滚事务；测试确认较早 hunk 成功后，后续失败会保留此前修改。
  `ApplyPatchFailure` 携带已提交的 `AppliedPatchDelta`。
  - 证据路径：`codex-rs/apply-patch/src/lib.rs`、
    `apply-patch/tests/fixtures/scenarios/015_failure_after_partial_success_leaves_changes/patch.txt`、
    `apply-patch/tests/suite/tool.rs`
  - 如何用于本章：正文明确教学版的“完整计划预验证”是主动差异，不描述为真实行为。
- `AppliedPatchDelta` 按提交顺序保存实际变化，并记录 delta 是否 exact；失败写入可能让 exactness
  变为 false。
  - 证据路径：`codex-rs/apply-patch/src/lib.rs`
  - 如何用于本章：作为生产边界，教学版只返回成功摘要。
- 核心 ApplyPatchHandler 先 parse、选择 environment、验证文件系统状态，再解析目标权限并经过安全
  评估和 ToolOrchestrator。
  - 证据路径：`codex-rs/core/src/tools/handlers/apply_patch.rs`、
    `codex-rs/core/src/apply_patch.rs`、`codex-rs/core/src/tools/runtimes/apply_patch.rs`
  - 如何用于本章：本章只实现 parse/verify/apply，approval 与 sandbox 留到 s06/s07。
- move 操作的权限/审批目标同时包含源路径和目标路径。
  - 证据路径：`codex-rs/core/src/tools/handlers/apply_patch.rs`、
    `codex-rs/core/src/tools/handlers/apply_patch_tests.rs`
  - 如何用于本章：move 暂未实现，并列为生产边界。
- Handler 可消费正在流式生成的 patch argument，并发出节流后的 `PatchApplyUpdated` 事件。
  - 证据路径：`codex-rs/core/src/tools/handlers/apply_patch.rs`、
    `codex-rs/core/src/tools/handlers/apply_patch_tests.rs`
  - 如何用于本章：教学版只在完整 patch 到达后解析，不实现 patch 进度事件。
- shell 与 unified exec 中符合支持形态的 apply_patch 会被拦截，走 patch 生命周期；集成测试确认
  不产生普通 exec-command begin/end 事件。
  - 证据路径：`codex-rs/apply-patch/src/invocation.rs`、
    `codex-rs/core/src/tools/handlers/apply_patch.rs`、
    `codex-rs/core/tests/suite/unified_exec.rs`
  - 如何用于本章：正文用它说明结构化编辑边界优于普通 shell 执行边界。
- 本章检索的核心内建工具 spec 中没有独立 `read_file`；测试中出现的 `read_file` 也可来自 MCP
  filesystem 工具。
  - 证据路径：`codex-rs/core/src/tools/handlers/apply_patch_spec.rs`、
    `codex-rs/core/src/tools/handlers/mcp.rs`
  - 如何用于本章：明确把教学 `read_file` 标为课程设计，而非真实核心接口复刻。

## 教学实现的简化

- 教学版用函数工具 JSON 参数承载 patch；真实 Codex apply_patch 是 freeform tool。
- 教学 parser 只支持 add/delete/update 和单个匿名 update chunk。
- 教学 update 只接受唯一精确旧文本；未实现真实实现的宽松匹配和 Unicode 归一化。
- 教学版拒绝 add 覆盖已有文件；真实 apply-patch 测试确认 add 可以覆盖。
- 教学版先验证完整计划，再开始提交；真实实现按 hunk 应用并追踪部分成功。
- 教学版同一 patch 禁止重复目标，未实现多个 update chunk 或对同一文件的连续操作。
- 教学版限制在一个本地 workspace，不支持绝对目标、remote environment 或 ExecutorFileSystem。
- 教学版没有 approval、sandbox、permission profile、hooks、turn diff tracker 和事件。
- 教学版只处理 UTF-8 文本，不保留权限、换行风格和编码，也没有竞态防护。
- 教学版 `read_file` 是课程工具，不声称对应真实 Codex 内建核心工具。

## 未确认与不写入正文的内容

- 不声称真实 Codex 的 apply_patch 是原子事务或会自动回滚失败补丁。
- 不声称所有文件读取都通过某个固定核心工具完成。
- 不声称真实 Codex 只允许工作区内路径；真实行为取决于 sandbox、approval 与权限配置。
- 不声称真实 Codex 使用 Python pathlib、字符串 replace 或教学版唯一匹配算法。
- 不描述公开源码无法确认的服务端文件修改行为。
