"""L2 低风险（low）工具的输出契约（P2 result_schema 扩面·第四批 39 个）。

口径同前三批：能安全真调的实调（grep / ext_list / list_* / workspace_* / software_probe /
read_pdf 缺文件 / run_lint 空 path / search_memory 缺参 / todo_write 非法清单 / 各写工具缺参），
成功分支受副作用或宿主状态限制的按实现 return 字面量核对。
**两个例外**：\`get_status\` / \`get_sensor_summary\` 直接返回宿主原始状态字典（无 ok 键），
其 schema 的 required 为空数组 —— 这里单独断言，不伪造 ok。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent.capregistry.contract import RESULT_SCHEMA_REQUIRED_TOOLS, validate_against_schema

ROOT = Path(__file__).resolve().parents[2]

LOW_TOOLS = [
    "arch_diagram", "browser_close", "cancel_task", "code_review", "ext_list",
    "get_persona_info", "get_preferences", "get_task_result", "get_task_status",
    "get_weather", "grep", "kb_capture", "kb_card", "kb_lint", "kb_search",
    "list_async_tasks", "list_mcp_connections", "list_scheduled_tasks", "merge_pdf",
    "notify", "pause_scheduled_task", "read_pdf", "read_pdf_tables", "remember",
    "resume_scheduled_task", "run_lint", "search_lifetrace", "search_memory",
    "software_probe", "split_pdf", "sqlite_query", "todo_write", "trigger_distillation",
    "web_search", "weekly_report", "workspace_init", "workspace_list",
]
RAW_DICT_TOOLS = ["get_status", "get_sensor_summary"]
ALL_TOOLS = LOW_TOOLS + RAW_DICT_TOOLS

SUCCESS = {
    "arch_diagram": {"ok": True, "path": "C:/tmp/a.html"},
    "browser_close": {"ok": True, "closed": True},
    "cancel_task": {"ok": True, "cancelled": True},
    "code_review": {"ok": True, "findings": [],
                    "summary": {"total": 0, "critical": 0, "informational": 0}},
    "ext_list": {"ok": True, "extensions": []},
    "get_persona_info": {"ok": True, "data": {"identity": "x", "expression_style": {}}},
    "get_preferences": {"ok": True, "data": "## 学习到的用户偏好"},
    "get_task_result": {"ok": True, "task_id": "task_x", "status": "completed", "result": {}},
    "get_task_status": {"ok": True, "task_id": "task_x", "status": "running",
                        "progress": "", "elapsed": 1.2},
    "get_weather": {"ok": True, "data": "Beijing: +25C", "format": "text", "city": "Beijing"},
    "grep": {"ok": True, "matches": [], "count": 0, "truncated": False},
    "kb_capture": {"ok": True, "slug": "s", "hint": "已入库 inbox，可继续 kb_distill"},
    "kb_card": {"ok": True, "slug": "c", "status": "draft", "hint": "draft"},
    "kb_lint": {"ok": True, "report": {"total_cards": 0}},
    "kb_search": {"ok": True, "hits": []},
    "list_async_tasks": {"ok": True, "tasks": [], "total": 0},
    "list_mcp_connections": {"ok": True, "connections": [], "count": 0, "note": "..."},
    "list_scheduled_tasks": {"ok": True, "tasks": [], "total": 0, "count": 0,
                             "counts_by_engine": {"scheduler": 0, "system_command": 0}},
    "merge_pdf": {"ok": True, "output_path": "C:/tmp/o.pdf", "total_pages": 2,
                  "merged_files": 2, "error": None},
    "notify": {"ok": True, "delivered": ["log"], "failed": []},
    "pause_scheduled_task": {"ok": True, "paused": True},
    "read_pdf": {"ok": True, "text": "...", "pages": 2, "extracted_pages": 2},
    "read_pdf_tables": {"ok": True, "tables": [], "pages": 1, "extracted_pages": 1},
    "remember": {"ok": True, "data": "已记住", "mem_id": "m1"},
    "resume_scheduled_task": {"ok": True, "resumed": True},
    "run_lint": {"ok": True, "tool": "ruff", "clean": True, "error_count": 0,
                 "warning_count": 0, "summary": "clean", "returncode": 0,
                 "duration_sec": 0.5, "command": ["ruff", "check"], "cwd": "C:/repo",
                 "timeout_sec": 120, "output_tail": "", "output_truncated": False},
    "search_lifetrace": {"ok": True, "data": "- x", "count": 1},
    "search_memory": {"ok": True, "data": "x", "count": 1, "query": "q", "scope": "all"},
    "software_probe": {"ok": True, "installed": True, "version": "2.45.0",
                       "path": "C:/x", "source": "registry",
                       "matched_display_name": "Git", "match": "exact"},
    "split_pdf": {"ok": True, "output_dir": "C:/tmp", "files": ["C:/tmp/page_001.pdf"],
                  "total_outputs": 1, "error": None},
    "sqlite_query": {"ok": True, "columns": ["a"], "rows": [{"a": 1}], "row_count": 1,
                     "truncated": False, "limit": 100, "auto_limit_applied": True,
                     "cell_truncated": False, "db_path": "data/x.db",
                     "read_only_uri": "file:x?mode=ro"},
    "todo_write": {"ok": True, "todos": [], "counts": {"pending": 0, "in_progress": 0,
                                                       "completed": 0},
                   "rendered": "", "persisted": True},
    "trigger_distillation": {"ok": True, "data": "人格蒸馏已触发！"},
    "web_search": {"ok": True, "results": [], "total_found": 0, "summary": "",
                   "_was_truncated": False},
    "weekly_report": {"ok": True, "output_dir": "data/reports", "files": [], "report": {}},
    "workspace_init": {"ok": True, "path": "C:/repo/workspace"},
    "workspace_list": {"ok": True, "path": "", "type": "dir", "items": []},
    "get_status": {"云枢": {"版本": "2.0"}, "系统": {"工具数量": 101}},
    "get_sensor_summary": {"total_sensors": 3, "enabled_sensors": 2,
                           "disabled_sensors": 1, "sensors": [], "health": {}},
}

FAILURE = {
    "arch_diagram": {"ok": False, "error": "arch_diagram 需要提供 title..."},
    "browser_close": {"ok": False, "error": "关闭浏览器失败（详见服务日志）"},
    "cancel_task": {"ok": False, "error": "请提供 task_id"},
    "code_review": {"ok": False, "error": "审查内容为空，请提供待审查的 path 或 diff"},
    "ext_list": {"ok": False, "error": "扩展管理器不可用"},
    "get_persona_info": {"ok": False, "error": "Persona 系统未启用，此工具不可用",
                         "available": False},
    "get_preferences": {"ok": False, "error": "人格蒸馏功能未启用", "available": False},
    "get_task_result": {"ok": False, "error": "请提供 task_id"},
    "get_task_status": {"ok": False, "error": "请提供 task_id"},
    "get_weather": {"ok": False, "error": "网络连接失败: ...", "city": "x"},
    "grep": {"ok": False, "error": "请提供正则表达式（pattern）"},
    "kb_capture": {"ok": False, "error": "须提供 text 或 file_path 之一"},
    "kb_card": {"ok": False, "error": "须提供 note_slug 或 discussion_path 之一"},
    "kb_lint": {"ok": False, "error": "审计失败"},
    "kb_search": {"ok": False, "error": "须提供 query"},
    "list_async_tasks": {"ok": False, "error": "执行器不可用"},
    "list_mcp_connections": {"ok": False, "error": "MCP 管理器不可用"},
    "list_scheduled_tasks": {"ok": False, "error": "调度器不可用"},
    "merge_pdf": {"ok": False, "output_path": "", "total_pages": 0,
                  "merged_files": 0, "error": "请提供要合并的文件列表（paths）"},
    "notify": {"ok": False, "error": "请提供通知标题（title）"},
    "pause_scheduled_task": {"ok": False, "error": "请提供 task_id"},
    "read_pdf": {"ok": False, "text": "", "pages": 0, "extracted_pages": 0,
                 "error": "文件不存在"},
    "read_pdf_tables": {"ok": False, "tables": [], "pages": 0, "extracted_pages": 0,
                        "error": "文件不存在"},
    "remember": {"ok": False, "error": "请提供 key 和 content 参数"},
    "resume_scheduled_task": {"ok": False, "error": "请提供 task_id"},
    "run_lint": {"ok": False, "error": "未检测到可用的 lint 工具"},
    "search_lifetrace": {"ok": False, "error": "LifeTrace 系统未启用，此工具不可用",
                         "available": False},
    "search_memory": {"ok": False, "error": "请提供搜索关键词", "query": "", "scope": "all"},
    "software_probe": {"ok": False, "error": "探测失败（详见服务日志）"},
    "split_pdf": {"ok": False, "output_dir": "", "files": [], "total_outputs": 0,
                  "error": "请提供源 PDF 文件路径（path）"},
    "sqlite_query": {"ok": False, "error": "请提供数据库路径（db_path）"},
    "todo_write": {"ok": False, "error": "todos 必须是数组"},
    "trigger_distillation": {"ok": False, "error": "人格蒸馏功能未启用", "available": False},
    "web_search": {"ok": False, "error": "请提供搜索关键词"},
    "weekly_report": {"ok": False, "error": "周报生成失败（详见服务日志）"},
    "workspace_init": {"ok": False, "error": "工作区初始化失败（详见服务日志）"},
    "workspace_list": {"ok": False, "error": "路径越出工作区"},
    "get_status": {"error": "状态不可用"},
    "get_sensor_summary": {"total_sensors": 0, "error": "采集失败"},
}


def _schema(name: str) -> dict:
    doc = yaml.safe_load((ROOT / "data" / "tool_definitions" / f"{name}.yaml")
                         .read_text(encoding="utf-8"))
    schema = doc.get("result_schema")
    assert isinstance(schema, dict) and schema, f"{name} 缺少 result_schema"
    return schema


@pytest.mark.parametrize("name", ALL_TOOLS)
def test_declares(name):
    schema = _schema(name)
    assert schema.get("type") == "object", name
    assert schema.get("additionalProperties") is True, name
    if name in RAW_DICT_TOOLS:
        assert schema.get("required") in ([], None), name
    else:
        assert "ok" in (schema.get("required") or []), name


@pytest.mark.parametrize("name", ALL_TOOLS)
def test_in_required_tuple(name):
    assert name in RESULT_SCHEMA_REQUIRED_TOOLS, (
        f"{name} 已有 result_schema 但没进 RESULT_SCHEMA_REQUIRED_TOOLS")


@pytest.mark.parametrize("name", ALL_TOOLS)
def test_success_sample_passes(name):
    ok, errors, validator = validate_against_schema(SUCCESS[name], _schema(name))
    assert ok, f"{name} 成功样本被判违约（{validator}）：{errors}"


@pytest.mark.parametrize("name", ALL_TOOLS)
def test_failure_sample_passes(name):
    ok, errors, validator = validate_against_schema(FAILURE[name], _schema(name))
    assert ok, f"{name} 失败样本被判违约（{validator}）：{errors}"


def test_samples_cover_every_tool():
    assert set(SUCCESS) == set(ALL_TOOLS)
    assert set(FAILURE) == set(ALL_TOOLS)
