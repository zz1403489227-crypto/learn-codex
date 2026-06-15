# Source Notes

## 研究快照

- `openai/codex`: `f297b9f07de10c7d8b9ed284b674d06cc5ff7723`
- 获取日期：2026-06-13
- 本章研究日期：2026-06-15

## 实际阅读

### 配置层与合并

- `codex-rs/config/src/loader/README.md`
- `codex-rs/config/src/loader/mod.rs`
- `codex-rs/config/src/state.rs`
- `codex-rs/config/src/merge.rs`
- `codex-rs/config/src/config_layer_source.rs`
- `codex-rs/config/src/config_toml.rs`
- `codex-rs/config/src/constraint.rs`
- `codex-rs/config/src/config_requirements.rs`

### 权限解析与锁

- `codex-rs/core/src/config/permissions.rs`
- `codex-rs/core/src/config/resolved_permission_profile.rs`
- `codex-rs/core/src/config_lock.rs`
- `codex-rs/core/src/session/config_lock.rs`

### 测试

- `codex-rs/config/src/state_tests.rs`
- `codex-rs/config/src/merge_tests.rs`
- `codex-rs/config/src/loader/tests.rs`
- `codex-rs/core/src/config/config_loader_tests.rs`
- `codex-rs/core/src/config/config_tests.rs`
- `codex-rs/core/src/config/permissions_tests.rs`

## 从源码确认的事实

- `codex-config` loader 是加载和描述配置层的规范入口，产出 effective merged config、字段来源
  metadata 和每层稳定版本。
- `ConfigLayerStack` 内部按低优先级到高优先级保存层，折叠时后面的层覆盖前面的层。
- 当前 loader 文档记录的普通配置层优先级包含 system、enterprise managed、user、user profile、
  project、session flags，以及仍处于兼容期的 legacy managed config。
- 合并规则是递归 TOML table merge；当任一侧不是 table 时，overlay 整体替换 base。
- `effective_config()` 只合并启用的普通配置层。带 `disabled_reason` 的层仍可向 UI 展示，但不参与
  effective config 和 origins。
- `origins()` 记录合并视图中字段来自哪个配置层；requirements 来源单独跟踪，不包含在普通
  config origins 中。
- project config 从 repository 内容加载。未知或 untrusted 项目中的 project layers 会被加载为
  disabled layers，不参与 effective config。
- trusted project config 仍不能设置一组高风险顶层键。当前 denylist 包含 provider、API base URL、
  notify、profile/profiles、otel 等配置。
- project layers 从 project root 到 cwd 依次加载，越接近 cwd 的层优先级越高。
- 无效 project config 在 trusted 项目中会报错；在 unknown 或 untrusted 项目中可被忽略并保留
  disabled layer 表示。
- trust decision 与默认 sandbox/approval 行为是不同问题。当前快照中，显式 trusted 或
  untrusted 项目默认都可选择 workspace permission profile；untrusted 项目默认使用
  `UnlessTrusted` approval policy。未知项目默认更保守。
- `default_permissions` 可选择 built-in 或命名 permission profile。命名 profile 必须经过配置
  解析与编译，不能只凭 profile id 构造运行权限。
- requirements 与普通配置层分开加载和组合。requirements 可以定义允许的 permission profiles
  和 managed default，并限制普通配置或运行时修改可选择的值。
- 当配置选择的 permission profile 被 requirements 禁止时，相关测试确认运行时会回退到
  requirements 允许的默认 profile，并产生 warning。
- `Constrained<T>` 保存当前值与 validator；`can_set` 可探测候选值，`set` 在违反约束时拒绝修改
  并保留旧值。
- `PermissionProfileState` 把 concrete profile、active profile identity 与 profile workspace
  roots 作为受约束的 resolved state 管理。
- config lock 保存 resolved effective session config，用于导出和 replay validation。验证会检查
  lock version、Codex version 和 resolved config 差异。
- session config lock 不只是保存原始输入；它会物化若干经过默认、feature 或 session setup
  解析后的值，以比较实际运行行为。

## 教学实现的简化

- 教学版只有 system、user、project、session 四种普通配置源。
- 教学版使用 Python dict 模拟 TOML table，不实现 TOML 解析、key aliases、相对路径解析、profile
  文件、cloud bundle、MDM 或 legacy managed config。
- 教学版要求调用方按低到高优先级传入 layers，不实现真实 loader 的目录遍历和自动插入。
- 教学版只记录 leaf origins，不生成 layer version 或完整 UI metadata。
- 教学版 project trust 是一个传入的 `TrustLevel`，不实现 git root、canonical path、worktree 或
  用户 config 中的 projects trust map 查找。
- 教学版 project denylist 只保留少量代表性键。
- 教学版 requirements 只约束 permission profile id，并要求提供允许集合中的 fallback default。
- 教学版只支持 `:read-only`、`:workspace` 和 `:danger-full-access`，不实现命名 profile、
  extends、workspace roots 或 profile 编译。
- 教学版 config lock 是 resolved values 的内存快照，只演示 drift detection；不导出 TOML、不检查
  Codex version，也不实现 replay。
- 教学版将 resolved permission profile 直接接入 s07 的进程内 workspace sandbox。

## 未确认与不写入正文的内容

- 不把当前完整层列表和 legacy managed config 的相对位置描述为永远不变的公共协议。
- 不声称 trust level 直接等同于 sandbox mode；当前源码将 project config gating、approval 默认值
  和 permission profile 默认值分别处理。
- 不声称 trusted project 可以设置任意配置。
- 不声称 untrusted project 一定只能 read-only；当前快照在非 Windows 默认可使用 workspace
  profile，但采用更严格的 approval 默认值。
- 不声称 requirements 是一个最高优先级普通 config overlay；它们是单独组合和执行的约束。
- 不声称教学版 config lock 等同于真实 Codex 的 lockfile 格式或 replay contract。
