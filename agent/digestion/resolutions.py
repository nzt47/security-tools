"""人工裁定留痕台账（TASK-S8-05 步骤 4 / D4）

## 为什么需要它（D4 的本质）

2026-09-12 人工复核暴露的关键对比：

- **"改内容"能被规则识别** —— `global-core-principles` 的 RK-5 风险项在**正文修订**后
  自动消失（触发条件真的没了，规则重算自然不再报）；
- **"改 trust 值"不能** —— 2 条 `data_class` 已由 Owner 写入 `internal`
  （`update_trust(actor="Owner")` 有审计留痕），但 needs 重算**仍报 DC-2**，
  因为 DC-2 的判据是 `is_sensitive=True` 这个**原始内容特征**，与已写入的 trust 值无关。

后果：清单**永久无法清空** ⇒ 复核疲劳（v7.2 **R11**）。修复方向不是放宽规则，
而是让"**人工裁定**"成为规则可读的**一等输入**：已裁定的不再重复提醒，未裁定的照报。

## 三条纪律（本模块的设计约束，逐条对应任务书 §五硬约束）

1. **不改写结论**：本模块只**记录**裁定，绝不回写/覆盖资产字段（写入仍走
   `registry.update_trust` 等既有唯一入口，由裁定人显式执行）。
2. **不为清空而放宽**：`apply_resolutions()` 只**跳过已裁定项**，不做任何规则豁免；
   未裁定的项**逐条照报**（`skipped` 为空时输出与从前逐字一致）。
3. **报告可见依据**：每条跳过都带 ``basis``（谁、何时、依据什么规则、理由、证据），
   在 needs 报告与复核表中单列 —— 不允许"悄悄消失"。

## 裁定语义

``ResolutionRecord`` 是**按 (scope, asset_id/case_id, rule) 三元组**的一条裁定，
追加写（JSONL，只增不改）。查询"是否已裁定"按同一三元组命中**最新一条**：

- 命中且 ``verdict=accepted`` ⇒ **已裁定**（规则不该再报）；
- 命中但 ``verdict=deferred`` ⇒ **仍要报**（"待补证据"不是裁定完成，
  如实标注 `deferred`，避免把"还没定"记成"已定"）；
- ``revoked=True`` ⇒ 该裁定**已撤销**，回到"未裁定"（规则照报）。

导入期零副作用（唯一写盘是显式构造的 `ResolutionStore`）。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("agent.digestion.resolutions")

# ════════════════════════════════════════════════════════════
#  常量（词表单点定义）
# ════════════════════════════════════════════════════════════

#: 裁定域（scope）——「这条裁定管的是哪一类待办」
SCOPE_DESCRIPTOR = "descriptor"      # 资产级字段（provenance / data_class / risk / undo_hint）
SCOPE_CASE = "case"                  # 判定集用例（抽检队列）
SCOPE_CAPABILITY = "capability"      # 能力级（灰度/内化门）
SCOPES: Tuple[str, ...] = (SCOPE_DESCRIPTOR, SCOPE_CASE, SCOPE_CAPABILITY)

#: 裁定结论
VERDICT_ACCEPTED = "accepted"        # 已裁定（规则不再重复提醒）
VERDICT_DEFERRED = "deferred"        # 待补证据（**仍要报**，只是标注为待补）
VERDICT_REJECTED = "rejected"        # 裁定不成立（回到"按规则处理"，仍然报）
VERDICTS: Tuple[str, ...] = (VERDICT_ACCEPTED, VERDICT_DEFERRED, VERDICT_REJECTED)

#: 规则 ID 词表上限（供校验，不枚举——规则由各模块定义，本模块不自建第二套规则表）
MAX_RULE_LEN = 64

#: 审计动作（D4：可追溯、可验签）
AUDIT_ACTION_RESOLUTION = "resolution.record"

#: 落盘位置（**运行时区**，gitignore；测试须显式传路径）
DEFAULT_RESOLUTION_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "descriptors")
RESOLUTION_DIR_ENV = "CP_RESOLUTION_DIR"
RESOLUTION_FILENAME = "resolutions.jsonl"

#: 台账 schema 版本（进每条记录：口径变更可辨）
RESOLUTION_SCHEMA_VERSION = 1


class ResolutionError(ValueError):
    """裁定记录不合法（不静默接受）"""


def default_resolution_path() -> str:
    """默认台账路径（``CP_RESOLUTION_DIR`` 覆盖目录；非法值回退默认）"""
    base = str(os.environ.get(RESOLUTION_DIR_ENV, "") or "").strip() \
        or DEFAULT_RESOLUTION_DIR
    return os.path.join(base, RESOLUTION_FILENAME)


# ════════════════════════════════════════════════════════════
#  记录
# ════════════════════════════════════════════════════════════


@dataclass
class ResolutionRecord:
    """一条人工裁定留痕（**追加写，不改写历史**）

    | 字段 | 语义 |
    |---|---|
    | ``scope`` | 裁定域（`SCOPES`） |
    | ``asset_id`` | 资产/能力标识（descriptor 域用；case 域可为空） |
    | ``case_id`` | 判定集用例 id（case 域用；descriptor 域为空） |
    | ``rule`` | 被裁定的规则 ID（``DC-2`` / ``PRV-5`` / ``RK-5`` …；**引用既有规则表**，本模块不新建） |
    | ``verdict`` | ``accepted`` / ``deferred`` / ``rejected`` |
    | ``decided_by`` | 裁定人（Owner / 角色） |
    | ``decided_at`` | 裁定时间（**可注入**，便于跨日复现与留痕） |
    | ``written_value`` | 裁定写入的值（如 ``data_class=internal``）；**只记录，不回写** |
    | ``reason`` | 裁定理由（人类可读，报告直接展示） |
    | ``evidence`` | 证据列表（复核记录、命令、文件等；可追溯） |
    | ``revoked`` | 是否已撤销（撤销后该三元组回到"未裁定"） |
    """

    scope: str
    rule: str
    verdict: str = VERDICT_ACCEPTED
    asset_id: str = ""
    case_id: str = ""
    capability_id: str = ""
    written_value: Any = None
    reason: str = ""
    evidence: List[str] = field(default_factory=list)
    decided_by: str = ""
    decided_at: float = 0.0
    revoked: bool = False
    source: str = ""
    schema_version: int = RESOLUTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        self.scope = str(self.scope or "").strip()
        self.rule = str(self.rule or "").strip()
        self.verdict = str(self.verdict or VERDICT_ACCEPTED).strip().lower()
        self.asset_id = str(self.asset_id or "").strip()
        self.case_id = str(self.case_id or "").strip()
        self.capability_id = str(self.capability_id or "").strip()
        self.reason = str(self.reason or "")
        self.decided_by = str(self.decided_by or "")
        self.source = str(self.source or "")
        self.evidence = [str(e) for e in (self.evidence or []) if str(e).strip()]
        if not self.decided_at:
            self.decided_at = time.time()

    # ── 校验 ────────────────────────────────────────────────

    def validate(self) -> List[str]:
        """不变量校验 → 违规理由列表（空列表 = 合法）"""
        reasons: List[str] = []
        if self.scope not in SCOPES:
            reasons.append(f"scope {self.scope!r} 不在 {SCOPES}")
        if self.verdict not in VERDICTS:
            reasons.append(f"verdict {self.verdict!r} 不在 {VERDICTS}")
        if not self.rule:
            reasons.append("rule 为空（裁定必须指向被裁定的规则，否则无法回算）")
        elif len(self.rule) > MAX_RULE_LEN:
            reasons.append(f"rule 过长（>{MAX_RULE_LEN}）：{self.rule[:40]!r}…")
        if self.scope == SCOPE_CASE and not self.case_id:
            reasons.append("case 域裁定必须给出 case_id")
        if self.scope == SCOPE_DESCRIPTOR and not (self.asset_id or self.capability_id):
            reasons.append("descriptor 域裁定必须给出 asset_id 或 capability_id")
        if not self.decided_by:
            reasons.append("decided_by 为空（无裁定人的裁定不可追溯）")
        return reasons

    def require_valid(self) -> "ResolutionRecord":
        reasons = self.validate()
        if reasons:
            raise ResolutionError(reasons)
        return self

    # ── 查询键 ──────────────────────────────────────────────

    def key(self) -> Tuple[str, str, str, str]:
        """三元组键 ``(scope, subject, rule, verdict? )`` —— 见 `subject`"""
        return (self.scope, self.subject, self.rule, self.verdict)

    @property
    def subject(self) -> str:
        """裁定主体标识（case 域用 case_id；其余用 asset_id 或 capability_id）"""
        return self.case_id or self.asset_id or self.capability_id

    @property
    def applies(self) -> bool:
        """该记录是否**生效**（``accepted`` 且未撤销 ⇒ 规则不再重复提醒）"""
        return self.verdict == VERDICT_ACCEPTED and not self.revoked

    def basis(self) -> str:
        """人类可读依据（报告/复核表直接展示 —— D4"依据可见"的载体）"""
        stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(self.decided_at))
        wrote = (f"｜写入 {self.written_value!r}" if self.written_value is not None
                 else "")
        who = self.decided_by or "（未署名）"
        text = (f"{who} 于 {stamp} 裁定 {self.rule} → {self.verdict}{wrote}"
                f"：{self.reason or '（未填理由）'}")
        if self.revoked:
            text = "[已撤销] " + text
        return text

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "scope": self.scope, "rule": self.rule, "verdict": self.verdict,
            "asset_id": self.asset_id, "case_id": self.case_id,
            "capability_id": self.capability_id,
            "written_value": self.written_value, "reason": self.reason,
            "evidence": list(self.evidence), "decided_by": self.decided_by,
            "decided_at": self.decided_at, "revoked": bool(self.revoked),
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResolutionRecord":
        known = set(cls.__dataclass_fields__)
        return cls(**{k: v for k, v in dict(data or {}).items() if k in known})


# ════════════════════════════════════════════════════════════
#  台账
# ════════════════════════════════════════════════════════════


class ResolutionStore:
    """裁定留痕台账（JSONL 追加写；同三元组以**最后一条**为准）

    用法::

        store = ResolutionStore(path=...)          # 显式路径（测试隔离）
        store.record(ResolutionRecord(
            scope=SCOPE_DESCRIPTOR, asset_id="engineering-test-delivery",
            rule="DC-2", verdict=VERDICT_ACCEPTED, written_value="internal",
            reason="通用测试流程规范，不含敏感数据；原 confidential 系自动推断误伤",
            decided_by="Owner", evidence=["人工复核裁定记录_20260912 §3.2"]))
        store.is_resolved(SCOPE_DESCRIPTOR, asset_id="engineering-test-delivery",
                          rule="DC-2")     # -> True
    """

    def __init__(self, path: str = "", *, directory: str = "",
                 filename: str = RESOLUTION_FILENAME,
                 clock: Optional[Callable[[], float]] = None,
                 audit: bool = True) -> None:
        base = str(directory or os.environ.get(RESOLUTION_DIR_ENV, "") or "")
        self.path = str(path or (os.path.join(base, filename) if base
                                 else default_resolution_path()))
        self._clock = clock or time.time
        self._audit = bool(audit)
        # 构造期不建目录（只读调用方不应有文件系统副作用）

    def _ensure_dir(self) -> None:
        parent = os.path.dirname(self.path)
        if parent:
            os.makedirs(parent, exist_ok=True)

    # ── 写入 ────────────────────────────────────────────────

    def record(self, rec: ResolutionRecord, *, audit: Optional[bool] = None,
               actor: str = "") -> ResolutionRecord:
        """追加一条裁定（**逐条校验 + 逐条审计**；校验失败抛出，不静默落库）

        审计走 `agent.audit.facade.audit.record(action="resolution.record", …)`，
        使裁定**可追溯、可验签**；审计不可用时只告警，不阻断裁定落盘
        （留痕优先：台账本身已是追加写、不可改的历史）。
        """
        rec.require_valid()
        if self._audit if audit is None else bool(audit):
            _audit_resolution(rec, actor=actor or rec.decided_by)
        try:
            self._ensure_dir()
            with open(self.path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec.to_dict(), ensure_ascii=False,
                                    default=str) + "\n")
        except OSError as e:  # pragma: no cover - 落盘失败必须可见
            logger.warning("裁定台账写入失败: %s", e)
            raise
        return rec

    def record_many(self, records: Iterable[ResolutionRecord], *,
                    audit: Optional[bool] = None, actor: str = "") -> List[ResolutionRecord]:
        return [self.record(rec, audit=audit, actor=actor) for rec in records or []]

    def revoke(self, *, scope: str, rule: str, asset_id: str = "", case_id: str = "",
               capability_id: str = "", revoked_by: str = "", reason: str = "",
               now: float = 0.0, audit: Optional[bool] = None) -> ResolutionRecord:
        """撤销一条裁定（追加一条 ``revoked=True`` 的记录；历史不删）"""
        return self.record(ResolutionRecord(
            scope=scope, rule=rule, verdict=VERDICT_ACCEPTED,
            asset_id=asset_id, case_id=case_id, capability_id=capability_id,
            reason=f"撤销：{reason or '（未填理由）'}", decided_by=revoked_by,
            decided_at=float(now or self._clock()), revoked=True,
            source="revoke"), audit=audit, actor=revoked_by)

    # ── 读取 ────────────────────────────────────────────────

    def rows(self) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            return out
        return out

    def records(self) -> List[ResolutionRecord]:
        return [ResolutionRecord.from_dict(row) for row in self.rows()]

    def latest(self) -> Dict[Tuple[str, str, str], ResolutionRecord]:
        """``(scope, subject, rule) → 最新一条``（追加写语义：最后一条即当前状态）"""
        out: Dict[Tuple[str, str, str], ResolutionRecord] = {}
        for rec in self.records():
            out[(rec.scope, rec.subject, rec.rule)] = rec
        return out

    def is_resolved(self, scope: str, *, rule: str, asset_id: str = "",
                    case_id: str = "", capability_id: str = "") -> bool:
        """该三元组是否**已裁定**（``accepted`` 且未撤销）"""
        return self.lookup(scope, rule=rule, asset_id=asset_id, case_id=case_id,
                           capability_id=capability_id) is not None

    def lookup(self, scope: str, *, rule: str, asset_id: str = "",
               case_id: str = "", capability_id: str = ""
               ) -> Optional[ResolutionRecord]:
        """取生效裁定（无 ⇒ ``None``；``deferred``/``rejected``/已撤销 ⇒ ``None``）"""
        subject = case_id or asset_id or capability_id
        rec = self.latest().get((str(scope), str(subject), str(rule)))
        if rec is None or not rec.applies:
            return None
        return rec

    def basis_for(self, scope: str, *, rule: str, asset_id: str = "",
                  case_id: str = "", capability_id: str = "") -> str:
        """依据文本（未裁定 ⇒ 空串）—— 报告"依据可见"的取值入口"""
        rec = self.lookup(scope, rule=rule, asset_id=asset_id, case_id=case_id,
                          capability_id=capability_id)
        return rec.basis() if rec is not None else ""

    def by_subject(self, subject: str) -> List[ResolutionRecord]:
        """某主体（资产/用例）的全部生效裁定（按规则排序，确定性）"""
        rows = [r for r in self.latest().values()
                if r.subject == str(subject or "") and r.applies]
        return sorted(rows, key=lambda r: (r.scope, r.rule))

    def summary(self) -> Dict[str, Any]:
        """台账总览（供报告与验收对账；口径逐项标注）"""
        latest = self.latest()
        active = [r for r in latest.values() if r.applies]
        by_scope: Dict[str, int] = {}
        by_rule: Dict[str, int] = {}
        for rec in active:
            by_scope[rec.scope] = by_scope.get(rec.scope, 0) + 1
            by_rule[rec.rule] = by_rule.get(rec.rule, 0) + 1
        return {
            "path": self.path,
            "rows": len(self.rows()),
            "triples": len(latest),
            "active": len(active),
            "by_scope": dict(sorted(by_scope.items())),
            "by_rule": dict(sorted(by_rule.items())),
            "revoked": len([r for r in latest.values() if r.revoked]),
            "deferred_or_rejected": len([r for r in latest.values()
                                         if not r.applies and not r.revoked]),
            "schema_version": RESOLUTION_SCHEMA_VERSION,
        }


# ════════════════════════════════════════════════════════════
#  审计联动（D4：每次裁定写 resolution.record）
# ════════════════════════════════════════════════════════════


def _audit_resolution(rec: ResolutionRecord, *, actor: str = "") -> Tuple[int, str]:
    """写审计（``resolution.record``）；失败只告警，不阻断裁定落盘"""
    try:
        from agent.audit.facade import audit
        entry = audit.record(
            AUDIT_ACTION_RESOLUTION, actor=str(actor or rec.decided_by or ""),
            subject=_audit_subject(rec),
            payload={"scope": rec.scope, "rule": rec.rule, "verdict": rec.verdict,
                     "written_value": rec.written_value, "reason": rec.reason,
                     "evidence": list(rec.evidence), "revoked": bool(rec.revoked),
                     "decided_by": rec.decided_by},
            source="agent",
            status=("revoked" if rec.revoked else rec.verdict))
        if entry is None:
            return 0, ""
        return (int(getattr(entry, "seq", 0) or 0),
                str(getattr(entry, "self_hash", "") or ""))
    except Exception as e:  # noqa: BLE001  审计失败不得吞掉裁定
        logger.debug("裁定审计写入失败: %s", e)
        return 0, ""


def _audit_subject(rec: ResolutionRecord) -> str:
    if rec.scope == SCOPE_CASE or rec.case_id:
        return f"case:{rec.case_id or rec.subject}"
    if rec.asset_id:
        return f"asset:{rec.asset_id}"
    return f"{rec.scope}:{rec.subject}"


# ════════════════════════════════════════════════════════════
#  规则跳过（D4：needs 重算跳过已裁定项，**不放宽规则**）
# ════════════════════════════════════════════════════════════


def apply_resolutions(needs: Sequence[Dict[str, Any]], store: Optional[ResolutionStore],
                      *, scope: str = SCOPE_DESCRIPTOR,
                      rule_field: str = "rule",
                      asset_field: str = "asset_id",
                      case_field: str = "case_id",
                      capability_field: str = "capability_id",
                      ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """把待办清单拆成 **(仍需处理, 已裁定)** 两份（**只跳过，不改判据**）

    - ``store`` 为 ``None`` 或台账为空 ⇒ 第一份**逐字等于入参**（零行为变化）；
    - 命中已裁定 ⇒ 移入第二份并**附 ``basis``**（谁/何时/依据什么规则/理由/证据），
      调用方必须把它**展示出来**（"不得静默消失"）。

    本函数**不做**任何规则豁免：未命中的项原样保留（该报的仍报）。
    """
    if store is None:
        return list(needs or []), []
    remaining: List[Dict[str, Any]] = []
    resolved: List[Dict[str, Any]] = []
    for item in needs or []:
        row = dict(item)
        rec = store.lookup(
            scope, rule=str(row.get(rule_field) or ""),
            asset_id=str(row.get(asset_field) or ""),
            case_id=str(row.get(case_field) or ""),
            capability_id=str(row.get(capability_field) or ""))
        if rec is None:
            remaining.append(row)
            continue
        row["resolution"] = rec.to_dict()
        row["basis"] = rec.basis()
        resolved.append(row)
    return remaining, resolved


def enrich_needs(needs: Dict[str, Any],
                 store: Optional[ResolutionStore]) -> Dict[str, Any]:
    """needs 报告 → 附带"已裁定项及其依据"的完整报告（D4：依据可见）

    保持既有键不变（``needs_review`` / ``needs_undo_hint`` 现在是**跳过已裁定后的**
    待办），新增：

    - ``needs_review_resolved`` / ``needs_undo_hint_resolved``：已裁定项（含 ``basis``）；
    - ``resolution_summary``：台账总览；
    - ``resolution_skipped``：跳过条数（0 时报告与从前逐字一致）。
    """
    review = list(needs.get("needs_review") or [])
    undo = list(needs.get("needs_undo_hint") or [])
    review_left, review_done = apply_resolutions(review, store)
    undo_left, undo_done = apply_resolutions(undo, store)
    out = dict(needs)
    out["needs_review"] = review_left
    out["needs_undo_hint"] = undo_left
    out["needs_review_resolved"] = review_done
    out["needs_undo_hint_resolved"] = undo_done
    out["resolution_summary"] = (store.summary() if store is not None else
                                 {"active": 0, "path": "", "note": "未接入裁定台账"})
    out["resolution_skipped"] = len(review_done) + len(undo_done)
    out["resolution_note"] = (
        "已裁定项不再列入待办（D4）；依据见 *_resolved 的 basis 字段。"
        "本过滤**只跳过已裁定项，不放宽任何规则** —— 未裁定的项逐条照报。"
        if store is not None else "")
    return out


# ════════════════════════════════════════════════════════════
#  队列/复核表接入（与 `shadow.ManualReviewQueue` 同口径，避免两套判定）
# ════════════════════════════════════════════════════════════


def resolution_basis(store: Optional[ResolutionStore], *,
                     case_id: str, capability_id: str = "",
                     rules: Optional[Sequence[str]] = None) -> str:
    """用例是否已裁定 → 依据文本（未接入台账/未裁定 ⇒ 空串）

    ``rules=None`` ⇒ 匹配该用例在 ``case`` 域下的**任意**生效裁定（抽检队列的语义：
    这个用例已经被人裁过了，就不必再问一遍）。
    """
    if store is None:
        return ""
    subjects = [s for s in (str(case_id or ""), str(capability_id or "")) if s]
    for subject in subjects:
        rows = store.by_subject(subject)
        if not rows:
            continue
        if rules:
            wanted = {str(r) for r in rules}
            rows = [r for r in rows if r.rule in wanted]
        if rows:
            return "；".join(r.basis() for r in rows[:3])
    return ""


def resolution_sheet(store: Optional[ResolutionStore], *, capability_id: str = "",
                     case_ids: Optional[Iterable[str]] = None) -> str:
    """已裁定项及其依据（Markdown；复核表/报告共用，避免"看不清为什么消失"）"""
    if store is None:
        return ""
    wanted = {str(c) for c in (case_ids or []) if str(c)}
    rows: List[ResolutionRecord] = []
    for rec in store.latest().values():
        if not rec.applies:
            continue
        if capability_id and rec.capability_id and rec.capability_id != capability_id:
            if rec.subject not in wanted:
                continue
        if wanted and rec.subject not in wanted and rec.capability_id != capability_id:
            continue
        rows.append(rec)
    if not rows:
        return ""
    lines = ["## 已裁定项及其依据（不再重复提醒；裁定记录已入审计 resolution.record）",
             ""]
    for rec in sorted(rows, key=lambda r: (r.scope, r.subject, r.rule)):
        lines.append(f"- `{rec.subject}` · {rec.scope}/{rec.rule} → "
                     f"{rec.verdict}：{rec.basis()}")
        for ev in rec.evidence:
            lines.append(f"  - 证据：{ev}")
    return "\n".join(lines) + "\n"


def resolution_digest(records: Sequence[ResolutionRecord]) -> str:
    """裁定集合的稳定摘要（供重复登记检测与报告指纹；确定性）

    **排序键取 ``decided_at``**（不是整个 dict）：dict 之间不可比较，直接
    ``sorted(rows)`` 会抛 ``TypeError``。同刻的裁定再按三元组排序，保证
    "同一批裁定任意顺序输入 ⇒ 同一摘要"。
    """
    rows = sorted((r.to_dict() for r in records or []),
                  key=lambda row: (float(row.get("decided_at") or 0.0),
                                   str(row.get("scope") or ""),
                                   str(row.get("rule") or ""),
                                   str(row.get("case_id") or ""),
                                   str(row.get("asset_id") or "")))
    material = json.dumps(rows, ensure_ascii=False, sort_keys=True, default=str)
    return "res-" + hashlib.sha1(material.encode("utf-8")).hexdigest()[:12]


__all__ = [
    "ResolutionError",
    # 常量
    "SCOPE_DESCRIPTOR", "SCOPE_CASE", "SCOPE_CAPABILITY", "SCOPES",
    "VERDICT_ACCEPTED", "VERDICT_DEFERRED", "VERDICT_REJECTED", "VERDICTS",
    "AUDIT_ACTION_RESOLUTION", "DEFAULT_RESOLUTION_DIR", "RESOLUTION_DIR_ENV",
    "RESOLUTION_FILENAME", "RESOLUTION_SCHEMA_VERSION", "MAX_RULE_LEN",
    "default_resolution_path",
    # 台账
    "ResolutionRecord", "ResolutionStore",
    # 接入
    "apply_resolutions", "enrich_needs", "resolution_basis", "resolution_sheet",
    "resolution_digest",
]
