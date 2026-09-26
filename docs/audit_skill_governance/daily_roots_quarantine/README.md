# 审计日根封印日志 · 3 条测试污染记录的处置裁定（选项 A）

> **处置对象**：`data/audit/daily_roots.jsonl` 第 **11–13 行**
> **处置方式**：**剔除**（本文件与同目录的 `daily_roots.excised_rows.jsonl` 是该处置的完整依据）
> **授权**：**owner（用户）于 2026-09-26 明确选择「选项 A」**，并指示此后由主审计（DSH）代为决策
> **执行者**：主审计（DSH）
> **时间**：2026-09-26 09:5x（本机）

---

## 1. 为什么这是一次**正当**的剔除，而不是篡改

判断依据不是「我们认为它们是垃圾」，而是**三条可独立复算的事实**：

| # | 事实 | 证据 |
|---|---|---|
| 1 | 三条记录的**取值在物理上不可能属于 2026-09-25** | 该 UTC 日在审计链里是 `seq 72261..72700`、**440 条**（只读 SQL 复算）；三条却分别是 `leaf_count=4/2/4`、`first_seq` 全 **= 1** —— `seq 1..4` 的 `ts` 是 **2026-09-12** |
| 2 | 三条的**成因已被差分复现** | AUDIT-ROOT-REPAIR 卡用**进程内遮蔽 `getenv("AUDIT_ROOTS_PATH")`** 模拟修复前路径：`AuditChain(临时库)`（不传 `roots_path=`）把日根写进 **DEFAULT（生产等价）**文件，产物 `date=2026-09-25 / leaf_count=4 / first_seq=1 / prev=65c70ab7… / 生产签名公钥` 与这三条**逐字段同型** |
| 3 | 三条的**写入者是测试进程**，不是审计主体 | 三条 `created_at` 落在 `2026-09-26T00:00:00.65/.77/.87+00:00` —— **相隔 0.22 s 的三个写入者**；而 `auto_seal` 只在 `append` 路径触发，当天 00:00 UTC 前后本机有多个 pytest 进程各持独立临时库 |

**并且这一次剔除是「可审计的」**：被剔除的 3 行**逐字节**保存在 `daily_roots.excised_rows.jsonl`，
原文件**字节级完整备份**保存在 `daily_roots.full_backup_before_excision.jsonl`（同目录）与仓库外
`%TEMP%\auditroot_optionA\daily_roots.BEFORE_optionA.jsonl`。

---

## 2. 为什么必须剔除，而不能「只追加一条正确的根」

日根外层链的校验是**位置式**的（`agent/audit/chain.py:3298-3319`）：它按行序比较
`第 i 行.prev_entry_hash == 第 i-1 行.entry_hash`，**遇到第一个断点即 return**。
断点在第 **12** 行（第 2 条污染记录的 `prev` 不等于第 11 行的 `entry_hash`）⇒
**任何追加的行都永远落在断点之后，永远修不到 FAIL=0**。

**副本反证（AUDIT-ROOT-REPAIR 卡实测）**：

| 副本构造 | 结果 |
|---|---|
| 13 行原样 + 追加一条正确的根 | **FAIL=9**（与生产实测一致） |
| **摘掉第 11–13 行** + 保留正确的新根 | **FAIL=0 / WARN=2 / rc=0** |

---

## 3. 处置前后

| | 处置前 | 处置后 |
|---|---|---|
| 行数 | 14 | **11** |
| 字节 | 12,568 | **9,895** |
| sha256 | `E4C1F1B82119B071…` | `04991152919985FC…` |
| `audit_governance_check.py` | **FAIL=9 / rc=1**（含 7 条「篡改类」） | **PASS / FAIL=0 / rc=0**（`G3 日根 10 天（通过 9）`、窗口失败 **0**、历史失败 1） |
| 审计链本体 | 72,700 条、seq 无缺口无重复 | **未变**（复核一致） |
| 剩余 WARN | 2 | **2（同一对历史事项，与本次事件无关）**：2026-09-21「封印后回填」的历史缺陷；7 天缺 Merkle 日根 |

**保留的行逐字节未改**：第 1–10 行与第 14 行原样保留；第 14 行 `prev_entry_hash` 等于第 10 行的 `entry_hash`
⇒ 剔除后**根链恢复连续**。

---

## 4. 根因是什么，以及它已被修好（**这是本处置真正的价值**）

**根因**：`agent/audit/chain.py:1152` 原为 `roots_path or DEFAULT_ROOTS_PATH` ——
**直接构造 `AuditChain(db)` 不读 `AUDIT_ROOTS_PATH` 环境变量**，而门面 `facade.py:199` **读**。
`tests/conftest.py:260-261` 明明把 `AUDIT_DB_PATH` 与 `AUDIT_ROOTS_PATH` 都隔离到会话临时目录，
但**不读 env 的构造路径**让测试的 `auto_seal` 把测试小链的日根写进了生产文件。

**修复**（主审计 2026-09-26 落地并实测）：改为
`roots_path or os.getenv("AUDIT_ROOTS_PATH") or DEFAULT_ROOTS_PATH`。
设 env ⇒ 跟随 env；不设 env ⇒ 回落 `DEFAULT_ROOTS_PATH` ⇒ **生产行为零变化**。

**回归护栏**：`tests/unit/test_audit_production_isolation.py`（AUDIT-ROOT-REPAIR 卡新增 3 条）。

---

## 5. 诚实边界（不要把它读成「问题全部消失」）

1. **本次剔除**动了一个**只用不改**的封印日志。它之所以正当，**完全依赖上面第 1 节的三条证据**与第 2 节的留档；
   **换一个没有这些证据的场景，同样的动作就是篡改。** 后人在引用本文件时，请一并引用证据，不要只引用结论。
2. **2026-09-21 的历史缺陷仍在**（`root_hash_mismatch`，记录 235 叶 vs 现算 245 叶），属**已知**、未处置，仍是 WARN。
3. **9 天缺 Merkle 日根**的历史事实未变。
4. **仍有 5 处构造点不显式传 `roots_path=`**（`scripts/chaos_s4_03_drill.py:239`、
   `test_guardrails_egress_chain.py:262`、`test_guardrails_foreign_taint.py:454`、
   `test_guardrails_instruction_data.py:322`、`test_s6_01_ui_panels.py:620`）—— 现由修好的 env 兜住，
   但**这些测试链此前是用「生产私钥」签日根的**，建议单开卡收敛。
5. `tests/conftest.py:287` 用 `setdefault` ⇒ **外部继承**的 `AUDIT_ROOTS_PATH` 仍会压过会话隔离（非硬隔离）。
