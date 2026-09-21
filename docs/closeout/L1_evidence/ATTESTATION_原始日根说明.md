# 原始日根（daily_roots.ORIGINAL.jsonl）出证附注

> 口径来源：主会话裁定 **D-20260921-04 / L1-e** —— 采纳**严格口径**：**原件为"原始出证"唯一凭据**，
> **不做文件替换**，改以**职责分离**落地。
> 执行者：TASK-09 子代理（独占窗口）。执行日期：2026-09-21。
> 本文件为**新增文件**，不改动任何既有档案。

---

## 0. 一句话结论

本档案（`docs/closeout/L1_evidence/daily_roots.ORIGINAL.jsonl`，6 条）是
**2026-09-21 审计链修复之前**的原始日根，**唯一凭据**；
现行 `data/audit/daily_roots.jsonl`（同 6 条）**与原始日根不再对应**，
**仅用于运行期机器校验**。两者**职责分离、并存**，**不做文件替换**。

---

## 1. 原始日根是什么（及其未被改动）

| 项 | 值 |
|---|---|
| 路径 | `docs/closeout/L1_evidence/daily_roots.ORIGINAL.jsonl` |
| 条数 | **6** |
| 字节 | **5398** |
| sha256 | `3A776CB928D6875EBF64F7019345EEEB28F56CD7DB39AF92AE45F92BE5718181` |
| mtime | **2026-09-20T08:00:00.7673725+08:00**（即修复前最后一次封印时刻，未被触碰） |

**三方独立对拍（同一哈希，互为佐证）**：

| 来源 | sha256 | 字节 | mtime |
|---|---|---|---|
| `docs/closeout/L1_evidence/daily_roots.ORIGINAL.jsonl`（本档案） | `3A776CB9…` | 5398 | 2026-09-20T08:00:00.7673725+08:00 |
| `_ci_logs/audit_backup_20260921_035434/daily_roots.jsonl`（修复前独立备份） | `3A776CB9…` | 5398 | 2026-09-20T08:00:00.7673725+08:00 |
| `MANIFEST.json` → `pre_repair[3]` 记录的哈希 | `3A776CB928D6875EBF64F7019345EEEB28F56CD7DB39AF92AE45F92BE5718181` | 5398 | 2026-09-20T08:00:00.7673725+08:00 |

⇒ **逐条对拍一致，本档案确实未被改动**（sha256、字节数、mtime 三项全等）。

复核命令（只读）：

```powershell
Get-FileHash docs\closeout\L1_evidence\daily_roots.ORIGINAL.jsonl -Algorithm SHA256
(Get-Item docs\closeout\L1_evidence\daily_roots.ORIGINAL.jsonl) | Select-Object Length,LastWriteTime
```

---

## 2. 必须披露的事实（**修复对日根做了什么**）

**在 2026-09-21 的审计链修复中，为了修复链完整性受损（断点 6 处 / seq 空洞 15 个），
对 19,628 行的 `prev_hash` + `self_hash` 两列做了前向重算，并按新 `self_hash`
用同一密钥重签了 6 个 ed25519 日根。**

逐项口径（证据见 `docs/closeout/L1_审计链修复报告_20260921.md` 与
`docs/closeout/L1_evidence/PRODUCTION_receipt.json`）：

| 项 | 修复前 | 修复后 | 证据 |
|---|---|---|---|
| 链接断点 | **6** | **0** | `PRODUCTION_receipt.json` → `before.breaks=6` / `after.breaks=0` |
| seq 空洞 | **15**（19220, 19768, 20077–20085, 20092–20095） | **0** | `PRODUCTION_receipt.json` → `before.holes`（15 个）/`recovered_seqs`（15 个）/`unrecoverable_seqs: []` |
| 记录条数 | 20089 | 20104 | 同上 `before.rows` / `after.rows` |
| **重算行数** | — | **19,628**（`seq 477..20104`） | `PRODUCTION_receipt.json` → `relink_first_affected=477`、`relink_rows=19628`；报告 §"共 19,628 行（97.7%）" |
| 重算的列 | — | `prev_hash` + `self_hash` **两列** | 报告："`UPDATE` 19,628 行的 `prev_hash` + `self_hash` 两列（`seq 477..20104`）；其他 11 列"保持原样 |
| 日根 | 6 条（本档案） | **重签 6 条** | `MANIFEST.json` `post_repair[3]`：`9FCD4B32…`；签署密钥**同一把** |

