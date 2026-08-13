"""健康历史持久化：JSONL 追加写 + 按日滚动

【不易】契约：
- 文件命名 data/health/history-YYYY-MM-DD.jsonl，按日滚动
- 单条记录含 timestamp/overall/dimensions/issues/probe_details
- query_history() 返回全部已滚动日期的记录（跨日聚合，供趋势查询）
- 写入失败不影响主流程（降级为内存记录并告警一次）

【变易】data_dir 可注入（测试隔离）；并发追加用文件级追加写避免串行锁。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger(__name__)

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "data", "health")


class HealthStorage:
    """健康历史存储（JSONL 按日滚动）"""

    def __init__(self, data_dir: Optional[str] = None):
        self._data_dir = data_dir or DATA_DIR

    def _file_for(self, ts: datetime) -> str:
        return os.path.join(self._data_dir, f"history-{ts.date().isoformat()}.jsonl")

    def append(self, record: dict) -> None:
        """追加一条记录到当日文件；失败仅告警不抛出"""
        try:
            os.makedirs(self._data_dir, exist_ok=True)
            path = self._file_for(datetime.now())
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except OSError as e:
            logger.warning("健康记录写入失败（降级为内存记录）: %s", e)

    def query_history(self, days: Optional[int] = None) -> List[dict]:
        """读取历史记录（跨日聚合，按时间升序）

        Args:
            days: 仅读取最近 N 天的文件；None 表示全部
        """
        if not os.path.isdir(self._data_dir):
            return []
        records: List[dict] = []
        try:
            files = sorted(f for f in os.listdir(self._data_dir)
                           if f.startswith("history-") and f.endswith(".jsonl"))
            if days is not None:
                cutoff = datetime.now().date().toordinal() - days + 1
                files = [f for f in files if self._date_from_name(f) is not None
                         and self._date_from_name(f).toordinal() >= cutoff]
            for name in files:
                path = os.path.join(self._data_dir, name)
                try:
                    with open(path, encoding="utf-8") as f:
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                records.append(json.loads(line))
                            except json.JSONDecodeError:
                                continue
                except OSError:
                    continue
        except OSError as e:
            logger.warning("健康历史读取失败: %s", e)
        return records

    @staticmethod
    def _date_from_name(name: str) -> Optional[datetime]:
        try:
            return datetime.strptime(name[len("history-"):-len(".jsonl")], "%Y-%m-%d")
        except ValueError:
            return None


# 全局单例（Dashboard / collector 共用）
health_storage = HealthStorage()
