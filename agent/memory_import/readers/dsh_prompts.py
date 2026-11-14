"""DeepSeek Harness 提问轨迹读取器（「① 提炼导入」用；只读）。

与 readers/dsh.py（② 全话导入）共用同一批会话文件，但**口径不同**：

- 本模块只取**真人** user/message（data.source.kind == "user"），不取 assistant、
  不取系统注入（system prompt 快照 / 技能目录 / 后台通知 / 协调器转发）；
- 默认跳过 subagent 会话：子代理会话里的 user 消息是协调器转发的任务，不是
  "用户问过什么"（实测 294 个 subagent 会话 / 295 条，与本机真人提问 589 条分开）；
- 产出 MemoryRecord(kind=prompt)，交给 memory_import.pipeline 去噪/去重/按周期分批，
  最终写知识卡片/向量记忆（原文全文仍由 ② 负责）。

【不易】不做摘要、不调 LLM：这里只负责"把提问轨迹读出来"；摘要由 pipeline 的
规则式 digest 完成（prompts 批次不会走 LLM）。
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

import zstandard

from ..models import KIND_PROMPT, ROLE_USER, SOURCE_DSH, MemoryRecord
from . import dsh as dsh_reader
from . import record_cap

#: 单源记录上限默认值（本机实测真人提问 589 条；留足余量）
MAX_RECORDS = 20000
#: 单条正文上限（与 dsh / claude 读取器同口径）
MAX_MESSAGE_CHARS = 20000
#: 只解析这几类事件（与 dsh.py 同口径，兼容紧凑/带空格两种 JSON）
_EVENT_MARKERS = ('"user/message"', '"type":"session"', '"type": "session"')


@dataclass
class DshPromptResult:
    records: List[MemoryRecord] = field(default_factory=list)
    report: Dict[str, Any] = field(default_factory=dict)


def iter_prompt_records(root: Optional[Path], *, stats: Dict[str, Any],
                        include_subagents: bool = False,
                        max_records: int = 0) -> Iterator[MemoryRecord]:
    """逐文件产出 prompt 记录（只读；坏行只计数）。"""
    files = dsh_reader.iter_session_files(root)
    stats["sessions"]["files"] = len(files)
    for path in files:
        session_id = path.parent.name
        project = path.parent.parent.name
        origin = "top"
        skipped = False
        try:
            with path.open("rb") as fh:
                reader = zstandard.ZstdDecompressor().stream_reader(fh)
                text_stream = io.TextIOWrapper(reader, encoding="utf-8", errors="replace")
                for line in text_stream:
                    stats["lines"] += 1
                    if len(line) > dsh_reader.MAX_LINE_CHARS:
                        stats["bad_lines"] += 1
                        continue
                    if not any(marker in line for marker in _EVENT_MARKERS):
                        continue
                    try:
                        obj = json.loads(line)
                    except (ValueError, TypeError):
                        stats["bad_lines"] += 1
                        continue
                    if not isinstance(obj, dict):
                        stats["bad_lines"] += 1
                        continue
                    kind = str(obj.get("type") or "")
                    if kind == "session":
                        session_id = str(obj.get("id") or session_id)
                        origin = str(obj.get("origin") or "top")
                        stats["sessions"]["parsed"] += 1
                        if origin == "subagent" and not include_subagents:
                            stats["sessions"]["subagent_skipped"] += 1
                            skipped = True
                            break
                        continue
                    if kind != "user/message" or skipped:
                        continue
                    data = obj.get("data")
                    if not isinstance(data, dict):
                        stats["bad_lines"] += 1
                        continue
                    source = data.get("source") or {}
                    if not isinstance(source, dict) or str(source.get("kind") or "") != "user":
                        stats["injected_user_messages"] += 1
                        continue
                    body = dsh_reader._text_blocks(data.get("content"))
                    if not body:
                        stats["empty_messages"] += 1
                        continue
                    truncated = len(body) > MAX_MESSAGE_CHARS
                    if truncated:
                        body = body[:MAX_MESSAGE_CHARS]
                        stats["truncated_messages"] += 1
                    ts = dsh_reader._iso_ms(obj.get("time"))
                    record = MemoryRecord(
                        source=SOURCE_DSH, session_id=session_id, ts=ts,
                        role=ROLE_USER, text=body, kind=KIND_PROMPT,
                        project=project, origin=str(path),
                        # 每会话单独成批：单会话最多 50 条提问，规则 digest 不会截断；
                        # 写成 prompts:<project>/<session> 让 pipeline 按它分组（见 build_batches）
                        meta={"dsh_origin": origin, "truncated": truncated,
                              "batch_scope": f"prompts:{project}/{session_id}"},
                    )
                    stats["prompts"] += 1
                    stats["chars"] += len(body)
                    if ts:
                        if not stats["ts_first"] or ts < stats["ts_first"]:
                            stats["ts_first"] = ts
                        if not stats["ts_last"] or ts > stats["ts_last"]:
                            stats["ts_last"] = ts
                    yield record
                    if max_records and stats["prompts"] >= max_records:
                        stats["warnings"].append(f"达到记录上限 {max_records}")
                        return
        except OSError as exc:
            if len(stats["warnings"]) < 20:
                stats["warnings"].append(f"读取失败（{Path(path).name}）：{type(exc).__name__}")


def read_dsh_prompts(*, root: Optional[Path] = None,
                     include_subagents: bool = False,
                     max_records: Optional[int] = None) -> DshPromptResult:
    """读 DSH 的真人提问轨迹（默认不含 subagent 会话）。"""
    cap = record_cap(MAX_RECORDS) if max_records is None else int(max_records)
    base = Path(root) if root else dsh_reader.default_dsh_root()
    stats: Dict[str, Any] = {
        "root": str(base), "exists": base.is_dir(), "lines": 0, "bad_lines": 0,
        "injected_user_messages": 0, "empty_messages": 0, "truncated_messages": 0,
        "prompts": 0, "chars": 0, "ts_first": "", "ts_last": "", "warnings": [],
        "sessions": {"files": 0, "parsed": 0, "subagent_skipped": 0},
    }
    records: List[MemoryRecord] = []
    if not base.is_dir():
        stats["warnings"].append(f"DSH 目录不存在: {base}")
        return DshPromptResult(records, stats)
    for record in iter_prompt_records(root, stats=stats,
                                      include_subagents=include_subagents,
                                      max_records=cap):
        records.append(record)
    stats["total_records"] = len(records)
    return DshPromptResult(records, stats)


__all__ = [
    "MAX_MESSAGE_CHARS", "MAX_RECORDS", "DshPromptResult",
    "iter_prompt_records", "read_dsh_prompts",
]
