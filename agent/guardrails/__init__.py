# P6 安全护栏 — Guardrails
"""P6 安全护栏 — Guardrails

【注入防御六机制（TASK-S4-03 / v7.2 §5.7）——模块地图】

    foreign_taint.py         机制 1  Taint 标记：外来文本禁入 system prompt 与决策分支
    instruction_data.py      机制 2  指令/数据分离：工具参数只能由决策层生成
    capability_exposure.py   机制 3  能力最小暴露：sub_agent 工具集裁剪契约（S4-04 落地）
    egress_chain.py          机制 4  出域链路监测：读密钥→外发链路，命中即熔断 + 事故卡
    boundary_words.py        机制 5  人机边界词：永不自动化五类 + 单次 action + 60s 时效
    safe_render.py           机制 6  UI 安全渲染：HTML 白名单 / CSP / 结构化槽位
    injection_defense.py     六机制统一接线与总闸门（`guard_tool_execution` / `defense_status`）

    （机制 ⑦ 审批面安全由 S4-01 `agent/security/` 承担，本包不重造。）

【既有模块（TASK-S4-02 交付，本任务一行未改）】

    egress_guard.py          出域**执行点**（P7.1-20 执行侧；被 `HttpClient` 调用）
    input_guard.py           输入护栏（8 种注入模式检测）
    output_guard.py          输出护栏
    output_schema.py         输出 schema 校验 + 熔断/降级
    observability.py         护栏埋点

【两套 taint 的边界（勿混用）】

    `agent.policy.taint`             = **密钥材料**污点（读密钥 → 出域拒绝；§5.7 机制 4）
    `agent.guardrails.foreign_taint` = **外来文本**污点（注入防御；§5.7 机制 1）
    语义正交，可同时命中，各自独立记账。

【注意】本包不做 eager import：`injection_defense` 等模块的跨包依赖全部延迟导入，
保持 `import agent.guardrails` 的轻量契约。
"""

__all__ = [
    # 既有（S4-02）
    "egress_guard", "input_guard", "output_guard", "output_schema", "observability",
    # 新增（S4-03 注入防御六机制）
    "foreign_taint", "instruction_data", "capability_exposure",
    "egress_chain", "boundary_words", "safe_render", "injection_defense",
]
