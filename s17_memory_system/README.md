# s17: Memory — 跨线程保留真正稳定的信息

```mermaid
flowchart LR
    T["Eligible Threads"] --> E["Extract"]
    E --> C["Consolidate"]
    C --> M["Durable Memory"]
    M --> S["Select for Future Thread"]
```

> 状态：待编写。目标是区分线程历史、摘要和跨线程记忆。

