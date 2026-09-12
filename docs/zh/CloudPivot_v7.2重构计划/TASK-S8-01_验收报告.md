# TASK-S8-01 验收报告：数据生命周期治理（TTL / 保留策略 / 冷归档 / 可还原）

> 任务书：[`TASK-S8-01_数据生命周期治理.md`](TASK-S8-01_数据生命周期治理.md)｜批次总表：[`PARALLEL_S8批次总表.md`](PARALLEL_S8批次总表.md)
> worktree：`s801`（`--base master`）｜验收日期：**2026-09-13**｜验收结论：**通过（8/8 + 3 项红线自证）**
> 交付物：`docs/zh/数据生命周期策略.md`、`docs/zh/数据生命周期_指标复算一致性报告.md`、
> `agent/retention/`、`scripts/scan_data_assets.py`、`scripts/run_retention.py`、本报告

---

## 一、结论速览

| # | 验收项 | 结论 | 证据 |
|---|---|---|---|
| 1 | 7 类以上运行时数据均有成文策略（保留期/归档/删除/执行者/依据） | ✅ | 策略表 **12 类**（`docs/zh/数据生命周期策略.md` §三）；`test_policy_covers_at_least_seven_runtime_classes` 等 5 例 |
| 2 | **审计链与每日根标注"禁止删除"**，`PurgeGuard` 有拦截用例（含**误传审计类即拒**） | ✅ | 红线 3 类 + `PurgeGuard` 六条拒绝码；`test_mis_passed_audit_class_is_refused` 等 9 例 |
| 3 | dry-run 默认先跑、不落盘；输出将被处理的数据清单与体积 | ✅ | 默认即 dry-run；`test_dry_run_writes_nothing` 用**目录树 sha256 全量对照**断言零改动 |
| 4 | 归档**可还原**：抽样还原往返一致 | ✅ | 逐文件 `sha256`（SQLite 用行级摘要）判定；真实数据实测 2/2 通过 |
| 5 | **归档前后指标一致**（≥2 个指标复算对照，口径未变） | ✅ | **3 个**既有指标逐字段一致（专用报告 §二） |
| 6 | 调度**默认关闭**；每次执行入链式审计（含条数与体积） | ✅ | `CP_RETENTION_ENABLED` 默认 false；两次连续 CLI 执行留下 **2 条**独立链上记录 |
| 7 | 记忆类删除走 S5-01「删记忆不删证据」 | ✅ | `delete_mode=s5_01_forgetting`；`PurgeGuard` 返回 `forgetting_path` 并给出 `redirect` |
| 8 | 既有 `observability`/`audit`/`digestion` 零回归；新增单测全绿、覆盖率 ≥80% | ✅ | 邻接 **1158 passed / 0 failed**；新增 **133 例全绿**、`agent/retention` 覆盖率 **88%**（最低模块 81%） |

**红线自证**（批次总表 §二三条不可越界原则逐条机器化）：

| 原则 | 落点 | 自证命令 |
|---|---|---|
| ① 审计链永久保留、只归档不删 | `policy.REDLINE_CLASS_IDS` + `PurgeGuard(CODE_REDLINE)` | `python scripts/run_retention.py --guard`（12 类全部拒绝，红线 3 类为 `redline`） |
| ② 默认保守（首跑 dry-run、只归档不删除、调度默认关闭） | `delete_source=false` 默认 + 调度器首跑强制 dry-run + `CP_RETENTION_ENABLED` 默认关 | `load_policy(env={})` 三断言（`test_load_policy_defaults_are_conservative`） |
| ③ 不改既有统计口径 | `metrics.py` 归档前后复算 + 温层仅对分片感知读端开启 | `--metrics --execute` 退出码 0；`scan_data_assets.py --check` |

---

## 二、交付物清单

