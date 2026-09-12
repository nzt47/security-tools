"""v7.2 治理可观测面板数据层（TASK-S6-01）

【任务定位】
    本包是 §7「六面板」的**只读数据层**：把 S0–S5 已交付的数据设施聚合成面板可直接
    渲染的 JSON 契约。它**不新建任何数据源**，也不重复实现聚合规则——每个数字都
    来自下游已结案模块的既有产出，并逐项标注来源与公式（§0.3 口径纪律）。

    | 面板 | 数据源（已交付） |
    |---|---|
    | 消化流水线 | `agent/digestion/shadow.py`（ShadowLedger/ShadowReport）、`internalize.py`（InternalizeDecision/PromotePR）、`stage.py`（StageLedger）、`events.EV_DIGEST_STAGE` |
    | 能力地图 | `agent/descriptors/registry.py::list_with_trust()` |
    | 审批收件箱 | `agent/skills_mgmt/approval.py::ApprovalFlow` + `security/alerts.py` |
    | ROI / 成本 | `agent/observability/utc.py`、`agent/monitoring/cost_brake.py` |
    | 自愈事故 | `agent/self_healing/levels.py`（IncidentCard/list_incidents） |
    | 记忆 / 技能库 | `agent/memory/layered_store.py` + `taxonomy.recall_priority_key()` |
    | ACR/UTC/降级/逃逸/审计 | `observability/{acr,utc,model_degrade,escape}.py`、`audit/chain.py` |
    | 安全渲染 / 边界词 | `agent/guardrails/{safe_render,boundary_words}.py`（**常量单一来源**） |

【口径纪律（§0.3，机器化于 `schema.py`）】
    1. 样本 < 20 → 只披露不考核（`sample_discipline()` 自动加
       `insufficient_sample` / `disclosure_only` / `note`）；
    2. 数据源缺位**一律记 `None`**，绝不以 0 冒充（`absent()` / `metric()`）；
    3. 每个数字带数据源与公式（`metric()` 返回 `source`/`formula`）；
    4. 不可追溯的百分比禁止上屏 —— 本包的 `metric()` 是唯一出口，
       调用方无法"顺手"塞一个裸百分比。

【不做】
    - 不落盘（除审计导出流式读台账外全部只读）；
    - 不修改任何既有公开接口签名与行为；
    - 不做写动作（七动作的写路径在 `agent/server_routes/routes_ui_panels.py`
      内统一走既有审批，**不旁路**）。
"""

from __future__ import annotations

from .schema import (  # noqa: F401
    DEFAULT_MIN_DISCLOSURE_SAMPLE,
    DISCLOSURE_NOTE,
    PANEL_DATASOURCES,
    PANEL_PRIORITY,
    absent,
    min_disclosure_sample,
    panel_map,
    sample_discipline,
)

__all__ = [
    "DEFAULT_MIN_DISCLOSURE_SAMPLE",
    "DISCLOSURE_NOTE",
    "PANEL_DATASOURCES",
    "PANEL_PRIORITY",
    "absent",
    "min_disclosure_sample",
    "panel_map",
    "sample_discipline",
]
