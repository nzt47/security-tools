"""感知周期采集（2026-11-12）：让"感知侧学习"真的有输入。

【为什么必须有它（§11.94 C 节已考证，别重新考古）】
本部署的 18 个传感器全部 enabled，但**没有人周期性地去采集**：
- `ChangeDetector.collect()` 只在 `sensor_server.py` / `sensor/main.py` / 测试里被调用，
  而本部署不起 sensor_server ⇒ 快照 diff（硬件/进程/服务）**从不发生**；
- `FileWatcher` 即便配了 `watch_dirs`，其事件也堆在缓冲区里，只有 `BodySensor.collect_all()`
  才 drain —— 而生产没有周期调用方；
- 结果：`novelty_memory.jsonl` 里全是 behavior_drift（周级任务自己在跑），
  hardware_change / process_change / file_change 各 0 条。

【本模块只做三件事（其余一概不做）】
1. **drain 文件事件**：把 `FileWatcher` 缓冲区的 created/modified/deleted/moved 聚合成
   **一条** `file_change`（低置信 0.30 → 只写记忆，不产草稿），交给既有学习钩子
   （`agent.learning.novelty_hooks.make_learning_hook`，分类/分级/落点全复用，不新造管线）。
2. **按节流跑快照 diff**：`ChangeDetector.collect()` 会顺带触发 BodySensor 上已挂的学习钩子
   （硬件/进程变更）；默认 30 分钟一次，而不是每个 tick 都跑 —— 设备枚举/进程枚举是 WMI
   重活，且进程增减在开发机上每 5 分钟就有，过密只会把记忆刷成噪音。
3. **落一份状态**：`data/learning/perception_status.json`（最后 tick / 最后文件事件 /
   最后快照 diff / 监听目录 / watcher 是否在跑），供 `/api/alarms/coverage` 把
   "感知是不是在动"变成**可见读数**（在此之前"没事件"是完全静默的）。

【口径】
- 学习开关仍由既有 `SENSOR_LEARNING_ENABLED` / `learning.sensor_learning.enabled` 决定：
  钩子内部自己短路，本模块**不复制**这个判断（避免两处各说各话）。
- 没有 BodySensor（未接线 / 测试 / CLI）⇒ 如实返回 no_body 并落状态，**不抛异常**：
  调度器的 python_func 任务只看异常判失败，抛一次就会被报警器记成"计划任务失败"。
- 失败只记日志：单轮采集失败不该让调度线程停摆（与健康采集线程同族）。
- 数字与路径都进模块常量（调用参数里不留字面量，硬编码门同口径）。

环境变量（都已进 agent/settings/registry.py）：
    SENSOR_PERCEPTION_ENABLED                   总开关（默认 true）
    SENSOR_PERCEPTION_INTERVAL_SECONDS          tick 间隔（默认 300s）
    SENSOR_CHANGE_DETECT_INTERVAL_SECONDS       快照 diff 节流（默认 1800s）
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from agent.learning.novelty_hooks import _memory_dir, make_learning_hook
from sensor.novelty import classify_changes

logger = logging.getLogger(__name__)

__version__ = "1.0.0"

#: 调度任务名（UI/日志里认这个名字；也用于按名注销）
TASK_NAME = "感知周期采集"

#: tick 间隔（秒）：文件事件 drain 很便宜，5 分钟一轮足够把"刚发生的变化"变成事件
DEFAULT_TICK_SECONDS = 300
#: 快照 diff 节流（秒）：进程/服务/注册表这组便宜（<1s），30 分钟一次足够
DEFAULT_CHANGE_DETECT_SECONDS = 1800

#: 便宜分片（每轮 diff 都刷新）：实测合计 ~0.5–0.7s
#: （_list_processes 0.3s + psutil 服务 0.23s + 注册表/环境/系统信息 ~0.05s）
FAST_SECTIONS = ("processes", "services", "registry", "environment", "system_info")

#: 贵分片（低频轮次才刷新）：WMI Win32_PnPEntity 实测 2.2–3.1s、磁盘分区扫描 0.8s。
#: 首版把它们和便宜组放一起，导致每 30 分钟阻塞调度线程 ~25–29s（实测 duration_ms 43499 峰值）。
SLOW_SECTIONS = ("devices", "disk_partitions")

#: 贵分片刷新间隔（秒）：设备插拔/磁盘挂载本来就稀疏，一小时一次足够
DEFAULT_CHANGE_DETECT_SLOW_SECONDS = 3600

#: 状态文件（运行期状态；data/learning/ 已在 .gitignore）
STATUS_REL_PATH = "data/learning/perception_status.json"
#: novelty 记忆文件名（与 novelty_hooks._memory_record 的落点同源）
MEMORY_FILE_NAME = "novelty_memory.jsonl"
#: 状态里保留的样例路径条数（再多就只是把日志换个地方存）
SAMPLE_LIMIT = 5
#: "长期无文件事件"的提示阈值（秒）：只标注，不报警（没人往监听目录写不是故障）
LONG_SILENT_SECONDS = 24 * 3600
#: 读 novelty 记忆尾行时最多回看的字节数（文件可能很大，只取尾部）
TAIL_READ_BYTES = 8192

#: 单位看板判"感知停摆"的阈值：超过 3 个 tick 周期（且不少于 15 分钟）才算停摆
UNIT_STALE_TICKS = 3
UNIT_STALE_MIN_SECONDS = 900

ENV_ENABLED = "SENSOR_PERCEPTION_ENABLED"
ENV_TICK_SECONDS = "SENSOR_PERCEPTION_INTERVAL_SECONDS"
ENV_CHANGE_SECONDS = "SENSOR_CHANGE_DETECT_INTERVAL_SECONDS"
ENV_CHANGE_SLOW_SECONDS = "SENSOR_CHANGE_DETECT_SLOW_INTERVAL_SECONDS"

_TRUTHY = frozenset({"1", "true", "yes", "on", "enable", "enabled"})
_FALSY = frozenset({"0", "false", "no", "off", "disable", "disabled"})

#: watchdog 事件类型 → ChangeDetector 的变更类型（送入 classify_change 的 type 字段）
_CHANGE_TYPE_BY_EVENT: Dict[str, str] = {
    "created": "file_added",
    "modified": "file_modified",
    "deleted": "file_removed",
    "moved": "file_changed",
}
_TYPE_LABELS: Dict[str, str] = {
    "file_added": "新增",
    "file_modified": "修改",
    "file_removed": "删除",
    "file_changed": "移动/其他",
}

_body: Optional[Any] = None
_lock = threading.Lock()
_state: Dict[str, Any] = {
    "ticks": 0,
    "last_tick_at": None,
    "last_file_event_at": None,
    "last_change_at": None,
    "last_change_detect_at": None,
    "last_slow_detect_at": None,
    "file_events_total": 0,
    "change_events_total": 0,
}
_hook: Optional[Callable[[List[Dict[str, Any]]], None]] = None


def _env_bool(name: str, default: bool) -> bool:
    raw = (os.environ.get(name) or "").strip().lower()
    if not raw:
        return default
    if raw in _TRUTHY:
        return True
    if raw in _FALSY:
        return False
    return default


def enabled() -> bool:
    """感知周期采集总开关（默认开：不开就等于感知面继续空转）。"""
    return _env_bool(ENV_ENABLED, True)


def tick_seconds() -> int:
    """tick 间隔（秒；非法/过小回退默认，最小 30s 防止写坏配置把 CPU 打满）。"""
    return _env_int(ENV_TICK_SECONDS, DEFAULT_TICK_SECONDS, minimum=30)


def change_detect_seconds() -> int:
    """便宜分片 diff 节流（秒；最小 60s）。"""
    return _env_int(ENV_CHANGE_SECONDS, DEFAULT_CHANGE_DETECT_SECONDS, minimum=60)


def change_detect_slow_seconds() -> int:
    """贵分片（设备/磁盘）刷新间隔（秒；最小 300s，且不小于便宜组间隔）。"""
    return max(change_detect_seconds(),
               _env_int(ENV_CHANGE_SLOW_SECONDS, DEFAULT_CHANGE_DETECT_SLOW_SECONDS,
                        minimum=300))


def _env_int(name: str, default: int, *, minimum: int) -> int:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        logger.warning("[Perception] 非法 %s=%r，使用默认 %s", name, raw, default)
        return default


def attach_body(body: Any) -> None:
    """把生产 BodySensor 交给周期任务（app_server 启动时调用；幂等）。

    不在这里初始化任何传感器 —— 初始化发生在首轮 tick 的
    `BodySensor.ensure_passive_sensors()`，启动路径因此保持轻。
    """
    global _body
    with _lock:
        _body = body
    logger.info("[Perception] BodySensor 已接线（周期采集将使用同一实例）")


def attached_body() -> Optional[Any]:
    return _body


def _status_path(status_path: Optional[Path] = None) -> Path:
    if status_path is not None:
        return Path(status_path)
    return Path(__file__).resolve().parents[2] / STATUS_REL_PATH


def _iso(ts: Optional[float]) -> Optional[str]:
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts).astimezone().isoformat(timespec="seconds")
    except (OSError, OverflowError, ValueError):  # Windows 上 1970 附近的负 epoch 会抛 OSError
        return None


def _drain_file_events(body: Any) -> List[Any]:
    """取出 FileWatcher 缓冲区里的事件读数（未启动/无 watcher ⇒ 空列表）。"""
    watcher = getattr(body, "file_watcher", None)
    if watcher is None or not getattr(watcher, "is_running", False):
        return []
    readings = watcher.collect() or []
    # collect() 会额外插一条 filewatch_events 汇总读数（metadata 里没有 event_type）
    return [r for r in readings if (getattr(r, "metadata", None) or {}).get("event_type")]


def _batch_change(readings: List[Any]) -> Optional[Dict[str, Any]]:
    """把一批文件事件聚合成**一条** ChangeDetector 形状的变更（供 classify_change）。

    为什么聚合成一条：文件批量变更在口径里就是"低置信 file_change"（sensor/novelty.py
    的注释写明"文件批量变更→低置信"）。逐条落库会让一次批量导入往记忆里灌几百行。
    """
    if not readings:
        return None
    counts: Dict[str, int] = {}
    samples: List[str] = []
    has_delete = False
    for reading in readings:
        meta = getattr(reading, "metadata", None) or {}
        event_type = str(meta.get("event_type") or "")
        change_type = _CHANGE_TYPE_BY_EVENT.get(event_type, "file_changed")
        counts[change_type] = counts.get(change_type, 0) + 1
        if change_type == "file_removed":
            has_delete = True
        if len(samples) < SAMPLE_LIMIT:
            samples.append(str(getattr(reading, "value", "") or meta.get("src_path") or ""))

    total = sum(counts.values())
    single_type = next(iter(counts)) if len(counts) == 1 else "file_changed"
    parts = " / ".join("%s %d" % (_TYPE_LABELS.get(k, k), v) for k, v in sorted(counts.items()))
    description = "文件变更 %d 项（%s）" % (total, parts)
    if samples:
        description += "：样例 " + "、".join(samples)
    return {
        "name": "change_file_batch",
        "type": single_type,
        "severity": "warning" if has_delete else "normal",
        "description": description,
        "detail": {"by_type": counts, "samples": samples, "event_count": total},
    }


def _learning_hook() -> Callable[[List[Dict[str, Any]]], None]:
    """复用既有学习钩子（默认路径 = 生产路径；测试必须显式注入 tmp 钩子）。"""
    global _hook
    if _hook is None:
        _hook = make_learning_hook()
    return _hook


def _classify_count(changes: List[Dict[str, Any]]) -> int:
    """这批 diff 里"会被分类成 NoveltyEvent"的条数（只计数，不重复落库）。"""
    try:
        return len(classify_changes(changes))
    except Exception:  # noqa: BLE001 纯统计失败不影响主链路
        return 0


def _write_status(status: Dict[str, Any], status_path: Optional[Path] = None) -> None:
    """原子写状态文件（失败只记日志：状态是旁路读数，不该拖垮采集）。"""
    path = _status_path(status_path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(str(tmp), str(path))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[Perception] 状态写入失败（%s）", type(exc).__name__)


def run_perception_tick(*, body: Optional[Any] = None, now: Optional[float] = None,
                        hook: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
                        status_path: Optional[Path] = None) -> Dict[str, Any]:
    """跑一轮感知采集（调度器 func；也可在诊断/测试里直接调）。

    Args:
        body: BodySensor（缺省用 attach_body 注入的那个；两者都没有 ⇒ no_body）。
        now: 当前时刻（epoch 秒；测试可注入，控制快照 diff 节流）。
        hook: 学习钩子（缺省 = make_learning_hook(); 测试传 tmp 路径的钩子）。
        status_path: 状态文件路径（缺省 data/learning/perception_status.json）。

    Returns:
        {"status": "ok"|"no_body", "file_events": n, "changed": n, "diff_ran": bool, ...}
    """
    ts = float(now if now is not None else time.time())
    target = body if body is not None else _body
    summary: Dict[str, Any] = {
        "status": "ok", "file_events": 0, "changed": 0, "diff_ran": False,
        "classified": 0, "ts": _iso(ts),
    }
    status: Dict[str, Any] = {
        "ts": _iso(ts),
        "body_attached": target is not None,
        "watch_dirs": [],
        "watcher_running": False,
        "file_events": 0,
        "last_file_event_at": None,
        "change_detector_ready": False,
        "change_detect_ran": False,
        "changes": 0,
        "last_change_at": None,
    }

    if target is None:
        # 没有 BodySensor：如实上屏，不抛异常（抛一次就会被记成"计划任务失败"）
        summary["status"] = "no_body"
        _write_status(dict(status, status="no_body"), status_path)
        logger.warning("[Perception] 没有接线的 BodySensor，本轮跳过（感知周期采集未生效）")
        return summary

    # 1) 被动传感器就绪（ChangeDetector + FileWatcher；不启动 EventMonitor）
    try:
        ensure = getattr(target, "ensure_passive_sensors", None)
        if callable(ensure):
            ensure()
    except Exception as exc:  # noqa: BLE001 单轮初始化失败不拖垮调度
        logger.warning("[Perception] 被动传感器初始化失败（%s）", type(exc).__name__)

    watcher = getattr(target, "file_watcher", None)
    status["watcher_running"] = bool(watcher is not None and getattr(watcher, "is_running", False))
    status["watch_dirs"] = list(getattr(watcher, "watched_dirs", []) or [])

    # 2) 文件事件 → 一条 file_change（钩子内部按学习开关短路）
    try:
        readings = _drain_file_events(target)
    except Exception as exc:  # noqa: BLE001
        readings = []
        logger.warning("[Perception] 文件事件读取失败（%s）", type(exc).__name__)
    summary["file_events"] = len(readings)
    status["file_events"] = len(readings)
    if readings:
        with _lock:
            _state["last_file_event_at"] = ts
            _state["file_events_total"] += len(readings)
        batch = _batch_change(readings)
        if batch is not None:
            try:
                (hook or _learning_hook())([batch])
                summary["changed"] = 1
            except Exception as exc:  # noqa: BLE001 学习钩子异常不该拖垮采集
                logger.warning("[Perception] 学习钩子处理失败（%s）", type(exc).__name__)
    status["last_file_event_at"] = _iso(_state.get("last_file_event_at"))

    # 3) 快照 diff（按节流；ChangeDetector 自己的学习钩子会处理硬件/进程变更）
    #
    # 【贵/便宜分开采（2026-11-12）】首版每个 diff 轮次都全量采集，实测每 30 分钟
    # 阻塞调度线程 25–29s（task_history 里 43499ms 峰值），把同线程的「系统心跳」
    # 顶成 90s 一次。现在：便宜组（进程/服务/注册表/环境/系统信息）每轮都刷，
    # 贵组（WMI 设备 2–3s、磁盘分区 0.8s）按 change_detect_slow_seconds 低频刷新。
    detector = getattr(target, "change_detector", None)
    status["change_detector_ready"] = detector is not None
    last_detect = _state.get("last_change_detect_at")
    due = last_detect is None or (ts - float(last_detect)) >= change_detect_seconds()
    if detector is not None and due:
        last_slow = _state.get("last_slow_detect_at")
        if last_slow is None:
            # 【首轮不采贵分片】启动时 lifecycle 刚做过一次**全量** establish_baseline，
            # 设备/磁盘的快照是新的；再在首轮（通常是启动后 1–2 分钟）重采一次 = 白花 3–4s
            # （实测冷启动首轮 9.4s，其中 WMI 设备占大头）。把 slow 的计时从此刻起算。
            with _lock:
                _state["last_slow_detect_at"] = ts
            slow_due = False
        else:
            slow_due = (ts - float(last_slow)) >= change_detect_slow_seconds()
        sections = list(FAST_SECTIONS) + (list(SLOW_SECTIONS) if slow_due else [])
        try:
            changes = detector.collect(sections=sections) or []
            summary["diff_ran"] = True
            status["change_detect_ran"] = True
            status["change_detect_sections"] = sections
            with _lock:
                _state["last_change_detect_at"] = ts
                if slow_due:
                    _state["last_slow_detect_at"] = ts
                if changes:
                    _state["last_change_at"] = ts
                    _state["change_events_total"] += len(changes)
            summary["changes"] = len(changes)
            summary["classified"] = _classify_count(changes)
            status["changes"] = len(changes)
            if changes:
                logger.info("[Perception] 快照 diff %d 条（分片 %s；分类后 %d 条事件）",
                            len(changes), ",".join(sections), summary["classified"])
            else:
                logger.debug("[Perception] 快照 diff 无变更（分片 %s）", ",".join(sections))
        except Exception as exc:  # noqa: BLE001 WMI/枚举失败只记日志
            logger.warning("[Perception] 快照 diff 失败（%s）", type(exc).__name__)
    status["last_change_at"] = _iso(_state.get("last_change_at"))
    status["status"] = "ok"

    with _lock:
        _state["ticks"] += 1
        _state["last_tick_at"] = ts
        status["ticks"] = _state["ticks"]
        status["file_events_total"] = _state["file_events_total"]
        status["change_events_total"] = _state["change_events_total"]
    _write_status(status, status_path)
    return summary


def read_status(status_path: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """读状态文件（缺失/坏 JSON ⇒ None；调用方据此报"还没有读数"）。"""
    path = _status_path(status_path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:  # noqa: BLE001 坏文件按"没有读数"处理
        logger.warning("[Perception] 状态文件读取失败（%s）", type(exc).__name__)
        return None
    return raw if isinstance(raw, dict) else None


def _last_novelty_ts(memory_dir: Optional[str] = None) -> Optional[str]:
    """novelty 记忆文件的最后一条 ts（只读尾部若干字节；没有文件 ⇒ None）。"""
    try:
        base = Path(memory_dir or _memory_dir())
        path = base / MEMORY_FILE_NAME
        if not path.exists():
            return None
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - TAIL_READ_BYTES))
            tail = f.read().decode("utf-8", errors="replace")
        for line in reversed([ln for ln in tail.splitlines() if ln.strip()]):
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            ts = rec.get("ts") if isinstance(rec, dict) else None
            if ts:
                return str(ts)
    except Exception as exc:  # noqa: BLE001 读数失败按"没有"处理
        logger.debug("[Perception] novelty 尾行读取失败（%s）", type(exc).__name__)
    return None


def _age_seconds(iso_text: Optional[str], now: float) -> Optional[float]:
    """ISO 时间 → 距今秒数（解析失败/空 ⇒ None，绝不返回 0 冒充"刚刚"）。"""
    if not iso_text:
        return None
    try:
        dt = datetime.fromisoformat(str(iso_text).replace("Z", "+00:00"))
        return max(0.0, now - dt.timestamp())
    except (TypeError, ValueError):
        return None


#: 感知面「刻意不采」的清单（2026-11-13）：把隐私面从"静默盲区"变成"显式决定"。
#: 口径：感知面负责**文件输入**；系统指标归 health 五层，外设归 BodySensor。
PERCEPTION_EXCLUDED = (
    {"surface": "剪贴板",
     "why": "常含口令/验证码/私密文本，采了就必须加密与留存治理",
     "decision": "不采"},
    {"surface": "邮件与浏览历史",
     "why": "隐私 + 需要外部凭据与服务授权；本机不接",
     "decision": "不采"},
    {"surface": "桌面窗口标题/截屏",
     "why": "会记录用户正在看什么；sensor/window_sensor 只在显式调用时采，不做常驻",
     "decision": "按需（不常驻）"},
)


def _scope_block(watch_dirs: List[str]) -> Dict[str, Any]:
    """感知面的范围读数：监听哪些目录、还能怎么加、哪些面刻意不采。"""
    return {
        "watch_dirs": list(watch_dirs or []),
        "configurable_via": ["SENSOR_WATCH_DIRS（环境变量，逗号/分号分隔）",
                             "config.yaml: sensor.watch_dirs"],
        "excluded": [dict(item) for item in PERCEPTION_EXCLUDED],
        "note": "感知面 = 文件输入面；系统指标由 health 五层采集、外设由 BodySensor 采集",
    }


def gap_summary(*, status_path: Optional[Path] = None,
                memory_dir: Optional[str] = None,
                now: Optional[float] = None) -> Dict[str, Any]:
    """给 `/api/alarms/coverage` 的"静默缺口"读数（只读，不写盘）。

    【为什么需要它】在此之前，"感知很久没事件"是完全静默的：页面只有 enabled 开关，
    没有"最后一次事件是什么时候"。过期 = 过期，必须能看见（L5 同口径：没测到 ≠ 正常）。
    """
    ts = float(now if now is not None else time.time())
    status = read_status(status_path)
    last_novelty = _last_novelty_ts(memory_dir)
    if status is None:
        return {
            "available": False,
            "watch_dirs": [],
            "scope": _scope_block([]),
            "watcher_running": False,
            "last_tick_at": None,
            "last_file_event_at": None,
            "last_change_at": None,
            "last_novelty_at": last_novelty,
            "last_novelty_age_seconds": _age_seconds(last_novelty, ts),
            "long_silent": False,
            "detail": "还没有感知周期读数（任务未注册或刚启动）：无法判断感知是否在动",
        }

    last_file = status.get("last_file_event_at")
    file_age = _age_seconds(last_file, ts)
    tick_age = _age_seconds(status.get("ts"), ts)
    dirs = status.get("watch_dirs") or []
    running = bool(status.get("watcher_running"))
    # 「长期无事件」必须**有已知的事件时间**才敢说：刚启动还没事件 ≠ 长期静默
    long_silent = bool(running) and file_age is not None and file_age > LONG_SILENT_SECONDS
    if not running:
        detail = "FileWatcher 未运行（watch_dirs 为空或目录都被剔除）⇒ 文件变化不会被感知"
    elif file_age is None:
        detail = "监听 %d 个目录，运行中；自上次启动以来还没有文件事件" % len(dirs)
    else:
        detail = "监听 %d 个目录，运行中；最近一次文件事件 %.0f 分钟前" % (len(dirs), file_age / 60.0)
        if long_silent:
            detail += "（超过 24h 无事件：不是故障，只是没人写监听目录）"
    return {
        "available": True,
        "watch_dirs": dirs,
        "scope": _scope_block(dirs),
        "watcher_running": running,
        "last_tick_at": status.get("ts"),
        "last_tick_age_seconds": tick_age,
        "last_file_event_at": last_file,
        "last_file_event_age_seconds": file_age,
        "last_change_at": status.get("last_change_at"),
        "last_change_detect_ran": bool(status.get("change_detect_ran")),
        "last_novelty_at": last_novelty,
        "last_novelty_age_seconds": _age_seconds(last_novelty, ts),
        "long_silent": long_silent,
        "detail": detail,
    }


def unit_probe(*, status_path: Optional[Path] = None,
               now: Optional[float] = None) -> Dict[str, Any]:
    """单位看板探针（**只读**）：感知周期采集在不在动。

    【为什么它必须只读】探针每 60s 被报警采集线程调一次；任何 get_scheduler /
    get_singleton 都会把"看一眼"变成"建一个"（§11.93 / §11.95 两次实证）。
    这里只读 perception_status.json 与 novelty 记忆尾部。
    """
    gap = gap_summary(status_path=status_path, now=now)
    if not gap.get("available"):
        return {"status": "never",
                "summary": "还没有感知周期读数（任务未注册或刚启动）",
                "metrics": {}, "issues": [], "last_activity": None}
    running = bool(gap.get("watcher_running"))
    tick_age = gap.get("last_tick_age_seconds")
    status = "ok"
    issues: List[str] = []
    stale_after = max(UNIT_STALE_MIN_SECONDS, tick_seconds() * UNIT_STALE_TICKS)
    if not running:
        status = "warn"
        issues.append("FileWatcher 未运行（watch_dirs 为空或目录都被剔除）⇒ 文件变化不会被感知")
    elif tick_age is not None and tick_age > stale_after:
        status = "warn"
        issues.append("最近一次 tick 已过去 %.0f 分钟（超过 %d 个周期）"
                      % (tick_age / 60.0, UNIT_STALE_TICKS))
    return {
        "status": status,
        "summary": str(gap.get("detail") or ""),
        "metrics": {
            "watch_dirs": len(gap.get("watch_dirs") or []),
            "watcher_running": running,
            "last_tick_age_seconds": None if tick_age is None else round(float(tick_age), 1),
            "last_file_event_age_seconds": gap.get("last_file_event_age_seconds"),
            "last_novelty_age_seconds": gap.get("last_novelty_age_seconds"),
        },
        "issues": issues,
        "last_activity": gap.get("last_tick_at"),
    }


# ════════════════════════════════════════════════════════════
#  调度注册（与 BehaviorDriftScheduler 同款：开关关闭 ⇒ disabled，零副作用）
# ════════════════════════════════════════════════════════════

def register_perception_scheduler(scheduler: Optional[Any] = None) -> Dict[str, Any]:
    """注册"感知周期采集"间隔任务（幂等：同名任务只保留一份）。"""
    if not enabled():
        logger.info("[Perception] 周期采集已关闭（%s=false）", ENV_ENABLED)
        return {"status": "disabled", "reason": "%s=false" % ENV_ENABLED}
    try:
        if scheduler is None:
            from agent.task_scheduler import get_scheduler
            scheduler = get_scheduler()
    except Exception as exc:  # noqa: BLE001 调度器不可用不算致命
        logger.error("[Perception] 调度器不可用（%s）", type(exc).__name__)
        return {"status": "error", "reason": "scheduler_unavailable"}

    _remove_same_name(scheduler)
    interval = tick_seconds()
    scheduler.add_interval_task(
        TASK_NAME, func=run_perception_tick, interval_seconds=interval,
        zero_token=True, owner="agent/learning/perception_cycle.py",
        zero_token_note="本地文件事件 drain + 快照 diff，不调 LLM")
    task_id = scheduler.tasks[-1]["task_id"] if getattr(scheduler, "tasks", None) else ""
    logger.info("[Perception] 周期采集已注册 interval=%ds task_id=%s", interval, task_id)
    return {"status": "scheduled", "task_id": task_id, "interval_seconds": interval}


def _remove_same_name(scheduler: Any) -> bool:
    for task in list(getattr(scheduler, "tasks", []) or []):
        if task.get("name") == TASK_NAME:
            scheduler.remove_task(task["task_id"])
            return True
    return False


def unschedule_perception_scheduler(scheduler: Optional[Any] = None) -> bool:
    """注销周期采集任务（按固定任务名定位，可跨实例）。"""
    try:
        if scheduler is None:
            from agent.task_scheduler import get_scheduler
            scheduler = get_scheduler()
    except Exception as exc:  # noqa: BLE001
        logger.error("[Perception] 调度注销失败（%s）", type(exc).__name__)
        return False
    return _remove_same_name(scheduler)


__all__: List[str] = [
    "TASK_NAME", "ENV_ENABLED", "ENV_TICK_SECONDS", "ENV_CHANGE_SECONDS",
    "ENV_CHANGE_SLOW_SECONDS", "STATUS_REL_PATH", "FAST_SECTIONS", "SLOW_SECTIONS",
    "enabled", "tick_seconds", "change_detect_seconds", "change_detect_slow_seconds",
    "attach_body", "attached_body", "run_perception_tick", "read_status",
    "gap_summary", "unit_probe", "register_perception_scheduler",
    "unschedule_perception_scheduler",
]
