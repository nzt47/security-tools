"""技能使用台账（match / load / exec）—— 纯本地 JSONL，跨进程加锁追加。

【为什么需要】2026-10-28 实测：主轨 44 个技能的 usage_count 全 0、审计里
/execution 上报 0 次、L3 指标样本 0 ⇒「技能有没有在发挥作用」无法回答，
离线进化（min_usage=10）的候选池也恒空。已有的三层埋点要么只在进程内存
（重启归零），要么没有 skill_id（L1），所以需要一条**可查询、可持久**的台账。

【口径】
- 一行一个事件：{ts, stage, skill_id, success, latency_ms, trace_id, source, extra}；
- stage 取 match / load / exec / tool：
  · match：一次 L1 检索（skill_id = top1，extra.top_ids 记前 5 名，用于算命中率）；
  · load：一次 L2 说明加载（skill_id 必须非空）；
  · exec：一次 L3 脚本执行（success 必填）；
  · tool：一次**工具**调用（2026-10-29 补：工具名放 `tool` 字段，success/latency_ms 必填，
    extra.effect / extra.error_code 可选）。写入点是 `ToolCalling._record_unified_tool_trace`
    —— 它是成功 / ToolError / 未捕获异常 / 重试耗尽**四条终态的唯一收口**，一次调用恰好一条。
- 按日归档复用 log_archiver 的跨进程锁与 <stem>-<YYYY-MM-DD><ext> 命名，
  读端同时扫活动文件与分片，保证「历史不丢」。

【不易】写失败**永不抛给调用方**：台账是观测，不能拖垮检索/执行主链路。
取锁超时设得很短（0.15s），拿不到锁走 log_archiver 的降级无锁追加（不丢数据）。
"""
from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from .log_archiver import append_jsonl_locked, archive_daily_file

_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_PATH = _ROOT / "data" / "skill_usage_events.jsonl"
_ENV_PATH = "CP_SKILL_USAGE_LOG"

#: 台账写锁等待上限（秒）。观测是旁路：宁可降级无锁追加，也不阻塞检索/执行主链路。
#: 放在模块常量里而不是直接写字面量 —— 仓库的「硬编码边界」门禁只认调用点字面量，
#: 而这里的取值需要一处可改（--baseline 基线不因调参而漂）。
_WRITE_TIMEOUT_SEC = 0.15

_ARCHIVED: set[str] = set()
_ARCHIVE_LOCK = threading.Lock()

STAGES = ("match", "load", "exec", "tool")


def usage_path() -> Path:
    """台账文件路径（CP_SKILL_USAGE_LOG 可覆盖，供测试隔离）。"""
    raw = (os.environ.get(_ENV_PATH) or "").strip()
    return Path(raw) if raw else _DEFAULT_PATH


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _maybe_archive(path: Path) -> None:
    """每个进程每天最多归档一次（归档不删数据：历史行搬到按日分片）。"""
    key = f"{path}:{date.today().isoformat()}"
    with _ARCHIVE_LOCK:
        if key in _ARCHIVED:
            return
        _ARCHIVED.add(key)
    try:
        archive_daily_file(path)
    except Exception:  # noqa: BLE001 归档失败不影响写入
        pass


def record(stage: str, skill_id: Optional[str] = None, *,
           success: Optional[bool] = None,
           latency_ms: Optional[float] = None,
           trace_id: Optional[str] = None,
           source: str = "",
           extra: Optional[Dict[str, Any]] = None) -> None:
    """记一条使用事件（best-effort，永不抛异常）。"""
    if stage not in STAGES:
        return
    try:
        path = usage_path()
        payload: Dict[str, Any] = {
            "ts": _now(),
            "stage": stage,
            "skill_id": str(skill_id) if skill_id else "",
            "source": str(source or ""),
        }
        if success is not None:
            payload["success"] = bool(success)
        if latency_ms is not None:
            payload["latency_ms"] = round(float(latency_ms), 3)
        if trace_id:
            payload["trace_id"] = str(trace_id)
        if extra:
            payload["extra"] = extra
        path.parent.mkdir(parents=True, exist_ok=True)
        append_jsonl_locked(path, json.dumps(payload, ensure_ascii=False), timeout=_WRITE_TIMEOUT_SEC)
        _maybe_archive(path)
    except Exception:  # noqa: BLE001 观测失败绝不拖垮主链路
        return


def record_tool(tool_name: str, *, success: Optional[bool] = None,
                latency_ms: Optional[float] = None,
                effect: str = "", error_code: str = "",
                source: str = "tool_calling") -> None:
    """记一条**工具**调用事件（stage=tool；工具名放 `tool`，不占用 `skill_id`）。

    与技能事件共用同一文件/锁/按日归档：读端只扫一遍就能同时算技能与工具的使用量，
    不必再开第二份台账（第二份 = 第二真相源，迟早对不上）。
    best-effort：写失败永不抛，观测不能拖垮工具执行主路径。
    """
    name = str(tool_name or "").strip()
    if not name:
        return
    try:
        path = usage_path()
        payload: Dict[str, Any] = {
            "ts": _now(),
            "stage": "tool",
            "tool": name,
            "source": str(source or ""),
        }
        if success is not None:
            payload["success"] = bool(success)
        if latency_ms is not None:
            payload["latency_ms"] = round(float(latency_ms), 3)
        extra: Dict[str, Any] = {}
        if effect:
            extra["effect"] = str(effect)
        if error_code:
            extra["error_code"] = str(error_code)[:120]
        if extra:
            payload["extra"] = extra
        path.parent.mkdir(parents=True, exist_ok=True)
        append_jsonl_locked(path, json.dumps(payload, ensure_ascii=False), timeout=_WRITE_TIMEOUT_SEC)
        _maybe_archive(path)
    except Exception:  # noqa: BLE001 观测失败绝不拖垮主链路
        return


