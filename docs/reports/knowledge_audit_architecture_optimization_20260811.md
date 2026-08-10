# 知识审计系统架构优化建议（内存占用 + 日志噪音）

- 生成时间：2026-08-11
- 依据基线：`docs/reports/knowledge_audit_perf_scale_20260811.md`（1200 卡：检测 314.27ms / 总耗时 314.28ms，CSafeLoader）
- 约束（不易）：只读巡检契约不变、五类检测语义不变、健康分算法不变、降级铁律不变

## 一、现状基线

| 维度 | 现状 | 实测证据 |
|---|---|---|
| 耗时 | 1200 卡总耗时 ≈ 314ms（CSafeLoader 后） | 性能报告 §一 |
| 并发 | ThreadPoolExecutor 读盘 1.09x，无显著提升 | 性能报告 §三（GIL 瓶颈） |
| 缓存 | `_list_cache` 全量 Card 对象驻留内存 | diag 脚本：冷读 1149ms / 热读 2.6ms |
| 日志 | 命中类日志走 `logger.warning`，明细逐条打印 | perf 脚本需 `logging.disable()` 全禁才压得住 |

检测阶段（卡片加载 + YAML 解析）占审计耗时 99%，是本轮优化主战场；但**耗时已压到 300ms 级，继续压耗时的边际收益低于内存与日志治理**。本报告聚焦后两者。

## 二、内存占用分析

### 2.1 现状：全量加载 + 多份副本

1. **全量卡片对象驻留**：`CardStore.list()`（含 `_list_from_disk`）每次对全库每张卡做一次文件读 + YAML 解析，生成全新 `Card` 对象列表。`lint_all` 拿到的 `cards` 是整个知识库的内存副本。
2. **缓存与审计双份驻留**：若开启 `list(use_cache=True)`，`_list_cache` 再保留一份全量列表；`lint_all` 的 `list(parallel=...)` 默认走磁盘，二者并存时同一批卡在内存中最多存在 2 份。
3. **检测过程再建多个集合**：`find_broken_links` 构建 `known: dict[slug, Card]`、`disk_slugs: set`、`resolve_cache: dict`；`find_orphans` 构建 `incoming: set`。均为与卡片数线性相关的额外内存。
4. **报告导出二次读盘**：`report_to_json(report, store=...)` 为附加 `cards[]` 明细，再次调用 `store.list()` 全量读盘 + 解析（workflow `run_audit` 第 202 行 `len(store.list())` 同样多一次全量读盘）。
5. **正文字段浪费**：检测五类问题只需要 `slug / status / type / date / links / contradictions` 六个字段，但 `Card` 对象携带 `insight`、正文 `content` 等大字段随全量列表一起驻留。

### 2.2 优化建议（按收益/成本排序）

**P0-1：检测卡片加载瘦身为「检测视图」**
- 在 `_list_from_disk` 旁提供 `list_light()`：解析 frontmatter 后只保留检测六字段（slug/status/type/date/links/contradictions），丢弃 `insight` 与正文。
- 收益：1200 卡场景单卡内存体积显著下降（正文常为 frontmatter 的 5~10 倍）；五类检测完全只依赖这六字段，语义不变（不易）。
- 成本：新增一个方法 + 复用 `_md_to_card` 解析路径；`_md_to_card` 可加 `light=True` 参数复用。

**P0-2：报告导出复用已加载的卡片列表**
- `report_to_json` 增加可选 `cards` 参数，`workflow.run_audit` 把 `lint_all` 已加载的 `cards` 直接传入，消除二次全量读盘。
- 收益：每次 audit 少一次全量读盘 + 解析（实测冷读约 1.1s，即省掉接近一倍的加载成本）。
- 成本：接口加一个可选参数，向后兼容（默认 None 时保持原行为）。

**P1-3：审计场景不启用内存缓存，改用「指纹 + 序列化」缓存**
- 审计是只读长任务，`_list_cache`（全量 Card 对象）在审计期间只会产生双份驻留收益。建议：
  - 审计入口统一走 `list(use_cache=False)`（已是默认），明确文档化；
  - 若需跨次提速，用磁盘序列化缓存（如 `pickle` + 指纹校验）替代内存缓存，热读成本与内存缓存同量级但不占进程常驻内存。
- 注意（变易）：指纹机制（`_fingerprint` + `_list_fingerprint`）已验证「只慢不坏」，序列化缓存可复用同一指纹语义，不改写安全边界。

**P2-4：大库分批检测（可选，≥5000 卡时再启用）**
- 1200 卡下全内存计算无压力（检测 <1ms 除加载外），不必现在做。留作规模阈值触发的演进项即可，拒绝过度抽象（简易）。

## 三、日志噪音分析

### 3.1 现状：逐条明细刷屏 + 级别错位

