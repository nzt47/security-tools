"""LLM 禁用时段闸门 — 在计费高峰停止 LLM 请求（2026-11-20，2026-11-20 修订）。

【需求口径（Owner，2026-11-20 第二轮修订）】
    北京时间（UTC+8，无夏令时）：
      · 默认禁用时段 = 周一至周五 09:00-12:00、14:00-18:00（中国法定节假日除外）；
      · 禁用时段内 **所有** LLM 请求一律拒绝（含用户对话）；
      · 其余时段（计费空闲：午休、夜间、周末、法定节假日）放行。
    禁用时段由用户在顶栏开关 / 配置弹窗里**自由增删**（按星期 + 起止时刻），
    不再写死在代码里；默认值只是首次使用时的初始配置。

【为什么拦截点放在 SDK 的 chat.completions.create（而不是只堵 /api/chat）】
    实测全仓 LLM 调用有多个入口：memory/llm_service.py、agent/tool_calling.py、
    agent/model_router/adapters.py、agent/orchestrator/orchestrator.py、
    agent/ingestion/toolbuild.py。逐个补闸门必然漏，而它们最终都走
    OpenAI 兼容的 client.chat.completions.create（或 Anthropic client.messages.create）。
    故在类方法上装一次闸门，覆盖进程内全部客户端。

【配置存储】
    data/llm_gate_config.json（运行期状态，不提交）：
      {
        "enabled": true,
        "windows": [{"days": [0,1,2,3,4], "start": "09:00", "end": "12:00"}, ...],
        "skip_holidays": true,
        "lead_seconds": 300
      }
    读取时按文件 mtime/size 缓存；文件不存在时用默认值（不落盘，避免测试写生产 data/）。
    LLM_IDLE_GATE_ENABLED 只作为**首次生成默认配置**时的 enabled 初值。

【为什么默认关闭】
    "全部请求一律拒绝"是强行为：默认开启会让任何未配置该开关的部署在高峰时段
    全部对话失败（含 CI/本地开发）。本机 .env 显式置 1；顶栏开关会在运行期覆盖它。
    install() 无条件打补丁（禁用时 guard 是 no-op），故运行期改开关即时生效。

【提前收尾（Owner 第三点）】
    任务开始前 / 每轮 LLM 调用前，调用 planning_note() / wrapup_directive() 拿到
    "距离禁用时段还剩多久"的提示；调度器对可能调用 LLM 的任务在提前量不足时**顺延**，
    不启动"做一半会被掐断"的任务。

【节假日表】
    数据源：国务院办公厅《关于 2025/2026/2027 年部分节假日安排的通知》，
    只登记放假日期（周末本来就是空闲时段）。未覆盖年份 fail-open 并每年告警一次。

【边界（明确不拦）】
    · 本地 embedding / rerank（不走 LLM API，也不按 token 计费）；
    · LLMResponseCache 命中（没有发请求，不产生计费）；
    · 直接 requests.post 打 LLM 端点（当前主链路无此调用；若有需另接闸门）。
    · 只拦"新发起"的请求：切换点前已在途的请求不会被强制掐断。
"""

from __future__ import annotations

import datetime as dt
import functools
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

#: 北京时间（固定 +08:00；中国自 1991 年起不实行夏令时）
TZ = dt.timezone(dt.timedelta(hours=8), name="Asia/Shanghai")

#: 首次默认配置的 enabled 初值（仅当配置文件不存在时读取）
ENV_ENABLED = "LLM_IDLE_GATE_ENABLED"

#: 配置文件路径覆盖（测试用；生产默认 data/llm_gate_config.json）
ENV_CONFIG_FILE = "LLM_GATE_CONFIG_FILE"

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = _REPO_ROOT / "data" / "llm_gate_config.json"

#: 视为"关闭"的取值（与仓库其它布尔开关同口径）
_OFF_VALUES = frozenset({"0", "false", "no", "off", "disable", "disabled"})

#: 默认禁用时段（可被用户在顶栏配置里改）
DEFAULT_WINDOWS: Tuple[Dict[str, Any], ...] = (
    {"days": [0, 1, 2, 3, 4], "start": "09:00", "end": "12:00"},
    {"days": [0, 1, 2, 3, 4], "start": "14:00", "end": "18:00"},
)
DEFAULT_SKIP_HOLIDAYS = True
DEFAULT_LEAD_SECONDS = 300
MAX_WINDOWS = 24

