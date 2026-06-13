# s05: File Tools & Apply Patch — 让修改可表达、可检查

```mermaid
flowchart LR
    R["Read"] --> P["Structured Patch"]
    P --> V["Validate Target"]
    V --> A["Apply"]
    A --> D["Diff"]
```

> 状态：待编写。目标是用结构化文件工具替代脆弱的 shell 文本拼接。

