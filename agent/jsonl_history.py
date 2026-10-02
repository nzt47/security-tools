"""JSONL 尾部读取 / 追加 / 计数（"历史记录"类数据源的公共小工具）

【为什么单独一个模块】
后台任务历史（``agent/async_executor.py`` 写的 ``data/async_tasks.jsonl``）与委派记录
（``agent/subagent/delegation_history.py`` 写的 ``data/subagent_delegations.jsonl``）
都需要"读最近 N 条 + 总数"。两份实现必然漂移 —— 本仓已多次出现"同一口径写两遍、
两处慢慢长歪"的缺陷（例如结果字段映射、鉴权判据），故在此收口成一份。

【不易】契约（三条都是 fail-soft，调用方不必再包 try）：
- 文件缺失 / 不可读 / 日志被别的进程写坏 ⇒ 返回"空结果"，**绝不抛出**。
  理由：历史展示永远是附带信息，不能让"读历史失败"把主流程（列后台任务、列分身）打挂。
- ``read_jsonl_tail`` 只读文件**尾部窗口**（默认 256 KiB）：历史无限增长时，
  内存占用与耗时都不随之增长。窗口首行可能是被截断的半行，丢弃。
- 坏行（非法 JSON）跳过而不是中断：一行脏数据不该让整段历史消失。
- 返回顺序 = **文件原序（旧 → 新）**；要"最新在前"的调用方自行反转。

【变易】``max_bytes`` / ``max_count_bytes`` 可注入，便于测试窗口截断与超大文件分支。
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional

logger = logging.getLogger(__name__)

#: 尾部读取的默认窗口大小（字节）。取 256 KiB：足够装下数百条记录，
#: 又不会因为历史文件涨到 GB 级而把整个文件读进内存。
DEFAULT_TAIL_BYTES = 256 * 1024

#: 计数的默认上限（字节）。超过则不计数并返回 None —— 宁可"未统计"，
#: 也不返回一个看着精确、实则截断的数字（静默失真比缺失更难查）。
DEFAULT_COUNT_BYTES = 64 * 1024 * 1024


def append_jsonl(path: str, record: dict) -> bool:
    """追加一条记录（一行 JSON）；失败仅告警并返回 False

    Args:
        path: 目标 JSONL 文件路径；父目录不存在时自动创建。
        record: 待写入的记录（必须可 JSON 序列化）。

    Returns:
        True = 写入成功；False = 写入失败（调用方**不应**因此改变主流程行为）。
    """
    try:
        parent = os.path.dirname(os.path.abspath(path))
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        return True
    except (OSError, TypeError, ValueError) as e:
        # TypeError/ValueError：record 不可序列化（调用方给了非 JSON 值）。
        # 这属于调用方缺陷，但同样不该让"记历史"打挂业务，故一并收口并告警。
        logger.warning("JSONL 追加失败: path=%s err=%s", path, e)
        return False


def read_jsonl_tail(path: str, limit: int, *,
                    max_bytes: int = DEFAULT_TAIL_BYTES) -> list[dict]:
    """读取文件末尾最多 limit 条记录（文件原序：旧 → 新）

    Args:
        path: JSONL 文件路径。
        limit: 最多返回多少条；<= 0 直接返回空列表。
        max_bytes: 尾部窗口上限（字节）。文件大于该值时只读最后这一段，
            因此返回的是"最近 max_bytes 字节内的最后 limit 条"。

    Returns:
        解析成功的记录列表（只保留 JSON 对象；数组/标量行跳过）。
    """
    if limit <= 0:
        return []
    try:
        size = os.path.getsize(path)
    except OSError:
        return []  # 文件不存在 = 还没有历史，不是错误
    if size <= 0:
        return []

    window = max(0, int(max_bytes))
    start = max(0, size - window) if window else size
    try:
        with open(path, "rb") as f:
            if start:
                f.seek(start)
            blob = f.read()
    except OSError as e:
        logger.warning("JSONL 尾部读取失败: path=%s err=%s", path, e)
        return []

    lines = blob.decode("utf-8", errors="replace").splitlines()
    if start > 0 and lines:
        # 从字节偏移处切开，首行大概率是半行 —— 丢弃，不能让它变成一条假记录
        lines = lines[1:]
    return _parse_lines(lines[-limit:])


def _parse_lines(lines: list[str]) -> list[dict]:
    """逐行解析（坏行跳过，保持原序）"""
    records: list[dict] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj: Any = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            records.append(obj)
    return records


def count_jsonl_lines(path: str, *,
                      max_count_bytes: int = DEFAULT_COUNT_BYTES) -> Optional[int]:
    """统计非空行数；文件超过 max_count_bytes 时返回 None（"未统计"）

    分块流式计数（不整读进内存）。

    Args:
        path: JSONL 文件路径。
        max_count_bytes: 计数上限。超过即返回 None，避免在每次轮询里
            对一个超大文件做全量扫描。

    Returns:
        行数（int）；文件不存在 = 0；超过上限或读取失败 = None。
    """
    try:
        size = os.path.getsize(path)
    except OSError:
        return 0
    if size <= 0:
        return 0
    if size > max(0, int(max_count_bytes)):
        return None

    count = 0
    tail = b""
    try:
        with open(path, "rb") as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                data = tail + chunk
                lines = data.split(b"\n")
                tail = lines.pop()  # 最后一段可能是半行，留给下一块
                count += sum(1 for ln in lines if ln.strip())
            if tail.strip():
                count += 1
    except OSError as e:
        logger.warning("JSONL 计数失败: path=%s err=%s", path, e)
        return None
    return count