#: 中国法定节假日放假区间（含调休连休），闭区间。
HOLIDAY_RANGES: Tuple[Tuple[str, str], ...] = (
    # ── 2025 ──────────────────────────────────────────────
    ("2025-01-01", "2025-01-01"),   # 元旦
    ("2025-01-28", "2025-02-04"),   # 春节
    ("2025-04-04", "2025-04-06"),   # 清明节
    ("2025-05-01", "2025-05-05"),   # 劳动节
    ("2025-05-31", "2025-06-02"),   # 端午节
    ("2025-10-01", "2025-10-08"),   # 国庆节 + 中秋节
    # ── 2026 ──────────────────────────────────────────────
    ("2026-01-01", "2026-01-03"),   # 元旦
    ("2026-02-15", "2026-02-23"),   # 春节
    ("2026-04-04", "2026-04-06"),   # 清明节
    ("2026-05-01", "2026-05-05"),   # 劳动节
    ("2026-06-19", "2026-06-21"),   # 端午节
    ("2026-09-25", "2026-09-27"),   # 中秋节
    ("2026-10-01", "2026-10-07"),   # 国庆节
    # ── 2027 ──────────────────────────────────────────────
    ("2027-01-01", "2027-01-03"),   # 元旦
    ("2027-02-05", "2027-02-13"),   # 春节
    ("2027-04-03", "2027-04-05"),   # 清明节
    ("2027-05-01", "2027-05-05"),   # 劳动节
    ("2027-06-09", "2027-06-09"),   # 端午节
    ("2027-09-15", "2027-09-15"),   # 中秋节
    ("2027-10-01", "2027-10-07"),   # 国庆节
)

COVERED_YEARS = frozenset({2025, 2026, 2027})

_WEEKDAY_CN = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

_holidays: Optional[Dict[int, frozenset]] = None
_holidays_lock = threading.Lock()
_warned_years: set = set()
_warned_lock = threading.Lock()

#: 配置缓存：key = (path, mtime_ns, size)，value = 规范化配置
_config_cache: Optional[Dict[str, Any]] = None
_config_sig: Optional[Tuple[str, int, int]] = None
_config_lock = threading.RLock()


class LLMIdleBlocked(RuntimeError):
    """禁用时段拒绝 LLM 请求（调用方可按 code 分支，不解析文案）。"""

    code = "LLM_IDLE_BLOCKED"

    def __init__(self, state: Dict[str, Any]):
        self.state = dict(state)
        super().__init__(str(state.get("message") or "当前处于 LLM 禁用时段，请求已暂停"))


# ══════════════════════════════════════════════════════════════
#  时钟 / 节假日
# ══════════════════════════════════════════════════════════════

def _now() -> dt.datetime:
    """当前北京时间（单独成函数：测试可 monkeypatch，避免依赖机器时区）。"""
    return dt.datetime.now(TZ)


def _to_bj(moment: Optional[dt.datetime]) -> dt.datetime:
    if moment is None:
        return _now()
    if moment.tzinfo is None:
        return moment.replace(tzinfo=TZ)
    return moment.astimezone(TZ)


def _build_holidays() -> Dict[int, frozenset]:
    out: Dict[int, set] = {}
    for start_s, end_s in HOLIDAY_RANGES:
        try:
            start = dt.date.fromisoformat(start_s)
            end = dt.date.fromisoformat(end_s)
        except ValueError:
            logger.error("[llm-gate] 节假日区间格式非法，已跳过: %s ~ %s", start_s, end_s)
            continue
        if end < start:
            logger.error("[llm-gate] 节假日区间起止颠倒，已跳过: %s ~ %s", start_s, end_s)
            continue
        day = start
        while day <= end:
            out.setdefault(day.year, set()).add(day)
            day += dt.timedelta(days=1)
    return {year: frozenset(days) for year, days in out.items()}


def _holiday_set() -> Dict[int, frozenset]:
    global _holidays
    if _holidays is None:
        with _holidays_lock:
            if _holidays is None:
                _holidays = _build_holidays()
    return _holidays


def is_holiday(day: dt.date) -> bool:
    """是否中国法定节假日（放假）日；未覆盖年份 fail-open + 每年告警一次。"""
    if day.year not in COVERED_YEARS:
        with _warned_lock:
            if day.year not in _warned_years:
                _warned_years.add(day.year)
                logger.warning(
                    "[llm-gate] %d 年节假日表未覆盖（COVERED_YEARS=%s），"
                    "该年按非节假日处理；请按国务院通知更新 llm_idle_gate.HOLIDAY_RANGES",
                    day.year, sorted(COVERED_YEARS))
        return False
    return day in _holiday_set().get(day.year, frozenset())