| # | 交付物 | 路径 | 说明 |
|---|---|---|---|
| 1 | 数据生命周期策略（成文） | `docs/zh/数据生命周期策略.md` | 12 类策略表 + 红线标注 + 三层模型 + 闸门口径；与代码**同源守护** |
| 2 | 指标复算一致性报告 | `docs/zh/数据生命周期_指标复算一致性报告.md` | 3 指标逐字段对照 + "判据有牙齿"的反向用例 |
| 3 | 保留策略包 | `agent/retention/`（9 模块 1507 行） | `policy` / `scan` / `manifest` / `archiver` / `restorer` / `guard` / `metrics` / `scheduler` |
| 4 | 数据资产普查脚本 | `scripts/scan_data_assets.py` | 只读；复用 S2-02 `inventory_legacy_files`；`--check` 自带策略自检 |
| 5 | 归档/还原/指标 CLI | `scripts/run_retention.py` | `--dry-run`（默认）/`--execute`/`--verify`/`--metrics`/`--guard` |
| 6 | 调度注册 | `agent/retention/scheduler.py::register_retention_job` | 默认关闭；**首跑强制 dry-run**；每次执行入链审计 + 事件 |
| 7 | 开关注册 | `agent/settings/registry.py`（8 条）+ `config.yaml retention:` | 机械提取**零缺口**（330/330） |
| 8 | 单测 | `tests/unit/test_retention_{policy,archive,guard,scheduler,metrics,scan}.py` + `retention_testkit.py` | 133 例 |
| 9 | 本报告 | `TASK-S8-01_验收报告.md` | — |

---

## 三、步骤 1：数据资产普查与策略表

### 3.1 普查结果（真实数字，`2026-09-13`）

```powershell
python scripts/scan_data_assets.py --root C:\Users\Administrator\agent --check
```

```
数据类                      文件       记录        体积  保留期       归档         可否删除        执行者
audit_chain               1    21042    16.5MB  永久保留      cold_pack  禁止删除(红线)    审计链本体（S2-02）
skills_audit              8     8772     1.6MB  永久保留      cold_pack  禁止删除(红线)    技能管理审计轨
legacy_audit_archive      0        0        0B  永久保留      cold_pack  禁止删除(红线)    迁移设施
unified_traces            1     1455     3.1MB  90 天      cold_pack  禁止删除        轨迹设施（S2-01）
events                    4     2509     1.2MB  永久保留      warm_daily 禁止删除        事件出口（S2-03）
case_store                2        2     1.1MB  永久保留      cold_pack  禁止删除        消化设施（S3-02）
digestion_drafts          0        0        0B  180 天     cold_pack  可删          消化设施（S3-01）
shadow_ledger             2       41    15.7KB  永久保留      cold_pack  禁止删除        灰度设施（S3-03）
policy_decisions          1       16    13.8KB  永久保留      warm_daily 禁止删除        策略设施（S4-02）
cost_daily                0        0        0B  永久保留      cold_pack  禁止删除        成本设施（S5-03）
memory_entries            3        3     1.0MB  永久保留      cold_pack  可删          记忆设施（S5-01）
memory_snapshots          0        0        0B  30 天     cold_pack  可删          记忆设施（S5-01）
合计：12 类（有数据 8 / 未落盘 4）、22 文件、33840 记录、24.5MB
红线类（禁止删除）：audit_chain、skills_audit、legacy_audit_archive
标为可删的类：digestion_drafts、memory_entries、memory_snapshots
存量审计轨盘点（复用 S2-02 inventory_legacy_files）：53/54 个目标存在，合计 2.1MB
[scan_data_assets] 自检通过：策略表完整、红线未越界、温层口径自洽
```

**未落盘的 4 类如实标注、不按 0 计入体积**：把"没有数据"与"数据是 0 字节"混为一谈
会让普查失去意义（§0.3 口径纪律）。

### 3.2 策略表与文档同源

`tests/unit/test_retention_policy.py::test_policy_matches_doc` 逐类断言
`docs/zh/数据生命周期策略.md` 覆盖了全部 12 个 `class_id`、包含"禁止删除"字样与
关键结论（只归档不删除 / dry-run / `CP_RETENTION_ENABLED` / `retention.run` / S5-01）——
**文档与代码不许漂移**。

### 3.3 红线标注

`audit_chain`（链 + 每日 Merkle 根）、`skills_audit`（纯审计轨）、
`legacy_audit_archive`（只读镜像）三类在策略表与代码里都写死 **禁止删除**：
`deletable=False` + `delete_mode=none` + `retention_days=None`（永久保留）。

> **签名私钥不进归档**：`data/audit/audit_signing_key.pem` **刻意不在** `audit_chain` 的
> globs 内（把私钥复制进归档目录会扩大暴露面）。用例：`test_signing_key_is_not_archived`。

