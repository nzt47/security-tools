# knowledge 循环依赖修复方案（阶段 1 · P1）（2026-08-10）

> 目标：消除 `agent.knowledge.links_index → agent.knowledge.card` 循环依赖违规，使架构影响可见性检查转绿。
> 关联：[develop_green_roadmap_20260810.md](../observability/develop_green_roadmap_20260810.md) 阶段 1

---

## 1. 违规事实

- 违规边：`no_circular_dependency` `agent.knowledge.links_index → agent.knowledge.card`（`links_index.py:103`，1 个未豁免）
- 环结构：`card.py:34` 顶层 `from agent.knowledge.links_index import read_links_index, update_links_delta`（边 card→links_index）
  ↔ `links_index.py:103` 函数内 `from agent.knowledge.card import CardStore`（边 links_index→card）
- 检测机制：arch_rules 用 `ast.walk(tree)` 遍历**全部**节点，函数内 `from` 语句同样成边 → 惰性导入无法在 AST 层规避

## 2. 依赖面分析（实测）

| 符号 | 位置 | 依赖 card？ | 说明 |
|------|------|------------|------|
| `read_links_index` | links_index.py:54 | 否 | 纯文件解析（stdlib） |
| `update_links_delta` | links_index.py:121 | 否 | 纯文件操作（read/_render/_atomic_write） |
| `rebuild_links_index` | links_index.py:101 | **是（唯一）** | 仅通过 `store.list()` 遍历卡片 |

**关键结论**：整条边仅由 `rebuild_links_index` 引入，且它只用 `CardStore(wiki_root).list()` 一个接口。**调用方仅 `tests/unit/test_links_index.py`（6 处）**，生产代码无调用点。

## 3. 方案对比

### 方案 A：依赖注入断边（根因治理，推荐中期执行）

把 `rebuild_links_index` 的 CardStore 依赖外移为参数：

```python
# links_index.py —— 现状（L101-105）
def rebuild_links_index(wiki_root, index_path) -> int:
    from agent.knowledge.card import CardStore  # ← AST 边源头
    store = CardStore(wiki_root)
    ...

# links_index.py —— 改造后（无 card 依赖）
def rebuild_links_index(cards, index_path) -> int:
    """cards: 可迭代卡片对象，每项含 .slug/.links（鸭子类型，由调用方构造）"""
    refs = {}
    for card in cards:
        for link in card.links:
            ...
```

- 调用方变更：`test_links_index.py` 6 处 `rebuild_links_index(wiki_root, index_path)` → `rebuild_links_index(CardStore(wiki_root).list(), index_path)`
- 效果：links_index.py 零 card 依赖 → 环断 → **无需豁免**
- 验证：`pytest tests/unit/test_links_index.py`（不变量由该文件锁定）+ `python -m agent.observability.arch_rules --check --root agent --exemptions docs/architecture/legacy_exemptions.json --config config.yaml`
- 改动文件：`agent/knowledge/links_index.py` + `tests/unit/test_links_index.py`（**不触碰他人重构中的 card/index/workflow**）
- 风险：低；需与 knowledge 重构负责人确认 `CardStore.list()` 契约不变

### 方案 B：豁免清单（与 ARCH-DEBT-003~007 同构，最快转绿）

`docs/architecture/legacy_exemptions.json` 追加一条（对齐既有 5 条先例——prometheus/loki/alert_notifier/error_handler 均为"函数内延迟导入、运行时安全"）：

```json
{
  "rule_id": "no_circular_dependency",
  "source": "agent.knowledge.links_index",
  "target": "agent.knowledge.card",
  "reason": "rebuild_links_index 在函数内延迟 import CardStore（links_index.py:103），运行期模块已加载完全，不会触发循环；arch_rules 静态分析检测到 from 语句。",
  "added_at": "2026-08-10",
  "owner": "knowledge-team",
  "tech_debt_ticket": "ARCH-DEBT-008",
  "mitigation": "应将 CardStore 依赖以参数注入 rebuild_links_index（见方案 A），消除静态边后撤销本豁免。"
}
```

- 效果：5 分钟转绿；豁免机制对循环依赖**双向匹配**（key 方向无关）
- 风险：豁免仅用于存量代码（本违规为存量，P0-2 优化时引入的设计）；不掩盖新增违规

### 方案 C：方法迁移（不推荐）

将 rebuild 逻辑并入 `CardStore` 方法——改动大、移动 API、测试引用面广，收益与方案 A 相同但成本更高。

## 4. 推荐执行路径

| 阶段 | 动作 | 目标 |
|------|------|------|
| 短期（立即） | **方案 B**：追加 ARCH-DEBT-008 豁免 | 架构检查转绿，解除每次 push 阻塞 |
| 中期（根因） | **方案 A**：依赖注入断边 → 本地验证 → 撤销 ARCH-DEBT-008 | 无豁免转绿，架构图诚实 |

> 两方案可叠加执行（B 先行、A 后清）；若 knowledge 重构正在改动 `CardStore.list()` 接口，先执行 B 等待重构落地，避免方案 A 与其冲突。

## 5. 验证清单

- [ ] `pytest tests/unit/test_links_index.py`（方案 A 后）全绿
- [ ] `python -m agent.observability.arch_rules --check --root agent --exemptions docs/architecture/legacy_exemptions.json --config config.yaml` → 0 未豁免违规
- [ ] 架构影响可见性检查 job 转绿（CI run）
- [ ] （方案 A 后）撤销豁免条目，确认仍 0 违规
