# BOM 污染复发根因分析技术复盘

- **复盘日期**: 2026-08-05
- **现象**: `scripts/run_l3_regression_tests.ps1` 在数小时内被连续两次写入叠加 BOM（EF BB BF x3）
- **严重度**: 高 —— 编码契约破坏属 BLOCK 级异常，且暴露本地 hook 防线的可绕过性
- **关联**: guard_bom_pollution.py（L3 CI 防线）、maintenance_check M8、ci.yml BOM 监控步骤

---

## 1. 时间线

| 时刻 | 事件 | 证据 |
|---|---|---|
| 2026-08-05 上午 | 首次检出 `run_l3_regression_tests.ps1` 工作区叠加 BOM x3 | `python scripts/check_ps1_encoding.py` → BLOCK |
| — | 首次处置: `git restore` 恢复 HEAD（1 BOM + LF） | 编码契约恢复 BLOCK 0 |
| 数分钟后 | `guard_bom_pollution.py` 首次正常态校验**再次检出**叠加 BOM x3 | 工作区 `EF BB BF x3 + <#`，HEAD 为 `EF BB BF + <#` |
| — | 二次处置: `git restore` | 当前 BLOCK 0 |

> 关键事实：两次污染发生在同一天极短间隔内，证明这是**持续写入行为**而非一次性事故。

---

## 2. 现象特征（污染指纹）

| 维度 | HEAD（正确） | 工作区（污染） |
|---|---|---|
| 文件头 | `EF BB BF`（1 BOM）+ `<#` | `EF BB BF EF BB BF EF BB BF`（3 BOM）+ `<#` |
| 行尾 | LF (`0A`) | CRLF (`0D 0A`) |
| 正文内容 | — | 与 HEAD 一致（git diff 仅首行 BOM 差异） |

**指纹解读**：
- 叠加 BOM 是**追加式**写入的特征——写入工具对"已带 BOM 的文件"再次以"UTF-8 with BOM"编码整文件写出，于是已有 BOM + 新增 BOM 叠成 2/3 份；
- CRLF 表明写入方使用 Windows 风格换行（与 Git 仓库 LF 归一化冲突）；
- 正文一致说明这不是内容修改，是**编码重写**。

---

## 3. 根因分析（五问法）

### 3.1 直接原因
并行会话/自动提交脚本对 `scripts/` 下的 `.ps1` 文件执行了整文件编码重写（UTF-8 with BOM + CRLF），覆盖了原文件。

### 3.2 为什么 hook 拦不住（失效链路）
```
写入方（并行会话/自动脚本）
   │  直接写盘（不经 git add + commit hook 链）
   │  或 git commit --no-verify
   │  或 SKIP_ENCODING_CHECK=1 git commit
   ▼
pre-commit hook ENCODING_CHECK —— 完全绕过
   ▼
污染进入工作区/暂存区/提交
```

pre-commit hook 的生效前提是：
1. 走 `git commit`（无 `--no-verify`）；
2. 未设置 `SKIP_ENCODING_CHECK`。

两者均可被并行会话轻易满足 → **本地防线对 --no-verify 场景零防御**。

### 3.3 深层原因
1. **本地 hook 防线可绕过**：`--no-verify`/`SKIP_*` 开关是合法逃生通道，却也成为污染入口；
2. **无 CI 层自动检查**：此前 BOM 检查只存在于 hook（L1）与人工巡检（L2），污染可静默存在数小时才被发现；
3. **无受保护文件清单概念**：`check_ps1_encoding.py` 是全仓扫描，对"历史污染点"无差异化盯防与预警；
4. **多会话共享仓库无写入纪律**：并行会话/自动脚本直接改盘，绕过全部提交前检查。

### 3.4 为什么"git restore 后仍复发"
`git restore` 只修复了**这一次**的文件状态，没有改变**写入方行为**。只要写入方还在跑（且继续以 UTF-8 with BOM + CRLF 写盘），污染就会复发。这是典型的"修复症状而非根因"陷阱。

### 3.5 根本原因（一句话）
> **共享仓库的编码契约缺少 hook 之外、不可绕过的自动防线，且受保护文件没有显式清单与持续盯防。**

---

## 4. 防线分层（纵深防御，本次落地）

| 层 | 机制 | 可被绕过? | 状态 |
|---|---|---|---|
| L1 | pre-commit hook ENCODING_CHECK | 是（--no-verify / SKIP_ENCODING_CHECK） | 已有 |
| L2 | maintenance_check M3（全仓） + **M8（受保护清单，新增）** | 需人工运行 | 已有（M8 已接入） |
| L3 | **ci.yml `code-quality` job BOM 监控 step（新增）** | 否 | 本次落地 |
| L4 | `guard_bom_pollution.py` 独立脚本（--json 供 CI/脚本消费） | 否 | 已有 |

**设计要点**：
- L3 是 hook 被绕过后的**最终自动兜底**：每次 push/PR 强制检查，退出码非零即阻断；
- 受保护清单 `WATCH_DEFAULT = REQUIRE_BOM_DEFAULT + 历史污染点`，未来每次污染事件后将文件加入清单，形成"污染点 → 永久盯防"闭环。

---

## 5. 改进措施（已落地/建议）

### 已落地
1. `scripts/guard_bom_pollution.py`（77181b25）—— 复用 `ps_bom_contract` 单一事实源，`--watch` 可扩展清单；
2. `maintenance_check.py` 新增 M8 巡检项（巡检 7 → 8 项）；
3. `ci.yml` `code-quality` 新增 BOM 监控 step（本次）；
4. 回归报告归档 `regression_test_report_hook_refactor_20260805.md`。

### 建议（后续）
1. **并行会话写盘纪律**：涉及 `scripts/`、`packages/` 下 `.ps1/.psm1` 的写入应统一经 `fix_ps_bom.py --apply` 归一化，或显式检查编码；
2. **CI 失败通知**：L3 失败时应触发通知（现有 slack_notify.py 链路可复用）；
3. **清单持续扩充**：任何新污染事件发生后，把对应文件加入 `WATCH_DEFAULT`（或 CI 临时 `--watch`），防止三犯。

---

## 6. 教训

1. **hook 是"提高门槛"而非"绝对保障"**：凡有 `--no-verify` 逃生通道的防线，都必须在 CI 侧有不可绕过的对等防线；
2. **git restore 修复 ≠ 问题解决**：持续写入方不识别并干预，同款污染必然复发；
3. **污染指纹可复用**：叠加 BOM + CRLF + 正文不变 = 编码重写行为特征，后续巡检可按此模式快速甄别；
4. **受保护清单是廉价的记忆**：一次污染 → 加入清单 → 永久盯防，成本远低于反复修复。

---

*配套文档: [CI 配置修改草案：ci.yml 集成 BOM 监控步骤](../ci_guidelines/bom_pollution_ci_guard_draft_20260805.md)*
