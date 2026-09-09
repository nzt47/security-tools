"""云枢 ToolDescriptor v2.1 契约层（TASK-S1-01）

独立包 agent/descriptors/（S0-01 RFC 方案 A + S0-02 C7 裁定：跨 skills_mgmt /
mcp / workflow_learning 复用），模块划分：

    models.py    — ToolDescriptor（v7.2 §3.2 九字段组）+ 枚举 + DescriptorValidationError
    validator.py — validate_descriptor()/assert_valid()：三不变量（destructive 三件套 /
                    secret 禁外部端点 / borrowed 轨迹策略）+ ID 规则 + cp.hint 注记
    registry.py  — DescriptorRegistry：注册/查询/列表、三路投票 ≥0.9 去重合并与
                   variant 分裂、alias 表、JSON 持久化、回填写入 API（update_trust/
                   mark_provenance/set_stage/set_governance）、list_with_trust 导出
    bridge.py    — 读侧桥接（MCP list_tools/内置工具/SKILL.md → descriptor 视图，
                   Skill↔Descriptor 只读映射，advisory 失败隔离，demo_pipeline）
    backfill.py  — 存量技能资产 provenance/trust/undo_hint 回填引擎（TASK-S1-02：
                   摸底表 + 确定性规则 + 分批复填/批级回滚 + NEEDS_* 待补清单 +
                   install_precheck provenance 合并分类）

对象范围（S0-02 C7）：L1 原子工具为主，L3 SKILL.md 资产以轻量 Descriptor 视图承载；
存量资产字段回填（trust/provenance/undo_hint）由 TASK-S1-02 消费本包写入 API 实施。
"""

from .models import (
    AuditLevel,
    CapabilityInfo,
    CP_HINT_KEY,
    DataClass,
    DescriptorValidationError,
    EvolutionInfo,
    EvolutionStage,
    GovernanceInfo,
    MetaInfo,
    OriginInfo,
    ProvenanceLevel,
    QualityInfo,
    RetryMode,
    RetryPolicy,
    RiskLevel,
    RuntimeInfo,
    SourceType,
    TenancyInfo,
    TenancyScope,
    ToolDescriptor,
    TrustInfo,
    extract_cp_hints,
)
from .registry import (
    MERGE_THRESHOLD,
    NAME_FLOOR,
    DescriptorRegistry,
    RegisterResult,
    name_similarity,
    normalize_name,
    schema_diff_keys,
    structural_similarity,
    text_similarity,
    three_way_vote,
)
from .bridge import (  # noqa: F401  (bridge 模块顶部未导入；此处补齐公共面)
    bridge_to_skill,
    map_skill_stage,
    register_bridge_view,
    skill_to_descriptor,
)
from .backfill import (
    classify_install_provenance,
    full_backfill,
    load_skill_assets,
    plan_backfill,
    run_backfill,
    survey_markdown,
    survey_skill_assets,
    sync_skill_descriptor,
)
from .validator import (
    DescriptorValidationResult,
    assert_valid,
    validate_descriptor,
)

__all__ = [
    # models
    "AuditLevel", "CapabilityInfo", "CP_HINT_KEY", "DataClass",
    "DescriptorValidationError", "EvolutionInfo", "EvolutionStage",
    "GovernanceInfo", "MetaInfo", "OriginInfo", "ProvenanceLevel", "QualityInfo",
    "RetryMode", "RetryPolicy", "RiskLevel", "RuntimeInfo", "SourceType",
    "TenancyInfo", "TenancyScope", "ToolDescriptor", "TrustInfo",
    "extract_cp_hints",
    # registry
    "MERGE_THRESHOLD", "NAME_FLOOR", "DescriptorRegistry", "RegisterResult",
    "name_similarity", "normalize_name", "schema_diff_keys",
    "structural_similarity", "text_similarity", "three_way_vote",
    # validator
    "DescriptorValidationResult", "assert_valid", "validate_descriptor",
    # bridge
    "bridge_to_skill", "map_skill_stage", "register_bridge_view",
    "skill_to_descriptor",
    # backfill（TASK-S1-02）
    "classify_install_provenance", "full_backfill", "load_skill_assets",
    "plan_backfill", "run_backfill", "survey_markdown", "survey_skill_assets",
    "sync_skill_descriptor",
]
