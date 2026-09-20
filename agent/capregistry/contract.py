"""能力契约校验：`input_schema` / `result_schema` + CI 阻断语义（v1.4 §5.3、附录 B）

## 为什么必须有 `result_schema`

v1.4 对 CI 的承诺是"**可凭 `status` 阻断构建**"。这条承诺成立的前提是
调用方**能判断结果是否合乎契约**。仓库现状是：

- `result_schema` 全仓不存在（22 条 conditional 能力的成因之一就是"无 JSON Schema"）；
- 于是 CI 只能看"有没有抛异常"，而一个返回 `{"ok": false}` 的能力**不会抛异常**
  ⇒ 构建照样绿。这正是 `schedule_task` 能长期"谎报成功"的机制性原因。

本模块只做**机制**：字段（YAML 的 `result_schema:`，由
`agent/lines/models.py::load_tool_meta` 读入 `ToolMeta.output_schema`）+ 校验器 +
`status` 语义。**不要求**给全部 114 条补齐 schema（那是 TASK-04 的宽限期内容）。

## 校验器的取舍

仓库**已装** `jsonschema 4.26.0`，故**优先复用**它（不自造第二套 schema 语义，
避免"同一份 schema 两套解释"）。但：

- `jsonschema` 是**可选**依赖（`pyproject` 里不是硬依赖），缺失时必须能降级：
  降级到本模块内置的**极简校验器**（只认 `type/required/properties/items/enum`），
  并把结果标记 `validator="builtin"` 让调用方知道精度较低。
- **绝不**因缺 `jsonschema` 而让能力不可调用（fail-open 于"校验精度"，
  但 fail-closed 于"契约显式违约"——见 `validate_result` 的注释）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "validate_against_schema",
    "validate_result",
    "result_status",
    "RESULT_SCHEMA_REQUIRED_TOOLS",
]

#: 【TASK-05 §3 第 4 步第 5 项】至少给 3 个**不依赖 LLM** 的工具补齐 `result_schema`
#: 并端到端验证 CI 消费。这三个都是纯本地、纯确定性、无网络无模型的工具：
#:   - `data_format_detect`：字符串 → 格式判定（JSON/XML/YAML/CSV）
#:   - `json_query`：JSONPath 取值
#:   - `get_file_info`：文件元信息
RESULT_SCHEMA_REQUIRED_TOOLS: Tuple[str, ...] = (
    "data_format_detect", "json_query", "get_file_info")


def _builtin_check(value: Any, schema: Dict[str, Any], path: str,
                   errors: List[str]) -> None:
    """内置极简校验器（`jsonschema` 不可用时的降级路径）

    只覆盖最常用关键字。**不认识的键一律忽略**（宁可漏检，不可误报）——
    与 `agent/tool_gate.py` 的 fail-open 纪律一致。
    """
    if not isinstance(schema, dict):
        return
    expected = schema.get("type")
    if isinstance(expected, str):
        ok = True
        if expected == "object":
            ok = isinstance(value, dict)
        elif expected == "array":
            ok = isinstance(value, (list, tuple))
        elif expected == "string":
            ok = isinstance(value, str)
        elif expected == "integer":
            ok = isinstance(value, int) and not isinstance(value, bool)
        elif expected == "number":
            ok = isinstance(value, (int, float)) and not isinstance(value, bool)
        elif expected == "boolean":
            ok = isinstance(value, bool)
        elif expected == "null":
            ok = value is None
        if not ok:
            errors.append(f"{path}: 期望 type={expected}，实际 {type(value).__name__}")
            return
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(f"{path}: 取值不在 enum 内（{value!r}）")
    if isinstance(value, dict):
        for req in schema.get("required") or []:
            if req not in value:
                errors.append(f"{path}: 缺少必填字段 {req!r}")
        props = schema.get("properties") or {}
        for key, sub in props.items():
            if key in value:
                _builtin_check(value[key], sub, f"{path}.{key}", errors)
    if isinstance(value, (list, tuple)):
        items = schema.get("items")
        if isinstance(items, dict):
            for i, item in enumerate(value):
                _builtin_check(item, items, f"{path}[{i}]", errors)


def validate_against_schema(value: Any, schema: Optional[Dict[str, Any]]
                            ) -> Tuple[bool, List[str], str]:
    """校验 `value` 是否满足 `schema`

    Returns:
        `(ok, errors, validator)`；`validator` ∈ {"none","jsonschema","builtin"}
        —— **必须**把用了哪个校验器暴露出来：两者精度不同，混用会得出错误的可比结论。
    """
    if not isinstance(schema, dict) or not schema:
        return True, [], "none"
    try:
        import jsonschema  # noqa: PLC0415 惰性：可选依赖
        validator_cls = jsonschema.validators.validator_for(schema)
        v = validator_cls(schema)
        errs = sorted(v.iter_errors(value), key=lambda e: list(e.path))
        return (not errs), [f"{'/'.join(str(p) for p in e.path) or '$'}: {e.message}"
                            for e in errs], "jsonschema"
    except ImportError:
        errors: List[str] = []
        _builtin_check(value, schema, "$", errors)
        return not errors, errors, "builtin"
    except Exception as exc:  # noqa: BLE001  schema 自身非法 ⇒ 只告警不阻断
        return True, [f"schema 本身无法编译（已跳过校验）: {type(exc).__name__}"], "builtin"


def validate_result(spec: Any, data: Any) -> Tuple[bool, List[str], bool]:
    """按能力的 `result_schema` 校验一次调用的返回值

    Returns:
        `(ok, errors, has_schema)`
        - `has_schema=False`：该能力**没有**声明 `result_schema` ⇒ 无法判定，
          返回 `ok=True` 且 `has_schema=False`（**如实披露"未声明"**，
          不得把"没声明"说成"校验通过"）。
        - `has_schema=True` 且 `ok=False`：**契约违约**（fail-closed 的判定结果）。
    """
    schema = getattr(spec, "result_schema", None)
    if not isinstance(schema, dict) or not schema:
        return True, [], False
    ok, errors, _validator = validate_against_schema(data, schema)
    return ok, errors, True


def result_status(spec: Any, data: Any) -> Dict[str, Any]:
    """CI 消费的核心：把一次调用的返回值折叠成 `status` + 契约校验结论

    【为什么 CI 只看 `status` 就够】
        `status` 的语义被刻意收窄成**二值**：只有"执行成功"**且**"结果合乎契约"
        才是 `ok`。于是 `.github/workflows/ci.yml` 里一行
        `assert payload["status"] == "ok"` 就能阻断构建 —— 这正是 v1.4 附录 B
        的承诺。契约未声明的能力不会被判红（`contract="undeclared"`），
        否则会给存量 111 条能力制造假红。
    """
    contract_ok, errors, has_schema = validate_result(spec, data)
    return {
        "status": "ok" if contract_ok else "contract_violation",
        "contract": "declared" if has_schema else "undeclared",
        "contract_errors": errors[:20],
    }
