"""结构化审计日志——Append-only 追加写入

每条审计记录包含：时间戳、Trace_ID、操作类型、输入输出哈希、调用栈深度
"""
import json
import hashlib
import traceback
import logging
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional

from agent.observability.tracer import get_trace_id

logger = logging.getLogger(__name__)


class AuditLogger:
    """审计日志记录器——Append-only"""

    def __init__(self, log_dir: str = "./data/audit/"):
        self._log_dir = Path(log_dir)
        self._log_dir.mkdir(parents=True, exist_ok=True)
        self._current_file = self._log_dir / f"audit_{datetime.now().strftime('%Y%m%d')}.jsonl"

    def log(self, action: str, input_data: str = "", output_data: str = "",
            status: str = "success", metadata: dict = None):
        """记录一条审计日志"""
        record = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "trace_id": get_trace_id() or "",
            "action": action,
            "input_hash": self._hash(input_data) if input_data else "",
            "output_hash": self._hash(output_data) if output_data else "",
            "stack_depth": len(traceback.extract_stack()),
            "status": status,
            "metadata": metadata or {},
        }
        with open(self._current_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
        logger.debug(f"[Audit] {action}: status={status}")

    def _hash(self, data: str) -> str:
        return hashlib.sha256(data.encode()).hexdigest()[:16]

    def query(self, trace_id: str = "", action: str = "",
              limit: int = 100) -> list[dict]:
        """查询审计日志"""
        results = []
        for log_file in sorted(self._log_dir.glob("audit_*.jsonl"), reverse=True):
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    record = json.loads(line.strip())
                    if trace_id and record.get("trace_id") != trace_id:
                        continue
                    if action and record.get("action") != action:
                        continue
                    results.append(record)
                    if len(results) >= limit:
                        return results
        return results


audit_logger = AuditLogger()
