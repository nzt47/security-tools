"""E4-2（第二期）：编排 run 的**只读投影**与结果面数据源

【不易·只投影事实，不搬正文】索引行只放「这次 run 发生了什么」：
run_id / 会话 id / 主线 / 结果码 / 耗时 / 调用次数 / 派发数 / 上限快照 / 白名单规模。
**不放**脚本源码、不放 stdout、不放进具体工具参数（这些在 run_dir 里，按需读）。

【变易·两份产物】
  - `data/orchestration/index.jsonl`：append-only 的 run 索引（结果面 / 运维用）；
  - `data/orchestration/<run_id>/run.json`：该 run 的 checkpoint（含 stderr 尾部与
    limits；同样不含源码与 stdout）——`read_file` 可直接读，不需要新工具。

【简易·读失败不影响写】索引写失败只记日志（与审计同款旁路）；读失败回空列表。
  两者都不进请求链路的关键路径：结果面是只读投影，坏了只是"看不到"。
"""
from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.orchestration.runtime import RUN_ROOT

logger = logging.getLogger(__name__)

INDEX_NAME = "index.jsonl"
CHECKPOINT_NAME = "run.json"
#: 结果面默认回带多少条（超过按时间倒序截断）
DEFAULT_RUNS_LIMIT = 20
#: 索引行里 stderr 尾部的最大字符数（与 runtime 的回带口径一致）
_CHECKPOINT_STDERR_CHARS = 2000
#: 详情面板回带的 checkpoint 字段白名单（**读侧再筛一遍**：写侧不含源码，
#: 读侧也不信"历史文件里一定没多塞东西"—— 白名单是对旧文件/手改文件的兜底）
CHECKPOINT_FIELDS = (
    "run_id", "session_id", "ts", "line", "ok", "code", "exit_code",
    "duration_ms", "calls", "state_ops", "spawned", "denied",
    "whitelist", "whitelist_size", "limits", "result",
    "stdout_bytes", "stderr_bytes", "stderr_tail", "node",
)
#: 详情里 stderr 尾部的最大字符数（比索引宽一倍，仍截断）
_DETAIL_STDERR_CHARS = 4000

_LOCK = threading.Lock()


def index_path(root: Optional[Path] = None) -> Path:
    return (root or RUN_ROOT) / INDEX_NAME


def _summary(entry: Dict[str, Any]) -> Dict[str, Any]:
    """索引行（**白名单字段**：新增字段必须显式加在这里，防"顺手把正文塞进索引"）"""
    limits = entry.get("limits") or {}
    return {
        "run_id": str(entry.get("run_id") or ""),
        "ts": str(entry.get("ts") or ""),
        "session_id": str(entry.get("session_id") or ""),
        "line": str(entry.get("line") or ""),
        "ok": bool(entry.get("ok")),
        "code": str(entry.get("code") or ""),
        "duration_ms": int(entry.get("duration_ms") or 0),
        "calls": int(entry.get("calls") or 0),
        "spawned": int(entry.get("spawned") or 0),
        "denied": int(entry.get("denied") or 0),
        "exit_code": entry.get("exit_code"),
        "whitelist_size": int(entry.get("whitelist_size") or 0),
        "limits": {k: int(v) for k, v in limits.items()} if isinstance(limits, dict) else {},
    }


def record_run(entry: Dict[str, Any], root: Optional[Path] = None) -> bool:
    """追加一条 run 索引（best-effort：失败只记日志，不影响 run 结果）"""
    row = _summary(entry)
    if not row["run_id"]:
        return False
    row["ts"] = row["ts"] or time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime())
    path = index_path(root)
    try:
        with _LOCK:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        return True
    except OSError as exc:
        logger.warning("[orchestration] run 索引写入失败: %s", type(exc).__name__)
        return False


