"""DescriptorRegistry — 按 capability_id 注册/查询/列出 + 去重合并/variant + 持久化

v7.2 §3.2 ID/冲突规则落地（TASK-S1-01）：

    ID 规则：cp.<source_id>.<upstream_id>
    冲突时 schema 相同走去重合并（三路投票 ≥0.9），schema 不同走 variant；
    合并留 alias 表。

云枢裁定（确定性、可复现；宁冗余勿误合，对齐 P7.2-05 语义打标原则）：
- **合并资格三路投票**：与既有登记逐条比较，得三路相似度
  votes = {input_schema, output_schema, name_or_description}；
  仅当 **schema 同门**（input/output 结构相似度均 ≥ MERGE_THRESHOLD=0.9）且
  **三路均值 ≥0.9** 且 **name_or_description ≥0.55（同名/近名前提）** 时自动合并；
  否则若同名但 schema 不同 → variant 分裂（记录 diff keys）；不同名不同 schema
  → 独立登记（不误合）。
- **跨租户禁合并**：tenant_id 或 scope 不一致（数据不出域、偏好不跨租户，不变量 #7）
  → 一律不合并，独立登记并在 RegisterResult.warnings 说明。
- **保守合并**：risk/data_class 取更严格、requires_approval 取 OR、idempotent 取 AND、
  timeout/retry 取更宽、provenance 取更高并并集 evidence、quality 样本求和+成功率
  加权、stage 以既有非 None 者优先——不产生比单方更乐观的声明。
- 同 capability_id 重复注册 = 更新（schema 变化记 warning，漂移重探归 S3/S5）。
- 持久化 data/descriptors.json（原子写 + 损坏备份，对齐 skills_mgmt/store.py 惯例）；
  load() 对非法条目 advisory 跳过并记入 invalid_entries，不阻断其余加载。
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import threading
import time
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .models import (
    AuditLevel,
    DataClass,
    DescriptorValidationError,
    EvolutionStage,
    ProvenanceLevel,
    RiskLevel,
    SourceType,
    ToolDescriptor,
    _now_iso,
    severity_index,
    stricter,
)
from .validator import assert_valid, validate_descriptor

logger = logging.getLogger(__name__)

# 合并阈值与规则常量（可测试引用）
MERGE_THRESHOLD = 0.9        # schema/三路投票阈值（§3.2 ≥0.9）
NAME_FLOOR = 0.55            # 同名/近名前提（低于此不判合并/分裂，独立登记）
SCHEMA_VERSION = 1
_DEFAULT_STORE_PATH = Path(__file__).parent.parent.parent / "data" / "descriptors.json"
_AUDIT_CAP = 2000


# ═════════════════════════════════════════════════════════════
# 相似度原语（确定性、纯函数、无第三方依赖）
# ═════════════════════════════════════════════════════════════


_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def normalize_name(name: str) -> str:
    """名称归一：小写、非字母数字 → 空格、压缩空白。

    【S1-02 修正】纯 CJK 名称（无 ASCII 语义 token）不再被清洗为空串——
    原实现把"安全守护/上下文感知"都归为 ""，导致 name_similarity 恒 1.0，
    触发"宁冗余勿误合"原则下的误合并（5 个 persona 技能被并入同一 descriptor）。
    现保留原串小写，交由 name_similarity 走字符级比较。
    """
    if not name:
        return ""
    s = re.sub(r"\s+", " ", re.sub(r"[^0-9A-Za-z]+", " ", name.lower())).strip()
    if s:
        return s
    return name.lower().strip()


def name_similarity(a: str, b: str) -> float:
    """名称相似度 [0,1]：分词 Jaccard；无法分词时退化为编辑相似度。

    含 CJK 的名称走字符级 SequenceMatcher（token 化对单字中文名无区分度，
    且避免清洗为空导致的恒等误判）。
    """
    na, nb = normalize_name(a), normalize_name(b)
    if not na and not nb:
        return 1.0
    if not na or not nb:
        return 0.0
    if _CJK_RE.search(na) or _CJK_RE.search(nb):
        return SequenceMatcher(None, na, nb).ratio()
    ta, tb = set(na.split()), set(nb.split())
    if ta and tb:
        return len(ta & tb) / len(ta | tb)
    return SequenceMatcher(None, na, nb).ratio()


def text_similarity(a: str, b: str) -> float:
    """自由文本（description）编辑相似度 [0,1]"""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.strip().lower(), b.strip().lower()).ratio()


def _prop_signature(prop: Any) -> str:
    """属性级 JSON Schema 的规范化签名（type/items/enum/const/格式/cp.hint 计数）"""
    if not isinstance(prop, dict):
        return f"scalar:{type(prop).__name__}"
    parts: List[str] = []
    if isinstance(prop.get("type"), str):
        parts.append(f"type={prop['type']}")
    if isinstance(prop.get("items"), dict) and isinstance(prop["items"].get("type"), str):
        parts.append(f"items.type={prop['items']['type']}")
    if isinstance(prop.get("enum"), list):
        parts.append(f"enum={len(prop['enum'])}")
    if "const" in prop:
        parts.append(f"const={json.dumps(prop['const'], sort_keys=True)}")
    if isinstance(prop.get("format"), str):
        parts.append(f"format={prop['format']}")
    if "cp.hint" in prop:
        parts.append("cp.hint")
    if isinstance(prop.get("description"), str):
        parts.append("desc")  # 描述只占位计分，不影响类型判定
    return ";".join(sorted(parts))


def _schema_signatures(schema: Any) -> Dict[str, str]:
    """把 JSON Schema 摊平成 路径→签名 字典（仅计入显式声明的键）

    显式声明原则：空 dict（=无约束 schema）签名为空；仅当键出现时才计入，
    使 {} 与 {"type":"object","properties":{…}} 能区分（前者空 → 与任何
    非空 schema 相似度为 0，符合"空对空=1、空对非空=0"判定）。
    """
    sig: Dict[str, str] = {}
    if not isinstance(schema, dict):
        return sig
    if isinstance(schema.get("type"), str):
        sig["$type"] = schema["type"]
    if isinstance(schema.get("required"), list):
        sig["$required"] = ",".join(sorted(str(x) for x in schema["required"]))
    if "additionalProperties" in schema:
        sig["$additionalProperties"] = str(bool(schema["additionalProperties"]))
    props = schema.get("properties")
    if isinstance(props, dict):
        for pname, pval in props.items():
            sig[f"prop.{pname}"] = _prop_signature(pval)
            if isinstance(pval, dict) and isinstance(pval.get("properties"), dict):
                for sub, subv in pval["properties"].items():
                    sig[f"prop.{pname}.{sub}"] = _prop_signature(subv)
    return sig


def structural_similarity(a: Any, b: Any) -> float:
    """JSON Schema 结构相似度 [0,1]（属性签名集 F1；空对空=1，空对非空=0）

    用于 §3.2 "schema 相同"判定与三路投票 input/output 两路。
    """
    if isinstance(a, dict) and isinstance(b, dict):
        sa, sb = _schema_signatures(a), _schema_signatures(b)
        if not sa and not sb:
            return 1.0
        if not sa or not sb:
            return 0.0
        common = sum(1 for k, v in sa.items() if sb.get(k) == v)
        return (2.0 * common) / (len(sa) + len(sb))
    # 非 dict（应避免）：直接比较
    return 1.0 if a == b else 0.0


def schema_diff_keys(a: Any, b: Any) -> List[str]:
    """两 schema 差异路径清单（variants 记录用）"""
    sa, sb = _schema_signatures(a), _schema_signatures(b)
    keys = set(sa.keys()) | set(sb.keys())
    return sorted(k for k in keys if sa.get(k) != sb.get(k))


def three_way_vote(
    in_a: Any, out_a: Any, name_a: str, desc_a: str,
    in_b: Any, out_b: Any, name_b: str, desc_b: str,
) -> Dict[str, Any]:
    """三路投票：{input_schema, output_schema, name_or_description} 相似度 + 结论

    Returns:
        {sim_input, sim_output, sim_name_desc, mean, schema_same, merge}
    """
    sim_in = structural_similarity(in_a, in_b)
    sim_out = structural_similarity(out_a, out_b)
    sim_name = name_similarity(name_a, name_b)
    sim_desc = text_similarity(desc_a, desc_b)
    sim_name_desc = max(sim_name, sim_desc)
    mean = (sim_in + sim_out + sim_name_desc) / 3.0
    schema_same = sim_in >= MERGE_THRESHOLD and sim_out >= MERGE_THRESHOLD
    merge = (
        schema_same
        and mean >= MERGE_THRESHOLD
        and sim_name_desc >= NAME_FLOOR
    )
    return {
        "sim_input": round(sim_in, 4),
        "sim_output": round(sim_out, 4),
        "sim_name": round(sim_name, 4),
        "sim_name_desc": round(sim_name_desc, 4),
        "mean": round(mean, 4),
        "schema_same": schema_same,
        "merge": merge,
    }


# ═════════════════════════════════════════════════════════════
# 结果类型
# ═════════════════════════════════════════════════════════════


@dataclass
class RegisterResult:
    """register() 结论（供桥接层/审计对账）"""

    capability_id: str
    action: str = "created"       # created|updated|merged|variant|unchanged
    merged_from: List[str] = field(default_factory=list)   # 本次吸收的 alias id
    variant_of: Optional[str] = None                       # 分裂时的 canonical id
    votes: Optional[Dict[str, Any]] = None                 # 三路投票明细（合并时）
    warnings: List[str] = field(default_factory=list)
    reason: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "action": self.action,
            "merged_from": list(self.merged_from),
            "variant_of": self.variant_of,
            "votes": self.votes,
            "warnings": list(self.warnings),
            "reason": self.reason,
        }


def _merge_into_canonical(canonical: ToolDescriptor, incoming: ToolDescriptor) -> None:
    """保守合并：把 incoming 并入 canonical（就地更新 canonical 各组）。"""
    # origin：provenance 取更高并并集 evidence / manifest_ref 非空者优先
    if severity_index(incoming.origin.provenance) > severity_index(
            canonical.origin.provenance):
        canonical.origin.provenance = incoming.origin.provenance
    canonical.origin.evidence = sorted(
        set(canonical.origin.evidence) | set(incoming.origin.evidence))
    if incoming.origin.manifest_ref and not canonical.origin.manifest_ref:
        canonical.origin.manifest_ref = incoming.origin.manifest_ref
    canonical.origin.external_endpoint = (
        canonical.origin.external_endpoint or incoming.origin.external_endpoint)

    # trust：取更严格
    canonical.trust.risk_level = stricter(
        [canonical.trust.risk_level, incoming.trust.risk_level])
    canonical.trust.data_class = stricter(
        [canonical.trust.data_class, incoming.trust.data_class])
    canonical.trust.requires_approval = (
        canonical.trust.requires_approval or incoming.trust.requires_approval)

    # runtime：timeout/retry 取更宽；idempotent 取 AND（仅全员一致才可信）
    canonical.runtime.timeout_ms = max(
        canonical.runtime.timeout_ms, incoming.runtime.timeout_ms)
    cp, ip = canonical.runtime.retry_policy, incoming.runtime.retry_policy
    if cp.mode.value == "none" and ip.mode.value != "none":
        cp.mode, cp.max_retries, cp.backoff_ms, cp.backoff_factor = (
            ip.mode, ip.max_retries, ip.backoff_ms, ip.backoff_factor)
    else:
        cp.max_retries = max(cp.max_retries, ip.max_retries)
        cp.backoff_ms = max(cp.backoff_ms, ip.backoff_ms)
    canonical.runtime.idempotent = (
        canonical.runtime.idempotent and incoming.runtime.idempotent)

    # evolution：stage 既有非 None 优先；internalize_attempts 取 max
    if canonical.evolution.stage is None:
        canonical.evolution.stage = incoming.evolution.stage
    canonical.evolution.internalize_attempts = max(
        canonical.evolution.internalize_attempts,
        incoming.evolution.internalize_attempts)
    merged_shadow = dict(canonical.evolution.shadow_config)
    merged_shadow.update(incoming.evolution.shadow_config or {})
    canonical.evolution.shadow_config = merged_shadow
    if not canonical.evolution.trace_policy and incoming.evolution.trace_policy:
        canonical.evolution.trace_policy = incoming.evolution.trace_policy

    # quality：样本求和 + 成功率加权；p99 取更差；baseline 保留非空
    cq, iq = canonical.quality, incoming.quality
    total = cq.sample_count + iq.sample_count
    if total > 0:
        cq.success_rate = (
            cq.success_rate * cq.sample_count + iq.success_rate * iq.sample_count
        ) / total
    cq.sample_count = total
    cq.p99_latency_ms = max(cq.p99_latency_ms, iq.p99_latency_ms)
    if not cq.regression_baseline_id and iq.regression_baseline_id:
        cq.regression_baseline_id = iq.regression_baseline_id

    # governance：undo/补偿/政策引用保留非空并集；audit_level 取更高
    for attr in ("undo_hint", "compensating_action", "policy_ref"):
        cur = getattr(canonical.governance, attr)
        new = getattr(incoming.governance, attr)
        if new and new != cur:
            if cur:
                setattr(canonical.governance, attr, f"{cur} | {new}")
            else:
                setattr(canonical.governance, attr, new)
    if severity_index(canonical.governance.audit_level) < severity_index(
            incoming.governance.audit_level):
        canonical.governance.audit_level = incoming.governance.audit_level

    canonical.touch()


# ═════════════════════════════════════════════════════════════
# Registry
# ═════════════════════════════════════════════════════════════


class DescriptorRegistry:
    """capability_id → ToolDescriptor 注册表（线程安全 + 原子持久化）"""

    def __init__(self, path: Optional[Union[str, Path]] = None,
                 *, autosave: bool = True):
        self._path = Path(path) if path else _DEFAULT_STORE_PATH
        self._autosave = autosave
        self._lock = threading.RLock()
        self._descriptors: Dict[str, ToolDescriptor] = {}
        self._aliases: Dict[str, Dict[str, Any]] = {}   # alias_id → {canonical_id,...}
        self._variants: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self._audit_log: List[Dict[str, Any]] = []
        # 加载报告（advisory）
        self._invalid_entries: Dict[str, Dict[str, Any]] = {}
        self._load_warnings: List[str] = []
        self._loaded = False

    # ── 持久化 ──────────────────────────────────────────────

    def load(self) -> None:
        """从磁盘加载（缺失→空；损坏→备份重置；非法条目 advisory 跳过）"""
        with self._lock:
            self._descriptors = {}
            self._aliases = {}
            self._variants = {}
            self._audit_log = []
            self._invalid_entries = {}
            self._load_warnings = []
            if not self._path.exists():
                self._loaded = True
                return
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                if not isinstance(raw, dict):
                    raise ValueError("存储根节点必须是对象")
            except (json.JSONDecodeError, ValueError, OSError) as e:
                backup = self._path.with_suffix(".corrupted.json")
                try:
                    self._path.rename(backup)
                    self._load_warnings.append(
                        f"存储损坏已备份到 {backup}: {e}")
                    logger.warning("[DescriptorRegistry] %s", self._load_warnings[-1])
                except OSError:
                    self._load_warnings.append(f"存储损坏且备份失败: {e}")
                self._loaded = True
                return

            for cid, data in (raw.get("descriptors") or {}).items():
                try:
                    desc = ToolDescriptor.from_storage_dict(data)
                    result = validate_descriptor(desc)
                    if result.valid:
                        self._descriptors[cid] = desc
                    else:
                        self._invalid_entries[cid] = {
                            "raw": data,
                            "errors": result.errors,
                            "warnings": result.warnings,
                        }
                except Exception as e:  # noqa: BLE001
                    self._invalid_entries[cid] = {
                        "raw": data, "errors": [f"解析失败: {e}"],
                    }
            self._aliases = {
                k: v for k, v in (raw.get("aliases") or {}).items()
                if isinstance(v, dict) and v.get("canonical_id")
            }
            self._variants = raw.get("variants") or {}
            self._audit_log = list(raw.get("audit") or [])[-_AUDIT_CAP:]
            if self._invalid_entries:
                self._load_warnings.append(
                    f"跳过 {len(self._invalid_entries)} 条非法 descriptor "
                    f"(id: {', '.join(sorted(self._invalid_entries)[:10])}"
                    f"{'…' if len(self._invalid_entries) > 10 else ''})")
            self._loaded = True
            logger.info("[DescriptorRegistry] 加载完成 %s 条 (aliases=%d variants=%d)",
                        len(self._descriptors), len(self._aliases), len(self._variants))

    def save(self) -> None:
        """原子写（临时文件 + os.replace，Windows Defender 竞争重试）"""
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema_version": SCHEMA_VERSION,
                "descriptors": {
                    cid: d.to_storage_dict()
                    for cid, d in sorted(self._descriptors.items())
                },
                "aliases": dict(self._aliases),
                "variants": dict(self._variants),
                "audit": list(self._audit_log[-_AUDIT_CAP:]),
            }
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", delete=False,
                dir=str(self._path.parent), suffix=".tmp",
            ) as tmp:
                json.dump(payload, tmp, ensure_ascii=False, indent=2)
                tmp_path = tmp.name
            for attempt in range(3):
                try:
                    os.replace(tmp_path, self._path)
                    return
                except OSError:
                    if attempt == 2:
                        raise
                    time.sleep(0.1)

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def _maybe_save(self) -> None:
        if self._autosave:
            self.save()

    def _audit(self, action: str, capability_id: str, *,
               actor: str, detail: Optional[Dict[str, Any]] = None) -> None:
        entry = {
            "ts": _now_iso(),
            "action": action,
            "capability_id": capability_id,
            "actor": actor or "system",
            "detail": detail or {},
        }
        self._audit_log.append(entry)
        if len(self._audit_log) > _AUDIT_CAP:
            self._audit_log = self._audit_log[-_AUDIT_CAP:]
        # S2-02：能力台账（descriptor registry）变更属治理动作 → 同步写入链式审计
        # （与 UI/Agent 同表；内存审计环 + 链式留痕双轨，best-effort 不阻断登记）
        try:
            from agent.audit import audit as _audit_facade
            _action = action if str(action).startswith("descriptor.") \
                else f"descriptor.{action}"
            _audit_facade.record(
                _action, actor=actor or "system",
                subject=f"capability:{capability_id}",
                payload={"detail": detail or {}, "legacy": "descriptors.registry.audit"},
                source="agent", status="ok")
        except Exception as e:  # noqa: BLE001 审计失败不得影响台账操作
            logger.debug("descriptor 链式审计留痕失败 %s/%s: %s", action, capability_id, e)

    # ── 查询 ────────────────────────────────────────────────

    def resolve_alias(self, capability_id: str) -> Optional[str]:
        """alias → canonical（非 alias 原样返回自身存在性由调用方判断）"""
        self._ensure_loaded()
        with self._lock:
            if capability_id in self._aliases:
                return self._aliases[capability_id]["canonical_id"]
            return capability_id if capability_id in self._descriptors else None

    def get(self, capability_id: str) -> Optional[ToolDescriptor]:
        """按 capability_id 查询（alias 自动解析到 canonical）"""
        self._ensure_loaded()
        with self._lock:
            cid = self.resolve_alias(capability_id)
            if cid is None:
                return None
            return self._descriptors.get(cid)

    def list(self) -> List[ToolDescriptor]:
        self._ensure_loaded()
        with self._lock:
            return list(self._descriptors.values())

    def list_by_source(self, source_type: Union[SourceType, str]) -> List[ToolDescriptor]:
        st = source_type.value if isinstance(source_type, SourceType) else source_type
        return [d for d in self.list() if d.origin.source_type.value == st]

    def list_by_stage(self, stage: Optional[Union[EvolutionStage, str]]) -> List[ToolDescriptor]:
        """stage=None 时返回未入轨（stage 字段为 None）的条目"""
        want = stage.value if isinstance(stage, EvolutionStage) else stage
        return [d for d in self.list()
                if (d.evolution.stage.value if d.evolution.stage else None) == want]

    def count(self) -> int:
        self._ensure_loaded()
        with self._lock:
            return len(self._descriptors)

    def aliases_of(self, capability_id: str) -> List[str]:
        """某 canonical 的全部 alias（含方向说明见 alias_records）"""
        self._ensure_loaded()
        with self._lock:
            return sorted(a for a, rec in self._aliases.items()
                          if rec["canonical_id"] == capability_id)

    def alias_records(self) -> Dict[str, Dict[str, Any]]:
        self._ensure_loaded()
        with self._lock:
            return dict(self._aliases)

    def variants_of(self, capability_id: str) -> Dict[str, Dict[str, Any]]:
        """某 canonical 的 variant 分裂记录（capability_id → 记录）"""
        self._ensure_loaded()
        with self._lock:
            return dict(self._variants.get(capability_id, {}))

    def audit_trail(self) -> List[Dict[str, Any]]:
        self._ensure_loaded()
        with self._lock:
            return list(self._audit_log)

    def invalid_entries(self) -> Dict[str, Dict[str, Any]]:
        self._ensure_loaded()
        return dict(self._invalid_entries)

    def load_warnings(self) -> List[str]:
        self._ensure_loaded()
        return list(self._load_warnings)

    # ── 注册与冲突处理 ──────────────────────────────────────

    def register(
        self,
        descriptor: Union[ToolDescriptor, Dict[str, Any]],
        *,
        actor: str = "system",
        reason: str = "",
    ) -> RegisterResult:
        """注册/更新/合并/分裂。写前强制校验：违例抛 DescriptorValidationError。

        冲突处置（见模块 docstring 裁定）：
        同 id → updated/unchanged；同名近名 + schema 同门 + 三路投票 ≥0.9 → merged
        （旧 id 进 alias 表）；同名但 schema 不同 → variant 分裂；否则 → created。
        """
        desc = assert_valid(descriptor)  # 校验（I1-I4/cp.hint）+ 规整
        with self._lock:
            self._ensure_loaded()
            cid = desc.capability_id
            warnings: List[str] = []

            # 1) 同 id 重复注册 → 更新
            if cid in self._descriptors:
                existing = self._descriptors[cid]
                sim = structural_similarity(
                    desc.capability.input_schema, existing.capability.input_schema)
                new_dict = desc.to_storage_dict()
                old_dict = existing.to_storage_dict()
                # 时间戳属登记侧元数据，不参与内容等价判定
                new_dict["meta"].pop("updated_at", None)
                new_dict["meta"].pop("created_at", None)
                old_dict["meta"].pop("updated_at", None)
                old_dict["meta"].pop("created_at", None)
                if new_dict == old_dict:
                    action = "unchanged"
                else:
                    if sim < MERGE_THRESHOLD:
                        warnings.append(
                            "同 id 重复注册但 input_schema 结构差异 ≥10% "
                            "（schema 漂移，S3/S5 漂移重探处置）")
                    self._descriptors[cid] = desc
                    action = "updated"
                result = RegisterResult(cid, action=action, warnings=warnings,
                                        reason=reason)
                self._audit("descriptor.register", cid, actor=actor,
                            detail={"action": action, "reason": reason})
                self._maybe_save()
                return result

            # 2) 冲突判定：与既有登记比较
            best_merge: Optional[tuple] = None    # (cid, votes)
            best_variant: Optional[tuple] = None  # (cid, votes, diff)
            best_name_sim = 0.0
            cross_tenant_hit = False
            for other_id, other in self._descriptors.items():
                sim_name = name_similarity(
                    desc.capability.name, other.capability.name)
                if sim_name < NAME_FLOOR:
                    continue
                if (other.tenancy.tenant_id != desc.tenancy.tenant_id
                        or other.tenancy.scope != desc.tenancy.scope):
                    # 跨租户/跨 scope 不参与合并判定（不变量 #7 数据不出域）
                    cross_tenant_hit = True
                    continue
                votes = three_way_vote(
                    desc.capability.input_schema, desc.capability.output_schema,
                    desc.capability.name, desc.capability.description,
                    other.capability.input_schema, other.capability.output_schema,
                    other.capability.name, other.capability.description,
                )
                if sim_name > best_name_sim:
                    best_name_sim = sim_name
                if votes["merge"]:
                    if best_merge is None or votes["mean"] > best_merge[1]["mean"]:
                        best_merge = (other_id, votes)
                elif votes["schema_same"]:
                    pass  # schema 同门但投票不足：不误合（宁冗余）
                else:
                    diff = {
                        "input_diff_keys": schema_diff_keys(
                            desc.capability.input_schema, other.capability.input_schema),
                        "output_diff_keys": schema_diff_keys(
                            desc.capability.output_schema, other.capability.output_schema),
                    }
                    if best_variant is None or sim_name > best_variant[1].get(
                            "sim_name", 0.0):
                        best_variant = (other_id, votes, diff)

            # 3) 合并
            if best_merge is not None:
                canonical_id, votes = best_merge
                canonical = self._descriptors[canonical_id]
                if (canonical.tenancy.tenant_id != desc.tenancy.tenant_id
                        or canonical.tenancy.scope != desc.tenancy.scope):
                    # 理论不可达（候选已过滤），双保险
                    warnings.append("跨租户候选被拒合并，独立登记")
                else:
                    _merge_into_canonical(canonical, desc)
                    self._aliases[cid] = {
                        "canonical_id": canonical_id,
                        "reason": "dedupe-merge",
                        "votes": votes,
                        "created_at": _now_iso(),
                        "by": actor or "system",
                        "reason_note": reason,
                    }
                    canonical.touch()
                    self._audit("descriptor.register", cid, actor=actor, detail={
                        "action": "merged",
                        "canonical_id": canonical_id,
                        "votes": votes,
                        "reason": reason,
                    })
                    result = RegisterResult(
                        capability_id=cid,
                        action="merged",
                        merged_from=[cid],
                        votes=votes,
                        warnings=warnings,
                        reason=reason,
                    )
                    self._maybe_save()
                    return result

            # 4) variant 分裂（同名、schema 不同）
            if best_variant is not None:
                canonical_id, votes, diff = best_variant
                self._descriptors[cid] = desc
                self._variants.setdefault(canonical_id, {})[cid] = {
                    "reason": "schema_diff",
                    "input_diff_keys": diff["input_diff_keys"],
                    "output_diff_keys": diff["output_diff_keys"],
                    "votes": votes,
                    "created_at": _now_iso(),
                    "by": actor or "system",
                }
                if warnings:
                    warnings.append(f"同名近名登记 {canonical_id} 判定 variant (schema_diff)")
                self._audit("descriptor.register", cid, actor=actor, detail={
                    "action": "variant",
                    "canonical_id": canonical_id,
                    "diff": diff,
                    "reason": reason,
                })
                result = RegisterResult(cid, action="variant",
                                        variant_of=canonical_id,
                                        votes=votes, warnings=warnings,
                                        reason=reason)
                self._maybe_save()
                return result

            # 5) 独立登记
            self._descriptors[cid] = desc
            if cross_tenant_hit:
                warnings.append(
                    "存在跨租户/跨 scope 同名资产：已独立登记（不变量 #7 数据不出域）")
            if best_name_sim >= MERGE_THRESHOLD:
                warnings.append(
                    "存在同名/近名登记但三路投票未达合并阈值，独立保留（宁冗余勿误合）")
            self._audit("descriptor.register", cid, actor=actor,
                        detail={"action": "created", "reason": reason})
            result = RegisterResult(cid, action="created",
                                    warnings=warnings, reason=reason)
            self._maybe_save()
            return result

    def unregister(self, capability_id: str, *, actor: str = "system") -> bool:
        """删除登记（同时清理指向它的 alias/variant 记录与它名下的 variant）"""
        self._ensure_loaded()
        with self._lock:
            cid = self.resolve_alias(capability_id)
            if cid is None or cid not in self._descriptors:
                return False
            removed = self._descriptors.pop(cid)
            if cid in self._variants:
                self._variants.pop(cid)
            # 清理其它 canonical 名下指向本 id 的 variant 记录
            for canon, variant_map in list(self._variants.items()):
                if cid in variant_map:
                    del variant_map[cid]
                if not variant_map:
                    self._variants.pop(canon, None)
            for alias, rec in list(self._aliases.items()):
                if rec["canonical_id"] == cid:
                    del self._aliases[alias]
            self._audit("descriptor.unregister", cid, actor=actor,
                        detail={"name": removed.capability.name})
            self._maybe_save()
            return True

    # ── 回填写入 API（TASK-S1-01 步骤 3，供 S1-02 使用） ──

    def update_trust(self, capability_id: str, patch: Dict[str, Any], *,
                     actor: str = "system", reason: str = "") -> ToolDescriptor:
        """更新 trust 组（risk_level/data_class/requires_approval），写前校验不变量。

        destructive 三件套由校验器强制：仅设 risk=destructive 而未备齐 undo_hint/
        compensating_action 会抛 DescriptorValidationError——S1-02 回填时先补全
        governance（update_fields/set_governance）再标 destructive，或一次性传全。
        """
        allowed = {"risk_level", "data_class", "requires_approval"}
        unknown = set(patch) - allowed
        if unknown:
            raise DescriptorValidationError(
                [f"update_trust 仅允许键 {sorted(allowed)}，多余: {sorted(unknown)}"],
                descriptor_id=capability_id, code="TRUST_PATCH_INVALID")
        return self.update_fields(capability_id, {"trust": patch},
                                  actor=actor, reason=reason)

    def mark_provenance(self, capability_id: str, level: Union[ProvenanceLevel, str],
                        evidence: Optional[List[str]] = None, *,
                        actor: str = "system", reason: str = "", force: bool = False,
                        ) -> ToolDescriptor:
        """标记 provenance 四级（单调提升；verified/signed 必须带证据；降级需 force）

        force=False 且新级别低于现级别 → DescriptorValidationError(PROVENANCE_DOWNGRADE)
        level∈{verified, signed} 且无 evidence → DescriptorValidationError
        (PROVENANCE_NO_EVIDENCE)
        """
        new_level = level if isinstance(level, ProvenanceLevel) else ProvenanceLevel(level)
        evidence = list(evidence or [])
        with self._lock:
            self._ensure_loaded()
            cur = self.get(capability_id)
            if cur is None:
                raise DescriptorValidationError(
                    [f"capability 不存在: {capability_id}"],
                    descriptor_id=capability_id, code="NOT_FOUND")
            if new_level in (ProvenanceLevel.VERIFIED, ProvenanceLevel.SIGNED) \
                    and not evidence:
                raise DescriptorValidationError(
                    ["provenance=verified/signed 必须提供 evidence（§2.3 证据纪律）"],
                    descriptor_id=capability_id, code="PROVENANCE_NO_EVIDENCE")
            if not force and severity_index(new_level) < severity_index(
                    cur.origin.provenance):
                raise DescriptorValidationError(
                    [f"provenance 降级被拒: {cur.origin.provenance.value} → "
                     f"{new_level.value}（force=True 覆盖）"],
                    descriptor_id=capability_id, code="PROVENANCE_DOWNGRADE")
            cur.origin.provenance = new_level
            cur.origin.evidence = sorted(set(cur.origin.evidence) | set(evidence))
            cur.touch()
            self._audit("descriptor.provenance", capability_id, actor=actor,
                        detail={"level": new_level.value, "evidence": evidence,
                                "reason": reason, "forced": force})
            self._maybe_save()
            return cur

    def set_stage(self, capability_id: str,
                  stage: Optional[Union[EvolutionStage, str]], *,
                  actor: str = "system", reason: str = "",
                  trace_policy: Optional[str] = None) -> ToolDescriptor:
        """置 evolution.stage（七态/None）。borrowed 需 trace_policy（校验器强制）。

        转移条件（判定集/灰度/30 天台账）属 S3，本方法不设状态机门；
        deprecated/permanent_borrowed 亦由此写入（审计留痕）。
        """
        with self._lock:
            self._ensure_loaded()
            cur = self.get(capability_id)
            if cur is None:
                raise DescriptorValidationError(
                    [f"capability 不存在: {capability_id}"],
                    descriptor_id=capability_id, code="NOT_FOUND")
            new_stage = stage if isinstance(stage, EvolutionStage) or stage is None \
                else EvolutionStage(stage)
            old_stage = cur.evolution.stage
            cur.evolution.stage = new_stage
            if trace_policy is not None:
                cur.evolution.trace_policy = trace_policy
            cur.touch()
            try:
                assert_valid(cur)
            except DescriptorValidationError:
                cur.evolution.stage = old_stage
                raise
            self._audit("descriptor.stage", capability_id, actor=actor,
                        detail={"from": old_stage.value if old_stage else None,
                                "to": new_stage.value if new_stage else None,
                                "reason": reason})
            self._maybe_save()
            return cur

    def set_governance(self, capability_id: str, *,
                       undo_hint: Optional[str] = None,
                       compensating_action: Optional[str] = None,
                       policy_ref: Optional[str] = None,
                       audit_level: Optional[Union[AuditLevel, str]] = None,
                       actor: str = "system", reason: str = "") -> ToolDescriptor:
        """补 governance 组（S1-02 NEEDS_UNDO_HINT 待补队列处置入口）"""
        patch = {}
        gov: Dict[str, Any] = {}
        if undo_hint is not None:
            gov["undo_hint"] = undo_hint
        if compensating_action is not None:
            gov["compensating_action"] = compensating_action
        if policy_ref is not None:
            gov["policy_ref"] = policy_ref
        if audit_level is not None:
            gov["audit_level"] = audit_level.value if isinstance(
                audit_level, AuditLevel) else audit_level
        if gov:
            patch["governance"] = gov
        return self.update_fields(capability_id, patch, actor=actor, reason=reason)

    def update_fields(self, capability_id: str, patch: Dict[str, Any], *,
                      actor: str = "system", reason: str = "") -> ToolDescriptor:
        """通用组级/点路径补丁（如 {"trust": {"risk_level": "high"}} 或
        {"governance.undo_hint": "..."}）。写前全量重校验（含三不变量）。"""
        with self._lock:
            self._ensure_loaded()
            cur = self.get(capability_id)
            if cur is None:
                raise DescriptorValidationError(
                    [f"capability 不存在: {capability_id}"],
                    descriptor_id=capability_id, code="NOT_FOUND")
            data = cur.to_storage_dict()
            for key, value in patch.items():
                if "." in key:
                    head, _, tail = key.partition(".")
                    target = data.setdefault(head, {})
                    if not isinstance(target, dict):
                        raise DescriptorValidationError(
                            [f"补丁目标 {head!r} 不是对象，无法写 {key}"],
                            descriptor_id=capability_id, code="PATCH_INVALID")
                    target[tail] = value
                else:
                    if key in data and isinstance(data[key], dict) \
                            and isinstance(value, dict):
                        data[key].update(value)
                    else:
                        data[key] = value
            try:
                updated = ToolDescriptor(**data)
            except Exception as e:  # noqa: BLE001
                raise DescriptorValidationError(
                    [f"补丁后模型域校验失败: {e}"],
                    descriptor_id=capability_id, code="PATCH_INVALID") from e
            assert_valid(updated)  # 三不变量写前校验
            self._descriptors[capability_id] = updated
            self._audit("descriptor.patch", capability_id, actor=actor,
                        detail={"patch": patch, "reason": reason})
            self._maybe_save()
            return updated

    # ── 导出（供 UI 能力地图后续使用） ──────────────────────

    def list_with_trust(self) -> List[Dict[str, Any]]:
        """能力清单导出：id/名称/来源/provenance/trust/演进/治理摘要（能力地图输入）"""
        self._ensure_loaded()
        rows = []
        with self._lock:
            for d in sorted(self._descriptors.values(),
                            key=lambda x: x.capability_id):
                rows.append({
                    "capability_id": d.capability_id,
                    "name": d.capability.name,
                    "description": (d.capability.description or "")[:500],
                    "source_type": d.origin.source_type.value,
                    "source_id": d.origin.source_id,
                    "provenance": d.origin.provenance.value,
                    "risk_level": d.trust.risk_level.value if d.trust.risk_level else None,
                    "data_class": d.trust.data_class.value if d.trust.data_class else None,
                    "requires_approval": d.trust.requires_approval,
                    "stage": d.evolution.stage.value if d.evolution.stage else None,
                    "trace_policy": d.evolution.trace_policy,
                    "idempotent": d.runtime.idempotent,
                    "timeout_ms": d.runtime.timeout_ms,
                    "external_endpoint": d.origin.external_endpoint,
                    "has_undo_hint": bool(d.governance.undo_hint),
                    "has_compensating_action": bool(d.governance.compensating_action),
                    "audit_level": d.governance.audit_level.value,
                    "success_rate": d.quality.success_rate,
                    "sample_count": d.quality.sample_count,
                    "p99_latency_ms": d.quality.p99_latency_ms,
                    "aliases": self.aliases_of(d.capability_id),
                    "variant_count": len(self._variants.get(d.capability_id, {})),
                    "created_at": d.meta.created_at,
                    "updated_at": d.meta.updated_at,
                })
        return rows

    def snapshot_stats(self) -> Dict[str, Any]:
        """注册表统计快照（验收/演示对账）"""
        self._ensure_loaded()
        rows = self.list_with_trust()
        return {
            "total": len(self._descriptors),
            "aliases": len(self._aliases),
            "variants": sum(len(v) for v in self._variants.values()),
            "by_source": {
                s: len(self.list_by_source(s)) for s in
                sorted({d.origin.source_type.value
                        for d in self._descriptors.values()})
            },
            "by_stage": {
                (d.evolution.stage.value if d.evolution.stage else None):
                    len(self.list_by_stage(d.evolution.stage))
                for d in self._descriptors.values()
            },
            "destructive": sum(1 for r in rows if r["risk_level"] == "destructive"),
            "secret": sum(1 for r in rows if r["data_class"] == "secret"),
            "requires_approval": sum(1 for r in rows if r["requires_approval"]),
        }
