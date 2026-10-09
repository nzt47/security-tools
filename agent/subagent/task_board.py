"""共享任务看板（P4「母体中枢协同」）—— 委派任务的 append-only 事件流

【为什么需要它】
'delegation_history' 记的是"每一次委派发生过什么"（一次委派 = 一条流水），
它回答不了"某个任务现在处于什么状态"：同一个逻辑任务会有第 1 轮、第 2 轮、
重试、被取代……流水逐条读起来是"发生了什么"，而看板要的是"现在怎样"。

本模块把委派落成事件流（JSONL 追加写），状态由 'task_id' 折叠（最新一条胜）：
    create（首次上板） → update（后续状态/轮次变化）
从而既保留完整审计轨迹（不可回改），又能给出"当前态"视图。

【不易】四条硬约束：
1. 母体是唯一写板者。写 API 只被母体侧模块调用：
   'SubagentLifecycleManager._record_batch'（批量 fan_out）、
   'SubagentContainer._record_delegation'（单发/UI/工具）、
   'routes_subagent'（只读消费）。分身侧（'channel.py' 起的子进程、物化出的
   task_file）拿不到也不该拿到写入口 —— 分身只能经母体回报（见配套用例 G2）。
2. append-only，绝不回改：一行一个事件，靠 'append_jsonl' 追加。改状态 = 再写
   一行，而不是改旧行；因此任何时刻的文件前缀都不可变（可证伪守卫 G3）。
3. fail-soft：写失败只告警并计入 'write_failed'，绝不抛出、绝不影响委派本身
   （委派已经跑完，不能因为"记不上账"而失败）。
4. 只记元信息：不含交付物正文（'output_text' / 'payload' / 'artifacts' 正文）
   —— 那是外来文本且体量不可控（§5.7 机制 1），权威位置仍是 trace / 执行器产物。

【变易】'path' 可注入（测试隔离，不碰仓库 'data/'）；字段集见 'TaskRecord'。
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from agent.jsonl_history import append_jsonl, count_jsonl_lines, read_jsonl_tail

logger = logging.getLogger(__name__)

#: 仓库根：本文件在 'agent/subagent/' 下 ⇒ 上溯三层（subagent → agent → 仓库根）。
#: 与 'delegation_history.py' 同口径：按 '__file__' 定位，不依赖 CWD
#: （CWD 相对路径换个启动目录就写到别处去了）。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 默认看板文件（与 'data/subagent_delegations.jsonl' 同址）
DEFAULT_PATH = os.path.join(_REPO_ROOT, "data", "subagent_task_board.jsonl")

#: 目标截断长度（与 delegation_history 同口径：看板行只用来"认出这是哪个任务"）
GOAL_MAX_CHARS = 160

#: 折叠扫描上限：一次尾部读取返回多少原始事件。默认尾窗 256 KiB，故这个数
#: 实际上就是"窗口内的全部事件"；坏行由 read_jsonl_tail 跳过。
_FOLD_SCAN_LIMIT = 100_000

#: 合法 'board_op' / 'status' 取值（供调用方与用例对拍，防止口径漂移）
BOARD_OPS = ("create", "update")
STATUSES = ("pending", "running", "done", "failed", "superseded")

#: 「被新一轮取代」状态（二次派发轮次机制的写侧使用；必须在 STATUSES 内）。
#: 由 record_outcome 在写入跟单事件前，对上一轮**再写一行** update 事件来标记。
SUPERSEDED_STATUS = "superseded"


@dataclass
class TaskRecord:
    """看板一行（一个事件）的字段 schema（字段名即对外契约）

    【为什么用 dataclass 定义字段集】'build_record' 与 'query' 的字段必须一致；
    各写一份 dict 字面量必然漂移。这里 dataclass 是唯一字段清单，
    'build_record' 与 'TaskRecord.from_row' 都从它派生。
    """

    task_id: str = ""
    board_op: str = "create"
    status: str = "pending"
    delegation_id: str = ""
    trace_id: str = ""
    parent_trace_id: str = ""
    fan_out_batch_id: str = ""
    fan_out_index: Optional[int] = None
    round: int = 1
    previous_delegation_id: str = ""
    goal: str = ""
    line: str = ""
    ok: bool = False
    error_code: str = ""
    tokens: Optional[int] = None
    duration_ms: float = 0.0
    artifact_count: int = 0
    subagent: str = ""
    source: str = ""
    created_at: str = ""
    updated_at: str = ""
    tenant_id: str = "default"
    subject_id: str = ""
    workspace_id: str = ""

    def to_dict(self) -> dict:
        """字段声明序的普通 dict（JSON 可序列化）"""
        return asdict(self)

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> "TaskRecord":
        """既有行 → 记录（缺失字段回落默认值；多余字段忽略）

        读取永远 fail-soft：老行缺字段不该让整条看板读不出来。字段类型不做强制
        转换（原样回显），只有 'round' / 'fan_out_index' 这类整型做最小归一化。
        """
        data = {f: row.get(f, fld.default) for f, fld in cls.__dataclass_fields__.items()}
        data["round"] = _round_value(data.get("round"))
        data["fan_out_index"] = _int_or_none(data.get("fan_out_index"))
        return cls(**data)


# ════════════════════════════════════════════════════════════
#  纯函数：从 (ctx, outcome) 生成记录
# ════════════════════════════════════════════════════════════


def build_record(ctx: Any, outcome: Any, *, source: str = "",
                 subagent: str = "") -> dict:
    """'(ctx, outcome)' → 看板记录字典（纯函数，便于单测；不含交付物正文）

    Args:
        ctx: 'DelegationContext'（八要素 + 标识；'metadata' 携带 line /
            fan_out_index / workspace_id 等溯源叶子字段）。
        outcome: 'ExecutionOutcome'。
        source: 委派入口（'ui' / 'tool' / 'fan_out' / 'lifecycle'）。
        subagent: 分身名（容器 'config.name'）。

    Returns:
        按 'TaskRecord' 字段集组织的 dict；'board_op' 固定 'create'
        （是否升级为 'update' 由 TaskBoard.record_outcome 结合已有板面判定）。
    """
    meta = _metadata(ctx)
    delegation_id = _text(getattr(outcome, "delegation_id", "")) or _text(
        getattr(ctx, "delegation_id", ""))
    task_id = _text(getattr(ctx, "task_id", "")) or delegation_id
    now = _now_iso()
    record = TaskRecord(
        task_id=task_id,
        board_op="create",
        status=status_from_outcome(outcome),
        delegation_id=delegation_id,
        trace_id=_text(getattr(outcome, "trace_id", "")),
        parent_trace_id=_parent_trace_id(ctx, outcome),
        fan_out_batch_id=_text(meta.get("fan_out_batch_id", "")),
        fan_out_index=_int_or_none(meta.get("fan_out_index")),
        round=_round_value(meta.get("round")),
        previous_delegation_id=_text(meta.get("previous_delegation_id", "")),
        goal=_text(getattr(ctx, "goal", ""))[:GOAL_MAX_CHARS],
        line=_text(meta.get("line", "")),
        ok=bool(getattr(outcome, "ok", False)),
        error_code=_text(getattr(outcome, "error_code", "")),
        tokens=_total_tokens(getattr(outcome, "cost", None)),
        duration_ms=round(float(getattr(outcome, "duration_ms", 0.0) or 0.0), 1),
        artifact_count=len(getattr(outcome, "artifacts", ()) or ()),
        subagent=_text(subagent),
        source=_text(source),
        created_at=now,
        updated_at=now,
        tenant_id=_text(getattr(ctx, "tenant_id", "")) or "default",
        subject_id=_text(getattr(ctx, "subject_id", "")),
        workspace_id=_text(meta.get("workspace_id", "")),
    )
    return record.to_dict()


def status_from_outcome(outcome: Any) -> str:
    """执行结果 → 看板状态：'ok' ⇒ 'done'，否则 'failed'；无结果 ⇒ 'pending'

    'superseded'（被新一轮取代）与 'running' 由未来的轮次机制显式写入，
    不在本函数的映射里臆造。
    """
    if outcome is None:
        return "pending"
    return "done" if bool(getattr(outcome, "ok", False)) else "failed"


# ════════════════════════════════════════════════════════════
#  看板（追加写 JSONL + 尾部窗口折叠读）
# ════════════════════════════════════════════════════════════


class TaskBoard:
    """共享任务看板（事件流追加写 + 按 task_id 折叠读）"""

    def __init__(self, path: Optional[str] = None):
        self._path = path or DEFAULT_PATH
        self._write_failed = 0
        self._lock = threading.Lock()

    @property
    def path(self) -> str:
        return self._path

    @property
    def write_failed(self) -> int:
        """累计写失败次数（显式计数：fail-soft 不等于静默）"""
        with self._lock:
            return self._write_failed

    def _count_write_failure(self) -> None:
        with self._lock:
            self._write_failed += 1

    # ── 写 ──

    def record(self, record: Mapping[str, Any], *, board_op: Optional[str] = None) -> bool:
        """追加一条事件（失败只告警并计数，绝不抛出）

        Args:
            record: 记录字典（通常来自 build_record）。
            board_op: 显式覆盖 'board_op'（'create' / 'update'）；省略则原样。

        Returns:
            True = 已写入；False = 未写入（调用方不应因此改变委派结果）。
        """
        try:
            row = dict(record)
            if board_op:
                row["board_op"] = str(board_op)
            ok = append_jsonl(self._path, row)
        except Exception as e:  # noqa: BLE001 记录失败绝不影响委派链路
            logger.warning("[TaskBoard] 看板事件序列化失败: %s", e)
            ok = False
        if not ok:
            self._count_write_failure()
        return ok

    def record_create(self, record: Mapping[str, Any]) -> bool:
        """显式追加一条 'board_op=create' 事件"""
        return self.record(record, board_op="create")

    def record_update(self, record: Mapping[str, Any]) -> bool:
        """显式追加一条 'board_op=update' 事件（同 task_id 的状态推进）"""
        return self.record(record, board_op="update")

    def record_outcome(self, *, ctx: Any, outcome: Any, subagent: str = "",
                       source: str = "") -> bool:
        """从一次委派的 '(ctx, outcome)' 生成并追加看板事件（写板咽喉的唯一写入口）

        首次出现的 'task_id' 记 'create'，已在板上的记 'update' —— 这正是
        "状态由 task_id 折叠（最新一条胜）"在写侧的具体含义。

        【为什么要读一次板面】判断"是否首次上板"必须知道已有状态；读走的是同一份
        fail-soft 尾窗（读不到按"首次"处理）。写侧不做任何跨行回改。

        Returns:
            True = 已写入；False = 未写入（不影响委派结果）。
        """
        try:
            row = build_record(ctx, outcome, source=source, subagent=subagent)
            tid = str(row.get("task_id") or "")
            board_op = "update" if (tid and self.has_task(tid)) else "create"
            previous_id = str(row.get("previous_delegation_id") or "")
            if previous_id:
                # 二次派发（round>=2）：先把上一轮显式标成 superseded —— append-only
                # 契约下是**再写一行 update**，绝不回改旧行；再把本轮事件写上去。
                # 两步都 fail-soft 且各自独立（取代标记写失败不影响本轮上板）。
                self._supersede(task_id=tid, previous_delegation_id=previous_id)
            return self.record(row, board_op=board_op)
        except Exception as e:  # noqa: BLE001 看板永远不阻断委派
            logger.warning("[TaskBoard] 看板记录生成失败（不影响委派）: %s", e)
            self._count_write_failure()
            return False

    def _supersede(self, *, task_id: str, previous_delegation_id: str) -> bool:
        """把上一轮委派显式标成 superseded（append-only：再写一行 update）

        【为什么由本模块写、而不是调用方】看板写 API 只被母体侧模块调用（模块
        docstring 第 1 条）；把"取代"做成 record_outcome 的内部步骤，就仍保持
        **唯一写入口**，不新增第二个写点。

        【折叠读与原始流的两种视角】写的是与本轮相同的 task_id（同一条逻辑任务）
        的 update 事件 ⇒ 折叠读时被本轮事件覆盖（最新胜，用户看到的是第二轮）；
        原始事件流里则留下"这一轮取代了谁"的显式证据（status=superseded）。
        """
        try:
            now = _now_iso()
            row = TaskRecord(
                task_id=_text(task_id), board_op="update",
                status=SUPERSEDED_STATUS,
                delegation_id=_text(previous_delegation_id),
                round=1, created_at=now, updated_at=now).to_dict()
            return self.record(row, board_op="update")
        except Exception as e:  # noqa: BLE001 取代标记失败不得影响委派或本轮上板
            logger.warning("[TaskBoard] superseded 标记写入失败（不影响委派）: %s", e)
            self._count_write_failure()
            return False

    def has_task(self, task_id: str) -> bool:
        """板上是否已有该 'task_id'（fail-soft：读不到按 False）"""
        tid = _text(task_id)
        if not tid:
            return False
        try:
            for row in read_jsonl_tail(self._path, _FOLD_SCAN_LIMIT):
                if _text(row.get("task_id")) == tid:
                    return True
        except Exception as e:  # noqa: BLE001
            logger.debug("[TaskBoard] 板面探测失败（按未上板处理）: %s", e)
        return False

    # ── 读 ──

    def find_delegation(self, delegation_id: str) -> Optional[dict]:
        """按 delegation_id 查最近一条看板事件（尾部窗口；缺失/读失败 ⇒ None）

        【用途】二次派发的**跟单基准**：路由据此校验"上一轮确实上过板"，并取出它的
        task_id / round / tenant_id / subject_id / goal（见
        agent/subagent/rounds.py 的 validate_previous / link_follow_up）。

        只读、fail-soft（读失败当"没查到" ⇒ 上层 fail-closed 拒绝跟单），**不改**
        追加写契约；调用方不因此写板（母体唯一写板者不受影响）。
        """
        target = _text(delegation_id)
        if not target:
            return None
        try:
            rows = read_jsonl_tail(self._path, _FOLD_SCAN_LIMIT)
        except Exception as e:  # noqa: BLE001
            logger.debug("[TaskBoard] 委派查找失败（按未找到处理）: %s", e)
            return None
        for row in reversed(rows):
            if isinstance(row, Mapping) and _text(row.get("delegation_id")) == target:
                return dict(row)
        return None

    def query(self, limit: int = 20) -> list[dict]:
        """折叠 'task_id' 取最新态，返回最近 'limit' 个任务（最新在前）

        【折叠口径】按文件原序（旧 → 新）逐条覆盖同一 'task_id' 的状态；
        再按"该任务最后一次出现的位置"倒序 ⇒ 最近被更新的任务在前。
        【窗口】底层只读文件尾部窗口（默认 256 KiB）；超出窗口的极旧事件不参与折叠，
        这与 'delegation_history' 的尾部窗口契约一致。
        """
        try:
            limit = max(0, int(limit))
        except (TypeError, ValueError):
            limit = 20
        if limit <= 0:
            return []
        rows = read_jsonl_tail(self._path, _FOLD_SCAN_LIMIT)
        latest: dict[str, tuple[int, dict]] = {}
        for position, row in enumerate(rows):
            tid = _text(row.get("task_id"))
            if not tid:
                continue  # 没有 task_id 的行无法归属到任何任务（坏行）
            latest[tid] = (position, row)
        ordered = [row for _, row in sorted(latest.values(),
                                            key=lambda item: item[0], reverse=True)]
        return ordered[:limit]

    def total(self) -> Optional[int]:
        """看板事件总数（未折叠）；文件过大/不可读时为 None（"未统计"≠ 0）"""
        return count_jsonl_lines(self._path)

    def snapshot(self, limit: int = 20) -> dict:
        """读面载荷：'{records, count, total, write_failed}'（供 HTTP 段直接内联）"""
        records = self.query(limit)
        return {
            "records": records,
            "count": len(records),
            "total": self.total(),
            "write_failed": self.write_failed,
        }


# ════════════════════════════════════════════════════════════
#  辅助
# ════════════════════════════════════════════════════════════


def _metadata(ctx: Any) -> Mapping[str, Any]:
    """取 'ctx.metadata'（缺失/非 Mapping 时给空表，避免调用方到处判空）"""
    meta = getattr(ctx, "metadata", None)
    return meta if isinstance(meta, Mapping) else {}


def _parent_trace_id(ctx: Any, outcome: Any) -> str:
    """父 Trace 标识：优先进执行器写回的 'outcome.trace.parent_trace_id'，
    其次契约里的 'ctx.parent_trace_id' / 'ctx.trace_id'（父链来源见 executor）。"""
    trace = getattr(outcome, "trace", None)
    if isinstance(trace, Mapping):
        value = _text(trace.get("parent_trace_id", ""))
        if value:
            return value
    return _text(getattr(ctx, "parent_trace_id", "")) or _text(
        getattr(ctx, "trace_id", ""))


def _total_tokens(cost: Any) -> Optional[int]:
    """成本记录 → 总令牌数（成本缺失/字段异常时为 None，不臆造 0）"""
    if cost is None:
        return None
    try:
        return int(cost.total_tokens)
    except (AttributeError, TypeError, ValueError):
        return None


def _round_value(value: Any) -> int:
    """轮次归一化为 '1 | 2'（缺省 1；非法值回落 1，不放大口径）"""
    try:
        return 2 if int(value) == 2 else 1
    except (TypeError, ValueError):
        return 1


def _int_or_none(value: Any) -> Optional[int]:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str:
    """任意值 → 字符串（None → ""，不产生 "None" 文本）"""
    if value is None:
        return ""
    return str(value)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


#: 进程级单例（两处写板咽喉写、路由读，共用一份；与 delegation_history 同形）
task_board = TaskBoard()

__all__ = [
    "TaskRecord", "TaskBoard", "task_board", "build_record", "status_from_outcome",
    "DEFAULT_PATH", "GOAL_MAX_CHARS", "BOARD_OPS", "STATUSES", "SUPERSEDED_STATUS",
]