**"同一密钥"的证据**：原始与现行 6 条日根的 `signer_public_key` 完全相同，
均为 `b73dd748ad340a2fa87a4646b6846965bc8fb544e7a8951bb9c22b877d98ae6f`；
`data/audit/audit_signing_key.pem` 的 sha256 在修复前后均为
`CA751F366BB3A8477E32409431D63FB57FCF794F31A8FCD9BFA3ED3F1E04F128`（`MANIFEST.json` `pre_repair[4]` == `post_repair[4]`）。
⇒ **不是换钥重签，而是用同一密钥对新根重签**。

---

## 3. 原始日根 vs 现行运行期日根（逐日对照，证明"不再对应"）

| date | seq 区间 | leaf_count（原→现） | root_hash（原，前 16） | root_hash（现，前 16） | root_hash | signature |
|---|---|---|---|---|---|---|
| 2026-09-13 | 554..914 | 361 → 361 | `2497b8f4f98f1a1d` | `2e92520e69a8fd7e` | **不同** | **不同** |
| 2026-09-14 | 915..2782 | 82 → 82 | `c8347a073287432b` | `1fb4fe20f5a3155a` | **不同** | **不同** |
| 2026-09-16 | 3184..8436 | 5253 → 5253 | `30d158c1d2526b52` | `212658137830241e` | **不同** | **不同** |
| 2026-09-17 | 8437..19228 | **10791 → 10792** | `81620d83ede7ad60` | `1be20aa10e9406f3` | **不同** | **不同** |
| 2026-09-18 | 19229..19551 | 323 → 323 | `bd0039c8bb3be48c` | `26e7f8fac45ee3c0` | **不同** | **不同** |
| 2026-09-19 | 19552..19743 | 192 → 192 | `408f174951f0a1b1` | `5522f3bdea352021` | **不同** | **不同** |

- **6/6 的 `root_hash` 与 `signature` 全部不同**；
- `first_seq` / `last_seq` 6/6 **完全相同**（封印区间未变）；
- 唯一 `leaf_count` 变化：**2026-09-17 由 10791 → 10792**（修复补回 09-17 遗漏的 1 条，
  与 `PRODUCTION_receipt.json` 的 `INSERTED 15` 中落在该日的那 1 条一致）；
- 叶子总数 17002 → 17003。

⇒ 结论：**现行 `data/audit/daily_roots.jsonl` 与原始日根不再对应**。
把它换回原件，只会让 `scripts/verify_audit_chain.py` 重新报错（重算出的新 root 对不上旧签名），
**因此裁定"不做文件替换"**。

---

## 4. 职责分离（本裁定的落地方式）

| 角色 | 文件 | 用途 | 可否被替换 |
|---|---|---|---|
| **原始出证（唯一凭据）** | `docs/closeout/L1_evidence/daily_roots.ORIGINAL.jsonl` | 对外证明"修复前那一天的真实出证是什么样"；**合规/审计追溯的起点** | **否** —— 一经归档即冻结，只读 |
| **运行期机器校验用根** | `data/audit/daily_roots.jsonl` | 供 `scripts/verify_audit_chain.py --roots-check all` 做重放校验；随运行期 append-only 增长 | 可增长（**只追加、不改历史**） |

**为什么不能"换回原件"**：修复后的链只能与**重签后的根**对上。换回原件会令
`verify_audit_chain.py` 对 6/6 日根报 FAIL（root_hash 重算值与签名载荷不一致），
即用"恢复出证口径"换来"运行期校验失效"。裁定选择**两者并存、各司其职**。

