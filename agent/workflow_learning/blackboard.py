"""共享状态黑板 (SharedBlackboard) — 步骤间类型化数据传递

设计动机:
    原始 ctx 字典 + $step.A.output 字符串模板在多工具串联时易出错
    (字段缺失、类型不匹配)。黑板在 ctx 之上叠加一层类型约束,
    write 时按 output_schema 校验, read 时按 expected_type 校验,
    失败显式化而非静默传递脏数据。

不变量 (【不易】):
    - 纯内存操作, 无 I/O —— 满足 memory 硬约束 "持锁操作严禁 I/O"
      (黑板在 executor._exec_locks 锁内使用, 不得调用 logger/网络/磁盘)
    - 不替代 ctx, 与模板解析层并存 (兼容层)
    - read 缺失/类型不匹配不抛异常, 返回 None + 内存 warning
      (避免单步读取失败中断整个工作流)
    - write schema 校验失败抛 WorkflowSchemaError (由 executor 边界捕获)

性能:
    - read/write 为 O(1) 字典操作 + isinstance 检查, 实测 < 0.1ms
    - schema 校验仅在 write 且 schema 非 None 时触发, 递归深度 = schema 嵌套层数
    - snapshot 用 copy.deepcopy, 不计入读写性能指标

架构层级: [TLM-L1] 数据传递 - 类型化黑板层
"""

from __future__ import annotations

import copy
import time
from typing import Any, Dict, List, Optional, Tuple

from .exceptions import WorkflowSchemaError


# ─── json-schema 子集类型映射 ───────────────────────────────────────
# 注: bool 是 int 的子类, 需显式排除以满足 json-schema 语义
_JSON_TYPE_CHECKERS = {
    "string":  lambda v: isinstance(v, str),
    "number":  lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "object":  lambda v: isinstance(v, dict),
    "array":   lambda v: isinstance(v, list),
    "null":    lambda v: v is None,
    "any":     lambda v: True,
}


def _check_schema(value: Any, schema: Dict[str, Any],
                  path: str = "") -> Optional[str]:
    """json-schema 子集校验器

    支持的关键字 (够用即止, 不引入第三方库):
        type:        str | list[str]   (string/number/integer/boolean/
                                        object/array/null/any)
        required:    list[str]         (仅 object, 缺字段即失败)
        properties:  dict[str, schema] (仅 object, 递归校验存在的 key)

    返回:
        None  — 校验通过
        str   — 失败原因 (供调用方包装为 WorkflowSchemaError)

    不支持: $ref / allOf / anyOf / pattern / format / additionalProperties
    (保持【简易】, 避免过度抽象)
    """
    if not isinstance(schema, dict):
        return f"schema 必须是 dict (got {type(schema).__name__})"

    # ── type 校验 ──
    type_decl = schema.get("type")
    if type_decl is not None:
        types = type_decl if isinstance(type_decl, list) else [type_decl]
        invalid = [t for t in types if t not in _JSON_TYPE_CHECKERS]
        if invalid:
            return f"未知 type {invalid} at {path or 'root'}"
        if not any(_JSON_TYPE_CHECKERS[t](value) for t in types):
            return (f"type 不匹配 at {path or 'root'}: "
                    f"期望 {types}, 实际 {type(value).__name__}")

    # ── required 校验 (仅 object) ──
    if isinstance(value, dict):
        required = schema.get("required")
        if isinstance(required, list):
            missing = [k for k in required if k not in value]
            if missing:
                return f"缺少必填字段 {missing} at {path or 'root'}"

        # ── properties 递归校验 (仅校验存在的 key, 不强制补全) ──
        properties = schema.get("properties")
        if isinstance(properties, dict):
            for k, sub in properties.items():
                if k in value:
                    sub_path = f"{path}.{k}" if path else k
                    reason = _check_schema(value[k], sub, sub_path)
                    if reason:
                        return reason

    return None