---

## 四、步骤 2：分层与归档机制

### 4.1 三层归属（**不重造第二套**）

| 层 | 实现 | 是否新写 |
|---|---|---|
| 热 | 不动 | — |
| 温 | `agent/skills_mgmt/log_archiver.archive_daily_file`（**既有**） | ❌ 直接调用 |
| 冷 | `agent/retention/archiver.py`（自描述压缩包） | ✅ 本任务 |
| 删 | `agent/retention/guard.py`（默认关闭） | ✅ 本任务 |

`test_warm_layer_not_invented_here` 用 **spy** 断言温层确实走了
`log_archiver.archive_daily_file`，而不是本包自造的分片逻辑。

### 4.2 归档件自描述（`retention.archive.v1`）

```
data/archive/audit_chain/2026-09-13.db.gz          3 841 706 字节
data/archive/audit_chain/2026-09-13.manifest.json
data/archive/unified_traces/2026-09-13.db.gz         333 357 字节
data/archive/unified_traces/2026-09-13.manifest.json
```

清单含 `schema / schema_version / class_id / period / period_kind / created_at /
time_range / record_count / file_count / payload_bytes+sha256 / archive_bytes+sha256 /
codec / sqlite_table / files[*]（offset/bytes/sha256/行数/行摘要）`。
`ArchiveManifest.read()` 对**非法 schema 版本、非法 codec、切片不连续**一律拒绝
（不做"尽力兼容"）。

### 4.3 可还原（验收 #4）

**真实数据实测**（副本，未动线上）：

```
[还原] audit_chain@2026-09-13 → 文件 1 个，一致 1 个；归档件校验=True 载荷校验=True；判定=通过
  ✅ row_digest C:\...\root\data\audit\audit_chain.db（17268736 字节）
[还原] unified_traces@2026-09-13 → 文件 1 个，一致 1 个；归档件校验=True 载荷校验=True；判定=通过
  ✅ row_digest C:\...\root\agent\data\tool_trace.db（3219456 字节）
```

判定口径按数据形态分两种，都是**机械可判定**的：

| 形态 | 判定 | 说明 |
|---|---|---|
| JSONL / 文件树 | **逐字节**：还原后 `sha256` == 清单登记值 | 载荷是源文件原始字节的拼接，不重排/不解析 |
| SQLite | **行级摘要**：还原库的 `row_digest` == 清单登记值 | 载荷存的是**一致性备份副本**（sha256 必然不同于活动库），故比行内容而非字节 |

**还原默认不改线上**：缺省还原到临时目录；若目标会覆盖仍然存在的源文件，
必须显式 `allow_overwrite_live=True` 才放行（`test_restore_refuses_to_overwrite_live_files`）。

### 4.4 dry-run 默认先跑、不落盘（验收 #3）

`test_dry_run_writes_nothing` 对整棵临时目录树做 `{相对路径: sha256}` 前后对照，
断言 **完全一致**、且没有创建 `data/archive`；`test_run_without_confirm_is_dry_run`
断言 `run()` 不带 `confirm=True` 时**不会静默降级为执行**。

真实数据 dry-run 输出：

```
[DRY-RUN（未落盘）] 周期=2026-09-13 归档目录=...\archive
  - audit_chain            冷归档 1 文件 / 21056 记录 / 17268736 字节 → 2026-09-13.db.gz；拒绝删除（redline）
  - unified_traces         冷归档 1 文件 / 1457 记录 / 3219456 字节 → 2026-09-13.db.gz；拒绝删除（not_deletable）
  - policy_decisions       无待处理数据（无冷数据（90 天阈值内）；本轮温层移出 16 行，其分片将在下次运行进入冷层）
  ...（其余 9 类）
  合计：归档 2 个包 / 2 文件 / 22513 记录 / 20488192 字节（压缩前）；删除 0 文件 / 0 字节
  · dry-run：未写归档件、未写审计、未改任何源文件
```

---

## 五、步骤 3：调度与开关（默认保守）

