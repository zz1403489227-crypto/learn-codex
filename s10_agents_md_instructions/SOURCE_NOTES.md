# Source Notes

## 研究快照

- `openai/codex`: `f297b9f07de10c7d8b9ed284b674d06cc5ff7723`
- 获取日期：2026-06-13
- 本章研究日期：2026-06-15

## 实际阅读

### AGENTS.md 发现与组装

- `codex-rs/core/src/agents_md.rs`
- `codex-rs/core/src/agents_md_tests.rs`
- `codex-rs/core/src/context/user_instructions.rs`
- `codex-rs/core/src/session/mod.rs`
- `codex-rs/core/src/session/turn_context.rs`

### 配置与公开提示

- `codex-rs/core/src/config/mod.rs`
- `codex-rs/config/src/config_toml.rs`
- `codex-rs/config/src/project_root_markers.rs`
- `codex-rs/prompts/src/agents.rs`
- `codex-rs/prompts/templates/agents/hierarchical.md`
- `docs/agents_md.md`

## 从源码确认的事实

- Codex 的项目文档主文件名是 `AGENTS.md`，并支持配置
  `project_doc_fallback_filenames`。
- 每个目录中的候选顺序为 `AGENTS.override.md`、`AGENTS.md`、配置 fallback；同一目录只选择首个
  存在的普通文件。
- `AGENTS.override.md` 是优先于 `AGENTS.md` 的本地 override，而不是与同目录 `AGENTS.md` 一起
  拼接。
- 项目根通过从 cwd 向上查找 `project_root_markers` 决定；未配置时默认 marker 为 `.git`。
- 如果没有找到根 marker，只检查 cwd；显式空 marker 列表同样禁用父目录遍历。
- 发现范围从项目根到 cwd，包含两端，不越过项目根；文档按根到 cwd 顺序加载。
- `.git` marker 可以是文件或目录，只要 metadata 表明它存在即可。
- 计算 `project_root_markers` 时，当前实现不会让 project config layers 覆盖该值，避免项目内容
  改写自身发现边界。
- `agents_md_paths` 允许 symlink cwd，并保留所选 cwd 路径，不先 canonicalize 成另一条路径。
- 候选必须是普通文件；目录和特殊文件不会作为项目文档加载。
- `project_doc_max_bytes` 是单个环境内所有项目文档共享的累计字节预算，默认值为 32 KiB。
- 文档按根到叶顺序消耗预算，因此较早文档可以让较深层文档被截断；预算为零时项目文档加载关闭。
- 空白项目文档不会成为 instruction entry，也不会消耗累计预算。
- 无效 UTF-8 会产生 startup warning，并使用 lossy replacement text 继续加载。
- 文档在发现后被删除属于可恢复情况；其他 metadata 或 read I/O 错误会向上传播，由调用者决定
  如何处理。
- Host 提供的 user instructions 与项目文档分开保存。模型可见文本在从 user/internal instructions
  过渡到 project docs 时加入 `--- project-doc ---` 分隔标记。
- `LoadedAgentsMd` 保留有序 instruction entries 与 provenance，并可列出 user instructions 和
  项目文档的真实 source paths。
- 单一项目环境的最终文本被渲染为用户角色 contextual fragment，外层格式为
  `# AGENTS.md instructions for <cwd>` 与 `<INSTRUCTIONS>`。
- 多环境绑定时，每个环境独立应用项目文档字节预算；如果多个环境都贡献文档，最终文本会按
  environment id 与 cwd 分组标记。
- 当前多环境实现没有跨环境 aggregate cap；源码测试中明确保留了后续增加总上限的 TODO。
- `child_agents_md` feature 开启时，即使没有项目文档，也会追加一次内部层级作用域提示；该内部
  提示不是文件 source。
- Skills 与 Apps 不会被直接拼接到 AGENTS.md user instructions 中，它们属于其他上下文机制。
- Session 启动时加载 `LoadedAgentsMd`，TurnContext 再把其 render 结果作为 user instructions
  contextual fragment。

## 教学实现的简化

- 教学版只实现一个本地文件系统环境，不实现多个 environment bindings。
- 教学版使用 Python `Path` 直接读取文件，不抽象 `ExecutorFileSystem`、远程执行环境或
  `PathUri`。
- 教学版固定从构造参数接收 root markers、fallback filenames 和字节预算，不从分层 config
  自动解析它们。
- 教学版没有专门排除 project config 对 root markers 的覆盖，因为它不从 config stack 读取
  markers。
- 教学版保留 `.git` 作为默认 marker，但不实现无效 marker 配置 warning 和默认回退。
- 教学版只处理读取期间文件消失；其他 I/O 错误直接由 Python 异常传播。
- 教学版使用同步文件 I/O，不实现异步加载。
- 教学版保留 user/project 分隔、source paths、cwd wrapper 和累计字节预算，但不实现完整
  provenance 类型。
- 教学版不追加 `child_agents_md` 内部 guidance。
- 教学版不把渲染结果真正送进模型请求；只展示可注入的 contextual user fragment。s11 将统一
  组装上下文片段。

## 未确认与不写入正文的内容

- 不把当前候选文件全集、默认预算或多环境文本格式描述为永久稳定公共协议。
- 不声称 `AGENTS.override.md` 会与同目录 `AGENTS.md` 合并；当前行为是优先选择一个。
- 不声称更深层文档会在运行时结构化覆盖父级键；当前模型可见行为是按顺序拼接文本，并由层级提示
  解释作用域与优先级。
- 不声称 AGENTS.md 内容属于 system 或 developer role；当前实现将其渲染为 user contextual
  fragment。
- 不声称项目文档读取受工具 sandbox 限制；当前 loader 通过 environment filesystem 在 session
  初始化阶段读取。
- 不声称多环境已有跨环境总预算。
- 不声称 Skills、Apps、Hooks 或其他配置会自动进入 AGENTS.md 文本。
