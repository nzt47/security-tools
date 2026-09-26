# 台账重建 Runbook（descriptor 台账：S1-02 重建 → S3-01 首次入轨收口）

> 建立：**RUNBOOK-1（2026-09-26）**，起因见 `docs/audit_skill_governance/LEDGER1.md` §5「复发风险（中）」
> 适用范围：任何一次重跑 S1-02 回填 / M8 台账重建 / 给 `data/descriptors.json` 补 stage 的场景

---

## 谁该读

- 要**重跑 `scripts/run_s1_02_backfill.py`** 的人（含 M8/G1-B 类重建窗口）；
- 要在台账里**新增/调整技能资产**后重建的人；
- 见到这条红的人：

  ```
  tests/unit/test_s3_01_handover.py::TestL4StageIngestion::test_real_ledger_has_no_unstaged_asset
  E   assert ['cp.skill.xxx'] == []
  ```

## 为什么需要这份 runbook

`run_backfill`（S1-02）对「台账里没有的资产」只做 **wire（register 桥接视图）**，
**stage 归 S3-01 管**（`agent/digestion/stage.py::first_entry_stage`）。
于是「**主轨新增技能 + 重跑 S1-02**」会新 wire 出 `stage=None` 的条目；
若无人随后跑 S3-01 首次入轨，L4 不变量被打破 —— 这就是 LEDGER-1 判定的 (b)，
实证资产是 `cp.skill.skill`（2026-09-26T01:01:27 被 M8 重建 wire，无人收口）。

**RUNBOOK-1 起，这一步已经写进代码**：`run_s1_02_backfill.py` 实跑时
**默认自动顺带收口**，不需要你记得手动跑。本 runbook 说明流程与验证判据。

---

## 标准流程（3 步）

```powershell
# 0) 项目根，系统 Python 3.12（不要用 venv/）

# 1) 干跑（不落库）：看覆盖率与待办
python scripts/run_s1_02_backfill.py --dry-run

# 2) 实跑：写台账 + **自动跑 S3-01 首次入轨收口**（同一次调用内完成，幂等）
python scripts/run_s1_02_backfill.py

# 3) 验证（定向，别跑全量 tests/unit —— 124 分钟）
python -m pytest tests/unit/test_s3_01_handover.py tests/unit/test_s1_02_s3_01_fixpoint_guard.py -q
```

**通过判据**

- 第 2 步 stdout 必须出现 `S3-01 收口: 台账无未入轨资产（不动点）`；
  若出现 `[!] 仍需 S3-01 收口`，按它给出的处置行执行（见下"关掉自动收口"）。
- 第 3 步两条文件全绿（`test_s1_02_s3_01_fixpoint_guard.py` 守的正是
  「重建 → 入轨 → 再重建 台账逐字节不变 + 无 `stage=None`」这条不动点）。

## 关掉自动收口（兼容旧行为 / 只想要提示）

```powershell
python scripts/run_s1_02_backfill.py --no-ingest-stages   # 只 wire + 打印待收口清单
python scripts/run_s3_01_ingest.py --execute              # 再按官方入口手动收口
```

自动收口调用的就是 `backfill_stages(reg, execute=True)`，与
`scripts/run_s3_01_ingest.py --execute` **同一个函数、同一个 actor**（`digestion_pipeline`）；
`--execute` 会先把改前台账备份到 `data/digestion/descriptors_pre_ingest_<ts>.json`。

## 不动点判据（"我这次重建是不是干净的"）

同一份主轨数据连跑两次，台账 `sha256` 必须相同，且 `stage=None` 数 = 0：

```powershell
python scripts/run_s1_02_backfill.py
(Get-FileHash data\descriptors.json -Algorithm SHA256).Hash
python scripts/run_s1_02_backfill.py
(Get-FileHash data\descriptors.json -Algorithm SHA256).Hash   # 必须与上一次相同
```

## 什么时候**不要**动生产台账

先在台账副本上试：

```powershell
Copy-Item data\descriptors.json $env:TEMP\ledger_copy.json
python scripts/run_s1_02_backfill.py --registry-path $env:TEMP\ledger_copy.json --out-dir $env:TEMP\out
```

## 回滚

```powershell
Copy-Item data\digestion\descriptors_pre_ingest_<ts>.json data\descriptors.json -Force
```

**回滚不覆盖审计链**（`data/audit/audit_chain.db` 是 append-only，`descriptor.stage` 那条保留）。
禁止用 `git checkout` 回滚运行时台账（`data/descriptors.json` 是 gitignored 的运行时产物，
且工作区常有在途改动）。
