"""L2 中风险工具的输出契约（P2 result_schema 扩面·第二批 16 个）。

口径同 `test_l2_result_schema.py`：每个工具都先核对真实返回再写契约。
本批的"证据"分两类，逐条写在各 YAML 的 result_schema 注释里：
- **实调**：browser_navigate / browser_screenshot / cancel_scheduled_task / compress /
  delegate / distill_process_from_knowledge / kb_distill / process_distill_run /
  run_tests / set_clipboard / software_install / stop_process / submit_task /
  web_download / workspace_write（失败分支或安全成功分支）；
- **按实现 return 字面量核对**：成功分支受副作用/外部依赖限制的（delegate /
  run_tests / submit_task / distill* / software_install 成功态 / web_post），
  键取自函数体内 `return {...}` 的字面量，不按工具名猜。

断言只做三件事：① 声明还在；② 成功/失败样本能通过生产同一条校验路径；
③ 必填清单与 YAML 不各说各话。
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from agent.capregistry.contract import RESULT_SCHEMA_REQUIRED_TOOLS, validate_against_schema

ROOT = Path(__file__).resolve().parents[2]

MEDIUM_TOOLS = [
    "browser_navigate", "browser_screenshot", "cancel_scheduled_task", "compress",
    "delegate", "distill_process_from_knowledge", "kb_distill", "process_distill_run",
    "run_tests", "set_clipboard", "software_install", "stop_process", "submit_task",
    "web_download", "web_post", "workspace_write",
]

SUCCESS_SAMPLES = {
    "browser_navigate": {"ok": True, "title": "Example Domain",
                         "url": "https://example.com/", "text": "This domain is ..."},
    "browser_screenshot": {"ok": True, "screenshot_base64": "iVBORw0KGgoAAAANS..."},
    "cancel_scheduled_task": {"ok": True, "cancelled": True},
    "compress": {"ok": True, "output_path": "C:/tmp/out.zip", "compressed_size": 512,
                 "file_count": 3, "format": "zip"},
    "delegate": {"ok": True, "delegation_id": "dlg-abc12345", "tier": "standard",
                 "duration_ms": 1234.5, "result": "交付摘要"},
    "distill_process_from_knowledge": {
        "ok": True, "query": "sop", "paths": [], "llm_used": True,
        "materials": [{"id": "m1", "title": "t", "source_ref": "docs/a.md"}],
        "process": {}, "artifacts": {},
    },
    "kb_distill": {"ok": True, "slug": "n1", "distilled": True, "reason": "",
                   "hint": "笔记已落 processed/，可 approve 后 kb_card 产卡"},
    "process_distill_run": {"ok": True, "query": "sop", "paths": [], "llm_used": False,
                            "materials": [], "process": {}, "artifacts": {}},
    "run_tests": {"ok": True, "returncode": 0, "passed": 3, "failed": 0, "errors": 0,
                  "skipped": 0, "duration_sec": 1.23, "summary": "3 passed in 1.23s",
                  "output_tail": "...", "command": ["python", "-m", "pytest", "-q"],
                  "cwd": "C:/repo", "timeout_sec": 300},
    "set_clipboard": {"ok": True},
    "software_install": {"ok": True, "package_id": "Git.Git", "installer": "winget",
                         "exit_code": 0, "installed_before": False,
                         "installed_after": True, "version_before": "",
                         "version_after": "2.45.0", "changed": True, "verified": True,
                         "elapsed_ms": 42000, "log_tail": "...", "denied_reason": ""},
    "stop_process": {"ok": True, "pid": 4242, "name": "notepad.exe"},
    "submit_task": {"ok": True, "task_id": "task_0123456789ab", "status": "pending"},
    "web_download": {"ok": True, "filepath": "C:/tmp/x.html", "size": 1256,
                     "elapsed": 0.12},
    "web_post": {"ok": True, "status_code": 200, "reason": "OK",
                 "headers": {"Content-Type": "application/json"},
                 "content_length": 42, "text": '{"ok":true}', "encoding": "utf-8",
                 "url": "https://example.com/api", "elapsed": 0.2, "cookies": {},
                 "redirect_history": []},
    "workspace_write": {"ok": True, "path": "notes/plan.md", "size": 8},
}

FAILURE_SAMPLES = {
    "browser_navigate": {"ok": False, "error": "仅允许 http/https 协议"},
    "browser_screenshot": {"ok": False, "error": "浏览器不可用"},
    "cancel_scheduled_task": {"ok": False, "error": "任务不存在: t-1"},
    "compress": {"ok": False, "error": "源路径不存在: C:/nope"},
    "delegate": {"ok": False, "error_code": "E_DELEGATION_INCOMPLETE",
                 "error": "八要素不完整：目标(...)", "missing": {"goal": "..."}},
    "distill_process_from_knowledge": {
        "ok": False, "error": "query 与 paths 至少提供一个（没有素材无法蒸馏）"},
    "kb_distill": {"ok": False, "error": "须提供 source_path（inbox/raw 下素材路径）"},
    "process_distill_run": {"ok": False, "error": "query 与 paths 至少提供一个（没有素材无法蒸馏）"},
    "run_tests": {"ok": False, "error": "测试路径不存在: tests/__no_such__.py"},
    "set_clipboard": {"ok": False, "error": "请提供要写入剪贴板的文本（text）"},
    "software_install": {"ok": False, "package_id": "__no_such_pkg__", "installer": "",
                         "exit_code": -1, "installed_before": False,
                         "installed_after": False, "version_before": "",
                         "version_after": "", "changed": False, "verified": False,
                         "elapsed_ms": 0, "log_tail": "",
                         "denied_reason": "not-in-allowlist", "allowlist_size": 0},
    "stop_process": {"ok": False, "error": "进程不存在"},
    "submit_task": {"ok": False, "error": "请提供任务名称（name）"},
    "web_download": {"ok": False, "error": "请提供 URL"},
    "web_post": {"ok": False, "error": "请求超时: ..."},
    "workspace_write": {"ok": False, "error": "请提供工作区内的相对路径（path）"},
}


def _doc(name: str) -> dict:
    return yaml.safe_load((ROOT / "data" / "tool_definitions" / f"{name}.yaml")
                          .read_text(encoding="utf-8"))


def _schema(name: str) -> dict:
    schema = _doc(name).get("result_schema")
    assert isinstance(schema, dict) and schema, f"{name} 缺少 result_schema"
    return schema


@pytest.mark.parametrize("name", MEDIUM_TOOLS)
def test_declares(name):
    schema = _schema(name)
    assert schema.get("type") == "object", name
    assert "ok" in (schema.get("required") or []), name
    assert schema.get("additionalProperties") is True, name


@pytest.mark.parametrize("name", MEDIUM_TOOLS)
def test_in_required_tuple(name):
    assert name in RESULT_SCHEMA_REQUIRED_TOOLS, (
        f"{name} 已有 result_schema 但没进 RESULT_SCHEMA_REQUIRED_TOOLS")


@pytest.mark.parametrize("name", MEDIUM_TOOLS)
def test_success_sample_passes(name):
    ok, errors, validator = validate_against_schema(SUCCESS_SAMPLES[name], _schema(name))
    assert ok, f"{name} 成功样本被判违约（validator={validator}）：{errors}"


@pytest.mark.parametrize("name", MEDIUM_TOOLS)
def test_failure_sample_passes(name):
    ok, errors, validator = validate_against_schema(FAILURE_SAMPLES[name], _schema(name))
    assert ok, f"{name} 失败样本被判违约（validator={validator}）：{errors}"


def test_samples_cover_every_tool():
    assert set(SUCCESS_SAMPLES) == set(MEDIUM_TOOLS)
    assert set(FAILURE_SAMPLES) == set(MEDIUM_TOOLS)
