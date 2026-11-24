"""L2 高风险（high）工具的输出契约（P2 result_schema 扩面·第三批 11 个）。

证据口径（同前两批）：能在安全环境真调的实调——
write_file / edit / apply_patch / decompress（临时目录/临时文件，用后删除）、
git status / git add 无 confirm、run_program 非白名单、schedule_task、ext_send_channel、
workspace_delete、fan_out 空 tasks；成功分支受副作用限制的按实现 return 字面量核对
（skill_script_run 成功态、fan_out 汇总 envelope、schedule_task 实现后的 task 段）。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent.capregistry.contract import RESULT_SCHEMA_REQUIRED_TOOLS, validate_against_schema

ROOT = Path(__file__).resolve().parents[2]

HIGH_TOOLS = [
    "apply_patch", "decompress", "edit", "ext_send_channel", "fan_out", "git",
    "run_program", "schedule_task", "skill_script_run", "workspace_delete", "write_file",
]

SUCCESS_SAMPLES = {
    "apply_patch": {"ok": True, "dry_run": True, "applied": False,
                    "files": [{"path": "a.txt", "hunks": 1, "added": 1, "removed": 1,
                               "created": False, "bytes": 12}],
                    "total_added": 1, "total_removed": 1,
                    "message": "dry_run=true：全部 hunk 校验通过，未写入任何文件"},
    "decompress": {"ok": True, "output_dir": "C:/tmp/out", "file_count": 1,
                   "extracted_size": 2, "format": "zip"},
    "edit": {"ok": True, "path": "C:/tmp/a.txt", "replaced": 1, "bytes_written": 10},
    "ext_send_channel": {"ok": True, "message": "sent"},
    "fan_out": {"ok": True, "status": "all_succeeded", "partial": False, "total": 1,
                "succeeded": 1, "failed": 0, "max_concurrency": 4,
                "results": [{"index": 0, "status": "ok"}]},
    "git": {"ok": True, "action": "status", "argv": ["git", "status"],
            "cwd": "C:/repo", "returncode": 0, "stdout": "## master", "stderr": "",
            "truncated": False},
    "run_program": {"ok": True, "pid": 4242, "program": "notepad.exe"},
    "schedule_task": {"ok": True, "task": {"name": "t"},
                      "action_execution_implemented": True},
    "skill_script_run": {"ok": True, "skill_id": "s", "script_name": "main.py",
                         "success": True, "exit_code": 0, "duration_ms": 12.3,
                         "timed_out": False, "validation_status": "ok",
                         "result": {}},
    "workspace_delete": {"ok": True, "path": "notes/a.md"},
    "write_file": {"ok": True, "path": "C:/tmp/a.txt", "abs_path": "C:/tmp/a.txt", "size": 3},
}

FAILURE_SAMPLES = {
    "apply_patch": {"ok": False, "error": "请提供补丁内容（patch）"},
    "decompress": {"ok": False, "error": "文件不存在: C:/nope.zip"},
    "edit": {"ok": False, "error": "编辑前必须先读取该文件（请先调用 read_file）",
             "blocked": True},
    "ext_send_channel": {"ok": False, "message": "通道不存在"},
    "fan_out": {"ok": False, "error_code": "E_FAN_OUT_NO_TASKS",
                "error": "tasks 必须是非空数组"},
    "git": {"ok": False, "error": "git add 是写操作，需要人工确认",
            "action": "add", "confirm_required": True},
    "run_program": {"ok": False, "error": "程序不在白名单中。允许: notepad.exe"},
    "schedule_task": {"ok": False, "error_code": "NOT_IMPLEMENTED",
                      "error": "schedule_task 当前不可用", "implemented": False,
                      "action_execution_implemented": False, "guidance": "..."},
    "skill_script_run": {"ok": False, "error": "缺少 skill_id"},
    "workspace_delete": {"ok": False, "error": "请提供工作区内的相对路径（path）"},
    "write_file": {"ok": False, "error": "内容过大 (2.0MB)"},
}


def _schema(name: str) -> dict:
    doc = yaml.safe_load((ROOT / "data" / "tool_definitions" / f"{name}.yaml")
                         .read_text(encoding="utf-8"))
    schema = doc.get("result_schema")
    assert isinstance(schema, dict) and schema, f"{name} 缺少 result_schema"
    return schema


@pytest.mark.parametrize("name", HIGH_TOOLS)
def test_declares(name):
    schema = _schema(name)
    assert schema.get("type") == "object", name
    assert "ok" in (schema.get("required") or []), name
    assert schema.get("additionalProperties") is True, name


@pytest.mark.parametrize("name", HIGH_TOOLS)
def test_in_required_tuple(name):
    assert name in RESULT_SCHEMA_REQUIRED_TOOLS


@pytest.mark.parametrize("name", HIGH_TOOLS)
def test_success_sample_passes(name):
    ok, errors, validator = validate_against_schema(SUCCESS_SAMPLES[name], _schema(name))
    assert ok, f"{name} 成功样本被判违约（{validator}）：{errors}"


@pytest.mark.parametrize("name", HIGH_TOOLS)
def test_failure_sample_passes(name):
    ok, errors, validator = validate_against_schema(FAILURE_SAMPLES[name], _schema(name))
    assert ok, f"{name} 失败样本被判违约（{validator}）：{errors}"


def test_samples_cover_every_tool():
    assert set(SUCCESS_SAMPLES) == set(HIGH_TOOLS)
    assert set(FAILURE_SAMPLES) == set(HIGH_TOOLS)
