# Release Notes · v1.2.0（知识模块入链索引优化与容量扩展性评估）

> 分支：`release/v1.2.0`（与 `develop` 同步） ｜ 日期：2026-08-08 ｜ 状态：已推送 origin
> 范围：本次分支同步新增的「知识模块入链索引性能优化与容量评估」系列变更
> 前置：v1.2.0 基线（config_manager 测试强化 + TLM Step 2，见 `docs/releases/v1.2.0.md`）

---

## 1. 变更总览

| 类别 | 内容 | 文件 |
|---|---|---|
| 性能优化 | 入链索引（index_links.md）+ delete_many + index.md append | `agent/knowledge/links_index.py`、`card.py`、`index.py` |
| 容错降级 | 读路径回退全扫 / 写路径失败不阻断 | 同上 + 7 项降级单测 |
| 测试 | 一致性先行（TDD）+ 全分支覆盖 | `tests/unit/test_links_index.py`（11 项）、`test_knowledge_card.py`（56 项） |
| 文档 | 对比报告 / 容量预估系列（11 份）/ Wiki / PDF / 复盘 / 邮件 / Jira / 监控方案 / 根目录 README | `docs/`、`docs/wiki/`、`docs/archive/`、`ARCHITECTURE_EVOLUTION_README.md` |

## 2. 性能指标（实测）

| 优化项 | 变更前 | 变更后 | 提升 |
|---|---|---|---|
| 入链判定（`_has_incoming_links`） | 全库扫描 2249.3 ms | 查表 1.1 ms | **≈2094x** |
| 批量删除（`delete_many`，3000 卡删 50） | 逐次 delete 2342.9 ms | 复用索引 134.1 ms | **17.5x** |
| index.md 高频追加 | 字典序扫描定位 O(N) | append 模式 O(1) | 定位降阶 |

## 3. 测试状态

- 回归基线：**70+ passed**（card 56 + links_index 11 + observability 8 等），一致性不变量未破坏；
- 降级单测：7 项，其中 3 项关键分支 `--log-cli` 实证触发（告警文案逐条核对）；
- links_index 全分支覆盖：一致性 5、幂等 3、语义边界 1、读容错 2。

## 4. 兼容性

- **无 API 破坏**：`CardStore` 构造参数新增 `links_index_path`（默认 `index_links.md`，可选）；`update_index_delta` 新增 `append` 关键字参数（默认 `False`，行为不变）；
- **行为保持**：`_has_incoming_links` 索引缺失/损坏时回退全库扫描，判定结果与原全扫一致；
- **配置**：新增容量监控阈值（`.env`：`KNOWLEDGE_LINKS_REFS_ALERT_MB` / `KNOWLEDGE_CARD_FILE_ALERT_COUNT`），缺省不告警，零副作用。

## 5. 新增资产清单

| 类型 | 明细 |
|---|---|
| 代码 | `links_index.py`（新增）、`card.py`（CRUD 挂接/delete_many/降级）、`index.py`（append） |
| 容量报告 | 50万 / 100万 / 200万 / 500万 / 1000万 / 2000万 / 5000万 / 1亿 / 10亿 / 100亿 / 1万亿 / 1京 / 1垓（`docs/zh/`） |
| 团队 Wiki | `docs/wiki/knowledge_optimization_phase2_evolution_wiki.md` |
| 归档 | `docs/archive/知识模块性能优化_架构演进总结.pdf` |
| 专题 README | `ARCHITECTURE_EVOLUTION_README.md`（根目录） |
| 规划 | `docs/zh/知识模块性能优化_Jira任务清单.md`、`_监控埋点实现方案草案.md` |

## 6. 后续行动计划（不在本版本范围）

1. KN-101 流式扫描落地（iter_cards）——P1
2. KN-102 容量监控埋点落地——P1
3. KN-103 分片架构前置设计——P2
4. KN-104 存储层二级目录/对象存储评估——P3
5. KN-105 Confluence 推送（缺凭据）——P3

## 7. 回滚方案

- 代码回滚：`git revert` 对应提交（`links_index.py` 删除后 `_has_incoming_links` 自动回退全扫，行为不退化）；
- 文档资产回滚：删除 `docs/zh/` 容量报告与 `docs/wiki/` 总结文档即可，无运行时依赖；
- 分支回滚：`git reset --hard` 至 v1.2.0 基线（`docs/releases/v1.2.0.md` 前），再重放。

---

*详细数据见 [第二批对比报告](file:///c:/Users/Administrator/agent/docs/zh/知识模块性能优化第二批对比报告.md) 与 [架构演进总结（Wiki）](file:///c:/Users/Administrator/agent/docs/wiki/knowledge_optimization_phase2_evolution_wiki.md)。*
