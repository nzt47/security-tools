# TASK-S11-06 验收报告（补录 · 日期定时炸弹扫清与守卫）

> **补录说明（2026-09-14，主线）**：S11-06 交付时**未落结案/验收报告**，
> 本报告由**主线按实测补录**，内容严格限于**可复现证据**；主线未复验的部分显式标注，
> 不代替原会话的自我验收，也不改写它的任何代码或结论。

---

## 一、任务与交付

**任务**：修「日期定时炸弹」用例并**扫清同类面** + 补防复发守卫。
来源：[`START-S11-06_修日期定时炸弹测试.md`](START-S11-06_修日期定时炸弹测试.md)。

**前置（已由主线完成）**：`64665851` 修掉**已实测失败**的那一条
（`test_s801_warm_archive_shards_stay_visible`：把"今天"硬编码成 2026-09-13 ⇒
09-13 通过、09-14 起必然失败）+ 加参数化守卫 `test_warm_archive_is_date_independent`。

**S11-06 主体交付**：`d91978ef`（合并 `b6ef5e2e`）

| 文件 | 改动 | 说明 |
|---|---|---|
| `tests/_date_shift_plugin.py` | +168（新增） | **行为级检测工具**：把"今天"整体平移 N 天（`CP_DATE_SHIFT_DAYS=N`），用于**跑出**日期敏感用例，而非静态猜 |
| `tests/unit/test_date_bomb_guard.py` | +391（新增） | 防复发守卫 |
| `tests/unit/test_decision_log_rotation.py` | +30/−? | 同类面之一 |
| `tests/unit/test_knowledge_cli.py` | +7/−? | 同类面之一 |
| `tests/unit/test_routes_knowledge.py` | +10/−? | 同类面之一 |
| `tests/unit/test_s5_03_cost_brake.py` | +24/−? | 同类面之一 |

## 二、主线独立验收（实测证据）

| # | 验收点 | 结果 | 证据 |
|---|---|---|---|
| 1 | 合入且工作区干净 | ✅ | `s1106/main` 领先 master = 0；worktree 脏项 = 0 |
| 2 | **它改的 4 个文件在「未来 400 天」下稳定** | ✅ **240 passed**（18.64s） | `CP_DATE_SHIFT_DAYS=400 pytest tests/unit/test_decision_log_rotation.py tests/unit/test_knowledge_cli.py tests/unit/test_routes_knowledge.py tests/unit/test_s5_03_cost_brake.py -p tests._date_shift_plugin` |
| 3 | 对照：**过去方向 −400 天** | ✅ **170 passed**（10.21s） | 同插件，`CP_DATE_SHIFT_DAYS=-400`（只取其中两文件） |
| 4 | 它新增的守卫套件 | ✅ **5 passed** | `pytest tests/unit/test_date_bomb_guard.py -q` |
| 5 | 前置修复未被破坏 | ✅ **63 passed** | `pytest tests/unit/test_decision_log_rotation.py -q`（含主线加的 5 例参数化守卫） |
| 6 | 零缺口守卫 | ✅ **27 passed** | `pytest tests/unit/test_settings_registry.py -q` |
| 7 | kwarg 扫描（tests） | ✅ **0 处 HIGH** | `python scripts/scan_kwarg_conflicts.py --path tests --min-risk HIGH` |
| 8 | import-linter | ✅ **2 kept / 0 broken** | `lint-imports --config .importlinter` |

**评价**：它用**行为级**手段（平移"今天"去跑）替代了提示词里建议的"静态扫硬编码日期"，
**这是更好的选择** —— 静态扫会误伤"把日期当固定时间戳数据"的用例，而平移法直接暴露真实敏感点。

## 三、未由主线复验 / 未留档的部分（如实标注）

