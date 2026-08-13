"""规划模块评测集（阶段 4 规划质量基线）

任务分类（对应任务提示词：简单 / 多步 / 并行 / 失败注入）：
- simple:             chat 直连（不触发规划），验证非规划路径不误入
- multi_step:         plan + execute 多工具串行执行
- parallel:           plan + execute 并行组（mock LLM 分解出无依赖任务，
                      executor.parallel_execution=true 并发执行）
- failure_injection:  工具失败注入（抛异常 / 超时），验证失败归因进入 KPI 分布

约定：
- 任务描述含工具英文名（executor find_tool 策略 1 子串匹配），保证确定性；
- 可选 "llm_responses"：LLM JSON 响应列表（按序消费，mock LLM）；缺省走规则
  降级路径（无 LLM），评测结果可复现；
- "expect_planning" 标记该任务走规划路径（plan+execute），False 走 chat 直连。
"""

import asyncio
import json


def _raise_bad():
    """失败注入：普通异常"""
    raise RuntimeError("boom")


async def _slow_tool():
    """失败注入：异步慢工具（配合 tool_timeout 触发超时）"""
    await asyncio.sleep(2)
    return "done"


PLANNING_EVAL_SET = [
    # ── 简单（direct chat，不触发规划）─────────────────────────────────────
    {
        "id": "simple_greet",
        "category": "simple",
        "description": "你好",
        "tools": {},
        "expect_planning": False,
    },
    {
        "id": "simple_thanks",
        "category": "simple",
        "description": "谢谢",
        "tools": {},
        "expect_planning": False,
    },
    {
        "id": "simple_goodbye",
        "category": "simple",
        "description": "再见",
        "tools": {},
        "expect_planning": False,
    },

    # ── 多步（plan + execute 串行工具执行）─────────────────────────────────
    {
        "id": "multi_open_save",
        "category": "multi_step",
        "description": "首先运行 open_file，然后运行 save_file",
        "tools": {"open_file": lambda: "文件已打开", "save_file": lambda: "文件已保存"},
        "expect_planning": True,
    },
    {
        "id": "multi_three_steps",
        "category": "multi_step",
        "description": "首先运行 step_a，然后运行 step_b，最后运行 step_c",
        "tools": {"step_a": lambda: "A", "step_b": lambda: "B", "step_c": lambda: "C"},
        "expect_planning": True,
    },
    {
        "id": "multi_read_write",
        "category": "multi_step",
        "description": "首先运行 read_file，然后运行 write_file",
        # write_file/send_email 会被 executor._extract_params 注入 filename/content、
        # to/subject/body 等参数，工具须接受 kwargs（与真实工具签名一致）
        "tools": {"read_file": lambda **kw: "内容", "write_file": lambda **kw: "已写入"},
        "expect_planning": True,
    },
    {
        "id": "multi_search_notify",
        "category": "multi_step",
        "description": "首先运行 search，然后运行 send_email",
        "tools": {"search": lambda **kw: "结果", "send_email": lambda **kw: "已发送"},
        "expect_planning": True,
    },

    # ── 并行（mock LLM 分解无依赖任务 + parallel_execution 并发）───────────
    {
        "id": "parallel_two",
        "category": "parallel",
        "description": "并行处理两个文件",
        "llm_responses": [
            json.dumps({
                "subtasks": [
                    {"id": "step_1", "description": "运行 alpha_file", "type": "atomic",
                     "priority": 3, "dependencies": []},
                    {"id": "step_2", "description": "运行 beta_file", "type": "atomic",
                     "priority": 3, "dependencies": []},
                ],
                "execution_order": ["step_1", "step_2"],
                "parallel_groups": [["step_1", "step_2"]],
            }),
        ],
        "tools": {"alpha_file": lambda: "alpha ok", "beta_file": lambda: "beta ok"},
        "expect_planning": True,
    },
    {
        "id": "parallel_three",
        "category": "parallel",
        "description": "并行处理三个模块",
        "llm_responses": [
            json.dumps({
                "subtasks": [
                    {"id": "step_1", "description": "运行 mod_a", "type": "atomic",
                     "priority": 3, "dependencies": []},
                    {"id": "step_2", "description": "运行 mod_b", "type": "atomic",
                     "priority": 3, "dependencies": []},
                    {"id": "step_3", "description": "运行 mod_c", "type": "atomic",
                     "priority": 3, "dependencies": []},
                ],
                "execution_order": ["step_1", "step_2", "step_3"],
                "parallel_groups": [["step_1", "step_2", "step_3"]],
            }),
        ],
        "tools": {"mod_a": lambda: "A", "mod_b": lambda: "B", "mod_c": lambda: "C"},
        "expect_planning": True,
    },
    {
        "id": "parallel_mixed_deps",
        "category": "parallel",
        "description": "并行与串行混合处理",
        "llm_responses": [
            json.dumps({
                "subtasks": [
                    {"id": "step_1", "description": "运行 task_x", "type": "atomic",
                     "priority": 3, "dependencies": []},
                    {"id": "step_2", "description": "运行 task_y", "type": "atomic",
                     "priority": 3, "dependencies": ["step_1"]},
                    {"id": "step_3", "description": "运行 task_z", "type": "atomic",
                     "priority": 3, "dependencies": []},
                ],
                "execution_order": ["step_1", "step_3", "step_2"],
                "parallel_groups": [["step_1", "step_3"]],
            }),
        ],
        "tools": {"task_x": lambda: "X", "task_y": lambda: "Y", "task_z": lambda: "Z"},
        "expect_planning": True,
    },

    # ── 失败注入（验证失败归因进入 KPI 分布）───────────────────────────────
    {
        "id": "fail_tool_raises",
        "category": "failure_injection",
        "description": "首先运行 bad_tool",
        "tools": {"bad_tool": _raise_bad},
        "expect_planning": True,
        "expect_success": False,
    },
    {
        "id": "fail_tool_timeout",
        "category": "failure_injection",
        "description": "首先运行 slow_tool",
        "tools": {"slow_tool": _slow_tool},
        "expect_planning": True,
        "expect_success": False,
    },
    {
        "id": "fail_high_priority",
        "category": "failure_injection",
        "description": "首先运行 bad_high_tool",
        "tools": {"bad_high_tool": _raise_bad},
        "expect_planning": True,
        "expect_success": False,
    },
]


def get_eval_set(category: str = None):
    """按分类筛选评测集（None 返回全部）"""
    if category is None:
        return list(PLANNING_EVAL_SET)
    return [t for t in PLANNING_EVAL_SET if t["category"] == category]