def write_checkpoint(run_dir: Any, payload: Dict[str, Any]) -> bool:
    """把一次 run 的结果写成 `<run_dir>/run.json`（不含脚本源码）"""
    try:
        target = Path(run_dir) / CHECKPOINT_NAME
        data = {k: v for k, v in dict(payload or {}).items() if k != "script"}
        data.setdefault("ts", time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()))
        tail = str(data.get("stderr_tail") or "")
        if len(tail) > _CHECKPOINT_STDERR_CHARS:
            data["stderr_tail"] = tail[-_CHECKPOINT_STDERR_CHARS:]
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)
        return True
    except (OSError, TypeError, ValueError) as exc:
        logger.debug("[orchestration] checkpoint 写入失败: %s", type(exc).__name__)
        return False


def read_checkpoint(run_id: str, root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """读一次 run 的 checkpoint（详情面板用；**只读投影**，不含源码）

    【为什么用「列目录 + 名字等值匹配」而不是拼路径】run_id 来自查询参数，直接
    用 root / run_id / run.json 拼就是一个路径拼接面（CodeQL 的
    py/path-injection）。这里先列出 RUN_ROOT 的直接子目录，再拿**已经存在的
    目录名**做等值匹配 —— 名字不参与路径构造（与 agent/safe_paths.enumerate_path
    同一形态）。
    """
    key = str(run_id or "").strip()
    if not key.startswith("run-"):
        return None
    base = Path(root) if root is not None else RUN_ROOT
    try:
        matched = None
        for child in base.iterdir():
            if child.is_dir() and child.name == key:
                matched = child
                break
    except OSError as exc:
        logger.warning("[orchestration] checkpoint 目录读取失败: %s", type(exc).__name__)
        return None
    if matched is None:
        return None
    path = matched / CHECKPOINT_NAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug("[orchestration] checkpoint 读取失败: %s", type(exc).__name__)
        return None
    if not isinstance(data, dict):
        return None
    out: Dict[str, Any] = {k: data.get(k) for k in CHECKPOINT_FIELDS if k in data}
    tail = str(out.get("stderr_tail") or "")
    if len(tail) > _DETAIL_STDERR_CHARS:
        out["stderr_tail"] = tail[-_DETAIL_STDERR_CHARS:]
    return out


def read_runs(limit: int = DEFAULT_RUNS_LIMIT, session_id: str = "",
              root: Optional[Path] = None) -> Dict[str, Any]:
    """读 run 索引（**新的在前**；可按会话过滤）

    Returns:
        {"total": 匹配条数, "runs": [...], "path": str, "truncated": bool}
    """
    path = index_path(root)
    rows: List[Dict[str, Any]] = []
    try:
        if path.exists():
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except ValueError:
                        continue
                    if not isinstance(rec, dict):
                        continue
                    if session_id and str(rec.get("session_id") or "") != str(session_id):
                        continue
                    rows.append(rec)
    except OSError as exc:
        logger.warning("[orchestration] run 索引读取失败: %s", type(exc).__name__)
        return {"total": 0, "runs": [], "path": str(path), "truncated": False}
    rows.reverse()
    try:
        cap = max(1, min(200, int(limit)))
    except (TypeError, ValueError):
        cap = DEFAULT_RUNS_LIMIT
    return {"total": len(rows), "runs": rows[:cap], "path": str(path),
            "truncated": len(rows) > cap}


def stats(root: Optional[Path] = None) -> Dict[str, Any]:
    """结果面头部读数：总数 / 失败码分布 / 最近一次时间（只读）"""
    data = read_runs(limit=200, root=root)
    codes: Dict[str, int] = {}
    for row in data["runs"]:
        code = str(row.get("code") or "ok")
        codes[code] = codes.get(code, 0) + 1
    last = data["runs"][0]["ts"] if data["runs"] else ""
    return {"total": data["total"], "codes": codes, "last_ts": last,
            "index_path": data["path"]}


__all__ = [
    "CHECKPOINT_FIELDS", "CHECKPOINT_NAME", "DEFAULT_RUNS_LIMIT", "INDEX_NAME",
    "index_path", "read_checkpoint", "read_runs", "record_run", "stats",
    "write_checkpoint",
]
