# s16: Compaction & Token Budget — 压缩不能破坏关系

```mermaid
flowchart LR
    H["Long History"] --> B["Budget"]
    B --> C["Compact"]
    C --> V["Validate Item Relationships"]
    V --> K["Keep Working"]
```

> 状态：待编写。目标是在保留关键事件关系的前提下控制上下文大小。