def record_match(result: Any, *, intent: str = "") -> None:
    """由 SkillLoader.match 的返回结果记一条 match 事件。"""
    try:
        matches = list(getattr(result, "matches", []) or [])
        ids = [str(getattr(m, "skill_id", "") or "") for m in matches[:5]]
        ids = [i for i in ids if i]
        record("match", ids[0] if ids else None,
               latency_ms=getattr(result, "elapsed_ms", None),
               source="loader",
               extra={"top_ids": ids,
                      "count": len(matches),
                      "method": str(getattr(result, "retrieval_method", "") or "")})
    except Exception:  # noqa: BLE001
        return


def _event_files(path: Path, since: datetime) -> List[Path]:
    """活动文件 + 落在时间窗内的按日分片（<stem>-YYYY-MM-DD.jsonl）。"""
    files: List[Path] = []
    if path.exists():
        files.append(path)
    stem = path.stem
    for p in sorted(path.parent.glob(f"{stem}-*.jsonl")):
        suffix = p.stem[len(stem) + 1:]
        try:
            d = date.fromisoformat(suffix)
        except ValueError:
            continue
        if d >= since.date():
            files.append(p)
    return files


def _iter_events(days: int) -> Iterable[Dict[str, Any]]:
    path = usage_path()
    since = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    since_ts = since.timestamp()
    for f in _event_files(path, since):
        try:
            with f.open("r", encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except ValueError:
                        continue
                    try:
                        t = datetime.fromisoformat(str(ev.get("ts") or "").replace("Z", "+00:00"))
                        if t.tzinfo is None:
                            t = t.replace(tzinfo=timezone.utc)
                        if t.timestamp() < since_ts:
                            continue
                    except ValueError:
                        pass
                    yield ev
        except OSError:
            continue


def stats(days: int = 7) -> Dict[str, Any]:
    """按技能与工具聚合最近 N 天。

    技能：hits / loads / execs / exec_ok / exec_fail / last_used_at；
    工具（stage=tool）：calls / ok / fail / avg_ms / max_ms / last_error / last_used_at。
    两段共用同一次文件扫描 —— 工具台账不是第二份文件，避免两份口径漂移。
    """
    per: Dict[str, Dict[str, Any]] = {}
    tools: Dict[str, Dict[str, Any]] = {}
    totals = {"match_calls": 0, "hit_calls": 0, "load_calls": 0,
              "exec_calls": 0, "exec_ok": 0, "exec_fail": 0,
              "tool_calls": 0, "tool_ok": 0, "tool_fail": 0}

    def _row(sid: str) -> Dict[str, Any]:
        return per.setdefault(sid, {"skill_id": sid, "hits": 0, "loads": 0, "execs": 0,
                                    "exec_ok": 0, "exec_fail": 0, "last_used_at": ""})

    for ev in _iter_events(days):
        stage = ev.get("stage")
        sid = str(ev.get("skill_id") or "")
        ts = str(ev.get("ts") or "")
        if stage == "match":
            totals["match_calls"] += 1
            if sid:
                totals["hit_calls"] += 1
                r = _row(sid)
                r["hits"] += 1
                r["last_used_at"] = max(r["last_used_at"], ts)
        elif stage == "load" and sid:
            totals["load_calls"] += 1
            r = _row(sid)
            r["loads"] += 1
            r["last_used_at"] = max(r["last_used_at"], ts)
        elif stage == "exec" and sid:
            totals["exec_calls"] += 1
            r = _row(sid)
            r["execs"] += 1
            if bool(ev.get("success")):
                totals["exec_ok"] += 1
                r["exec_ok"] += 1
            else:
                totals["exec_fail"] += 1
                r["exec_fail"] += 1
            r["last_used_at"] = max(r["last_used_at"], ts)
        elif stage == "tool":
            name = str(ev.get("tool") or "")
            if not name:
                continue
            totals["tool_calls"] += 1
            r = tools.setdefault(name, {"tool": name, "calls": 0, "ok": 0, "fail": 0,
                                        "avg_ms": None, "max_ms": None,
                                        "last_error": "", "last_used_at": ""})
            r["calls"] += 1
            ok = bool(ev.get("success"))
            if ok:
                totals["tool_ok"] += 1
                r["ok"] += 1
            else:
                totals["tool_fail"] += 1
                r["fail"] += 1
            ms = ev.get("latency_ms")
            if isinstance(ms, (int, float)):
                prev_avg = float(r["avg_ms"] or 0.0)
                r["avg_ms"] = round((prev_avg * (r["calls"] - 1) + float(ms)) / r["calls"], 3)
                r["max_ms"] = round(max(float(r["max_ms"] or 0.0), float(ms)), 3)
            r["last_used_at"] = max(r["last_used_at"], ts)
            if not ok:
                err = str(((ev.get("extra") or {}) or {}).get("error_code") or "")
                if err:
                    r["last_error"] = err[:120]

    hit_rate = (totals["hit_calls"] / totals["match_calls"]) if totals["match_calls"] else 0.0
    tool_ok_rate = (totals["tool_ok"] / totals["tool_calls"]) if totals["tool_calls"] else 0.0
    return {"days": int(days), "skills": per, "tools": tools,
            "totals": dict(totals, hit_rate=round(hit_rate, 4),
                           tool_ok_rate=round(tool_ok_rate, 4)),
            "path": str(usage_path())}


def recent(limit: int = 50) -> List[Dict[str, Any]]:
    """最近 N 条事件（时间倒序；只读当前活动文件，够用且不扫分片）。"""
    path = usage_path()
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        return []
    return out[-max(1, int(limit)):][::-1]