| 项 | 实现 | 证据 |
|---|---|---|
| 默认关闭 | `CP_RETENTION_ENABLED` / `config.yaml retention.enabled` 默认 false ⇒ 不注册任务 | `test_default_is_disabled`；`test_disabled_does_not_even_build_a_scheduler`（关闭时连 `get_scheduler()` 都不调用） |
| 首跑强制 dry-run | 本进程第一次运行恒为 dry-run（不受 `CP_RETENTION_DRY_RUN` 影响） | `test_first_run_is_forced_dry_run_even_when_dry_run_disabled` |
| 每次执行入链审计 | `action="retention.run"`，payload 只放**计数与体积** | 见 §5.1 |
| 同时发事件 | `retention.run`（新增分组 `GOVERNANCE_EVENT_TYPES`，**不改动** §3.6/P7.1-18/§6.6 三组冻结清单） | `tests/unit/test_events_v1.py::test_governance_events_cover_s8_01` |
| 非法值回退默认 | env/config 非法值 → 默认并 warn；越界夹紧 | `test_load_policy_illegal_env_falls_back` 等 3 例 |
| 开关登记零缺口 | `agent/settings/registry.py` +8 条；`config.yaml retention:` | `python scripts/scan_settings.py --check` → **零缺口 ✅**（330/330） |

### 5.1 审计留痕（含条数与体积）——**并实测了落盘**

```
链式审计：recorded（action=retention.run，seq=1）   ← 第 1 次 CLI 执行
链式审计：recorded（action=retention.run，seq=2）   ← 第 2 次 CLI 执行
retention.run entries = 2 seqs = [1, 2]
chain ok = True checked = 2
```

链上载荷（`audit.chain.v1` 形状，包在 `payload` 键内）：

```json
{"dry_run": false, "period": "2026-09-13",
 "archives": 2, "archived_files": 2, "archived_records": 22513,
 "archived_bytes": 20488192, "packed_bytes": 4175063,
 "warm_lines_moved": 0, "deleted_files": 0, "deleted_bytes": 0,
 "purge_refused": 2, "manifests": ["data/archive/audit_chain/2026-09-13.manifest.json", ...]}
```

> **实现期发现的真实缺陷（已修）**：链的写入是**后台批量线程**（批大小 100 / 轮询 0.5s），
> 而 CLI 一次运行几百毫秒就退出 —— 首版不显式 `flush()`，两次连续执行**只留下 1 条**
> （第二次覆盖了同一个 `seq`）。已改为 append 后显式 `chain.flush()`，
> 并新增用例 `test_audit_entry_is_durable_for_an_independent_reader`
> （换一个**独立只读链**读得到该条才算数）。

---

## 六、步骤 4：指标口径可复算（验收 #5）

完整对照见 [`../数据生命周期_指标复算一致性报告.md`](../数据生命周期_指标复算一致性报告.md)。摘要：

| 指标 | 事实源（既有函数） | 归档前 | 归档后 | 判定 |
|---|---|---|---|---|
| `utc.weekly` | `utc.utc_weekly()` | `cost_normalized_cents=221.563`、`llm_calls=39` | 同 | **逐字段一致 ✅** |
| `digestion.throughput` | `ShadowLedger.rows()` + `ManualReviewQueue.summary()` | `ledger_runs=21`、`sampled_total=557` | 同 | **逐字段一致 ✅** |
| `audit.chain` | `AuditChain.reader().verify_chain()` + `count()` | `entries=21056`、`head_self_hash=32ba197f…` | 同 | **逐字段一致 ✅** |

**判据有牙齿**：`test_metric_check_has_teeth` 把事件明细整份搬出读端视野后复算，
断言 `consistent=false` 且 `changed_fields` 命中 `llm_calls`，并输出
"该类归档方式不合格，须改为「保留聚合摘要 + 明细归档」双轨"的处置提示。
没有这条，第 1 组用例就是**永远通过的摆设**。

### 6.1 温层不改口径的端到端证据（真实数据副本）

```
移除： data\policies\decisions.jsonl              sha256 FD2CCA637832C23C788F114C61C4382EB3CDAC22B13463E09A5257F27231FC42
新增： data\policies\decisions-2026-09-12.jsonl   sha256 FD2CCA637832C23C788F114C61C4382EB3CDAC22B13463E09A5257F27231FC42
变更： （无）        删除文件数：0
```

