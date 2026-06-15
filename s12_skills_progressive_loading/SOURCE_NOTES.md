# Source Notes

## 研究快照

- `openai/codex`: `f297b9f07de10c7d8b9ed284b674d06cc5ff7723`
- 获取日期：2026-06-13
- 本章研究日期：2026-06-15

## 实际阅读

### Skills 数据模型、扫描与加载

- `codex-rs/core-skills/src/model.rs`
- `codex-rs/core-skills/src/loader.rs`
- `codex-rs/core-skills/src/loader_tests.rs`
- `codex-rs/core-skills/src/manager.rs`
- `codex-rs/core-skills/src/manager_tests.rs`
- `codex-rs/core/src/session/mod.rs`
- `codex-rs/core/src/session/turn_context.rs`

### Skills 目录渲染与上下文片段

- `codex-rs/core-skills/src/render.rs`
- `codex-rs/core/src/context/available_skills_instructions.rs`
- `codex-rs/core/src/event_mapping.rs`
- `codex-rs/core/src/event_mapping_tests.rs`
- `codex-rs/ext/skills/src/fragments.rs`
- `codex-rs/ext/skills/tests/skills_extension.rs`

### 显式选择、注入与隐式调用记录

- `codex-rs/core-skills/src/injection.rs`
- `codex-rs/core-skills/src/injection_tests.rs`
- `codex-rs/core-skills/src/skill_instructions.rs`
- `codex-rs/core-skills/src/invocation_utils.rs`
- `codex-rs/core-skills/src/invocation_utils_tests.rs`
- `codex-rs/core/src/skills.rs`
- `codex-rs/core/src/session/turn.rs`

### Orchestrator / Extension Skill 工具

- `codex-rs/ext/skills/src/tools/list.rs`
- `codex-rs/ext/skills/src/tools/read.rs`
- `codex-rs/ext/skills/src/tools/schema.rs`
- `codex-rs/ext/skills/src/selection.rs`
- `codex-rs/ext/skills/src/render.rs`
- `codex-rs/ext/skills/src/provider/orchestrator.rs`
- `codex-rs/ext/skills/src/provider/host.rs`

## 从源码确认的事实

- Skill 主文件名是 `SKILL.md`；loader 从 YAML frontmatter 读取 `name`、`description` 和
  `metadata.short-description`，缺少 frontmatter 或必填字段会产生加载错误。
- Skill 还可以有旁路元数据文件 `agents/openai.yaml`，其中可提供 interface、dependencies 和
  policy；本章重点使用 policy 中的 `allow_implicit_invocation`。
- `SkillMetadata` 记录 name、description、short description、interface、dependencies、policy、
  `path_to_skills_md`、scope 和 plugin id。
- Skills root 来自多层来源：项目 `.codex/skills`、用户 `$CODEX_HOME/skills`、用户
  `$HOME/.agents/skills`、系统缓存 `.system`、admin `/etc/codex/skills`、plugin roots、
  extra roots，以及项目树中的 `.agents/skills`。
- loader 扫描 root 时跳过隐藏目录，最大扫描深度为 6，每个 root 最多扫描 2000 个目录；repo/user/admin
  scope 会跟随目录 symlink，system scope 不跟随。
- 加载结果会按路径去重，并记录 skill root、skill path 到 root 的映射，以及 skill path 到文件系统的
  映射。
- 加载排序按 Repo、User、System、Admin 优先；但目录渲染面向 prompt 时的优先级是
  System、Admin、Repo、User。
- `SkillPolicy::allow_implicit_invocation` 默认为允许；显式设置为 false 时，该 skill 不进入隐式调用
  候选。
- available skills 目录是 developer role 的上下文片段，使用 `<skills_instructions>` markers。
- available skills 目录只渲染 name、description 和文件/资源位置，不直接注入完整 `SKILL.md` 正文。
- 目录渲染默认有 metadata 预算：没有 context window 时用 8000 字符；有 context window 时用窗口的
  2% token 预算。
- 当目录超预算时，真实实现先尝试缩短 description；如果最小行仍放不下，才省略部分 skills，并产生
  warning。