# ══════════════════════════════════════════════════════════════
#  配置（读写 / 校验）
# ══════════════════════════════════════════════════════════════

def config_path() -> Path:
    override = str(os.environ.get(ENV_CONFIG_FILE, "") or "").strip()
    return Path(override) if override else DEFAULT_CONFIG_PATH


def _env_enabled_default() -> bool:
    raw = str(os.environ.get(ENV_ENABLED, "0")).strip().lower()
    return raw not in _OFF_VALUES and raw != ""


def _default_config() -> Dict[str, Any]:
    return {
        "enabled": _env_enabled_default(),
        "windows": [dict(w, days=list(w["days"])) for w in DEFAULT_WINDOWS],
        "skip_holidays": DEFAULT_SKIP_HOLIDAYS,
        "lead_seconds": DEFAULT_LEAD_SECONDS,
    }


def _parse_hhmm(value: Any) -> int:
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError(f"时间格式应为 HH:MM，收到 {text!r}")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"时间超出范围: {text!r}")
    return hour * 60 + minute


def _fmt_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def normalize_config(raw: Any, base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """把任意输入规范化成合法配置；非法抛 ValueError（文案直接给前端）。"""
    if not isinstance(raw, dict):
        raise ValueError("配置必须是 JSON 对象")
    base = base or _default_config()
    enabled = raw.get("enabled", base["enabled"])
    if not isinstance(enabled, bool):
        raise ValueError("enabled 必须是布尔值")
    skip_holidays = raw.get("skip_holidays", base["skip_holidays"])
    if not isinstance(skip_holidays, bool):
        raise ValueError("skip_holidays 必须是布尔值")
    try:
        lead_seconds = int(raw.get("lead_seconds", base["lead_seconds"]))
    except (TypeError, ValueError):
        raise ValueError("lead_seconds 必须是整数") from None
    if not (0 <= lead_seconds <= 3600):
        raise ValueError("lead_seconds 必须在 0-3600 秒之间")

    windows_raw = raw.get("windows", base["windows"])
    if not isinstance(windows_raw, list):
        raise ValueError("windows 必须是数组")
    if len(windows_raw) > MAX_WINDOWS:
        raise ValueError(f"禁用时段最多 {MAX_WINDOWS} 条")
    windows: List[Dict[str, Any]] = []
    for idx, item in enumerate(windows_raw):
        if not isinstance(item, dict):
            raise ValueError(f"第 {idx + 1} 条时段必须是对象")
        days_raw = item.get("days")
        if not isinstance(days_raw, list) or not days_raw:
            raise ValueError(f"第 {idx + 1} 条时段至少要选一个星期")
        days = sorted({int(d) for d in days_raw})
        if any(d < 0 or d > 6 for d in days):
            raise ValueError(f"第 {idx + 1} 条时段的星期必须是 0-6（周一=0）")
        start_min = _parse_hhmm(item.get("start"))
        end_min = _parse_hhmm(item.get("end"))
        if end_min <= start_min:
            raise ValueError(f"第 {idx + 1} 条时段的结束时间必须晚于开始时间")
        windows.append({
            "days": days,
            "start": _fmt_hhmm(start_min),
            "end": _fmt_hhmm(end_min),
        })
    return {
        "enabled": enabled,
        "windows": windows,
        "skip_holidays": skip_holidays,
        "lead_seconds": lead_seconds,
    }


def _read_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as e:
        logger.warning("[llm-gate] 配置读取失败（按默认值处理）: %s: %s", path, e)
        return None
    try:
        return normalize_config(raw)
    except ValueError as e:
        logger.warning("[llm-gate] 配置非法（按默认值处理）: %s: %s", path, e)
        return None


def get_config() -> Dict[str, Any]:
    """当前生效配置（按文件 mtime/size 缓存；文件不存在时用默认值，不落盘）。"""
    global _config_cache, _config_sig
    path = config_path()
    try:
        st = path.stat()
        sig: Optional[Tuple[str, int, int]] = (str(path), int(st.st_mtime_ns), int(st.st_size))
    except OSError:
        sig = None
    with _config_lock:
        if sig is None:
            return _default_config()
        if _config_cache is not None and _config_sig == sig:
            return json.loads(json.dumps(_config_cache))  # 深拷贝，防调用方改缓存
        cfg = _read_file(path)
        if cfg is None:
            return _default_config()
        _config_cache = cfg
        _config_sig = sig
        return json.loads(json.dumps(cfg))


def save_config(patch: Any) -> Dict[str, Any]:
    """部分更新并落盘；非法抛 ValueError。返回保存后的完整配置。"""
    global _config_cache, _config_sig
    current = get_config()
    merged = dict(current)
    if isinstance(patch, dict):
        merged.update(patch)
    cfg = normalize_config(merged, base=current)
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)
    st = path.stat()
    with _config_lock:
        _config_cache = cfg
        _config_sig = (str(path), int(st.st_mtime_ns), int(st.st_size))
    logger.info("[llm-gate] 配置已保存: enabled=%s windows=%d lead=%ss",
                cfg["enabled"], len(cfg["windows"]), cfg["lead_seconds"])
    return json.loads(json.dumps(cfg))


