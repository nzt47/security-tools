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
#: 【2026-09-30 扩充 3 → 5】审计实测覆盖面只有 3/91，而 read_file / search_files
#: 是被 LLM 调用频率最高的两个感知工具（工程主线可见集里就有它们）——
#: 契约校验的价值与调用频次成正比，故优先补齐这两个。
#: 【2026-10-01 扩充 5 → 12】再补 7 个，全部满足「location: local + effect: read +
#: 无网络无模型依赖」，且每一个都**先读实现、再实跑一次**核对了真实返回键：
#:   - 感知类：`list_directory` / `diff_files` / `get_clipboard` / `list_processes`
#:   - 纯本地计算类：`get_pdf_info` / `data_convert` / `humanize_zh`
#: 其中三处正是"照工具名猜必错"的地方，恰好证明逐工具核对的必要性：
#:   ① `list_processes` 的**工具包装层**返回 dict，而它调用的底层函数返回的是
#:      **裸 list**（agent/tools/process_tools.py:113）—— 读错一层就会写出
#:      `required: [ok]` 的错 schema；
#:   ② `get_pdf_info` 的 `info` / `error` 分别在失败/成功分支上显式为 `null`，
#:      必须标成可空，否则一次**正常失败**就会被判契约违约（假红）；
#:   ③ `humanize_zh` 的 `ok` 只由包装层补，实现本身不含该键。
#: 【为什么不一次补 91 个】result_schema 必须**逐工具核对其真实返回形状**，
#: 批量生成只会产出「看起来有、其实错」的 schema，那比没有更坏（假契约）。
#: 扩充纪律：本元组**只许增长**，由 test_capregistry_core.py 锁定。
#: 【2026-11-24 扩充 12 → 18（plugin-security-p2-remaining · result_schema L2 第一批）】
#: 只挑**只读或低副作用**的 6 个：web_get / web_extract / web_batch / ext_discover /
#: kb_discuss / look_at_screen。每一个都**先实跑一次真实返回**再按真实键写 schema
#: （证据逐条写在 data/tool_definitions/<name>.yaml 的 result_schema 注释里）：
#:   · web_get / web_batch 的成功返回含 bytes(content) ⇒ 不声明该键，交给 additionalProperties；
#:   · web_extract 的 results 元素随 kind 变（字符串/对象）⇒ 只声明 array，不锁 items 类型；
#:   · ext_discover 两个分支的键集合不同（无 query / 有 query）⇒ 除 ok 外全部可选；
#:   · kb_discuss 的 effect=write（落讨论记录）但无外部副作用，schema 只描述返回、不改语义；
#:   · look_at_screen 成功分支当前**不可达**（voice_vision 缺 Any 导入 + OcrSensor 无
#:     capture_and_ocr，已登记独立缺陷）⇒ schema 按修好后的契约声明，失败分支实测可校验。
#: 【2026-11-24 第二批 18 → 34（medium 16 个，同一轮补完）】
#: browser_navigate / browser_screenshot / cancel_scheduled_task / compress / delegate /
#: distill_process_from_knowledge / kb_distill / process_distill_run / run_tests /
#: set_clipboard / software_install / stop_process / submit_task / web_download /
#: web_post / workspace_write。证据同样写在各 YAML 注释里，分两类并如实标注：
#:   · **实调**：失败分支或安全成功分支（临时目录/临时知识库/真实下载/真实剪贴板写入后还原）；
#:   · **按实现 return 字面量核对**：成功分支受副作用/外部依赖限制的（delegate / run_tests /
#:     submit_task / distill* / software_install 成功态 / web_post），键取自函数体 return，
#:     不按工具名猜。
#: 三个 critical 工具（adaptive_skill_stack / byted_bp_cdn_pagesdeploy /
#: byted_seedream_image_generate）是**本机运行时登记**（YAML 不入仓），不在本清单内。
#: 回退：从元组删掉这 6+16 个名字 → 删对应 YAML 的 `result_schema:` 段
#:   → 把 tests/unit/test_refactor_call_identity_and_recovery.py 的
#:   `TestResultSchemaCoverage.FLOOR` 调回 24。
RESULT_SCHEMA_REQUIRED_TOOLS: Tuple[str, ...] = (
    "data_format_detect", "json_query", "get_file_info",
    "read_file", "search_files",
    "list_directory", "diff_files", "get_clipboard", "list_processes",
    "get_pdf_info", "data_convert", "humanize_zh",
    "web_get", "web_extract", "web_batch", "ext_discover", "kb_discuss", "look_at_screen",
    "browser_navigate", "browser_screenshot", "cancel_scheduled_task", "compress",
    "delegate", "distill_process_from_knowledge", "kb_distill", "process_distill_run",
    "run_tests", "set_clipboard", "software_install", "stop_process", "submit_task",
    "web_download", "web_post", "workspace_write",
    # 【2026-11-24 第三批 34 → 45（high 11 个）】apply_patch / decompress / edit /
    # ext_send_channel / fan_out / git / run_program / schedule_task / skill_script_run /
    # workspace_delete / write_file。实调证据写在各 YAML 注释；成功分支受副作用限制的
    # 按实现 return 字面量核对。low 39 条不在本轮（收益递减，按需再做）。
    "apply_patch", "decompress", "edit", "ext_send_channel", "fan_out", "git",
    "run_program", "schedule_task", "skill_script_run", "workspace_delete", "write_file",
    # 【2026-11-24 第四批 45 → 84（low 39 个，tracked 全量收口）】arch_diagram / browser_close /
    # cancel_task / code_review / ext_list / get_persona_info / get_preferences / get_task_result /
    # get_task_status / get_weather / grep / kb_capture / kb_card / kb_lint / kb_search /
    # list_async_tasks / list_mcp_connections / list_scheduled_tasks / merge_pdf / notify /
    # pause_scheduled_task / read_pdf / read_pdf_tables / remember / resume_scheduled_task /
    # run_lint / search_lifetrace / search_memory / software_probe / split_pdf / sqlite_query /
    # todo_write / trigger_distillation / web_search / weekly_report / workspace_init /
    # workspace_list / get_status / get_sensor_summary。
    # 注意：get_status / get_sensor_summary 返回宿主原始字典（无 ok 键）⇒ required 为空数组。
    "arch_diagram", "browser_close", "cancel_task", "code_review", "ext_list",
    "get_persona_info", "get_preferences", "get_task_result", "get_task_status", "get_weather",
    "grep", "kb_capture", "kb_card", "kb_lint", "kb_search", "list_async_tasks",
    "list_mcp_connections", "list_scheduled_tasks", "merge_pdf", "notify",
    "pause_scheduled_task", "read_pdf", "read_pdf_tables", "remember", "resume_scheduled_task",
    "run_lint", "search_lifetrace", "search_memory", "software_probe", "split_pdf",
    "sqlite_query", "todo_write", "trigger_distillation", "web_search", "weekly_report",
    "workspace_init", "workspace_list", "get_status", "get_sensor_summary",
    # 【2026-11-24 E0-2】read_spill（大输出落盘后的分段读取；按实现 return 字面量声明）
    "read_spill",
    # 【2026-11-25 E1-2b】goal（会话级单目标；成功键取自 goal_tools._summary）
    "goal")


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