- 真实渲染会在绝对路径和 alias path 两种形式之间选择更省预算的输出；alias 表使用 `r0`、`r1` 这类
  root 缩写。
- 使用说明明确要求：决定使用某个 skill 后，主 agent 必须完整读取对应 `SKILL.md`；如果读取被截断或
  分页，必须继续到 EOF。
- 使用说明要求相对路径先从 `SKILL.md` 所在目录解析；如果 skill 提供 scripts/assets/templates，应优先
  复用。
- 显式 skill 提及支持文本中的 `$skill-name`，也支持带路径的 `[$skill-name](path)`；结构化
  `UserInput::Skill` 选择优先于纯文本提及。
- 显式提及会跳过常见环境变量名，如 `$PATH`、`$HOME`、`$XDG_CONFIG_HOME`，避免误认为 skill。
- 纯名字提及只有在名字不歧义时才会选中；重名 skill 需要路径或结构化选择。
- 如果结构化选择或链接路径指向缺失/禁用 skill，不会退回到同名纯文本匹配。
- skill 注入以 user role 的 `<skill>` fragment 发送，body 包含 `<name>`、`<path>` 和完整
  `SKILL.md` 内容。
- host skill 注入会记录已注入路径，避免重复注入同一个 host prompt。
- 隐式调用检测不会注入正文；它用于识别 agent 已经在使用某个 skill，并记录 telemetry/analytics。
- 隐式调用检测会识别读取某个 `SKILL.md` 的命令，以及运行某个 skill `scripts/` 目录下脚本的命令。
- 脚本检测支持常见 runner，如 python、python3、bash、zsh、sh、node、deno、ruby、perl、pwsh；
  `python -c` 这类 inline command 不会被当成 skill script。
- 隐式调用记录会在同一 turn 内按 scope/path/name 去重。
- Extension skills 提供 `skills.list` 和 `skills.read` 工具；orchestrator-owned skills 使用 opaque
  package/resource handle，模型不能把它们当成本地文件路径。
- `skills.read` 要求 package 必须来自当前 authority 的可用 catalog，且 provider 返回的 resource 必须与
  请求 resource 一致。

## 教学实现的简化

- 教学版只实现本地 filesystem skills，不实现 executor/orchestrator authority、MCP resource 或 custom
  resource。
- 教学版的 YAML 解析只覆盖本章 fixture 需要的简单 frontmatter，不复刻 `serde_yaml` 的完整行为。
- 教学版只解析 `agents/openai.yaml` 中的 `policy.allow_implicit_invocation`，不实现 interface、
  dependencies、product restriction 或 icon/default prompt 解析。
- 教学版总是为已使用 root 输出 alias 表；真实 Codex 会比较绝对路径和 alias path 哪个更省预算。
- 教学版 metadata budget 使用字符数，不实现真实 token 估算。
- 教学版 description 截断按字符轮询分配，保留“先缩短描述、再省略 skill”的心智模型，不复刻所有
  warning 阈值和统计指标。
- 教学版的显式选择只处理 `$name` 与 `[$name](path)`，不实现完整 `UserInput`、text elements 或
  connector slug 计数来源。
- 教学版的隐式调用检测只演示 `SKILL.md` 读取和 `scripts/` 脚本运行，不覆盖真实读命令解析器的全部
  shell 语法。
- 教学版用 `SkillInvocationTracker` 演示 turn 内去重，不实现 OpenTelemetry 或 analytics event。
- 教学版把 available skills 接入 s11 的 `ContextAssembler`，但没有实现真实 session 中所有 extension
  contributors 的生命周期。

## 未确认与不写入正文的内容

- 不把当前的 root 来源、scan limit、warning 文案或 skill metadata 字段当成长期稳定公共 API；它们会随
  Codex 版本演进。
- 不声称所有插件 skill 都一定是本地文件；extension/orchestrator skills 可能必须通过 `skills.read`
  读取 opaque resource。
- 不声称 skill description 足以完成任务；真实使用说明要求选中后完整读取 `SKILL.md`。
- 不声称隐式调用等同于启用某个 skill 的全部指令；真实实现主要用于记录和去重。
