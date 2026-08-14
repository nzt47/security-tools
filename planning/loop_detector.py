"""状态哈希与决策循环检测 — 任务5 步骤1（D6 另一半：状态哈希终止保障）

将"决策链路循环"的检测从动作字符串匹配升级为状态哈希：
- state_hash：对 ReAct 思考结果（动作类型 + 工具名 + 参数摘要 + 关键上下文摘要）
  生成稳定指纹（sha1 前 16 位）——同状态同指纹，不同状态指纹不同；
- check：在回溯窗口内统计指纹出现次数，达到 max_repeats（默认 3）返回
  LoopSignal 终止信号（含解释性摘要），由上层决定降级/人工（衔接任务 7）。

【不易】约束：
- 纯模块，不依赖 planning.models（鸭子类型），不接入 ReActLoop.run，
  不改变现有 _detect_loop 行为（既有 30 个循环测试不受影响）；
- 哈希仅覆盖"可序列化状态"（动作意图 + 参数 + 上下文摘要），不涉及 LLM 内部状态。

对外接口：
- LoopDetector(max_repeats=3, window=8)
- LoopDetector.state_hash(thought, context=None, step_index=None) -> str
- LoopDetector.check(current_hash) -> LoopSignal | None
- LoopDetector.reset()
"""

import hashlib
import json
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, Optional


@dataclass
class LoopSignal:
    """循环检测终止信号

    terminate 恒为 True（检测到即终止）；summary 为人类可读的解释性摘要
    （重复的动作序列与参数摘要），写入结果与日志供上层决策。
    """
    repeated_hash: str       # 触发循环的状态哈希
    occurrences: int         # 窗口内出现次数
    summary: str             # 人类可读摘要：重复的动作/工具/参数/上下文
    terminate: bool = True


# 摘要裁剪上限（避免长参数/长上下文污染指纹长度与内存）
_MAX_VALUE_CHARS = 80
_MAX_CTX_KEYS = 8
_MAX_PARAM_KEYS = 16


def _summarize(value: Any) -> str:
    """稳定序列化单个值：标量直接 str，容器走 json（键排序、非 ASCII 保留）。"""
    if isinstance(value, (dict, list, tuple)):
        try:
            return json.dumps(value, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            return str(value)
    return str(value)


def _summarize_params(params: Dict[str, Any]) -> str:
    """参数 key 排序摘要（值截断）——同动作不同参数（key 或值）产生不同指纹。"""
    items = []
    for k in sorted(params.keys())[: _MAX_PARAM_KEYS]:
        v = _summarize(params[k])
        if len(v) > _MAX_VALUE_CHARS:
            v = v[: _MAX_VALUE_CHARS]
        items.append(f"{k}={v}")
    return "|".join(items)


class LoopDetector:
    """状态哈希 + 决策循环检测

    Args:
        max_repeats: 同一状态哈希在窗口内达到该次数即判定循环（默认 3）
        window: 哈希回溯窗口（步数），超出窗口的旧状态不再计入计数
    """

    def __init__(self, max_repeats: int = 3, window: int = 8):
        self.max_repeats = max_repeats
        self.window = window
        self._history: Deque[str] = deque()
        self._descriptions: Dict[str, str] = {}

    def state_hash(self, thought: Any, context: Optional[Dict[str, Any]] = None,
                   step_index: Optional[int] = None) -> str:
        """生成状态指纹：动作类型 + 工具名 + 参数 key 排序摘要 + 关键上下文摘要。

        step_index 不参与指纹（每步递增会导致同状态指纹漂移，破坏"状态"语义），
        仅保留签名以对齐任务文档接口。
        """
        parts: list = []

        # 1) 动作类型（ThoughtResult.action_type 或 Action.action_type）
        #    防御：规则思考路径的 action 可能是 mock/非标准对象，一律强制 str
        action_type = getattr(thought, "action_type", "") or ""
        action = getattr(thought, "action", None)
        if not isinstance(action_type, str):
            action_type = str(action_type)

        # 2) 工具名（Action.tool_name，推理/响应类动作可能缺失或非 str）
        tool_name = ""
        if action is not None:
            raw_name = getattr(action, "tool_name", None)
            tool_name = raw_name if isinstance(raw_name, str) else ""

        # 3) 参数 key 排序摘要（Action.tool_params）
        params: Dict[str, Any] = {}
        if action is not None:
            raw_params = getattr(action, "tool_params", None)
            if isinstance(raw_params, dict):
                params = raw_params
        param_summary = _summarize_params(params)

        # 4) 关键上下文摘要：非私有键（跳过 _ 开头），键排序 + 值摘要
        ctx_summary = ""
        if context:
            items = []
            for k in sorted(context.keys())[: _MAX_CTX_KEYS]:
                if k.startswith("_"):
                    continue
                v = _summarize(context[k])
                if len(v) > _MAX_VALUE_CHARS:
                    v = v[: _MAX_VALUE_CHARS]
                items.append(f"{k}={v}")
            ctx_summary = "|".join(items)

        parts = [action_type, tool_name, param_summary, ctx_summary]
        raw = "::".join(parts)
        fp = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

        # 记录该指纹的人类可读描述，供 check 生成解释性摘要
        self._descriptions.setdefault(fp, self._describe(parts))
        return fp

    def check(self, current_hash: str) -> Optional[LoopSignal]:
        """记录当前状态哈希出现，窗口内达到 max_repeats 返回终止信号。"""
        self._history.append(current_hash)
        if len(self._history) > self.window:
            self._history.popleft()

        occurrences = sum(1 for h in self._history if h == current_hash)
        if occurrences >= self.max_repeats:
            return LoopSignal(
                repeated_hash=current_hash,
                occurrences=occurrences,
                summary=self._descriptions.get(current_hash, current_hash),
            )
        return None

    def reset(self) -> None:
        """清空历史计数（新任务开始前调用，避免跨任务状态污染）。"""
        self._history.clear()
        self._descriptions.clear()

    def _describe(self, parts: list) -> str:
        """由 state_hash 分解部件生成人类可读摘要（动作/工具/参数/上下文）。"""
        action_type, tool_name, param_summary, ctx_summary = parts
        desc = f"动作={action_type or '?'}"
        if tool_name:
            desc += f" 工具={tool_name}"
        if param_summary:
            desc += f" 参数=[{param_summary}]"
        if ctx_summary:
            desc += f" 上下文=[{ctx_summary}]"
        return desc
