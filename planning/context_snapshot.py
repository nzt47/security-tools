"""轻量上下文快照 — 任务5 步骤2（秒级回滚旁路）

单机 JSON 快照：每轮任务执行前保存可序列化上下文，故障时 restore 还原，
实现进程内"秒级回滚"（不引入 etcd）。

设计要点（【不易】约束）：
- 快照仅覆盖可序列化上下文（任务/context/steps 摘要/token），不碰 LLM 内部状态；
- save 失败吞异常 + 单次告警，不阻断主循环（不变量）；
- 存储独立于 state_manager（其单例持锁做文件 I/O，且 state_id 路径语义不匹配
  多文件快照场景）；命名遵循 p6_snapshot 风格，后续可切存储后端。
"""

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

SNAPSHOT_ROOT = Path("data/snapshots")
KEEP_N = 20                 # 每会话保留快照份数（配置可调）
MAX_BYTES = 256 * 1024      # 单快照序列化大小上限，超过降级"仅存摘要"
_VALUE_CAP = 80             # 摘要/降级模式下的标量截断长度
_MAX_DEPTH = 32             # 序列化递归深度防御（防深嵌套炸栈）

_MARKS = ("good", "bad", "unknown")


def _log(action: str, **kw) -> None:
    """结构化日志：module_name + action（任务5 验收7：snapshot_restored 含 step_index）"""
    kw.update({"module_name": "context_snapshot", "action": action})
    logger.info(json.dumps(kw, ensure_ascii=False, default=str))


@dataclass
class Snapshot:
    """单步上下文快照"""
    session_id: str
    step_index: int
    task: str
    context: Dict[str, Any]        # 已序列化（_serialize 预处理）
    steps_summary: str
    token_used: int
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    mark: str = "unknown"          # good/bad/unknown（恢复点标记）
    degraded: bool = False         # 序列化超 MAX_BYTES 时 True（仅存摘要）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Snapshot":
        return cls(**{k: d[k] for k in cls.__dataclass_fields__ if k in d})


def save_snapshot(
    session_id: str,
    step_index: int,
    task: str,
    context: Dict[str, Any],
    steps: List[Any],
    token_used: int,
    snapshot_root: Path = SNAPSHOT_ROOT,
    keep: int = KEEP_N,
    max_bytes: int = MAX_BYTES,
) -> str:
    """保存快照，返回 snapshot_id（"{session_id}/step_{index}"）；失败返回 ""（不抛异常）。

    保存成功后触发轮转（保留最近 keep 份）；序列化超 max_bytes 时降级"仅存摘要"。
    """
    snap = Snapshot(
        session_id=session_id,
        step_index=step_index,
        task=task,
        context=_serialize(context),
        steps_summary=_summarize_steps(steps),
        token_used=token_used,
    )
    try:
        payload = json.dumps(snap.to_dict(), ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as e:
        logger.warning(f"[context_snapshot] 序列化失败（不阻断）: {type(e).__name__}: {e}")
        return ""
    if len(payload) > max_bytes:
        # 降级：仅存摘要（键 + 值类型 + 截断预览），避免快照本身成为负担
        snap.degraded = True
        snap.context = _summarize_only(context)

    path = _snapshot_path(snapshot_root, session_id, step_index)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(snap.to_dict(), ensure_ascii=False), encoding="utf-8")
    except OSError as e:
        logger.warning(f"[context_snapshot] 保存失败（不阻断）: {type(e).__name__}: {e}")
        return ""

    _rotate(session_id, snapshot_root, keep)
    _log("saved", step_index=step_index, size_bytes=len(payload), degraded=snap.degraded)
    return f"{session_id}/step_{step_index}"


def restore_snapshot(snapshot_id: str, snapshot_root: Path = SNAPSHOT_ROOT) -> Optional[Dict[str, Any]]:
    """还原快照上下文；缺失/损坏返回 None + 告警（不抛异常）。

    返回的是快照中的 context（restore_retry 直接用其驱动下一轮）。
    """
    parsed = _parse_snapshot_id(snapshot_id)
    if parsed is None:
        logger.warning(f"[context_snapshot] 非法 snapshot_id: {snapshot_id}")
        return None
    session_id, step_index = parsed
    try:
        path = _snapshot_path(snapshot_root, session_id, step_index)
        if not path.exists():
            logger.warning(f"[context_snapshot] 快照不存在: {snapshot_id}")
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        snap = Snapshot.from_dict(data)
    except (OSError, ValueError, KeyError, TypeError) as e:
        logger.warning(f"[context_snapshot] 还原失败（不阻断）: {type(e).__name__}: {e}")
        return None
    _log("restored", step_index=snap.step_index, degraded=snap.degraded, mark=snap.mark)
    return snap.context


