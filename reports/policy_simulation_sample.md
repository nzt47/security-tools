# 策略模拟报告（P7.2-19）

- 生成时间：`2026-09-12T01:20:57.513+08:00`
- 数据源：`C:\Windows\TEMP\policy_demo_3n4zd84b\decisions.jsonl`
- 重放窗口：`7d`（7.0 天）
- 基线策略库指纹：`3e475f5dd5207980`
- 候选策略库指纹：`602f6abb178d9a8d`

## 一、候选策略

- id / version：`gov.confidential-external-ask` / `2.0.0`
- owner：`governance`
- effect：`allow`
- match（哈希 `a00116ac24b430de`）：

```json
{
  "all": [
    {
      "field": "capability.trust.data_class",
      "op": "eq",
      "value": "confidential"
    },
    {
      "field": "target.external",
      "op": "eq",
      "value": true
    }
  ]
}
```

## 二、结论

> **需人工确认：9 条高危变更**（原 deny/ask 现 allow）。合入门禁要求逐条确认。

| 指标 | 值 |
|---|---|
| 历史决策总数 | 80 |
| 未变 | 71 |
| **deny → allow（主指标）** | **0** |
| allow → deny | 0 |
| 变更为 ask | 0 |
| ask → allow | 9 |
| ask → deny | 0 |
| 其它 | 0 |
| 重放漂移（基线重算 ≠ 历史） | 0 |

## 三、高危命中清单

| # | 变更 | 能力 | 动作 | 租户 | 角色 | 原策略 | 新策略 | 时刻 |
|---|---|---|---|---|---|---|---|---|
| 1 | `ask→allow` | `cp.demo.src.act2` | `http.post` | `t-0` | `u-0` | `gov.confidential-external-ask` | `gov.confidential-external-ask` | `2026-09-12T01:20:57.487+08:00` |
| 2 | `ask→allow` | `cp.demo.src.act3` | `http.post` | `t-1` | `u-1` | `gov.confidential-external-ask` | `gov.confidential-external-ask` | `2026-09-12T01:20:57.487+08:00` |
| 3 | `ask→allow` | `cp.demo.src.act4` | `http.post` | `t-2` | `u-2` | `gov.confidential-external-ask` | `gov.confidential-external-ask` | `2026-09-12T01:20:57.487+08:00` |
| 4 | `ask→allow` | `cp.demo.src.act0` | `http.post` | `t-0` | `u-3` | `gov.confidential-external-ask` | `gov.confidential-external-ask` | `2026-09-12T01:20:57.490+08:00` |
| 5 | `ask→allow` | `cp.demo.src.act1` | `http.post` | `t-1` | `u-4` | `gov.confidential-external-ask` | `gov.confidential-external-ask` | `2026-09-12T01:20:57.490+08:00` |
| 6 | `ask→allow` | `cp.demo.src.act2` | `http.post` | `t-2` | `u-5` | `gov.confidential-external-ask` | `gov.confidential-external-ask` | `2026-09-12T01:20:57.491+08:00` |
| 7 | `ask→allow` | `cp.demo.src.act3` | `http.post` | `t-0` | `u-6` | `gov.confidential-external-ask` | `gov.confidential-external-ask` | `2026-09-12T01:20:57.491+08:00` |
| 8 | `ask→allow` | `cp.demo.src.act4` | `http.post` | `t-1` | `u-0` | `gov.confidential-external-ask` | `gov.confidential-external-ask` | `2026-09-12T01:20:57.491+08:00` |
| 9 | `ask→allow` | `cp.demo.src.act0` | `http.post` | `t-2` | `u-1` | `gov.confidential-external-ask` | `gov.confidential-external-ask` | `2026-09-12T01:20:57.493+08:00` |

## 四、影响面

- 按能力：`{"cp.demo.src.act2": 2, "cp.demo.src.act3": 2, "cp.demo.src.act4": 2, "cp.demo.src.act0": 2, "cp.demo.src.act1": 1}`
- 按策略：`{"gov.confidential-external-ask": 9}`

### 策略遮蔽诊断（首个命中生效的已知代价）

- allow gov.confidential-external-ask@2.0.0 排在 deny ops.freeze-external-egress@1.0.0 之前；两者 match 若重叠，后者的 deny 不会生效

## 五、合入检查项

合入门禁（`scripts/check_policy_change_gate.py`）要求：

1. 本报告**随 PR 提交**（JSON 版用于机读：`--json-out <path>.json`）；
2. 高危命中清单**逐条确认**——PR 描述 `## 高危确认` 段的**已勾选项**数量必须 ≥ 高危命中数，且每条写明策略 id（模板见 `.github/pull_request_template.md`）；
3. 若 `total=0`（无样本），PR 描述必须在 `## 无样本声明` 段**勾选**「无历史决策样本」——门禁不认可未勾选的声明，也不认可把「零变更」当作安全证据。
