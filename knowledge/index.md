# 知识库全局索引
> 此文件由 AI 自动维护，请勿手动修改。更新时间: 2026-08-15

## 概念 (Concepts)

## 实体 (Entities)

## 洞察 (Insights)
- [[git-维护复盘-task06-gc优化]] `current` — 并行会话活跃环境下执行 git gc 的安全策略：空闲窗口轮询 + 默认参数（禁 --prune=now，reflog 90 天保护）可零影响并行会话；实测 loose 3.09 MiB(673 个)→0、packs 3→2，策略已沉淀为 SOP §三-E。
- [[task-06-新颖性感知学习管线验证结论]] `current` — TASK-06 感知侧新颖性学习管线三项运行时验证全通过：观察模式（enabled=false）只记录不沉淀；行为漂移跨会话对比（drift_score 0.52 >= 0.3）正确触发事件；高置信事件仅产 DRAFT 草稿绝不注册技能。