1. **命中类日志走 `warning`，且逐条打印**：
   - `find_broken_links`（links.py）对**每条断链**发一条 `logger.warning`（`断链触发于文件=...`）；
   - `lint_all` 五类检测命中后，又各发一条带**全量明细**的 `warning`（孤儿/断链/漂移/过期/矛盾五处）。
   - 结果：一次 1200 卡的审计，断链命中时日志可达数千行；`logging.disable(logging.INFO)` 拦不住（warning 高于 INFO），perf 脚本被迫 `logging.disable()` 全禁。
2. **逐链接 info**：`resolve_link` 每次解析一条 info（命中/断链各一条）、`parse_links` 每次解析一条 info（含完整目标列表）。1200 卡 × 每卡多链接 = 数千条 info。
3. **CI 噪音**：`knowledge-audit-smoke` job 中任何 warning 都会打印；问题库上审计的 stdout/stderr 被明细淹没，定位真实失败困难。
4. **重复打印**：同一问题在 `find_broken_links`（逐条 warning）与 `lint_all`（批量 warning）各打印一次，内容重叠。

### 3.2 优化建议

**P0-1：逐条明细降级 + 批量汇总保留**
- `find_broken_links` 的逐条 `warning` 降为 `logger.debug`；命中数量与汇总由 `lint_all` 的批量 `warning` 统一输出（仅一条）。
- 收益：断链场景日志从「每条一行」降为「一行汇总」，数千行 → 数行；warning 级别保留问题可见性（CI 仍能感知但不被淹没）。
- 语义不变：日志内容不减（明细进 debug，需要时 `--verbose`/DEBUG 开启）。

**P0-2：`resolve_link` / `parse_links` 的逐条 info 降为 debug**
- 这两类日志是「逐链接级」的追踪信息，正常巡检下无排查价值；保留为 debug，由 `--verbose` 门控。
- 收益：正常运行日志量直接下降 2~3 个数量级（最直接的噪音削减）。

**P1-3：日志分级门控进 CLI**
- `audit` 子命令增加 `--quiet`（默认）与 `--verbose` 两档：默认只输出批量汇总（warning 以上）+ 结论行；`--verbose` 恢复全部 info/debug 明细。
- CI job 保持默认档，问题库上输出保持可读。

**P1-4：聚合日志模式（结构性去重）**
- 将「逐条明细 + 批量汇总」合并为单一结构化日志：一条记录含 `level=WARNING, kind=broken_links, count=N, sample=[前 10 条]`，`sample` 截断 + `truncated=N-10`。
- 收益：信息量不丢（明细可取样），行数恒定为 O(问题类别数) 而非 O(问题条数)。

**P2-5：CI 按结果分级输出**
- 审计 `ok=true` 时整段日志降为 info（CI 中近乎静默）；`ok=false` 时才输出问题汇总。与「检测 → 计算 → 报告」三步结论行配合，CI 日志从「数千行」收敛到「十行内」。

## 四、综合实施优先级

| 优先级 | 项 | 类型 | 预期收益 | 风险 |
|---|---|---|---|---|
| P0 | 检测视图 `list_light()` | 内存 | 单卡驻留内存降 5~10 倍 | 低（新增方法，不动既有路径） |
| P0 | 断链逐条 warning → debug | 日志 | 断链日志数千行 → 数行 | 低（明细仍在 debug） |
| P0 | resolve_link/parse_links info → debug | 日志 | 总日志量降 2~3 个数量级 | 低 |
| P1 | `report_to_json` 复用 cards | 内存+耗时 | 每次审计省一次全量读盘 | 低（可选参数向后兼容） |
| P1 | CLI 日志级别门控 | 日志 | CI 输出收敛、可读 | 低 |
| P1 | 磁盘序列化缓存替代内存缓存 | 内存 | 热读提速且不占常驻内存 | 中（需复用指纹语义） |
| P2 | 聚合日志（sample + truncated） | 日志 | 行数恒定为 O(类别数) | 中（格式变更，需更新断言） |
| P2 | 大库分批检测 | 内存 | ≥5000 卡场景可控 | 低（阈值触发，暂不实施） |

## 五、回归与验收

- 约束（不易）：五类检测语义、健康分算法、CLI 退出码契约均不可变；日志改动仅影响输出级别与格式，不改数据。
- 验收方式：复用现有边界测试——单元 11 项（`tests/unit/test_knowledge_audit_edge.py`）+ CI 集成 4 项（`tests/integration/test_knowledge_audit_ci_edge.py`）须全部通过；当前基线 623 passed / 1 skipped。
- 建议新增断言：`--quiet` 下问题库审计的 stdout/stderr 行数上限（如 < 50 行），固化「日志噪音」治理成效。

## 六、结论

耗时已达 300ms 级（CSafeLoader），进一步压耗时的边际收益有限；本轮建议的优化重心转向**内存**（检测视图瘦身 + 消除二次读盘）与**日志噪音**（逐条明细降级 + 批量汇总 + CLI 门控）。所有优化均保持「只读巡检、语义不变」的不易约束，按 P0 → P2 顺序小步演进、每步可回滚。
