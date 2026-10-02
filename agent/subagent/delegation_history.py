"""委派记录（「子代理」下拉的"委派记录"数据源）

【为什么需要它】
``SubagentLifecycleManager.list()`` 只列**当前存活的分身容器**，而所有委派都是
"用完即弃"（``destroy_after=True``，见 lifecycle.delegate 的 finally），
所以正常业务里那个列表**几乎恒为空** —— 用户看到的是"业务发生了、面板却什么都没有"。
真正的"发生过什么"需要一条**轻量**记录：本模块就是它。

【不易】契约：
- 文件：``<仓库根>/data/subagent_delegations.jsonl``，一行一次委派，追加写。
  【为什么不放 CWD 相对路径】``data/async_tasks.jsonl`` 用的是 CWD 相对路径，
  换个工作目录启动就写到别处去了；本模块按 ``agent/health/storage.py`` 的既有做法
  以 ``__file__`` 定位仓库根，启动目录无关。
- **只记元信息**：不含交付物正文（``output_text``）—— 那是外来文本（§5.7 机制 1），
  且体量不可控；正文的权威位置仍是 trace / 执行器产物。
- **fail-soft**：写失败只告警，绝不影响委派本身（委派已经跑完了，不能因为记不上账而失败）。
- 读取走 ``agent/jsonl_history.py`` 的尾部窗口，坏行跳过。

【变易】``path`` 可注入（测试隔离，不碰仓库 data/）。
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from agent.jsonl_history import append_jsonl, count_jsonl_lines, read_jsonl_tail

logger = logging.getLogger(__name__)

#: 仓库根：本文件在 ``agent/subagent/`` 下，故要**上溯三层**（subagent → agent → 仓库根）。
#: 【为什么写死层数而不是"找 app_server.py"】路径推导必须一眼可验；层数写错过一次
#: （当时照抄 ``agent/health/storage.py`` 的两层写法 ⇒ 记录落进了 ``agent/data/``），
#: 故配套用例改用"仓库标记文件"断言：见 tests/unit/test_subagent_delegation_history.py。
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 默认记录文件
DEFAULT_PATH = os.path.join(_REPO_ROOT, "data", "subagent_delegations.jsonl")

#: 目标截断长度：下拉菜单只用来"认出这是哪次委派"，不需要全文
GOAL_MAX_CHARS = 160

#: 错误信息截断长度
ERROR_MAX_CHARS = 300


class DelegationHistory:
    """委派记录（追加写 JSONL + 尾部窗口读）"""

    def __init__(self, path: Optional[str] = None):
        self._path = path or DEFAULT_PATH

    @property
    def path(self) -> str:
        return self._path

    # ── 写 ──

    def append(self, record: dict) -> bool:
        """追加一条记录（失败返回 False，不抛出）"""
        return append_jsonl(self._path, record)

    def record_outcome(self, *, ctx: Any, outcome: Any, subagent: str = "",
                       source: str = "") -> bool:
        """从一次委派的 (ctx, outcome) 生成并追加记录

        Args:
            ctx: ``DelegationContext``（八要素）。
            outcome: ``ExecutionOutcome``。
            subagent: 分身名（容器 ``config.name``；批量/临时分身也走这里）。
            source: 委派入口（``ui`` / ``tool`` / ``fan_out`` / ``lifecycle``），
                供 UI 区分"谁发起的"，缺省空串表示未标注。

        Returns:
            True = 已写入；False = 未写入（不影响委派结果）。
        """
        return self.append(self.build_record(
            ctx=ctx, outcome=outcome, subagent=subagent, source=source))

    @staticmethod
    def build_record(*, ctx: Any, outcome: Any, subagent: str = "",
                     source: str = "") -> dict:
        """(ctx, outcome) → 记录字典（纯函数，便于单测；不含交付物正文）"""
        cost = getattr(outcome, "cost", None)
        artifacts = getattr(outcome, "artifacts", ()) or ()
        return {
            "delegation_id": str(getattr(outcome, "delegation_id", "")
                                 or getattr(ctx, "delegation_id", "") or ""),
            "subagent": str(subagent or ""),
            "source": str(source or ""),
            "ok": bool(getattr(outcome, "ok", False)),
            "tier": str(getattr(outcome, "tier", "") or ""),
            "goal": str(getattr(ctx, "goal", "") or "")[:GOAL_MAX_CHARS],
            "duration_ms": round(float(getattr(outcome, "duration_ms", 0.0) or 0.0), 1),
            "trace_id": str(getattr(outcome, "trace_id", "") or ""),
            "error_code": str(getattr(outcome, "error_code", "") or ""),
            "error": str(getattr(outcome, "error", "") or "")[:ERROR_MAX_CHARS],
            "artifact_count": len(artifacts),
            "tokens": _total_tokens(cost),
            "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }

    # ── 读 ──

    def query(self, limit: int = 20) -> list[dict]:
        """最近 limit 条记录，**最新在前**（坏行跳过；文件缺失 = 空列表）"""
        records = read_jsonl_tail(self._path, max(0, int(limit)))
        records.reverse()
        return records

    def total(self) -> Optional[int]:
        """记录总数；文件过大/不可读时为 None（"未统计"，不是 0）"""
        return count_jsonl_lines(self._path)


def _total_tokens(cost: Any) -> Optional[int]:
    """成本记录 → 总令牌数（成本记录缺失/字段异常时为 None）"""
    if cost is None:
        return None
    try:
        return int(cost.total_tokens)
    except (AttributeError, TypeError, ValueError):
        return None


#: 进程级单例（容器写、路由读，共用一份；与 agent/health/storage.py 同形）
delegation_history = DelegationHistory()

__all__ = ["DelegationHistory", "delegation_history", "DEFAULT_PATH",
           "GOAL_MAX_CHARS", "ERROR_MAX_CHARS"]