温层是**纯移动**（逐字节相同），分片仍被 `_candidate_files()` 读到 ⇒ 口径不变。
`shadow_ledger` 因读端**只认单文件**，温层显式关闭
（`test_shadow_ledger_warm_is_off_because_reader_is_single_file`）。

---

## 七、步骤 5：回归与质量证据

| 门禁 | 命令 | 结果 |
|---|---|---|
| 新增单测 | `pytest tests/unit/test_retention_*.py -q` | **133 passed / 0 failed** |
| 新增模块覆盖率 | `--cov=agent.retention` | **88%**（`policy` 89 / `scan` 88 / `manifest` 94 / `archiver` 81 / `restorer` 94 / `guard` 97 / `metrics` 85 / `scheduler` 91 / `__init__` 100） |
| 邻接回归 | audit(8) + trace/observability(5) + digestion(5) + memory_forgetting + utc_cost + decision_logger + policy_engine | **1158 passed / 0 failed** |
| kwarg 扫描 | `scan_kwarg_conflicts.py --path agent/ --min-risk HIGH`（及 `--path tests/`） | **0 处**（两条） |
| mypy | `python -m mypy agent/retention/` | **新增 9 模块 0 error**（545 条均为既有其它模块的历史债，`agent\retention` 一条不出） |
| importlinter | `lint-imports` | **2 kept / 0 broken** |
| 开关零缺口 | `python scripts/scan_settings.py --check` | **零缺口 ✅**（330/330，无未声明动态家族） |
| 策略自检 | `python scripts/scan_data_assets.py --check` | **通过**（策略完整 / 红线未越界 / 温层口径自洽） |
| 产物漂移 | `git status --porcelain` | 见 §八 |

### 7.1 已改动的既有文件（**行为不变的加法**）

| 文件 | 改动 | 为什么不是破坏性 |
|---|---|---|
| `agent/observability/events.py` | 新增 `EV_RETENTION_RUN="retention.run"` + **新分组** `GOVERNANCE_EVENT_TYPES` + `EventType.RETENTION_RUN` + `__all__` | 只**新增**分组，§3.6 八事件 / P7.1-18 第 9 / §6.6 埋点三组**逐字未动**；未知类型不再被计入 `unknown_type_count` |
| `agent/observability/__init__.py` | 导出 `GOVERNANCE_EVENT_TYPES` | 纯导出 |
| `tests/unit/test_events_v1.py` | `test_all_types_contains_everything` 的集合等式加入新分组；新增 `test_governance_events_cover_s8_01` | 该等式是"三组并集＝全集"的不变量，新增第四组必然要同步 —— 且仍是**等式断言**（不是放宽） |
| `agent/settings/registry.py` | +8 条 `SettingSpec` | 开关中心"零缺口"是硬约束（`test_scan_zero_gap`），不登记即 CI 红 |
| `config.yaml` | 新增 `retention:` 段（默认 `enabled: false`） | 与 `slo_report:` 同风格；不改变既有段落 |
| `.gitignore` | 新增 `data/archive/` | 归档件是运行时压缩产物（性质同 `data/audit/`），入库会让仓库膨胀 |

---

## 八、验收逐条对照（任务书 §四）

- [x] **7 类以上运行时数据均有成文策略（保留期/归档/删除/执行者/依据）**
      → 12 类；`docs/zh/数据生命周期策略.md` §三；每类 `owner`/`basis` 非空由
      `test_every_class_has_owner_and_basis` 守护。
- [x] **审计链与每日根标注"禁止删除"，且 `PurgeGuard` 有拦截用例（含"误传审计类即拒"）**
      → `test_redline_classes_are_refused`（参数化 3 类）、
      `test_mis_passed_audit_class_is_refused`（带具体路径传入仍拒）、
      `test_no_non_deletable_class_can_pass_the_guard`（生产表整体自证）。
      拒绝文案含"**禁止删除**"字样（策略文档同步标注）。
- [x] **dry-run 默认先跑、不落盘；输出将被处理的数据清单与体积**
      → 默认即 `plan()`；`test_dry_run_writes_nothing`（目录树 sha256 全量对照）、
      `test_dry_run_lists_files_and_sizes`、`test_run_without_confirm_is_dry_run`。
