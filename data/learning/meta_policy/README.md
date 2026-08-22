# data/learning/meta_policy — 元规则版本化存储（任务4，护栏 G1）

> 所属计划：`docs/zh/进化机制重构计划/自进化机制重构计划/进化机制理想设计.md` 任务4
> 不变式：本目录**默认只读**——任何变更走审批链（approval.py），无审批零生效；
>         不改变任何既有模块参数读取语义（环境变量 > config.yaml > 硬编码默认值）。

## 目录布局

| 路径 | 内容 | 可写 |
| --- | --- | --- |
| `schema.json` | 元规则登记表（52 项：变异策略/评估阈值/进化权重/触发条件/预算/调度/回滚/护栏开关，逐项含类型/默认/合法范围/读取点/所属护栏） | 只读（人工维护） |
| `current.json` | 当前生效版本指针与值快照（仅审批链 merge 时更新） | 审批后 |
| `pending.json` | 待审批变更（bump/rollback 产物；单槽串行；未批准零生效） | 审批后 |
| `versions/vN.json` | 不可变版本快照（v1=初始登记默认值） | 仅 bump 追加 |
| `audit.jsonl` | G3 审计（ts/event/change_id/param/old/new/approver/actor/status/approval_record_id/rollback_command） | 仅追加 |

## CLI

```bash
python -m agent.learning.meta_policy list            # 版本列表
python -m agent.learning.meta_policy status          # 护栏 G1 状态快照
python -m agent.learning.meta_policy show --version v2
python -m agent.learning.meta_policy diff            # 当前 vs 上一版本
python -m agent.learning.meta_policy validate --param schedule.evolver_interval_days=14
python -m agent.learning.meta_policy bump --param schedule.evolver_interval_days=14 \
    --description "调整进化调度间隔"                    # → 审批队列（pending，零生效）
python -m agent.learning.meta_policy approve --record appr-xxx   # 批准后生效
python -m agent.learning.meta_policy reject --record appr-xxx --reason "..."
python -m agent.learning.meta_policy rollback        # 回滚到上一版本（走审批链）
python -m agent.learning.meta_policy migrate         # 迁移预演（默认 dry-run，仅报告差异）
python -m agent.learning.meta_policy audit           # 审计记录（G3）
```

## 生效值语义（验收锚点）

- `effective_values()` / `get_effective_value(name)`：只读当前生效快照，
  **pending 版本绝不进入**（未审批零生效，单测证明）。
- 非法值回退默认（沿用 EVO 总览 §六.2 约定）；变更失败绝不阻断主流程。
- 参数**实际生效值来源不变**（.env/config.yaml/代码默认），本目录仅登记+快照+门控+查询；
  灰度迁移（`migrate`）默认 dry-run。