| # | 事项 | 状态 |
|---|---|---|
| 1 | **逐条判定量化清单**（提示词 §三.3 要求："扫描 N 个 → 日期敏感 M 个 → 修 M 个 → 其余为何不敏感"） | ❌ **未留档**（无报告）。⇒ 主线**无法核对**它当初判定了哪些文件、为何放过其余 |
| 2 | 其余含硬编码 `2026-09-*T` 的测试文件是否都已被判定 | ⚠️ **未验证**。⚠️ 注意：不能用"硬编码日期出现次数"作前后对比 —— 新守卫自身夹具就含绝对日期，该指标改后反而从 16 文件涨到 17 文件，**不是有效度量**（主线实测踩过，特此记下以免误读） |
| 3 | `tests/integration/**` 是否存在同类炸弹 | ⚠️ **未验证**（S11-06 改动仅落在 `tests/unit/**`） |
| 4 | 插件在**全量** `tests/unit` 下的平移跑（最能反映整体） | ⏳ **未做**（全量单跑约 58 分钟；如需彻底收口，建议以 `CP_DATE_SHIFT_DAYS=400` 跑一次全量作为**最终**判据） |

## 四、遗留与建议

1. **建议补做一次「平移全量」**：`CP_DATE_SHIFT_DAYS=400 pytest tests/unit -q -p tests._date_shift_plugin`
   —— 这是把"日期敏感性"从"改了 4 个文件"升级为"整库有结论"的唯一硬判据；
   ⏳ 未做（成本约 1 小时），列入收口清单。
2. **建议把平移跑纳入 CI 矩阵**（可先在夜间任务），使该类炸弹在合入前而非"到某一天"才暴露。
3. 遗留 #1（量化清单）不阻塞交付：**行为级判据（本报告 §二 #2/#3）比清单更强**，但它挡住了
   "其余文件为何不敏感"的可追溯性 —— 若后续有人要复核该判断，需重新跑平移。

---

**SHA 记录**：S11-06 交付 `d91978ef`（合并 `b6ef5e2e`）｜前置修复 `64665851`｜
补录时 master = `b44b1c92`（补录提交见后续）｜双远端同点。

---

## 五、⚠️ 补录后的**重要更正**（2026-09-14 当晚，主线）

**本报告 §二 隐含的"扫清已完成"结论不成立 —— 残留量远比 S11-06 修掉的多。**

**证据（平移**全量**，非抽样）**：
```
CP_DATE_SHIFT_DAYS=400 python -m pytest tests/unit -q -p tests._date_shift_plugin
  → 54 failed / 18079 passed / 308 skipped（3414.66s / 56m54s）
```

**已证实是"真炸弹"而非平移副作用**（分类实验）：
```
tests/unit/test_snapshot_comprehensive.py
  · 不平移（基线） → 92 passed
  · 平移 +1 天     → 25 failed / 67 passed     ← 只差一天就崩，是真炸弹
  · 平移 +400 天   → 同样失败（复现）
```
⇒ 单这一个文件就有 **25 个**日期敏感用例，S11-06 **完全没有触及**它。

**因此 §三 的"未留档"一条要升级**：不只是"清单没写下来"，而是
**扫清本身未完成**（它只改了 `test_decision_log_rotation.py` / `test_knowledge_cli.py` /
`test_routes_knowledge.py` / `test_s5_03_cost_brake.py` 四个文件）。

**工具是好的，但用得不全**：`tests/_date_shift_plugin.py` 确实能跑出真实敏感点
（本轮即靠它查实），但 S11-06 **没有把它跑遍整库**。插件另有
`python tests/_date_shift_plugin.py --candidates` 输出**静态候选文件清单**
（含 `test_task_scheduler*.py`、`test_utc_cost.py`、`test_retention_*.py`、
`test_replay_*`、`test_precipitate_*`、`test_singleton_performance.py` 等，与时间域高度相关）。

**主线自身的失误（一并记录，避免后人误读）**：
1. 首次跑平移全量时用 `Select-Object -Last 25` **丢了完整 FAILED 清单**，
   只拿到总数与尾部 5 个文件名 ⇒ **修复前必须重跑一次并把输出落文件**；
2. 报告初稿把"S11-06 改的 4 个文件在 ±400 天下全过"当成足够强的判据就写了"扫清"，
   **未自行跑整库平移** ⇒ 教训：**单点判据不能替代全局判据**。

**结论**：本任务**未达交付标准**，残留 ≈54 个日期敏感用例（确切分布待完整清单确认）。
应立 **S11-07** 修复；**在 S11-07 完成前不得宣告"日期炸弹已清"**。

