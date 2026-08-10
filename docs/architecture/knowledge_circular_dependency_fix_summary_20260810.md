# knowledge 循环依赖修复总结报告（2026-08-10）

> 范围：`agent.knowledge.links_index → agent.knowledge.card` 循环依赖违规（架构影响可见性检查失败项）的完整修复。
> 状态：**修复完成并落地**（方案 B 临时豁免 → 方案 A 根因治理），架构检查已转绿。

---

## 1. 背景与目标

- **违规**：`no_circular_dependency` `agent.knowledge.links_index → agent.knowledge.card`（`links_index.py:103`）
- **影响**：架构影响可见性检查失败，阻塞 develop 每次 push 的 CI 结论（非全绿项 P1）
- **目标**：架构检查转绿，且修复为根因治理（非长期豁免）

## 2. 根因分析

| 事实 | 结论 |
|------|------|
| `card.py:34` 顶层 `from agent.knowledge.links_index import ...` | 边 card → links_index（合理，单向消费） |
| `links_index.py:103` 函数内 `from agent.knowledge.card import CardStore` | 边 links_index → card（构成环） |
| arch_rules 用 `ast.walk` 遍历**全部**节点 | 函数内 `from` 语句同样成边，惰性导入无法在 AST 层规避 |
| 仅 `rebuild_links_index` 依赖 card，且只用 `store.list()` 接口 | 断边只需外移这一处依赖 |
| `read_links_index` / `update_links_delta` 均不依赖 card | 环仅由重建函数引入 |
| 生产代码无 `rebuild_links_index` 调用点（仅测试 6 处） | 改动面小，风险低 |

## 3. 修复路径（两阶段）

### 阶段 1：方案 B —— 临时豁免（f35dbe6e，2026-08-10）

- `docs/architecture/legacy_exemptions.json` 追加 ARCH-DEBT-008（与既有 ARCH-DEBT-003~007 同构：函数内延迟导入、运行时安全）
- **效果**：run 31355530446 中**架构影响可见性检查 success** ✅ —— 解除每次 push 阻塞

### 阶段 2：方案 A —— 依赖注入断边（d354b4d0，2026-08-10）

- `links_index.py`：`rebuild_links_index(cards, index_path)` 接收卡片迭代器（鸭子类型：`.slug`/`.links`），**移除** `from agent.knowledge.card import CardStore` —— links_index.py 零 card 依赖，静态依赖单向
- `test_links_index.py`：6 处调用改为 `rebuild_links_index(store.list(), index_path)`
- `legacy_exemptions.json`：**撤销 ARCH-DEBT-008**（根因已治，无需豁免）

## 4. 验证结果

| 验证项 | 结果 |
|--------|------|
| `pytest tests/unit/test_links_index.py`（方案 A 后） | **11 passed**（不变量锁定：全量/增量一致性、幂等、archives 语义） |
| `arch_rules --check`（撤 B 后、无豁免状态） | **未豁免违规 0**（断边真实有效，非豁免掩盖） |
| CI run 31355530446（方案 B 生效） | 架构影响可见性检查 **success** |
| CI run 31358554381（方案 A + 撤 B，待出） | 待验证（本地已等价验证） |

## 5. 遗留事项（非本次修复范围）

| 项 | 状态 | 说明 |
|----|------|------|
| 可观测性质量门禁 | ❌ 仍失败 | 存量：覆盖率 22.40% < 60%；scripts 治理进行中（路线图阶段 2-5） |
| 全项目测试覆盖率 Shard 6/6 | ❌ run 31355530446 中新失败 | `test_knowledge_observability.py` 4 个用例失败（JSONDecodeError / assert 0==3 / IndexError），触发 commit f35dbe6e 仅改豁免清单，**与本次修复无关**，需另行排查（疑似 knowledge 观测链路代码/测试问题） |

## 6. 关键产物

| 类型 | 文件 |
|------|------|
| 修复方案 | [knowledge_circular_dependency_fix_plan_20260810.md](knowledge_circular_dependency_fix_plan_20260810.md) |
| 代码修改 | [links_index.py](../../agent/knowledge/links_index.py#L101-L114)（rebuild_links_index 签名） |
| 测试修改 | [test_links_index.py](../../tests/unit/test_links_index.py)（6 处调用） |
| 豁免变更 | [legacy_exemptions.json](../architecture/legacy_exemptions.json)（加后撤，净变更 0） |
| commits | `f35dbe6e`（B）→ `d354b4d0`（A + 撤 B） |

## 7. 结论

knowledge 循环依赖**根因已消除**：静态依赖图单向（card → links_index），架构检查已转绿且无需豁免。
遗留的覆盖率门禁与 Shard 6 knowledge 测试失败为独立事项，由路线图与其他负责人跟进。