def list_snapshots(session_id: Optional[str] = None,
                   snapshot_root: Path = SNAPSHOT_ROOT) -> List[dict]:
    """列出快照信息（按 step_index 升序）；session_id=None 时遍历全部会话。

    损坏文件跳过（不抛异常）；返回 [{state_id, file_path, size_bytes, created_at,
    degraded, step_index}]。
    """
    base = snapshot_root / session_id if session_id else snapshot_root
    out: List[dict] = []
    if not base.exists():
        return out
    dirs = [base] if session_id else sorted([p for p in base.iterdir() if p.is_dir()])
    for d in dirs:
        for p in sorted(d.glob("step_*.json"), key=_step_key):
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                out.append({
                    "state_id": f"{p.parent.name}/{p.stem}",
                    "file_path": str(p),
                    "size_bytes": p.stat().st_size,
                    "created_at": data.get("created_at", ""),
                    "degraded": data.get("degraded", False),
                    "step_index": data.get("step_index"),
                })
            except (OSError, ValueError):
                continue
    return out


def purge_snapshots(session_id: str, keep: int = KEEP_N,
                    snapshot_root: Path = SNAPSHOT_ROOT) -> int:
    """删除最旧快照至保留 keep 份，返回删除数（轮转与运维共用）。"""
    infos = list_snapshots(session_id, snapshot_root)
    excess = len(infos) - keep
    removed = 0
    if excess > 0:
        for info in infos[:excess]:
            try:
                Path(info["file_path"]).unlink(missing_ok=True)
                removed += 1
            except OSError:
                continue
        _log("purged", removed=removed, session_id=session_id)
    return removed


def mark_snapshot(snapshot_id: str, mark: str, snapshot_root: Path = SNAPSHOT_ROOT) -> bool:
    """恢复点标记写入 meta.json（good/bad/unknown）；非法 mark/格式返回 False。"""
    if mark not in _MARKS:
        return False
    parsed = _parse_snapshot_id(snapshot_id)
    if parsed is None:
        return False
    session_id, step_index = parsed
    try:
        meta = snapshot_root / session_id / "meta.json"
        meta.parent.mkdir(parents=True, exist_ok=True)
        meta.write_text(
            json.dumps({"step_index": step_index, "mark": mark}, ensure_ascii=False),
            encoding="utf-8",
        )
        return True
    except OSError:
        return False


# ── 序列化核心 ──────────────────────────────────────────────

def _serialize(value: Any, _depth: int = 0) -> Any:
    """上下文值 → 可 JSON 化表示；dict key 排序保证摘要稳定（与 loop_detector 同语义）。"""
    if _depth > _MAX_DEPTH:
        return str(value)[:_VALUE_CAP]
    if isinstance(value, dict):
        return {str(k): _serialize(v, _depth + 1)
                for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_serialize(v, _depth + 1) for v in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)[:_VALUE_CAP]      # datetime/自定义对象等兜底截断


def _summarize_steps(steps: List[Any]) -> str:
    """步骤摘要：序列化最近 3 步（文档要点：省 token，不重传全量历史）"""
    if not steps:
        return ""
    return json.dumps([_serialize(s) for s in steps][-3:], ensure_ascii=False)[:_VALUE_CAP * 8]


def _summarize_only(context: Dict[str, Any]) -> Dict[str, Any]:
    """降级模式：键 + 值类型 + 截断预览（避免快照本身超限）"""
    return {str(k): {"type": type(v).__name__, "preview": _serialize(v)}
            for k, v in context.items()}


def _snapshot_path(root: Path, session_id: str, step_index: int) -> Path:
    return root / session_id / f"step_{step_index}.json"


def _parse_snapshot_id(snapshot_id: str) -> Optional[tuple]:
    """解析 "{session_id}/step_{index}" → (session_id, step_index)；格式非法返回 None。

    兼容 "s1/step_0"（save 返回的规范格式）与防御性容忍 "s1/0" 变体。
    """
    try:
        session_id, sep, step_str = snapshot_id.partition("/")
        if not sep or not session_id:
            return None
        step_str = step_str.removeprefix("step_")
        return session_id, int(step_str)
    except (ValueError, TypeError):
        return None


def _step_key(p: Path) -> int:
    """glob 排序键：step_12 → 12（数值序而非字典序）"""
    try:
        return int(p.stem.split("_")[1])
    except (IndexError, ValueError):
        return 0


def _rotate(session_id: str, snapshot_root: Path, keep: int) -> None:
    """保留最近 keep 份，删除更旧的（save 成功后调用）。"""
    excess = len(list_snapshots(session_id, snapshot_root)) - keep
    if excess > 0:
        purge_snapshots(session_id, keep=keep, snapshot_root=snapshot_root)
