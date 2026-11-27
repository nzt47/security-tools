"""E4-2（第三期）：跨轮次**显式**状态（state.json）

【不易·只显式，不隐式继承】一次 run 的脚本上下文里**不会**自动注入上一次的状态：
要读就 await state.get()，要写就 await state.set(value)，要清就 await state.clear()。
设计稿 §5 的原话是「只有显式的 state.json，不保留活进程」——本模块就是那句的落地：

  - 状态文件在 **RUN_ROOT/state/<session 摘要>/state.json**（仍在编排根子树内，
    node 子进程没有该目录的写权限；只有宿主按常量路径写，脚本永远拿不到路径）；
  - TTL 24h：读的时候超过 24h 未更新 ⇒ 当不存在（并尽力删除），不无限期"继承"；
  - 大小上限 64KB：超限**不写**（回 state_too_large），避免把索引/结果面变成正文仓库；
  - 会话隔离：文件名是 session_id 的 sha256 摘要（不拼原始 id ⇒ 无路径注入面）；
    没有会话绑定就回 state_unavailable，**绝不**落到共享默认键（与 run 索引同口径）。

【变易·写原子】tmp + replace：半截文件不会被下一次 run 读到。

【简易·失败不致命】所有失败都回稳定信封（ok=False + code），由脚本自己决定
要不要继续；状态坏了不该让整次 run 崩掉。
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: 状态目录名（RUN_ROOT 的直接子目录；run 目录回收会显式跳过它）
STATE_DIR_NAME = "state"
#: 状态文件名
STATE_NAME = "state.json"
#: 状态有效期（秒）：超过这个时间未更新 ⇒ 下次读当不存在
STATE_TTL_SEC = 86400
#: 单份状态的字节上限（JSON 序列化后）
MAX_STATE_BYTES = 65536
#: 状态文件格式版本（将来换形状时读旧文件应当"当不存在"而不是猜）
STATE_VERSION = 1

_LOCK = threading.Lock()

#: 失败码 → 给脚本/模型的人读说明（中文唯一一份）
CODE_MESSAGES: Dict[str, str] = {
    "state_unavailable": "当前会话没有绑定 session_id ⇒ 不提供跨轮次状态"
                         "（绝不用共享默认键）。",
    "state_too_large": "状态超过大小上限（64KB），本次未写入。",
    "state_denied": "状态文件不在可写围栏内，本次未写入。",
    "state_failed": "状态文件读写失败（详见服务日志）。",
    "bad_request": "未知的状态操作（只支持 get / set / clear）。",
}


def _key(session_id: str) -> str:
    """会话 → 文件名（sha256 摘要；不拼原始 id，杜绝路径注入面）"""
    return hashlib.sha256(str(session_id or "").encode("utf-8")).hexdigest()[:16]


def state_path(session_id: str, root: Path) -> Optional[Path]:
    """状态文件路径；没有会话 ⇒ None（调用方回 state_unavailable）"""
    sid = str(session_id or "").strip()
    if not sid:
        return None
    return Path(root) / STATE_DIR_NAME / (_key(sid) + ".json")


def _envelope(code: str, **extra: Any) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"ok": False, "code": code,
                              "error": CODE_MESSAGES.get(code, "")}
    payload.update(extra)
    return payload


def _unlink(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def read(session_id: str, root: Path) -> Dict[str, Any]:
    """读状态：{"ok": True, "value": <任意 JSON | None>}"""
    path = state_path(session_id, root)
    if path is None:
        return _envelope("state_unavailable")
    with _LOCK:
        try:
            if not path.exists():
                return {"ok": True, "value": None}
            if time.time() - path.stat().st_mtime > STATE_TTL_SEC:
                _unlink(path)  # 过期即视为不存在（TTL 是"显式"的一部分）
                return {"ok": True, "value": None}
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            # 坏文件当不存在（并删掉）：下一次 set 能修复；坏 JSON 不该让 run 失败
            logger.warning("[orchestration] 状态文件损坏，按不存在处理: %s",
                           type(exc).__name__)
            _unlink(path)
            return {"ok": True, "value": None}
        except OSError as exc:
            logger.warning("[orchestration] 状态读取失败: %s", type(exc).__name__)
            return _envelope("state_failed")
    if not isinstance(data, dict):
        return {"ok": True, "value": None}  # 坏形状：当不存在，不猜
    try:
        version = int(data.get("v") or 0)
    except (TypeError, ValueError):
        return {"ok": True, "value": None}
    if version != STATE_VERSION:
        return {"ok": True, "value": None}  # 旧格式：当不存在，不猜
    return {"ok": True, "value": data.get("value")}


def write(session_id: str, value: Any, root: Path) -> Dict[str, Any]:
    """写状态：{"ok": True, "bytes": n}；超限/围栏拒绝都不落盘"""
    path = state_path(session_id, root)
    if path is None:
        return _envelope("state_unavailable")
    try:
        body = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return _envelope("bad_request")
    size = len(body.encode("utf-8"))
    if size > MAX_STATE_BYTES:
        return _envelope("state_too_large", limit=MAX_STATE_BYTES)
    # 设计稿 §5 的「fs_fence 保护」：即便路径是常量推导，也在写前过一遍策略围栏。
    try:
        from agent.sandbox import fs_fence as _fence
        denial = _fence.check_write(path, "orchestration_state")
        if denial:
            return _envelope("state_denied")
    except Exception as exc:  # noqa: BLE001 围栏不可用按拒绝处理（fail-closed）
        logger.warning("[orchestration] 状态写围栏不可用: %s", type(exc).__name__)
        return _envelope("state_denied")
    payload = {"v": STATE_VERSION,
               "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime()),
               "value": value}
    with _LOCK:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except OSError as exc:
            logger.warning("[orchestration] 状态写入失败: %s", type(exc).__name__)
            return _envelope("state_failed")
    return {"ok": True, "bytes": size}


def clear(session_id: str, root: Path) -> Dict[str, Any]:
    """清状态：{"ok": True, "cleared": bool}"""
    path = state_path(session_id, root)
    if path is None:
        return _envelope("state_unavailable")
    with _LOCK:
        try:
            existed = path.exists()
            if existed:
                path.unlink()
        except OSError as exc:
            logger.warning("[orchestration] 状态清理失败: %s", type(exc).__name__)
            return _envelope("state_failed")
    return {"ok": True, "cleared": existed}


def apply(op: str, value: Any, *, session_id: str, root: Path) -> Dict[str, Any]:
    """协议入口：把 runner 的 {t:"state", op} 映射到读/写/清（未知操作回 bad_request）"""
    action = str(op or "")
    try:
        if action == "get":
            return read(session_id, root)
        if action == "set":
            return write(session_id, value, root)
        if action == "clear":
            return clear(session_id, root)
        return _envelope("bad_request")
    except Exception as exc:  # noqa: BLE001 状态坏了不该让整次 run 崩掉
        logger.warning("[orchestration] 状态操作异常: %s", type(exc).__name__)
        return _envelope("state_failed")


__all__ = [
    "CODE_MESSAGES", "MAX_STATE_BYTES", "STATE_DIR_NAME", "STATE_NAME",
    "STATE_TTL_SEC", "STATE_VERSION", "apply", "clear", "read", "state_path",
    "write",
]