- [x] **归档可还原：抽样还原往返一致**
      → `test_roundtrip_is_byte_exact`、`test_roundtrip_sample_subset`、
      `test_sqlite_snapshot_roundtrip_uses_row_digest`（**双表库**回归防线）、
      `test_tampered_archive_is_detected`；真实数据实测 2/2 通过。
- [x] **归档前后指标一致（至少 2 个指标复算对照，口径未变）**
      → 3 个指标逐字段一致；反向用例 `test_metric_check_has_teeth` 保证判据有牙齿。
- [x] **调度默认关闭，需显式开关；每次执行入链式审计（含条数与体积）**
      → `CP_RETENTION_ENABLED` 默认 false；两次连续 CLI 执行留下 2 条独立链上记录；
      `test_audit_entry_is_durable_for_an_independent_reader`。
- [x] **记忆类删除走 S5-01 的"删记忆不删证据"**
      → `memory_entries` / `memory_snapshots` = `s5_01_forgetting`；
      `PurgeGuard` 返回 `forgetting_path` + `redirect=agent.memory.forgetting.ForgettingEngine`；
      `test_memory_classes_are_redirected_to_forgetting`（参数化 2 类）。
- [x] **既有 `observability`/`audit`/`digestion` 套件零回归；新增单测全绿、覆盖率 ≥80%**
      → 邻接 1158 passed / 0 failed；新增 133 全绿、覆盖率 88%。

### 明确不做（任务书 §五）逐条确认

- [x] ❌ 未实现"审计链裁剪/轮转"（红线类只归档不删；`PurgeGuard` 无条件拒绝）。
- [x] ❌ 未改变任何既有统计口径（3 指标复算一致；温层只对分片感知读端开启）。
- [x] ❌ 未实现跨机/对象存储归档（只做本地归档 + 可还原；导出打包留 P5）。

---

## 九、实现期发现并修复的 3 处真实缺陷

| # | 缺陷 | 影响 | 修复与防线 |
|---|---|---|---|
| D1 | **审计链未 flush 导致留痕丢失**：链写入是后台批量线程（批 100/轮询 0.5s），CLI 进程很快退出 ⇒ 连续两次执行只留 1 条（第二次覆盖同一 `seq`） | 直接违背验收 #6"每次执行入链式审计" | append 后显式 `chain.flush(timeout=5.0)`；新增 `test_audit_entry_is_durable_for_an_independent_reader`（**独立只读链**读到才算数）+ `audit["flushed"]` 断言 |
| D2 | **SQLite 还原漏传表名**：`restorer` 用"全库行摘要"比对，而清单登记的是"某表行摘要" ⇒ 多表库（`tool_trace.db` 同时有 `tool_traces`/`unified_traces`）**误报 mismatch** | 真实数据还原被判"不通过"，归档不可信 | 还原时传 `manifest.sqlite_table`；单测的 sqlite 库**加上第二张表**并反向断言"全库摘要必须不同"，否则该用例无法发现此缺陷 |
| D3 | **测试写进真实审计链**：`AUDIT_DB_PATH` 环境变量对进程级 `audit = AuditFacade()` **无效**（门面在模块导入时就读走环境，`reset_audit_facade()` 也不重读）⇒ 用例悄悄写进工作区 `data/audit/audit_chain.db` | 污染本机审计链；也会让"链上只有 1 条"的断言假失败 | 测试改为 `audit.bind(AuditChain(tmp_path))` **显式注入临时链**并 autouse 到整个调度套件；验收期间工作区审计产物已清理 |

> 另有两处**测试收集陷阱**已修（写进代码注释防复发）：
> `python_functions = test_* verify_*` 会把测试模块里**导入的** `test_class` /
> `verify_roundtrip_metrics` 当成用例收集 ⇒ 分别改名为 `retention_class` 与
> `check_roundtrip_metrics`。

---

## 十、遗留与处理（**如实登记，不假装完成**）

