"""工具注册模块 — 代码工具（审查、架构图、文本优化、数据处理、调度、异步任务）"""
import logging
from agent import tools as _tools

logger = logging.getLogger(__name__)


def register_all(dl):
    """注册所有代码/开发工具

    Args:
        dl: DigitalLife 实例（用于访问 self 属性）
    """

    # ════════════════════════════════════════════════════════════
    #  代码审查工具
    # ════════════════════════════════════════════════════════════

    @_tools.register("code_review", "执行结构化代码审查，检查代码在安全、性能、可维护性、API兼容性和测试方面的质量。支持审查文件或 git diff。基于 gstack review 检查清单", schema={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要审查的文件路径（绝对路径），可选"},
            "diff": {"type": "string", "description": "git diff 文本内容，可选。如果未提供 path 则使用此内容"},
            "dimensions": {
                "type": "array",
                "items": {"type": "string", "enum": ["安全", "性能", "可维护性", "API兼容性", "测试"]},
                "description": "审查维度列表，默认全部。安全(SQL注入/XSS/密钥泄露)、性能(N+1查询/算法复杂度)、可维护性(死代码/魔法数字)、API兼容性(破坏性变更)、测试(边界值/负路径)",
            },
        },
    })
    def _code_review(**kwargs):
        path = kwargs.get("path", "")
        diff = kwargs.get("diff", "")
        dimensions = kwargs.get("dimensions")
        from agent.code_review import code_review as _code_review
        return _code_review(path=path, diff=diff, dimensions=dimensions)

    # ════════════════════════════════════════════════════════════
    #  架构图工具
    # ════════════════════════════════════════════════════════════

    from agent.diagram_tools import generate_architecture_diagram

    @_tools.register("arch_diagram", "生成系统架构图。根据组件列表生成漂亮的 HTML+SVG 架构图文件，支持多种组件类型（frontend/backend/database/cloud/security/external）。必须同时提供 title（标题）、components（组件列表）和 output_path（输出路径）", schema={
        "type": "object",
        "properties": {
            "title": {"type": "string", "description": "架构图标题（必填）"},
            "components": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "组件名称"},
                        "type": {"type": "string", "description": "组件类型: frontend/backend/database/cloud/security/external"},
                        "description": {"type": "string", "description": "组件描述（可选）"},
                    },
                    "required": ["name", "type"],
                },
                "description": "组件列表（必填）",
            },
            "output_path": {"type": "string", "description": "输出 HTML 文件路径（必填）"},
        },
        "required": ["title", "components", "output_path"],
    })
    def _arch_diagram(**kwargs):
        title = kwargs.get("title", "")
        components = kwargs.get("components", [])
        output_path = kwargs.get("output_path", "")
        if not title and not components and not output_path:
            return {"ok": False, "error": "arch_diagram 需要提供 title（架构图标题）、components（组件列表）和 output_path（输出路径）三个参数。示例: arch_diagram(title=\"系统架构\", components=[{name: \"前端\", type: \"frontend\"}, {name: \"后端\", type: \"backend\"}], output_path=\"/path/to/diagram.html\")"}
        if not title:
            return {"ok": False, "error": f"arch_diagram 缺少 title 参数。请提供架构图标题，如: title=\"系统架构图\". 收到的参数名: {list(kwargs.keys())}"}
        # 权限检查
        perm = dl._permission.check_action(f"write_file:{output_path}", f"生成架构图到 {output_path}")
        if not perm.allowed:
            return {"ok": False, "error": f"权限系统拒绝: {perm.reason}", "blocked": True}
        return generate_architecture_diagram(title, components, output_path)

    # ════════════════════════════════════════════════════════════
    #  中文文本优化工具
    # ════════════════════════════════════════════════════════════

    from agent.text_tools import humanize_zh

    @_tools.register("humanize_zh", "检测中文文本中的 AI 写作痕迹并给出优化建议。基于 24 种 AI 写作模式检测规则（词汇/句式/结构/风格等），返回检测到的模式列表、问题数量和优化建议。aggressive=True 启用更严格的检测模式", schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "待检测的中文文本"},
            "aggressive": {"type": "boolean", "description": "是否启用严格检测模式（检测更多边缘情况，如同长度连续句子等），默认 false"},
        },
        "required": ["text"],
    })
    def _humanize_zh(**kwargs):
        text = kwargs.get("text", "")
        aggressive = kwargs.get("aggressive", False)
        if not text:
            return {"ok": False, "error": "请提供待检测的文本（text）"}
        result = humanize_zh(text, aggressive=aggressive)
        return {"ok": True, **result}

    # ════════════════════════════════════════════════════════════
    #  数据处理工具 — JSON / YAML 查询与转换
    # ════════════════════════════════════════════════════════════

    from agent.data_process_tools import (
        json_query, json_to_yaml, yaml_to_json,
        json_validate, data_format_detect,
    )

    @_tools.register("json_query", "使用 JSONPath 表达式从 JSON 数据中提取信息。支持：$ 根节点、.key 属性、[n] 数组索引、[*] 通配、..key 递归搜索。data 参数接受 JSON 字符串或 Python 对象", schema={
        "type": "object",
        "properties": {
            "data": {"type": "string", "description": "JSON 字符串或 Python 对象（dict/list）"},
            "path": {"type": "string", "description": "JSONPath 表达式，如 $.store.book[0].title 或 $..author"},
        },
        "required": ["data", "path"],
    })
    def _json_query(**kwargs):
        data = kwargs.get("data", "")
        path = kwargs.get("path", "")
        if not path:
            return {"ok": False, "error": "请提供 JSONPath 查询表达式（path）"}
        return json_query(data, path)

    @_tools.register("json_to_yaml", "将 JSON 字符串转换为 YAML 格式字符串", schema={
        "type": "object",
        "properties": {
            "json_data": {"type": "string", "description": "JSON 格式字符串"},
        },
        "required": ["json_data"],
    })
    def _json_to_yaml(**kwargs):
        json_data = kwargs.get("json_data", "")
        if not json_data:
            return {"ok": False, "error": "请提供 JSON 数据（json_data）"}
        return json_to_yaml(json_data)

    @_tools.register("yaml_to_json", "将 YAML 字符串转换为 JSON 格式字符串", schema={
        "type": "object",
        "properties": {
            "yaml_data": {"type": "string", "description": "YAML 格式字符串"},
        },
        "required": ["yaml_data"],
    })
    def _yaml_to_json(**kwargs):
        yaml_data = kwargs.get("yaml_data", "")
        if not yaml_data:
            return {"ok": False, "error": "请提供 YAML 数据（yaml_data）"}
        return yaml_to_json(yaml_data)

    @_tools.register("json_validate", "验证字符串是否为合法 JSON，返回验证结果和解析类型", schema={
        "type": "object",
        "properties": {
            "data": {"type": "string", "description": "待验证的 JSON 字符串"},
        },
        "required": ["data"],
    })
    def _json_validate(**kwargs):
        data = kwargs.get("data", "")
        if not data:
            return {"ok": True, "valid": False, "error": "数据为空"}
        return json_validate(data)

    @_tools.register("data_format_detect", "自动检测字符串属于哪种数据格式，支持 JSON/XML/YAML/CSV，返回格式名称和置信度评分（0~1）。用于识别未知数据来源的格式", schema={
        "type": "object",
        "properties": {
            "data": {"type": "string", "description": "待检测格式的字符串数据"},
        },
        "required": ["data"],
    })
    def _data_format_detect(**kwargs):
        data = kwargs.get("data", "")
        if not data:
            return {"ok": False, "error": "请提供待检测的数据（data）"}
        return data_format_detect(data)

    # ════════════════════════════════════════════════════════════
    #  定时调度工具
    # ════════════════════════════════════════════════════════════

    @_tools.register("schedule_task", "创建定时任务，按指定间隔或 cron 表达式周期性触发。必须提供 interval_minutes 或 cron_expr 之一（或两者）。cron 为 5 字段格式：分 时 日 月 周。支持暂停/恢复/取消管理", schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "任务名称（必填）"},
            "interval_minutes": {"type": "integer", "description": "执行间隔（分钟）。如 5 表示每 5 分钟执行一次"},
            "cron_expr": {"type": "string", "description": "cron 表达式（5字段 分 时 日 月 周）。如 0 9 * * * 表示每天9点，*/5 * * * * 每5分钟"},
            "action": {"type": "string", "description": "任务操作描述，如 run_shell_command"},
            "params": {"type": "object", "description": "执行参数，如 {\"command\": \"echo hello\"}"},
        },
        "required": ["name"],
    })
    def _schedule_task(**kwargs):
        name = kwargs.get("name", "")
        interval_minutes = kwargs.get("interval_minutes", 0)
        cron_expr = kwargs.get("cron_expr", "")
        action = kwargs.get("action", "")
        params = kwargs.get("params", {})

        if not name.strip():
            return {"ok": False, "error": "任务名称不能为空"}
        if interval_minutes <= 0 and not cron_expr.strip():
            return {"ok": False, "error": "必须提供 interval_minutes 或 cron_expr"}
        if cron_expr.strip():
            from agent.scheduling import Scheduler as _SchedValidate
            if not _SchedValidate.validate_cron_expr(cron_expr):
                return {"ok": False, "error": f"无效的 cron 表达式: {cron_expr}"}

        try:
            from agent.scheduling import get_schedule_scheduler
            sched = get_schedule_scheduler()
            result = sched.add_task(
                name=name, action=action, params=params,
                interval_minutes=interval_minutes, cron_expr=cron_expr,
            )
            return result
        except Exception as e:
            return {"ok": False, "error": f"创建任务失败: {e}"}

    @_tools.register("list_scheduled_tasks", "列出所有已创建的定时任务", schema={
        "type": "object",
        "properties": {},
    })
    def _list_scheduled_tasks(**kwargs):
        try:
            from agent.scheduling import get_schedule_scheduler
            sched = get_schedule_scheduler()
            return sched.get_tasks()
        except Exception as e:
            return {"ok": False, "error": f"列出任务失败: {e}"}

    @_tools.register("cancel_scheduled_task", "取消指定的定时任务", schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "任务 ID"},
        },
        "required": ["task_id"],
    })
    def _cancel_scheduled_task(**kwargs):
        task_id = kwargs.get("task_id", "")
        if not task_id:
            return {"ok": False, "error": "请提供 task_id"}
        try:
            from agent.scheduling import get_schedule_scheduler
            sched = get_schedule_scheduler()
            return sched.remove_task(task_id)
        except Exception as e:
            return {"ok": False, "error": f"取消任务失败: {e}"}

    @_tools.register("pause_scheduled_task", "暂停指定的定时任务", schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "任务 ID"},
        },
        "required": ["task_id"],
    })
    def _pause_scheduled_task(**kwargs):
        task_id = kwargs.get("task_id", "")
        if not task_id:
            return {"ok": False, "error": "请提供 task_id"}
        try:
            from agent.scheduling import get_schedule_scheduler
            sched = get_schedule_scheduler()
            return sched.pause_task(task_id)
        except Exception as e:
            return {"ok": False, "error": f"暂停任务失败: {e}"}

    @_tools.register("resume_scheduled_task", "恢复已暂停的定时任务", schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "任务 ID"},
        },
        "required": ["task_id"],
    })
    def _resume_scheduled_task(**kwargs):
        task_id = kwargs.get("task_id", "")
        if not task_id:
            return {"ok": False, "error": "请提供 task_id"}
        try:
            from agent.scheduling import get_schedule_scheduler
            sched = get_schedule_scheduler()
            return sched.resume_task(task_id)
        except Exception as e:
            return {"ok": False, "error": f"恢复任务失败: {e}"}

    # ════════════════════════════════════════════════════════════
    #  异步任务执行工具
    # ════════════════════════════════════════════════════════════

    from agent.async_executor import get_async_executor

    # 初始化全局异步执行器
    _executor_cfg = dl._config.get("async_executor", {})
    _async_exec = get_async_executor(
        max_workers=_executor_cfg.get("max_workers", 3),
        result_ttl=_executor_cfg.get("result_ttl", 3600),
    )

    @_tools.register("submit_task", "提交异步任务在后台执行，立即返回任务ID不阻塞对话。适用于耗时工具（如 web_search、大文件处理等）。用 get_task_status 查状态，用 get_task_result 获取结果", schema={
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "任务名称，便于后续识别（如 '搜索Python教程'）"},
            "tool_name": {"type": "string", "description": "要异步调用的工具名称，如 web_search、compress、execute_shell 等"},
            "params": {"type": "object", "description": "传给工具的参数字典，如 {\"query\": \"Python 教程\", \"num_results\": 5}"},
            "timeout": {"type": "integer", "description": "任务超时秒数（可选）。不设置则不限时"},
        },
        "required": ["name", "tool_name", "params"],
    })
    def _submit_task(**kwargs):
        name = kwargs.get("name", "")
        tool_name = kwargs.get("tool_name", "")
        params = kwargs.get("params", {})
        timeout = kwargs.get("timeout")
        if not name:
            return {"ok": False, "error": "请提供任务名称（name）"}
        if not tool_name:
            return {"ok": False, "error": "请提供要调用的工具名称（tool_name）"}
        if not isinstance(params, dict):
            return {"ok": False, "error": "params 必须是一个字典"}
        return _async_exec.submit(
            name=name,
            tool_name=tool_name,
            params=params,
            timeout=timeout,
        )

    @_tools.register("get_task_status", "查询异步任务的执行状态（pending/running/completed/failed/cancelled）", schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "任务 ID（由 submit_task 返回）"},
        },
        "required": ["task_id"],
    })
    def _get_task_status(**kwargs):
        task_id = kwargs.get("task_id", "")
        if not task_id:
            return {"ok": False, "error": "请提供任务ID（task_id）"}
        return _async_exec.get_status(task_id)

    @_tools.register("get_task_result", "获取异步任务的执行结果。已完成的结果保留 1 小时后自动清理，未完成返回当前状态提示。通常流程：submit_task → get_task_status（轮询） → get_task_result", schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "任务 ID（由 submit_task 返回）"},
        },
        "required": ["task_id"],
    })
    def _get_task_result(**kwargs):
        task_id = kwargs.get("task_id", "")
        if not task_id:
            return {"ok": False, "error": "请提供任务ID（task_id）"}
        return _async_exec.get_result(task_id)

    @_tools.register("cancel_task", "取消正在等待或运行中的异步任务。已完成的无法取消", schema={
        "type": "object",
        "properties": {
            "task_id": {"type": "string", "description": "任务 ID（由 submit_task 返回）"},
        },
        "required": ["task_id"],
    })
    def _cancel_task(**kwargs):
        task_id = kwargs.get("task_id", "")
        if not task_id:
            return {"ok": False, "error": "请提供任务ID（task_id）"}
        return _async_exec.cancel(task_id)

    @_tools.register("list_async_tasks", "列出所有异步任务（含已完成、运行中、等待中）", schema={
        "type": "object",
        "properties": {},
    })
    def _list_async_tasks(**kwargs):
        return _async_exec.list_tasks()