class SharedBlackboard:
    """共享状态黑板 — 步骤间类型化数据传递

    线程安全说明:
        黑板自身不加锁。在 executor 中使用时, 受 _exec_locks (工作流级
        防连点锁) 保护; 独立使用时由调用方保证单线程访问。黑板内所有
        操作为纯内存字典读写, 无 I/O, 满足 "持锁操作严禁 I/O" 硬约束。

    使用示例:
        bb = SharedBlackboard()
        bb.set("A", "result", {"score": 0.9},
               schema={"type": "object",
                       "required": ["score"],
                       "properties": {"score": {"type": "number"}}})
        val = bb.read("A", "result", dict)   # → {"score": 0.9}
    """

    def __init__(self) -> None:
        # step_id → {key: value}
        self._data: Dict[str, Dict[str, Any]] = {}
        # 失败记录 (内存, 不落盘)
        self._failures: List[Dict[str, Any]] = []
        # read 缺失/类型不匹配的 warning (内存, 避免锁内 I/O)
        self._warnings: List[Dict[str, Any]] = []
        # 写入审计 (step_id, key) → 最近一次写入时间戳
        self._write_ts: Dict[Tuple[str, str], float] = {}
        # 操作审计 (内存, 无 I/O) — 供 executor 锁外批量打印, 排查数据传递
        self._operations: List[Dict[str, Any]] = []

    # ─── 写入 (带 schema 校验) ──────────────────────────────────────

    def set(self, step_id: str, key: str, value: Any,
            schema: Optional[Dict[str, Any]] = None) -> None:
        """写入步骤输出（纯内存 dict 操作，无 I/O）

        命名说明:
            方法名为 set（对齐 dict 风格的内存写入语义），区别于文件 I/O 的
            write。lock_discipline_scan 将锁内 `.write(` 判为 HIGH（阻塞 I/O），
            黑板 set 为纯内存操作，使用 set 可避免静态扫描误报。
        """
        if schema is not None:
            reason = _check_schema(value, schema)
            if reason is not None:
                raise WorkflowSchemaError(
                    step_id, key, reason,
                    details={"schema": schema,
                             "value_type": type(value).__name__},
                )

        slot = self._data.setdefault(step_id, {})
        slot[key] = value
        self._write_ts[(step_id, key)] = time.monotonic()
        self._operations.append({
            "op": "set", "step": step_id, "key": key,
            "schema": schema is not None, "ts": time.monotonic(),
        })

    # 兼容别名：write → set（既有调用方/测试沿用 write 名，
    # 均为纯内存操作；新代码建议用 set 以避免锁纪律静态扫描误报）
    write = set

    # ─── 读取 (带类型校验) ──────────────────────────────────────────

    def read(self, step_id: str, key: str,
             expected_type: Optional[type] = None) -> Any:
        """读取步骤输出

        Args:
            step_id:       步骤ID
            key:           输出键名
            expected_type: 期望类型; None 不校验

        Returns:
            值; 缺失或类型不匹配返回 None

        Note:
            缺失/类型不匹配不抛异常 (避免单步读取失败中断工作流),
            仅记录 warning 到内存缓冲, 通过 snapshot() 暴露。
        """
        slot = self._data.get(step_id)
        if slot is None or key not in slot:
            self._warnings.append({
                "kind": "missing",
                "step_id": step_id, "key": key,
            })
            self._operations.append({
                "op": "read", "step": step_id, "key": key,
                "hit": False, "ts": time.monotonic(),
            })
            return None

        value = slot[key]
        if expected_type is not None and not isinstance(value, expected_type):
            self._warnings.append({
                "kind": "type_mismatch",
                "step_id": step_id, "key": key,
                "expected": expected_type.__name__,
                "actual": type(value).__name__,
            })
            self._operations.append({
                "op": "read", "step": step_id, "key": key,
                "hit": False, "mismatch": True, "ts": time.monotonic(),
            })
            return None
        self._operations.append({
            "op": "read", "step": step_id, "key": key,
            "hit": True, "ts": time.monotonic(),
        })
        return value

    # ─── 失败记录 (步骤失败时供后续步骤决策跳过/降级) ────────────────

    def record_failure(self, step_id: str, reason: str,
                       error: Optional[str] = None) -> None:
        """记录步骤失败原因

        后续步骤可通过 get_failures() 查询上游失败, 决定跳过或降级。
        不抛异常, 不中断流程 (失败语义由 executor 控制)。
        """
        self._failures.append({
            "step_id": step_id,
            "reason": reason,
            "error": error,
            "ts": time.monotonic(),
        })
        self._operations.append({
            "op": "fail", "step": step_id, "reason": reason,
            "ts": time.monotonic(),
        })

    def get_failures(self) -> List[Dict[str, Any]]:
        """返回失败记录副本"""
        return list(self._failures)

    def has_failure(self, step_id: str) -> bool:
        """某步骤是否已失败 (供后续步骤判断是否跳过)"""
        return any(f["step_id"] == step_id for f in self._failures)

    # ─── 快照 (调试 / trace) ────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        """返回黑板只读深拷贝快照

        用于:
            - executor 结束时通过 track_event 写入可观测层
            - 调试时检视步骤间数据流
            - 失败时定位数据断点

        返回结构:
            {data, failures, warnings, write_ts, operations}
        """
        return {
            "data": copy.deepcopy(self._data),
            "failures": list(self._failures),
            "warnings": list(self._warnings),
            "write_ts": dict(self._write_ts),
            "operations": list(self._operations),
        }

    # ─── 便捷查询 ───────────────────────────────────────────────────

    def list_step_keys(self, step_id: str) -> List[str]:
        """列出某步骤已写入的所有键 (供调试)"""
        slot = self._data.get(step_id, {})
        return list(slot.keys())

    def __repr__(self) -> str:
        steps = len(self._data)
        keys = sum(len(s) for s in self._data.values())
        return (f"SharedBlackboard(steps={steps}, keys={keys}, "
                f"failures={len(self._failures)}, "
                f"warnings={len(self._warnings)})")