| # | 遗留 | 归属 | 说明 |
|---|---|---|---|
| L1 | 真实审计链库 `data/audit/audit_chain.db` 在 `seq=17` 处 `prev_hash_mismatch`（`seq_range=(1,21060)` 而 `entries=21056`，另有 4 个空洞），`ok=False` | **S2-02 / 运营期**（非本任务） | **测得时点 `2026-09-13 01:44`**。归档前后**完全相同**（`checked=16`、`head_self_hash` 不变）⇒ 归档未动链。S2-02 的既有裁定是"只归档不删、不改链"，故此断裂不由 S8-01 修复。本任务已把它**机械记录下来**（`audit.chain` 指标含 `ok`/`first_bad_seq`/`bad_seq_count`/`head_seq`/`head_self_hash`，见 §十.1 补记）。**注：该库已于同日 02:00 被重置，见 L7。** |
| **L7** | **🔴 `data/audit/` 于 `2026-09-13 02:00` 被清空重建：21 056 条链式审计留痕 + 54 个存量审计轨文件（约 2.1 MB）已不在磁盘上**；重建后的链只有 10 条（并行会话 S8-05 的脚本产物：`resolution.record`×6、`digest.shadow.enqueue_rejected`×3、`digest.shadow.review_stale`×1） | **Owner 复核归因**（非本任务） | S8-01 的归档**只读**审计链（`delete_source` 默认 false，红线类被 `PurgeGuard` 无条件拒绝删除），`01:44` 之后只做过只读普查与只读护栏查询 ⇒ **非本任务所致**。`data/audit/` 被 `.gitignore` 忽略 ⇒ Git 无任何副本，**S8-01 验收取证时留下的副本是仅存的完整留痕**，已保全到 `~/.cloudpivot/vault/audit_pre_reset_20260913/`（原样副本 + 已验证冷归档件 + 签名私钥 + 还原说明），见 §十二。 |

| L2 | 每日 Merkle 根 `data/audit/daily_roots.jsonl` **当前不存在**（`daily_roots=0`） | S2-02 / 运营期 | 策略表已把 `daily_roots.jsonl` 纳入 `audit_chain`（红线，禁止删除），一旦生成即受保护并被归档。 |
| L3 | 温层对 `policy_decisions` 会在**首次真实运行**时把历史行移入分片（实测：16 行 → `decisions-2026-09-12.jsonl`，逐字节相同） | S8-01 → **S8-02** | 读端分片感知（`_candidate_files()`），故口径不变；写入端轮转归 **S8-02**。本策略只消费其产物，不改写入语义。 |
| L4 | 12 类中 4 类当前"未落盘"（`legacy_audit_archive`/`digestion_drafts`/`cost_daily`/`memory_snapshots`） | 运营期 | 无数据即无归档；策略已就位，落盘后自动纳入。 |
| L5 | 归档件加密（静止数据加密）与远端异地副本 | **P5 Backlog** | 任务书 §五明确不做跨机/对象存储。 |
| L6 | `zstandard` 在本机可用但**未进 `requirements`**，故压缩用 stdlib `gzip`（`mtime=0`，确定性） | 本任务口径 | 硬依赖未声明的包会让 CI 在干净环境上失败；编解码器记在清单 `codec` 字段，将来换 zstd 可**向后兼容**（老清单仍按 gzip 解）。 |

---

### 十.1 结案后的补记（2026-09-13，`agent/retention` 一处增强）

复核阶段的侦察确认 `AuditChain.verify_chain()` **在首个断裂处即停**（真实链 `checked=16`
而 `entries=21056`），因此只报 `ok=False` 无法定位问题，运营期也无法把"归档动了链"与
"链本来就断了"区分开。故为 `audit.chain` 指标**补两个字段**（纯增量，不改既有字段）：

| 新字段 | 来源 | 用途 |
|---|---|---|
| `first_bad_seq` | `ChainVerification.first_bad_seq` | 指出**链从哪一条起不可信** |
| `bad_seq_count` | `len(ChainVerification.bad_seqs)` | 断裂条目数（按 `_MAX_BAD_RECORDS` 截断） |

用例 `test_audit_chain_metric_locates_the_break_point` 在临时链上篡改 `seq=2` 的
`payload_hash`，断言 `ok=False` 且 `first_bad_seq == 2`，并断言 `entries` **不受校验结论影响**
（归档前后仍可比）。既有对照表（§5.1 / 指标复算报告 §2.3）的所有字段保持不变，
新增字段两侧同值，故"逐字段一致"的结论不受影响。

---

### 十.2 结案后的补记之二：闸门**管辖边界**（撤销一处过度声称）

