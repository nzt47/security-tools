# 策略模拟报告（P7.2-19）

- 生成时间：`2026-09-12T01:20:57.547+08:00`
- 数据源：`C:\Windows\TEMP\policy_demo_3n4zd84b\decisions.jsonl`
- 重放窗口：`7d`（7.0 天）
- 基线策略库指纹：`3e475f5dd5207980`
- 候选策略库指纹：`041d01799d514568`

## 一、候选策略

- id / version：`ops.freeze-external-egress` / `2.0.0`
- owner：`ops`
- effect：`allow`
- match（哈希 `f3ed947e7ceb35df`）：

```json
{
  "all": [
    {
      "field": "attributes.freeze_window",
      "op": "eq",
      "value": true
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

> **需人工确认：7 条高危变更**（原 deny/ask 现 allow）。合入门禁要求逐条确认。

| 指标 | 值 |
|---|---|
| 历史决策总数 | 80 |
| 未变 | 73 |
| **deny → allow（主指标）** | **7** |
| allow → deny | 0 |
| 变更为 ask | 0 |
| ask → allow | 0 |
| ask → deny | 0 |
| 其它 | 0 |
| 重放漂移（基线重算 ≠ 历史） | 0 |

## 三、高危命中清单

| # | 变更 | 能力 | 动作 | 租户 | 角色 | 原策略 | 新策略 | 时刻 |
|---|---|---|---|---|---|---|---|---|
| 1 | `deny→allow` | `cp.demo.src.act3` | `http.post` | `t-1` | `u-3` | `ops.freeze-external-egress` | `ops.freeze-external-egress` | `2026-09-12T01:20:57.503+08:00` |
| 2 | `deny→allow` | `cp.demo.src.act4` | `http.post` | `t-2` | `u-4` | `ops.freeze-external-egress` | `ops.freeze-external-egress` | `2026-09-12T01:20:57.503+08:00` |
| 3 | `deny→allow` | `cp.demo.src.act0` | `http.post` | `t-0` | `u-5` | `ops.freeze-external-egress` | `ops.freeze-external-egress` | `2026-09-12T01:20:57.507+08:00` |
| 4 | `deny→allow` | `cp.demo.src.act1` | `http.post` | `t-1` | `u-6` | `ops.freeze-external-egress` | `ops.freeze-external-egress` | `2026-09-12T01:20:57.508+08:00` |
| 5 | `deny→allow` | `cp.demo.src.act2` | `http.post` | `t-2` | `u-0` | `ops.freeze-external-egress` | `ops.freeze-external-egress` | `2026-09-12T01:20:57.508+08:00` |
| 6 | `deny→allow` | `cp.demo.src.act3` | `http.post` | `t-0` | `u-1` | `ops.freeze-external-egress` | `ops.freeze-external-egress` | `2026-09-12T01:20:57.508+08:00` |
| 7 | `deny→allow` | `cp.demo.src.act4` | `http.post` | `t-1` | `u-2` | `ops.freeze-external-egress` | `ops.freeze-external-egress` | `2026-09-12T01:20:57.508+08:00` |

## 四、影响面

- 按能力：`{"cp.demo.src.act3": 2, "cp.demo.src.act4": 2, "cp.demo.src.act0": 1, "cp.demo.src.act1": 1, "cp.demo.src.act2": 1}`
- 按策略：`{"ops.freeze-external-egress": 7}`

## 五、合入检查项

合入门禁（`scripts/check_policy_change_gate.py`）要求：

1. 本报告**随 PR 提交**（JSON 版用于机读：`--json-out <path>.json`）；
2. 高危命中清单**逐条确认**——PR 描述 `## 高危确认` 段的**已勾选项**数量必须 ≥ 高危命中数，且每条写明策略 id（模板见 `.github/pull_request_template.md`）；
3. 若 `total=0`（无样本），PR 描述必须在 `## 无样本声明` 段**勾选**「无历史决策样本」——门禁不认可未勾选的声明，也不认可把「零变更」当作安全证据。