def is_enabled() -> bool:
    return bool(get_config()["enabled"])


# ══════════════════════════════════════════════════════════════
#  时段判定 / 状态
# ══════════════════════════════════════════════════════════════

def _window_minutes(window: Dict[str, Any]) -> Tuple[int, int]:
    return _parse_hhmm(window["start"]), _parse_hhmm(window["end"])


def active_window(moment: Optional[dt.datetime] = None) -> Optional[Dict[str, Any]]:
    """当前命中的禁用时段（未启用 / 节假日豁免 / 不在窗口内 → None）。"""
    cfg = get_config()
    if not cfg["enabled"]:
        return None
    now = _to_bj(moment)
    if cfg["skip_holidays"] and is_holiday(now.date()):
        return None
    minutes = now.hour * 60 + now.minute
    for window in cfg["windows"]:
        if now.weekday() not in window["days"]:
            continue
        start, end = _window_minutes(window)
        if start <= minutes < end:
            return window
    return None


def is_blocked(moment: Optional[dt.datetime] = None) -> bool:
    return active_window(moment) is not None


def next_block_start(moment: Optional[dt.datetime] = None) -> Optional[dt.datetime]:
    """下一个禁用时段起点；无可用时段（开关关/未配置）→ None。"""
    cfg = get_config()
    if not cfg["enabled"] or not cfg["windows"]:
        return None
    now = _to_bj(moment)
    if is_blocked(now):
        return now
    best: Optional[dt.datetime] = None
    for offset in range(0, 15):          # 最多看 15 天（跨长假足够）
        day = now.date() + dt.timedelta(days=offset)
        if cfg["skip_holidays"] and is_holiday(day):
            continue
        for window in cfg["windows"]:
            if day.weekday() not in window["days"]:
                continue
            start, _end = _window_minutes(window)
            cand = dt.datetime.combine(day, dt.time(start // 60, start % 60), tzinfo=TZ)
            if cand > now and (best is None or cand < best):
                best = cand
    return best


def next_unblock(moment: Optional[dt.datetime] = None) -> Optional[dt.datetime]:
    """当前禁用时段结束后恢复的时刻；未处于禁用时段 → None。"""
    now = _to_bj(moment)
    cfg = get_config()
    if not cfg["enabled"]:
        return None
    if cfg["skip_holidays"] and is_holiday(now.date()):
        return None
    minutes = now.hour * 60 + now.minute
    ends: List[int] = []
    for window in cfg["windows"]:
        if now.weekday() not in window["days"]:
            continue
        start, end = _window_minutes(window)
        if start <= minutes < end:
            ends.append(end)
    if not ends:
        return None
    end = max(ends)
    return dt.datetime.combine(now.date(), dt.time(end // 60, end % 60), tzinfo=TZ)


def seconds_until_blocked(moment: Optional[dt.datetime] = None) -> Optional[int]:
    """距下一个禁用时段还有多少秒（已禁用 → 0；无下一段 → None）。"""
    now = _to_bj(moment)
    if is_blocked(now):
        return 0
    nxt = next_block_start(now)
    if nxt is None:
        return None
    return max(0, int((nxt - now).total_seconds()))


def _fmt(moment: dt.datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M")


def state(moment: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """当前闸门状态（纯计算，不抛异常；供路由、定时任务、日志使用）。"""
    cfg = get_config()
    now = _to_bj(moment)
    window = active_window(now)
    blocked = window is not None
    nxt = next_block_start(now) if (cfg["enabled"] and not blocked) else None
    unblock = next_unblock(now) if blocked else None
    secs = 0 if blocked else None
    if cfg["enabled"] and not blocked:
        secs = None if nxt is None else max(0, int((nxt - now).total_seconds()))
    holiday = is_holiday(now.date())
    if blocked and window is not None:
        message = (
            f"当前处于 LLM 禁用时段（北京时间 {_fmt(now)} {_WEEKDAY_CN[now.weekday()]}，"
            f"窗口 {window['start']}-{window['end']}），LLM 请求已暂停；"
            f"{_fmt(unblock) if unblock else '稍后'} 恢复。")
    elif not cfg["enabled"]:
        message = "LLM 禁用时段开关已关闭，当前放行 LLM 请求。"
    elif secs is None:
        message = "当前允许 LLM 请求（未配置禁用时段）。"
    else:
        message = (
            f"当前允许 LLM 请求（北京时间 {_fmt(now)}）；下一个禁用时段："
            f"{_fmt(nxt) if nxt else '—'}（剩余 {max(0, secs) // 60} 分钟）。")
    return {
        "enabled": cfg["enabled"],
        "blocked": blocked,
        "now": now.isoformat(timespec="seconds"),
        "date": now.date().isoformat(),
        "weekday": now.weekday(),
        "weekday_cn": _WEEKDAY_CN[now.weekday()],
        "holiday": holiday,
        "holiday_year_covered": now.year in COVERED_YEARS,
        "window": dict(window) if window else None,
        "next_block": nxt.isoformat(timespec="minutes") if nxt else None,
        "next_unblock": unblock.isoformat(timespec="minutes") if unblock else None,
        "seconds_until_blocked": secs,
        "windows": [dict(w, days=list(w["days"])) for w in cfg["windows"]],
        "skip_holidays": cfg["skip_holidays"],
        "lead_seconds": cfg["lead_seconds"],
        "message": message,
    }


def blocked_state(moment: Optional[dt.datetime] = None) -> Optional[Dict[str, Any]]:
    """被拦截时返回状态字典，否则 None（路由层用：先给友好提示，再进主链路）。"""
    st = state(moment)
    return st if st["blocked"] else None


def guard(source: str = "", moment: Optional[dt.datetime] = None) -> None:
    """LLM 调用闸门：命中禁用时段抛 LLMIdleBlocked；否则返回 None。"""
    now = _to_bj(moment)
    if not is_blocked(now):
        return
    st = state(now)
    st["source"] = source
    raise LLMIdleBlocked(st)


# ══════════════════════════════════════════════════════════════
#  提前收尾 / 任务顺延
# ══════════════════════════════════════════════════════════════

def planning_note(moment: Optional[dt.datetime] = None) -> Dict[str, Any]:
    """给任务/对话的"时间预算"提示：提前规划，避免做到一半被禁用时段掐断。

    Returns:
        {"text": str, "seconds_left": Optional[int], "deadline": Optional[str],
         "blocked": bool, "wrapup": bool}
    """
    now = _to_bj(moment)
    cfg = get_config()
    blocked = is_blocked(now)
    if blocked or not cfg["enabled"]:
        return {"text": "", "seconds_left": 0 if blocked else None,
                "deadline": None, "blocked": blocked, "wrapup": False}
    nxt = next_block_start(now)
    if nxt is None:
        return {"text": "", "seconds_left": None, "deadline": None,
                "blocked": False, "wrapup": False}
    secs = max(0, int((nxt - now).total_seconds()))
    lead = int(cfg["lead_seconds"])
    if secs <= lead:
        text = (
            f"【时间预算·收尾】距离 LLM 禁用时段仅剩 {secs} 秒（北京时间 {_fmt(nxt)} 起停止）。"
            "请立即收尾：停止发起新的工具调用/长链路步骤，基于已有信息给出可交付结论。")
        wrapup = True
    else:
        text = (
            f"【时间预算】LLM 将在北京时间 {_fmt(nxt)} 停止（剩余 {secs // 60} 分钟）。"
            "请据此规划步骤，把可交付结论放在停止之前；临近停止时系统会提醒收尾。")
        wrapup = False
    return {"text": text, "seconds_left": secs, "deadline": nxt.isoformat(timespec="minutes"),
            "blocked": False, "wrapup": wrapup}


def wrapup_directive(moment: Optional[dt.datetime] = None) -> str:
    """临近禁用时段时的收尾指令（未临近/已禁用 → 空串）。"""
    note = planning_note(moment)
    return str(note["text"]) if note["wrapup"] else ""


def should_defer_task(moment: Optional[dt.datetime] = None,
                       min_seconds: Optional[int] = None) -> bool:
    """调度器是否应把可能调用 LLM 的任务顺延到下一个空闲时段。

    - 已处于禁用时段 → True（现在启动必然失败/半截）；
    - 距禁用时段不足 min_seconds（默认取配置 lead_seconds）→ True；
    - 开关关闭 / 无下一段 → False。
    """
    cfg = get_config()
    if not cfg["enabled"]:
        return False
    now = _to_bj(moment)
    if is_blocked(now):
        return True
    secs = seconds_until_blocked(now)
    if secs is None:
        return False
    threshold = cfg["lead_seconds"] if min_seconds is None else max(0, int(min_seconds))
    return secs < threshold


# ══════════════════════════════════════════════════════════════
#  SDK 拦截（进程内全部 OpenAI 兼容 / Anthropic 客户端）
# ══════════════════════════════════════════════════════════════

_install_lock = threading.Lock()
_installed = False
_originals: List[Tuple[Any, str, Any]] = []


def _patch_sync(cls: Any, label: str) -> None:
    orig = getattr(cls, "create")

    @functools.wraps(orig)
    def _create(self, *args, **kwargs):
        guard(source=label)
        return orig(self, *args, **kwargs)

    _originals.append((cls, "create", orig))
    setattr(cls, "create", _create)


def _patch_async(cls: Any, label: str) -> None:
    orig = getattr(cls, "create")

    @functools.wraps(orig)
    async def _create(self, *args, **kwargs):
        guard(source=label)
        return await orig(self, *args, **kwargs)

    _originals.append((cls, "create", orig))
    setattr(cls, "create", _create)


def install() -> int:
    """安装 SDK 层闸门；幂等，返回打补丁的类方法数。

    必须在 agent.llm_monitor.install_hooks() 之前调用：
    监控钩子会把 client 实例上的 create 换成闭包并缓存 orig_create；
    若先装监控，闭包里存的是未打补丁的原函数，闸门会被绕过。
    """
    global _installed
    with _install_lock:
        if _installed:
            return len(_originals)
        patched = 0
        try:
            from openai.resources.chat.completions import AsyncCompletions, Completions
            _patch_sync(Completions, "openai.chat.completions")
            _patch_async(AsyncCompletions, "openai.chat.completions.async")
            patched += 2
        except Exception as e:  # noqa: BLE001 缺 SDK 不是错误
            logger.debug("[llm-gate] 未安装 openai 闸门（%s）", e)
        try:
            from anthropic.resources.messages import AsyncMessages, Messages
            _patch_sync(Messages, "anthropic.messages")
            _patch_async(AsyncMessages, "anthropic.messages.async")
            patched += 2
        except Exception as e:  # noqa: BLE001 同上
            logger.debug("[llm-gate] 未安装 anthropic 闸门（%s）", e)
        _installed = True
        logger.info("[llm-gate] 已安装 LLM 请求闸门（%d 个入口，enabled=%s）",
                    patched, is_enabled())
        return patched


def uninstall() -> None:
    """撤销闸门（仅测试用；生产不调用）。"""
    global _installed
    with _install_lock:
        while _originals:
            cls, name, orig = _originals.pop()
            try:
                setattr(cls, name, orig)
            except Exception as e:  # noqa: BLE001
                logger.debug("[llm-gate] 撤销闸门失败: %s", e)
        _installed = False


def installed() -> bool:
    return _installed


#: 定时任务的状态切换缓存（只在变化时写日志，避免每 tick 刷屏）
_last_tick_blocked: Optional[bool] = None


def tick() -> Dict[str, Any]:
    """定时任务体：校准状态并在切换点写一条日志（零 token，不调 LLM）。"""
    global _last_tick_blocked
    st = state()
    prev = _last_tick_blocked
    _last_tick_blocked = bool(st["blocked"])
    if prev is None:
        # 首次 tick 只是初始读数，不得说成"离开禁用时段"（那是状态迁移，不是开机）
        logger.info("[llm-gate] 初始状态（%s）：%s",
                    "拦截" if st["blocked"] else "放行", st["message"])
    elif prev != st["blocked"]:
        if st["blocked"]:
            logger.warning("[llm-gate] 进入禁用时段，LLM 请求开始拒绝：%s", st["message"])
        elif st["enabled"]:
            logger.info("[llm-gate] 离开禁用时段，LLM 请求恢复：%s", st["message"])
        else:
            logger.info("[llm-gate] 开关关闭（状态：%s）", st["message"])
    return st