复核上游侦察结论时发现，配套的 `agent/retention/guard.py` docstring 与
`docs/zh/数据生命周期策略.md` §五初版写的是**绝对声称**：

> ~~"任何删除动作在真正 `os.remove` 之前，必须先过 `PurgeGuard.check()`"~~

这**不成立**。仓库里存在 8 处**各模块自带的既有清理路径**，它们不经过本闸门
（已逐条核实存在）：

| 既有清理路径 | 清理对象 |
|---|---|
| `agent/monitoring/replay_storage.py::cleanup_old_records` | 回放记录（`DELETE` + `os.remove`） |
| `agent/p6/snapshot.py::cleanup_snapshots` / `agent_p6_snapshot.py::cleanup_snapshots` | 旧快照文件 |
| `agent/memory/forgetting.py::prune` | 记忆快照库到期件（S5-01 自有口径） |
| `agent/policy/inbox.py::prune_recent` | 策略收件箱近期项 |
| `agent/tool_fewshot_store.py::cleanup_expired` | 过期 few-shot 样本 |
| `agent/task_scheduler.py::cleanup_old_logs` | 调度器日志 |
| `agent/skills_mgmt/cleanup.py::cleanup_orphans` / `cleanup_unused` | 技能资产孤儿/未用 |
| `agent/monitoring/resource_monitor.py::cleanup_persisted_history` | 资源监控历史 |

**已更正为准确口径**：本闸门管辖 **`agent/retention` 自己发起的删除**，提供的是
"**销毁类动作在本层不可绕过**"，**不是**"全仓库只有一条删除路径"。本任务**不接管**
那些既有路径（接管属重构而非治理，会变更既有语义）。

**为什么这条更正重要**：过度声称会让人把"闸门存在"误读成"所有删除都被守卫"，
从而对**未受守卫**的路径放松警惕 —— 这正是治理文档最危险的失效方式。
`test_doc_states_guard_jurisdiction_boundary` 把边界声明与"绝对声称不得复现"一并钉住。
策略文档另新增 §5.2「已知落点风险」（`decisions.py` 默认路径相对 CWD、`log_archiver`
docstring 后缀与实际不符两处如实登记）。

---

## 十一、验收结论

任务书 §四 八项验收标准**逐条通过**，三条不可越界原则**逐条机器化自证**。交付物齐备，邻接套件零回归，门禁四绿（kwarg 0 处 / mypy 新增 0 error /
`lint-imports` 2 kept 0 broken / 开关零缺口），实现期发现并修复 3 处真实缺陷，
7 项遗留已如实登记归属。

**结论：通过。**

---

## 十二、审计链留痕保全件（L7 的处置证据）

L7 记录的清空事故发生在验收取证**之后**。`data/audit/` 无 Git 副本，而 S8-01 在取证时
恰好留下了两份完整副本，故已保全到仓库树之外的用户 profile 目录，**不做任何原地还原**
（还原会覆盖并行会话当前正在写的链，须由 Owner 决定）：

```
~/.cloudpivot/vault/audit_pre_reset_20260913/
├─ audit_chain.db                     17 268 736 B   重置前链库原样副本（21 056 条）
├─ 2026-09-13.db.gz                    3 841 706 B   S8-01 冷归档件（已验签）
├─ 2026-09-13.db.gz.manifest.json          1 502 B   自描述清单（record_count / row_digest / 双校验和）
├─ audit_signing_key.pem                     119 B   ⚠️ 私钥（原 data/audit/ 内，清空后原位已无）
└─ README_还原说明.md                                时点/证据/还原命令/处置建议
```

保全时已用**交付的还原路径**实测（不是"应该能还原"）：

```
archive_verified = True    payload_verified = True
record_count = 21056       还原后 audit_chain 行数 = 21056      row_digest 一致 = True
```

**处置建议（供 Owner 决策）**：① 先保全再归因；② 私钥二选一（保留用于验证历史签名，
或轮换后删除）；③ 把 `data/audit/` 纳入日常备份，并排查并行会话中直接清理
`data/audit/` 的脚本/用例（S8-01 实现期已发现同型问题：`AUDIT_DB_PATH` 环境变量对
进程级 `AuditFacade` 无效，用例会写到默认链上）；④ 若还原，`seq=17` 的既有断点会一并回来，
属预期，需单独排查。

