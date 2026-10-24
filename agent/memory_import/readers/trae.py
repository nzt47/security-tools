"""Trae 本地记忆读取器（只读）。

【本机实测口径 2026-10-24】Trae CN 的会话正文**并不在本地**：
- %APPDATA%/Trae CN/User/globalStorage/state.vscdb 只有 ItemTable，771 键；
  chat.ChatSessionStore.index 的值是 {"version":1,"entries":{}}（空索引），
  说明会话索引为空、正文在云端；
- IndexedDB（vscode-file://vscode-app 一个库）里是 47 个 SVG 预览缓存，
  没有消息记录；
- Local Storage（leveldb）里是偏好/主题/i18n，不含会话。

**能拿到的是"用户提问轨迹"**：ai-chat.chatQueryCompletion.v1.* 每个键缓存
一段输入补全候选，形如
    {version:1, response:{resp:{result:[{id, text, references}]}}}
其中 text 是用户当时真实输入的提问，id 前 14 位是 YYYYMMDDHHMMSS，
可据此还原"某天问了什么"（粒度到天，不是会话）。

因此本读取器的产出分两类：
1. kind=prompt，role=user：提问轨迹（session_id 按日期归并 —— 本地没有
   会话边界，伪造一个会产生假会话）；
2. kind=message：只有在 IndexedDB / Local Storage 里真的扫到 role/content
   结构时才产出（当前为 0，保留通路以便 Trae 改版后仍能解析）。

【不易】不读 %APPDATA% 之外的路径，不写任何文件，不打开 leveldb 的 LOCK；
所有失败只进 warnings，绝不因一个坏文件丢掉整批。
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from ..models import (
    KIND_MESSAGE, KIND_PROMPT, ROLE_ASSISTANT, ROLE_USER, SOURCE_TRAE, MemoryRecord,
)
from . import leveldb

#: 提问补全缓存的键前缀（实测 270 个键，全部是这一族）
QUERY_KEY_PREFIX = "ai-chat.chatQueryCompletion.v1."
#: 会话索引键（值为 {"version":1,"entries":{}} 时表示本地无会话）
SESSION_INDEX_KEY = "chat.ChatSessionStore.index"
#: 单个 state.vscdb 值最多读 8 MiB（防止异常大 BLOB 拖垮盘点）
MAX_VALUE_BYTES = 8 * 1024 * 1024
#: 单次扫描产出的记录上限（防"扫到一堆垃圾"把预览撑爆）
MAX_RECORDS = 5000

_TS_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})")


def default_trae_appdata() -> Path:
    """%APPDATA%/Trae CN（Trae 国际版目录名为 Trae，两者都试）。"""
    roaming = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(roaming)


def find_trae_roots(appdata: Optional[Path] = None) -> List[Path]:
    """返回存在的 Trae 数据根（Trae CN 优先，其次 Trae）。"""
    base = Path(appdata) if appdata else default_trae_appdata()
    if base.name in ("Trae CN", "Trae") and base.is_dir():
        return [base]
    out: List[Path] = []
    for name in ("Trae CN", "Trae"):
        candidate = base / name
        if candidate.is_dir():
            out.append(candidate)
    return out


def _text_of(value: Any) -> str:
    if isinstance(value, (bytes, bytearray)):
        return bytes(value[:MAX_VALUE_BYTES]).decode("utf-8", "replace")
    return str(value or "")


def read_vscdb(path: Path) -> Tuple[Dict[str, str], List[str]]:
    """只读打开 state.vscdb（immutable=1，不加锁、不建 -wal）。"""
    warnings: List[str] = []
    path = Path(path)
    if not path.is_file():
        return {}, [f"state.vscdb 不存在: {path}"]
    uri = f"file:{path.as_posix()}?mode=ro&immutable=1"
    try:
        con = sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        return {}, [f"打开失败（{path}）：{exc}"]
    try:
        rows = con.execute("select key, value from ItemTable").fetchall()
    except sqlite3.Error as exc:
        return {}, [f"读取 ItemTable 失败（{path}）：{exc}"]
    finally:
        con.close()
    out: Dict[str, str] = {}
    for key, value in rows:
        if not isinstance(key, str):
            continue
        out[key] = _text_of(value)
    return out, warnings


def parse_ts_from_id(raw: str) -> str:
    """提问 id 前 14 位 -> ISO 时间；解析不出来返回空串（不猜）。"""
    match = _TS_RE.match(str(raw or ""))
    if not match:
        return ""
    y, mo, d, h, mi, s = (int(x) for x in match.groups())
    if not (1 <= mo <= 12 and 1 <= d <= 31 and h <= 23 and mi <= 59 and s <= 59):
        return ""
    return f"{y:04d}-{mo:02d}-{d:02d}T{h:02d}:{mi:02d}:{s:02d}"


def parse_query_key(value: str) -> Tuple[List[Tuple[str, str]], str]:
    """解析一个 chatQueryCompletion 值 -> ([(id, text)], 错误说明)。"""
    try:
        payload = json.loads(value)
    except (ValueError, TypeError) as exc:
        return [], f"JSON 解析失败: {exc}"
    if not isinstance(payload, dict):
        return [], "值不是对象"
    resp = payload.get("response")
    body = resp.get("resp") if isinstance(resp, dict) else None
    results = body.get("result") if isinstance(body, dict) else None
    if not isinstance(results, list):
        return [], "缺少 response.resp.result"
    out: List[Tuple[str, str]] = []
    for item in results:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        out.append((str(item.get("id") or ""), text))
    return out, ""


def _walk_messages(node: Any, out: List[Tuple[str, str, str]], depth: int = 0) -> None:
    """从任意 JSON 结构里找 {role, content|text}（Trae 改版后的兜底通路）。"""
    if depth > 6 or len(out) >= 500:
        return
    if isinstance(node, dict):
        role = node.get("role")
        body = node.get("content", node.get("text"))
        if isinstance(role, str) and isinstance(body, str) and body.strip():
            if role.lower() in (ROLE_USER, ROLE_ASSISTANT, "system"):
                ts = str(node.get("createdAt") or node.get("timestamp") or "")
                out.append((role.lower(), body.strip(), ts))
                return
        for value in node.values():
            _walk_messages(value, out, depth + 1)
        return
    if isinstance(node, list):
        for item in node:
            _walk_messages(item, out, depth + 1)


@dataclass
class TraeReadResult:
    """Trae 读取结果 + 只读取证（供 preview 报告与盘点文档复用）。"""

    records: List[MemoryRecord] = field(default_factory=list)
    report: Dict[str, Any] = field(default_factory=dict)


def read_trae(*, appdata: Optional[Path] = None, max_records: int = MAX_RECORDS,
              scan_leveldb_dirs: bool = True) -> TraeReadResult:
    """读 Trae 本地记忆（prompt 轨迹 + 兜底消息通路）。

    返回的记录**未经去重/脱敏**：那两件事由 pipeline 统一做，
    这样 Claude / Trae 走同一套口径（单一事实源）。
    """
    report: Dict[str, Any] = {
        "roots": [], "warnings": [], "vscdb_keys": 0, "query_keys": 0,
        "prompts": 0, "unique_prompts": 0, "bad_values": 0, "empty_values": 0,
        "session_index_entries": 0, "leveldb": [], "chat_like_values": 0,
        "messages": 0, "ts_first": "", "ts_last": "",
    }
    records: List[MemoryRecord] = []
    roots = find_trae_roots(appdata)
    report["roots"] = [str(r) for r in roots]
    if not roots:
        report["warnings"].append("未找到 Trae 数据目录（Trae CN / Trae）")
        return TraeReadResult(records, report)

    seen_prompts: Dict[str, str] = {}
    for root in roots:
        vscdb = root / "User" / "globalStorage" / "state.vscdb"
        values, warnings = read_vscdb(vscdb)
        report["warnings"].extend(warnings)
        report["vscdb_keys"] += len(values)
        index_raw = values.get(SESSION_INDEX_KEY, "")
        if index_raw:
            try:
                index = json.loads(index_raw)
                entries = index.get("entries") if isinstance(index, dict) else None
                report["session_index_entries"] += len(entries or {})
            except (ValueError, TypeError):
                report["warnings"].append("chat.ChatSessionStore.index 解析失败")
        for key, value in values.items():
            if not key.startswith(QUERY_KEY_PREFIX):
                continue
            report["query_keys"] += 1
            if not str(value or "").strip():  # 实测 8 个键是空缓存（不是坏数据）
                report["empty_values"] += 1
                continue
            items, error = parse_query_key(value)
            if error:
                report["bad_values"] += 1
                if len(report["warnings"]) < 40:
                    report["warnings"].append(f"{key}: {error}")
                continue
            for item_id, text in items:
                ts = parse_ts_from_id(item_id)
                date = ts[:10] if ts else "unknown"
                session_id = f"trae-prompts-{date}"
                record = MemoryRecord(
                    source=SOURCE_TRAE, session_id=session_id, ts=ts,
                    role=ROLE_USER, text=text, kind=KIND_PROMPT,
                    project=(root.name if len(roots) > 1 else ""),
                    origin=f"{vscdb.name}:{QUERY_KEY_PREFIX}*",
                    meta={"trae_id": item_id, "trae_value": key},
                )
                records.append(record)
                report["prompts"] += 1
                seen_prompts[record.dedupe_key] = ts
                if ts:
                    if not report["ts_first"] or ts < report["ts_first"]:
                        report["ts_first"] = ts
                    if not report["ts_last"] or ts > report["ts_last"]:
                        report["ts_last"] = ts
                if len(records) >= max_records:
                    report["warnings"].append(f"达到记录上限 {max_records}，提前停止")
                    break
            if len(records) >= max_records:
                break

        if not scan_leveldb_dirs:
            continue
        for sub in ("IndexedDB", "Local Storage", "Session Storage"):
            base = root / sub
            if not base.is_dir():
                continue
            dirs: Iterable[Path]
            if sub == "IndexedDB":
                dirs = [p for p in base.glob("*.leveldb") if p.is_dir()]
            else:
                dirs = [base / "leveldb"]
            for db_dir in dirs:
                if not db_dir.is_dir():
                    continue
                scan = leveldb.scan_leveldb(db_dir)
                chat_like = 0
                messages = 0
                for _key, candidate in leveldb.iter_json_values(scan):
                    try:
                        payload = json.loads(candidate)
                    except (ValueError, TypeError):
                        continue
                    found: List[Tuple[str, str, str]] = []
                    _walk_messages(payload, found)
                    if not found:
                        continue
                    chat_like += 1
                    for role, text, ts in found:
                        messages += 1
                        date = str(ts)[:10] if ts else "unknown"
                        records.append(MemoryRecord(
                            source=SOURCE_TRAE, session_id=f"trae-idb-{date}",
                            ts=str(ts or ""), role=role, text=text,
                            kind=KIND_MESSAGE, project=root.name,
                            origin=f"{sub}:{db_dir.name}",
                        ))
                        if len(records) >= max_records:
                            break
                report["chat_like_values"] += chat_like
                report["messages"] += messages
                summary = scan.to_dict(sample=8)
                summary["chat_like_values"] = chat_like
                summary["messages"] = messages
                report["leveldb"].append(summary)
                report["warnings"].extend(scan.warnings[:10])

    report["unique_prompts"] = len(seen_prompts)
    report["total_records"] = len(records)
    return TraeReadResult(records, report)


__all__ = [
    "MAX_RECORDS", "QUERY_KEY_PREFIX", "SESSION_INDEX_KEY", "TraeReadResult",
    "default_trae_appdata", "find_trae_roots", "parse_query_key",
    "parse_ts_from_id", "read_trae", "read_vscdb",
]
