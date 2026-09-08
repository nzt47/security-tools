"""descriptors 测试共享构造器（非测试文件：python_files 不收集）

供 tests/unit/test_descriptors_*.py 复用 make_descriptor()。
"""

from typing import Any, Dict, Optional

from agent.descriptors.models import (
    CapabilityInfo,
    DataClass,
    EvolutionInfo,
    GovernanceInfo,
    MetaInfo,
    OriginInfo,
    ProvenanceLevel,
    QualityInfo,
    RiskLevel,
    RuntimeInfo,
    TenancyInfo,
    ToolDescriptor,
    TrustInfo,
)


def make_descriptor(
    cid: str = "cp.tool.demo",
    *,
    name: Optional[str] = None,
    source_type: str = "builtin",
    source_id: str = "demo-src",
    prov: Any = ProvenanceLevel.UNKNOWN,
    evidence: Optional[list] = None,
    external: bool = False,
    tenant: str = "default",
    scope: str = "project",
    risk: Any = None,
    data_class: Any = None,
    approval: bool = False,
    stage: Any = None,
    trace_policy: str = "",
    attempts: int = 0,
    undo: str = "",
    comp: str = "",
    policy_ref: str = "",
    input_schema: Optional[Dict[str, Any]] = None,
    output_schema: Optional[Dict[str, Any]] = None,
    description: str = "",
    version: str = "0.1.0",
    idempotent: bool = False,
    timeout_ms: int = 60000,
    sample_count: int = 0,
    success_rate: float = 0.0,
    manifest_ref: str = "",
    **extra: Any,
) -> ToolDescriptor:
    """构造合法 ToolDescriptor（默认 minimal、无未回填之外告警）"""
    return ToolDescriptor(
        meta=MetaInfo(id=cid, version=version),
        origin=OriginInfo(
            source_type=source_type,
            source_id=source_id,
            provenance=prov,
            evidence=list(evidence or []),
            external_endpoint=external,
            manifest_ref=manifest_ref,
        ),
        tenancy=TenancyInfo(tenant_id=tenant, scope=scope),
        capability=CapabilityInfo(
            name=name or cid.rsplit(".", 1)[-1],
            description=description,
            input_schema=input_schema if input_schema is not None else {},
            output_schema=output_schema if output_schema is not None else {},
        ),
        trust=TrustInfo(
            risk_level=risk,
            data_class=data_class,
            requires_approval=approval,
        ),
        runtime=RuntimeInfo(timeout_ms=timeout_ms, idempotent=idempotent),
        evolution=EvolutionInfo(
            stage=stage,
            trace_policy=trace_policy,
            internalize_attempts=attempts,
        ),
        quality=QualityInfo(
            sample_count=sample_count,
            success_rate=success_rate,
        ),
        governance=GovernanceInfo(
            undo_hint=undo,
            compensating_action=comp,
            policy_ref=policy_ref,
        ),
        **extra,
    )


def destructive_descriptor(cid: str = "cp.fs.delete", **over: Any) -> ToolDescriptor:
    """destructive 三件套齐全的合法描述（供合并/持久化测试）"""
    defaults: Dict[str, Any] = dict(
        name="delete", source_type="mcp", source_id="filesystem",
        prov=ProvenanceLevel.DECLARED,
        risk=RiskLevel.DESTRUCTIVE,
        data_class=DataClass.INTERNAL,
        approval=True,
        stage=None,
        undo="备份被删路径",
        comp="从 data/backups 恢复",
        description="删除文件",
        input_schema={"type": "object",
                      "properties": {"path": {"type": "string"}},
                      "required": ["path"]},
    )
    defaults.update(over)
    return make_descriptor(cid, **defaults)


def sample_skill(
    sid: str = "sample-skill",
    *,
    name: Optional[str] = None,
    category: str = "custom",
    source: str = "manual",
    status: str = "approved",
    config_schema: Optional[Dict[str, Any]] = None,
    output_schema: Optional[Dict[str, Any]] = None,
    metrics: Optional[Dict[str, Any]] = None,
    description: str = "示例技能",
) -> Dict[str, Any]:
    """Skill 存储字典（skills_mgmt 主轨同构）"""
    return {
        "id": sid,
        "name": name or sid,
        "description": description,
        "category": category,
        "source": source,
        "status": status,
        "config_schema": config_schema or {"type": "object", "properties": {}},
        "output_schema": output_schema if output_schema is not None else {},
        "metrics": metrics or {"usage_count": 0, "success_rate": 0.0},
    }
