"""黑匣子日志 — JSONL 格式，按文件大小滚动，支持查询与分析"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path


class BlackBoxError(Exception):
    """黑匣子操作异常"""
    pass


class BlackBox:
    """黑匣子日志系统

    以 JSONL 格式记录事件，按文件大小自动滚动。
    支持按时间、事件类型、关键字查询。

    文件命名：blackbox_001.jsonl, blackbox_002.jsonl, ...
    """

    def __init__(self, log_dir: str = "./memory_data/blackbox",
                 max_size_bytes: int = 10 * 1024 * 1024,
                 max_files: int = 10):
        self.log_dir = Path(log_dir)
        self.max_size_bytes = max_size_bytes
        self.max_files = max_files
        self._counter = 0
        self._ensure_dir()

    def _ensure_dir(self):
        self.log_dir.mkdir(parents=True, exist_ok=True)

    def _get_current_file(self) -> Path:
        """获取当前写入文件（最新编号的文件）"""
        files = sorted(self.log_dir.glob("blackbox_*.jsonl"))
        if not files:
            return self.log_dir / "blackbox_001.jsonl"
        return files[-1]

    def _next_file(self) -> Path:
        """创建下一个编号的文件"""
        files = sorted(self.log_dir.glob("blackbox_*.jsonl"))
        if not files:
            return self.log_dir / "blackbox_001.jsonl"
        last_num = int(files[-1].stem.split("_")[1])
        new_file = self.log_dir / f"blackbox_{last_num + 1:03d}.jsonl"
        self._enforce_max_files()
        return new_file

    def _enforce_max_files(self):
        """删除超出 max_files 的最旧文件"""
        files = sorted(self.log_dir.glob("blackbox_*.jsonl"))
        while len(files) >= self.max_files:
            files[0].unlink()
            files = sorted(self.log_dir.glob("blackbox_*.jsonl"))

    def log(self, event_type: str, data: dict) -> str:
        """记录一条事件日志，返回事件 ID"""
        self._counter += 1
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        entry = {
            "id": f"bb_{self._counter:04d}",
            "timestamp": timestamp,
            "event_type": event_type,
            "data": data
        }
        line = json.dumps(entry, ensure_ascii=False) + "\n"

        current = self._get_current_file()
        if current.exists() and current.stat().st_size + len(line.encode()) > self.max_size_bytes:
            current = self._next_file()

        try:
            with open(current, "a", encoding="utf-8") as f:
                f.write(line)
        except OSError as e:
            raise BlackBoxError(f"写入日志失败: {e}") from e

        return entry["id"]

    def query(self, event_type: str = None, start: str = None,
              end: str = None, search: str = None,
              limit: int = 100) -> list[dict]:
        """查询日志条目

        Args:
            event_type: 按事件类型精确过滤
            start: 起始时间（含），ISO 格式字符串
            end: 结束时间（含），ISO 格式字符串
            search: 在 data 字段中搜索关键字
            limit: 最大返回条数

        Returns:
            按时间倒序排列的日志条目列表
        """
        results = []
        files = sorted(self.log_dir.glob("blackbox_*.jsonl"), reverse=True)

        for file_path in files:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        if event_type and entry.get("event_type") != event_type:
                            continue
                        if start and entry.get("timestamp", "") < start:
                            continue
                        if end and entry.get("timestamp", "") > end:
                            continue
                        if search:
                            data_str = json.dumps(entry.get("data", {}), ensure_ascii=False)
                            if search not in data_str:
                                continue

                        results.append(entry)
                        if len(results) >= limit:
                            return results
            except OSError:
                continue

        return results

    def analyze(self, event_type: str = None) -> dict:
        """统计分析日志

        Args:
            event_type: 指定事件类型，返回该类型的统计信息

        Returns:
            未指定类型时：{event_type: count, ...}
            指定类型时：{count: N}
        """
        files = sorted(self.log_dir.glob("blackbox_*.jsonl"), reverse=True)
        type_counts = {}

        for file_path in files:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        et = entry.get("event_type", "unknown")
                        type_counts[et] = type_counts.get(et, 0) + 1
            except OSError:
                continue

        if event_type:
            return {"count": type_counts.get(event_type, 0)}
        return type_counts
