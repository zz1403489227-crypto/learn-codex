# s20: Worktree & Git Isolation — 并行工作不共享修改目录

```mermaid
flowchart LR
    T["Task"] --> W["Git Worktree"]
    W --> A["Agent Thread"]
    A --> C["Commit / Diff"]
    C --> R["Review or Merge"]
```

> 状态：待编写。目标是把任务、线程和独立 Git 工作目录绑定起来。

