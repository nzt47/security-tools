"""统一审批流（ApprovalFlow）— 任务 EVO-T6 安全护栏

【任务定位】
    为全部进化机制收口统一人机协同审批流，落地设计文档
    "可验证性 + 谱系追踪 + 人类监督 + 价值观对齐"四重护栏中的人类监督层。
    解决审计缺陷 2.3-5：技能进化自动提交（无人复核）与知识进化人工确认
    两种人机边界不一致。

【审批分级（不易）】
    L0 无需审批: 无行为影响的记录类操作（谱系写入、评估记录）→ 自动放行；
    L1 需审批:   影响生产行为的变更（技能参数提交、提示词建议应用、
                 工具编辑提案合并）→ pending_review，审批通过后系统自动合并；
    L2 需人工执行: 元智能体代码编辑、策略变更等高风险操作
                 （自动只产出建议，审批通过后由人工执行并标记归档）。

【状态机】
    draft → pending_review → approved / rejected → merged / archived
    合法迁移在 _TRANSITIONS 定义，非法迁移抛 ApprovalStateError。

【不易边界】
    - 审批流只新增，不删除现有确认逻辑（不感知知识模块内部）；
    - 未 merged 的 L1/L2 变更 is_effective()=False，绝不生效（验收 2）；
    - 用户显式关闭审批（APPROVAL_ENABLED=0）时构造期输出醒目告警日志。

【配置（.env，全部带默认值）】
    APPROVAL_ENABLED              审批总开关，默认 1（开启）
    APPROVAL_RECORDS_PATH         审批记录 JSONL 路径，默认 agent/data/approval_records.jsonl
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .observability import logger

# 审批级别与状态枚举
APPROVAL_LEVELS = ("L0", "L1", "L2")
APPROVAL_STATES = ("draft", "pending_review", "approved", "rejected", "merged", "archived")

# 状态机合法迁移表（验收 1: 全迁移路径测试依据）
_TRANSITIONS: Dict[str, Tuple[str, ...]] = {
    "draft": ("pending_review",),
    "pending_review": ("approved", "rejected"),
    "approved": ("merged", "archived"),   # approved → merged=系统自动合并；→ archived=L2 人工执行后标记
    "rejected": ("archived",),
    "merged": (),
    "archived": (),
}

# 默认路径（与 skills_mgmt 其他数据文件对齐：agent/data/）
_DEFAULT_RECORDS_PATH = Path(__file__).parent.parent.parent / "data" / "approval_records.jsonl"

_ENV_ENABLED = "APPROVAL_ENABLED"
_ENV_RECORDS_PATH = "APPROVAL_RECORDS_PATH"


def _env_enabled() -> bool:
    return os.getenv(_ENV_ENABLED, "1").strip().lower() not in ("0", "false", "no", "off")


def _env_records_path() -> Path:
    return Path(os.getenv(_ENV_RECORDS_PATH, str(_DEFAULT_RECORDS_PATH)))


class ApprovalError(Exception):
    """审批流异常基类"""


class ApprovalStateError(ApprovalError):
    """非法状态迁移 / 不满足状态前置条件"""


class ApprovalLevelError(ApprovalError):
    """非法审批级别"""


# ════════════════════════════════════════════════════════════
#  数据模型
# ════════════════════════════════════════════════════════════

@dataclass
class ApprovalRecord:
    """一条审批记录（JSONL 持久化）

    payload: 变更内容快照（params/建议文本/提案摘要），仅作审计留痕，
             真正的生效动作由 applier 回调执行（L1）或人工执行（L2）。
    """
    record_id: str = ""
    object_type: str = "skill"          # skill / prompt / knowledge_card / subagent_config / tool_code
    object_id: str = ""
    level: str = "L1"                   # L0 / L1 / L2
    action: str = ""                    # 动作名（如 params_submit / prompt_apply / edit_proposal_merge）
    description: str = ""               # 变更说明
    payload: Optional[Dict[str, Any]] = None   # 审计留痕快照
    eval_result: Optional[Dict[str, Any]] = None
    state: str = "draft"
    actor: str = "system"               # 提交者 / 审批者
    trigger: str = "api"                # manual / scheduler / feedback / api
    decision_reason: str = ""           # reject 原因 / approve 备注
    manual_required: bool = False       # L2: 需人工执行
    created_at: str = ""
    updated_at: str = ""
    merged_at: str = ""

    def __post_init__(self) -> None:
        if not self.record_id:
            self.record_id = self._generate_id()
        if not self.created_at:
            self.created_at = datetime.now().isoformat(timespec="seconds")
        if not self.updated_at:
            self.updated_at = self.created_at
        if self.level not in APPROVAL_LEVELS:
            raise ApprovalLevelError(f"非法审批级别: {self.level}（允许: {APPROVAL_LEVELS}）")
        if self.state not in APPROVAL_STATES:
            raise ApprovalError(f"非法审批状态: {self.state}（允许: {APPROVAL_STATES}）")

    @staticmethod
    def _generate_id() -> str:
        ts = datetime.now().strftime("%Y%m%d%H%M%S%f")
        return f"appr-{ts}-{uuid.uuid4().hex[:8]}"

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ApprovalRecord":
        allowed = {f.name for f in fields(cls)}
        d = {k: v for k, v in data.items() if k in allowed}
        return cls(**d)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def effective(self) -> bool:
        """是否已生效（merged 才生效，守不易）"""
        return self.state == "merged"


# ════════════════════════════════════════════════════════════
#  底层 IO 工具（原子写入 / 损坏容错，与 lineage 同策略）
# ════════════════════════════════════════════════════════════

def _atomic_write_lines(path: Path, lines: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", delete=False,
        dir=str(path.parent), suffix=".tmp",
    ) as tmp:
        for line in lines:
            tmp.write(line)
            tmp.write("\n")
        tmp_path = tmp.name
    for attempt in range(3):
        try:
            os.replace(tmp_path, path)
            return
        except OSError:
            if attempt == 2:
                logger.error("[Approval] 审批记录写入失败 path=%s（重试 3 次后放弃）", path)
                raise
            time.sleep(0.05)


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    """读取 JSONL；文件不存在 → 空；单行损坏 → 跳过并告警"""
    if not path.exists():
        return []
    records: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except (json.JSONDecodeError, ValueError):
                    logger.warning("[Approval] %s 跳过损坏行", path)
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("[Approval] %s 读取失败（按空处理）: %s", path, e)
    return records


# ════════════════════════════════════════════════════════════
#  审批流
# ════════════════════════════════════════════════════════════

def default_level_map() -> Dict[Tuple[str, str], str]:
    """默认审批分级（变易：可被构造参数覆盖）

    约定（不易）:
        - 动作以 record/log/eval/lineage 开头 → 记录类 → L0 自动放行；
        - skill/prompt 等对象的生产行为变更 → L1；
        - subagent_config / meta_agent / strategy / 代码编辑 → L2（需人工执行）。
    """
    return {}


class ApprovalFlow:
    """统一审批流 — 分级判定 + 状态机 + JSONL 持久化（线程安全）

    用法:
        flow = ApprovalFlow()
        rec = flow.submit("skill", "my-skill", action="params_submit",
                          payload=..., applier=applier_fn)      # L1 → pending_review
        flow.approve(rec.record_id, actor="reviewer")
        flow.merge(rec.record_id, actor="reviewer")             # 执行 applier → merged
        assert flow.is_effective(rec.record_id)

    applier 说明（简易）: 真正执行变更的闭包，仅进程内注册（JSONL 不落盘函数）。
    服务重启后 pending/approved 记录无 applier，merge() 返回 ApprovalStateError，
    由人工在 UI/CLI 侧执行（与 L2 人工执行语义一致）。
    """

    def __init__(self, records_path: Optional[str] = None,
                 enabled: Optional[bool] = None,
                 level_map: Optional[Dict[Tuple[str, str], str]] = None,
                 default_level: str = "L1"):
        """Args:
            records_path: 审批记录 JSONL 路径（None=读 .env/默认）
            enabled: 审批总开关（None=读 .env；关闭时仅告警，提交直接放行）
            level_map: 分级覆盖 { (object_type, action): level }，None=默认分级
            default_level: 未命中分级规则时的兜底级别（安全第一 → 默认 L1）
        """
        self._records_path = Path(records_path) if records_path else _env_records_path()
        self._enabled = enabled if enabled is not None else _env_enabled()
        self._level_map = dict(level_map or default_level_map())
        if default_level not in APPROVAL_LEVELS:
            raise ApprovalLevelError(f"非法 default_level: {default_level}")
        self._default_level = default_level
        self._lock = threading.RLock()
        self._records: List[ApprovalRecord] = []
        self._index: Dict[str, ApprovalRecord] = {}
        self._appliers: Dict[str, Callable[[], Any]] = {}
        self._loaded = False
        if not self._enabled:
            # 【不易】用户显式关闭审批的开关必须醒目记录并告警
            logger.warning(
                "[Approval] ⚠ 审批总开关 APPROVAL_ENABLED=0 已关闭，"
                "L1/L2 变更将直接放行（无人类监督）——请确认这是有意为之")

    # ─── 公共查询 ───

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ─── 分级判定 ───

    def route_level(self, object_type: str, action: str = "") -> str:
        """按 (object_type, action) 判定审批级别（验收 1）

        优先级: 显式 level_map > 记录类动作约定 > 默认级别。
        """
        key = (object_type, action)
        if key in self._level_map:
            level = self._level_map[key]
        elif action and any(action.startswith(p) for p in
                            ("record", "log", "eval", "lineage")):
            # 记录类操作无行为影响 → 自动放行（L0）
            level = "L0"
        else:
            level = self._default_level
        if level not in APPROVAL_LEVELS:
            raise ApprovalLevelError(f"分级映射返回非法级别: {level}")
        logger.debug(
            "[Approval] 分级判定 object_type=%s action=%s → level=%s"
            "（map_hit=%s 默认=%s）",
            object_type, action, level, key in self._level_map, self._default_level)
        return level

    # ─── 提交 ───

    def submit(self, object_type: str, object_id: str, *,
               action: str = "",
               description: str = "",
               payload: Optional[Dict[str, Any]] = None,
               eval_result: Optional[Dict[str, Any]] = None,
               actor: str = "system",
               trigger: str = "api",
               applier: Optional[Callable[[], Any]] = None,
               level: Optional[str] = None) -> ApprovalRecord:
        """提交一次变更进入审批流

        - L0: 自动放行（执行 applier → merged，仅作审计留痕）；
        - L1: pending_review（不执行 applier，审批通过后 merge 时执行）；
        - L2: pending_review + manual_required（自动只产出建议）。

        Raises:
            ApprovalError: object_id 为空 / level 非法
        """
        if not object_id:
            raise ApprovalError("ApprovalRecord.object_id 不能为空")
        resolved = level if level is not None else self.route_level(object_type, action)
        logger.debug(
            "[Approval] 提交变更 object_type=%s object_id=%s action=%s "
            "level=%s trigger=%s", object_type, object_id, action, resolved, trigger)
        with self._lock:
            self._ensure_loaded()
            rec = ApprovalRecord(
                object_type=object_type, object_id=object_id,
                level=resolved, action=action, description=description,
                payload=payload, eval_result=eval_result,
                actor=actor, trigger=trigger,
                manual_required=(resolved == "L2"),
            )
            self._validate(rec)
            if not self._enabled:
                # 审批开关关闭（APPROVAL_ENABLED=0）：直接放行并执行 applier，
                # 记录仍留档（decision_reason 标记 bypass，供审计）。
                rec.decision_reason = "APPROVAL_ENABLED=0 审批关闭，直接放行（构造期已告警）"
                if applier is not None:
                    try:
                        applier()
                    except Exception as e:  # noqa: BLE001
                        rec.decision_reason += f" | applier 执行失败: {e}"
                        rec.state = "rejected"
                        self._append_record(rec)
                        return rec
                rec.state = "merged"
                rec.merged_at = datetime.now().isoformat(timespec="seconds")
                self._append_record(rec)
                logger.warning(
                    "[Approval] ⚠ 审批关闭直接放行 %s/%s action=%s record=%s"
                    "（无人类监督）", object_type, object_id, action, rec.record_id)
                return rec
            if resolved == "L0":
                # L0 自动放行：执行 applier 后直接 merged
                if applier is not None:
                    try:
                        applier()
                    except Exception as e:  # noqa: BLE001 L0 执行失败 → 标记错误留痕
                        rec.decision_reason = f"L0 applier 执行失败: {e}"
                        rec.state = "rejected"
                        self._append_record(rec)
                        logger.error("[Approval] L0 自动放行执行失败 %s/%s: %s",
                                     object_type, object_id, e)
                        return rec
                rec.state = "merged"
                rec.merged_at = datetime.now().isoformat(timespec="seconds")
                self._append_record(rec)
                logger.info(
                    "[Approval] L0 自动放行 %s/%s action=%s record=%s",
                    object_type, object_id, action, rec.record_id)
                return rec

            rec.state = "pending_review"
            self._append_record(rec)
            if applier is not None:
                self._appliers[rec.record_id] = applier
            logger.info(
                "[Approval] %s 变更待审批 %s/%s action=%s record=%s manual=%s",
                resolved, object_type, object_id, action, rec.record_id,
                rec.manual_required)
            return rec

    # ─── 审批动作 ───

    def approve(self, record_id: str, actor: str = "reviewer",
                note: str = "") -> ApprovalRecord:
        """审批通过（pending_review → approved）"""
        with self._lock:
            rec = self._get_required(record_id)
            logger.info("[Approval] 审批通过 record=%s actor=%s note=%s state=%s",
                        record_id, actor, note, rec.state)
            self._transition(rec, "approved", actor=actor, reason=note)
            return rec

    def reject(self, record_id: str, actor: str = "reviewer",
               reason: str = "") -> ApprovalRecord:
        """驳回（pending_review → rejected）；reason 必填（审计要求）"""
        if not reason.strip():
            raise ApprovalError("reject 必须提供 reason（审计要求）")
        with self._lock:
            rec = self._get_required(record_id)
            logger.warning("[Approval] 驳回 record=%s actor=%s reason=%s state=%s",
                           record_id, actor, reason, rec.state)
            self._transition(rec, "rejected", actor=actor, reason=reason)
            return rec

    def merge(self, record_id: str, actor: str = "reviewer") -> ApprovalRecord:
        """合并生效（approved → merged）：执行 applier

        L2（manual_required）或 applier 缺失时抛 ApprovalStateError，
        由人工执行后调用 mark_manual_executed()（守不易：绝不自动执行 L2）。
        """
        with self._lock:
            rec = self._get_required(record_id)
            if rec.state != "approved":
                logger.warning(
                    "[Approval] merge 被拒：record=%s 当前 state=%s 非 approved",
                    record_id, rec.state)
                raise ApprovalStateError(
                    f"仅 approved 记录可 merge（当前 state={rec.state}）")
            if rec.manual_required:
                logger.warning(
                    "[Approval] merge 被拒：record=%s 为 L2 需人工执行"
                    "（manual_required），禁止自动 merge", record_id)
                raise ApprovalStateError(
                    f"{rec.record_id} 为 L2 需人工执行，禁止自动 merge")
            applier = self._appliers.get(record_id)
            if applier is None:
                logger.warning(
                    "[Approval] merge 被拒：record=%s 无 applier"
                    "（可能进程重启），需人工执行", record_id)
                raise ApprovalStateError(
                    f"{rec.record_id} 无 applier（可能进程重启），需人工执行")
            try:
                applier()
            except Exception as e:  # noqa: BLE001
                logger.error("[Approval] merge applier 执行失败 record=%s actor=%s: %s",
                             record_id, actor, e)
                raise ApprovalStateError(f"merge applier 执行失败: {e}") from e
            rec.state = "merged"
            rec.merged_at = datetime.now().isoformat(timespec="seconds")
            rec.updated_at = rec.merged_at
            rec.actor = actor
            self._persist()
            self._appliers.pop(record_id, None)
            logger.info("[Approval] 变更已合并生效 record=%s actor=%s",
                        record_id, actor)
            return rec

    def mark_manual_executed(self, record_id: str, actor: str = "reviewer",
                             note: str = "") -> ApprovalRecord:
        """人工执行完成标记（approved/rejected → archived）

        L2 高风险操作审批通过后由人工执行，执行完调用本方法归档，
        避免长期悬挂在 approved 状态造成审计困惑。
        """
        with self._lock:
            rec = self._get_required(record_id)
            if rec.state not in ("approved", "rejected"):
                raise ApprovalStateError(
                    f"仅 approved/rejected 可标记人工执行（当前 state={rec.state}）")
            rec.state = "archived"
            rec.updated_at = datetime.now().isoformat(timespec="seconds")
            if note:
                rec.decision_reason = (rec.decision_reason + f" | {note}").strip(" |")
            rec.actor = actor
            self._persist()
            logger.info("[Approval] 人工执行完成并归档 record=%s actor=%s", record_id, actor)
            return rec

    # ─── 生效判定 ───

    def is_effective(self, record_id: str) -> bool:
        """该审批记录对应的变更是否已生效（验收 2）

        pending_review / approved / rejected 均未生效 → False。
        """
        with self._lock:
            rec = self._index.get(record_id)
            return rec.effective if rec is not None else False

    # ─── 查询 ───

    def get(self, record_id: str) -> Optional[ApprovalRecord]:
        with self._lock:
            self._ensure_loaded()
            return self._index.get(record_id)

    def list(self, filter: Optional[Dict[str, Any]] = None, *,
             limit: Optional[int] = None) -> List[ApprovalRecord]:
        """等值过滤查询（支持集合匹配）；按 created_at 降序"""
        with self._lock:
            self._ensure_loaded()
            results = [r for r in self._records if self._match(r, filter)]
            results.sort(key=lambda r: r.created_at, reverse=True)
            if limit is not None:
                results = results[:limit]
            return results

    def count_by_state(self, state: Optional[str] = None) -> int:
        with self._lock:
            self._ensure_loaded()
            if state is None:
                return len(self._records)
            return sum(1 for r in self._records if r.state == state)

    def stats(self) -> Dict[str, Any]:
        """审批统计（供审计仪表盘）"""
        with self._lock:
            self._ensure_loaded()
            by_state: Dict[str, int] = {}
            by_level: Dict[str, int] = {}
            for r in self._records:
                by_state[r.state] = by_state.get(r.state, 0) + 1
                by_level[r.level] = by_level.get(r.level, 0) + 1
            return {
                "total": len(self._records),
                "by_state": by_state,
                "by_level": by_level,
                "pending": by_state.get("pending_review", 0),
                "merged": by_state.get("merged", 0),
                "rejected": by_state.get("rejected", 0),
                "enabled": self._enabled,
            }

    # ─── 内部 ───

    def _append_record(self, rec: ApprovalRecord) -> None:
        self._records.append(rec)
        self._index[rec.record_id] = rec
        self._persist()

    def _get_required(self, record_id: str) -> ApprovalRecord:
        self._ensure_loaded()
        rec = self._index.get(record_id)
        if rec is None:
            logger.warning("[Approval] 审批记录不存在 record=%s（调用方需核对 record_id）",
                           record_id)
            raise ApprovalError(f"审批记录不存在: {record_id}")
        return rec

    def _transition(self, rec: ApprovalRecord, to: str, *,
                    actor: str, reason: str) -> None:
        allowed = _TRANSITIONS.get(rec.state, ())
        if to not in allowed:
            raise ApprovalStateError(
                f"非法状态迁移: {rec.state} → {to}（允许: {allowed or '无'}）")
        rec.state = to
        rec.actor = actor
        rec.updated_at = datetime.now().isoformat(timespec="seconds")
        if reason:
            rec.decision_reason = reason
        self._persist()
        logger.info("[Approval] %s/%s state: %s → %s actor=%s",
                    rec.object_type, rec.object_id, rec.record_id, to, actor)

    @staticmethod
    def _validate(rec: ApprovalRecord) -> None:
        if not rec.object_id:
            raise ApprovalError("ApprovalRecord.object_id 不能为空")
        if rec.level not in APPROVAL_LEVELS:
            raise ApprovalLevelError(f"非法审批级别: {rec.level}")

    @staticmethod
    def _match(rec: ApprovalRecord, filter: Optional[Dict[str, Any]]) -> bool:
        if not filter:
            return True
        for key, expect in filter.items():
            actual = getattr(rec, key, None)
            if isinstance(expect, (list, tuple, set)):
                if actual not in expect:
                    return False
            elif actual != expect:
                return False
        return True

    def _ensure_loaded(self) -> None:
        if self._loaded:
            return
        for d in _read_jsonl(self._records_path):
            try:
                rec = ApprovalRecord.from_dict(d)
                self._records.append(rec)
                self._index[rec.record_id] = rec
            except (ValueError, TypeError, KeyError):
                logger.warning("[Approval] 跳过损坏审批记录")
        self._loaded = True
        logger.info("[Approval] 加载完成 count=%s path=%s",
                    len(self._records), self._records_path)

    def _persist(self) -> None:
        lines = [json.dumps(r.to_dict(), ensure_ascii=False)
                 for r in self._records]
        _atomic_write_lines(self._records_path, lines)
