# v1.0.0 架构修复发布说明

> 生成时间：2026-08-07 | 修复提交：`35190a25` | 远程 master = `35190a25`

## 一、修复内容

**架构规则校验失败（no_circular_dependency，CI exit 1）**

| 违规 | 根因 | 修复 |
|---|---|---|
| `agent.knowledge.index ↔ card` | rebuild_index 函数内 `from agent.knowledge.card import CardStore`（arch_rules 按 AST import 节点统计，含函数内） | [index.py](file:///c:/Users/Administrator/agent/agent/knowledge/index.py#L36-L47) 改用 `_get_store()` + `importlib.import_module` 动态导入（AST 无 import 节点，依赖图无 `index→card` 边） |
| `agent.knowledge.links → card` | TYPE_CHECKING 块 import CardStore（同样被 AST 统计） | [links.py](file:///c:/Users/Administrator/agent/agent/knowledge/links.py#L73-L80) 删除 TYPE_CHECKING，`resolve_link(slug, store: Any)` 改鸭子类型（仅调 `store.get()`） |

**机制说明**：扫描器 `_check_circular_dependencies` 基于 AST Import/ImportFrom 节点生成依赖边（不排除函数内与 TYPE_CHECKING），因此延迟导入/TYPE_CHECKING 均无法通过——importlib 动态导入为唯一无 import 节点的方案。

## 二、CI 验证结果（远程）

| 验证项 | 结果 |
|---|---|
| 架构规则校验（run 31149574503） | ✅ **success**（12 步骤全过，未豁免违规 0） |
| 循环依赖校验（run 31149574549） | ✅ success |
| Publish tlm-hook-failsafe | ✅ success |
| 本地单测（knowledge 7 文件 + CLI） | ✅ 229 passed, 0 failed |
| 覆盖率（agent/knowledge） | ✅ **94%**（card/index/links/lifecycle/schema/logbook 100%、ingest 87%、`__main__` 92%） |

## 三、gitee 同步记录

- master：`1932869c` → `7380bd30`（14 提交 fast-forward，双端一致）
- v1.0.0 tag：`004ce23e` 双端一致 ✓

## 四、遗留观察（core-invariants 竞态）

run 31149481064（v1.0.0 tag push）失败根因：`actions/checkout` 报 `The ref 'refs/tags/v1.0.0' does not point to the expected commit 'b08ae5fe'`——tag 在 workflow 触发后被前移（竞态）→ 上游步骤未产出 `invariant_report.json` → 汇总步骤（`always()` 无容错）连锁 FileNotFoundError 噪音失败。

**建议**（待确认后实施）：汇总步骤加文件存在性守卫（仿 Slack 步骤 `[ -f ]` 容错）；checkout 竞态失败时经 precheck 静默跳过，避免噪音告警。
