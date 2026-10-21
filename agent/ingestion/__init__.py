"""云枢能力获取子系统（ingestion）：吞噬 → 消化 → 吸收的**入口端**

与 `agent/digestion` 的分工：

| 入口 | 输入 | 产出 |
|---|---|---|
| `agent.digestion` | 云枢**自身**的轨迹/经验 | stage 机（borrowed→…→native） |
| `agent.ingestion` | **外来**资产（技能包/文档/提示词/JS 插件包） | 可预览的 IngestPlan → 云枢技能/工作流/工具 |

两者在「吸收」端汇合：P4 会把外来技能以 `borrowed` 入 `agent/digestion/stage.py`
的阶段机，后续的 shadow/内化门控**复用不重写**。

**import 纪律**：本包是叶子包。模型/注册表/适配器只依赖标准库与轻量 helper；
`agent.state_manager`、`agent.skills_mgmt`、`agent.audit` 等重依赖一律在
函数体内懒加载（见 `service._default_installer` / `service._audit`）。

模块清单：

- `models`：来源 / 三级判定 / 建议 / 计划 / 结果 / 领域错误；
- `registry`：按 `sniff()` 打分选适配器（内容特征优先，不按生态名硬编码）；
- `adapters/`：skill_json / skill_md / docs（P0）+ claude / openclaw / trae / zip /
  JS 插件包（P1）；
- `sources`：本机默认扫描根与候选探测（`INGEST_SCAN_ROOTS` 可覆盖）；
- `service`：`preview()` / `commit()`（commit 复用既有 install_from_zip）。

设计文档：交接单 `C:/Users/Administrator/yunshu_ingestion_handoff.md` §2-§4。
"""

from __future__ import annotations

from .models import (  # noqa: F401
    FORMS,
    FORM_LABELS,
    FORM_SKILL,
    FORM_TOOL,
    FORM_WORKFLOW,
    LEVELS,
    LEVEL_L0,
    LEVEL_L1,
    LEVEL_L2,
    LEVEL_LABELS,
    Conformance,
    ConfirmationRequired,
    IngestPlan,
    IngestResult,
    IngestSource,
    IngestionError,
    InstallFailed,
    InvalidSource,
    NormalizedSkill,
    PlanStale,
    Recommendation,
    RewriteRequired,
    SourceNotFound,
    SourceTooLarge,
    UnsupportedSource,
)
from .registry import (  # noqa: F401
    AdapterRegistry,
    Selection,
    get_registry,
    register,
    reset_registry,
    select,
)
from .sources import discover_sources, parse_scan_roots  # noqa: F401

__all__ = [
    "AdapterRegistry",
    "Conformance",
    "ConfirmationRequired",
    "FORM_LABELS",
    "FORM_SKILL",
    "FORM_TOOL",
    "FORM_WORKFLOW",
    "FORMS",
    "IngestPlan",
    "IngestResult",
    "IngestSource",
    "IngestionError",
    "InstallFailed",
    "InvalidSource",
    "LEVELS",
    "LEVEL_L0",
    "LEVEL_L1",
    "LEVEL_L2",
    "LEVEL_LABELS",
    "NormalizedSkill",
    "PlanStale",
    "Recommendation",
    "RewriteRequired",
    "Selection",
    "SourceNotFound",
    "SourceTooLarge",
    "UnsupportedSource",
    "discover_sources",
    "get_registry",
    "parse_scan_roots",
    "register",
    "reset_registry",
    "select",
]