---

## 5. 本档案的边界（**不得被误读**）

1. 本档案**不是**"修复前链完整"的证据 —— 恰恰相反，修复前链有 **6 处断点 / 15 个 seq 空洞**，
   本档案记录的 6 个根正是**由那条断裂的链**计算出来的。
2. 本档案**不能**用来恢复生产链。生产链的恢复请走
   `docs/closeout/L1_evidence/ROLLBACK.ps1`（**注意：该脚本按当前所处目录存在路径缺陷，
   见 §6**）。
3. 本档案的 `signature` 字段**仍可独立验签**（公钥 `b73dd748…`），
   即"这些根确实由该密钥在当时签署过"这一事实**未被修复破坏**；
   被修复改变的是**根值本身**（因为其叶子哈希被前向重算了）。
4. 监管/合规方若需要"未被重算的原始链"，**在本机不存在**：
   修复是**就地重算**，修复前的链只保留在
   `_ci_logs/audit_backup_20260921_035434/audit_chain.db`（修复前独立备份，sha256
   `FDAC9BFA60874E6F065BBC533CB31A58922D9C5FE5FC3706B5BABD11F719E294`，
   见 `MANIFEST.json` `pre_repair[0]`）。**该备份是"原始链"的唯一载体**。

---

## 6. ⚠️ 附带发现（TASK-09 实测，未修，因不属本任务文件所有权）

`docs/closeout/L1_evidence/ROLLBACK.ps1` 第 7 行按 `$PSScriptRoot\..\..` 反推仓库根，
但该脚本当前位于 `docs\closeout\L1_evidence\`，反推结果是 **`<repo>\docs`**（少了一层），
于是第 10–11 行读到的 `_ci_logs\LAST_AUDIT_BACKUP.txt` 路径为
`<repo>\docs\_ci_logs\LAST_AUDIT_BACKUP.txt` —— **不存在**，脚本会 `throw` 退出。

实测证据（只读求值，**未执行回滚**）：

```
PSScriptRoot=C:\Users\Administrator\agent\docs\closeout\L1_evidence
Resolved repo(join ..\..)=C:\Users\Administrator\agent\docs
derived LAST_AUDIT_BACKUP.txt=C:\Users\Administrator\agent\docs\_ci_logs\LAST_AUDIT_BACKUP.txt
Test-Path=False
```

而 `_ci_logs/LAST_AUDIT_BACKUP.txt` 内容为 `_ci_logs\audit_backup_20260921_035434`，
该目录**确实存在且完整**（含 `audit_chain.db` 17162240 字节等 14 个文件）——
即**回滚数据完好，只是脚本的相对路径写错**（脚本原置于 `_ci_logs\l1_repair\`，移档时未同步修正）。

**处置建议（本次未执行，因 `ROLLBACK.ps1` 是既有文件、不属本任务所有权）**：
把第 7 行改为 `$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..\..')).Path`，
或改为以 `$PSScriptRoot` 定位并直接读取 `<repo>\_ci_logs\LAST_AUDIT_BACKUP.txt` 的绝对路径。
在修好之前，**不要**依赖 `pwsh -File docs\closeout\L1_evidence\ROLLBACK.ps1` 做回滚。

---

## 7. 归档与可复核性

- 本说明为**新增文件**；本次任务**未替换、未修改** `daily_roots.ORIGINAL.jsonl`（§1 三项全等即证）。
- 本次任务**未改动** `data/audit/daily_roots.jsonl` 的既有 6 条（修复后哈希 `9FCD4B32…` 保持不变；
  TASK-09 的追加见 `docs/closeout/TASK-09_审计链合规口径执行记录_20260921.md`）。
- 复核入口：`python scripts/verify_audit_chain.py --roots-check all --stats`（期望 exit 0、6/6 OK）。
