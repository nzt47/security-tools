"""L2 只读工具的输出契约（P2 result_schema 扩面·第一批 6 个）。

作者口径（**禁止批量猜**）：
每个工具都先在真实调用里核对过返回键，再按真实键写 `result_schema`；本文件里的
成功/失败样本就是那次真调的形状（证据逐条写在
`data/tool_definitions/<name>.yaml` 的 `result_schema` 注释里）：

- `web_get` / `web_batch`：真实网络请求 example.com / example.org；
- `web_extract`：三条分支（clean / css / clean+items）在本地 HTML 上实跑；
- `ext_discover`：无 query（内置/已装清单）与有 query（市场扁平结果）两条分支实跑；
- `kb_discuss`：临时知识库 + 假 LLM 走真实 WorkflowRunner；
- `look_at_screen`：失败分支实跑；成功分支当前不可达（见该 YAML 注释），
  样本取自包装层 + VoiceVision 的返回声明。

这里的断言只做两件事：① 声明还在（别被删）；② 真实形状能通过**生产同一条**
校验路径（`agent.capregistry.contract.validate_against_schema`，优先 jsonschema）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from agent.capregistry.contract import RESULT_SCHEMA_REQUIRED_TOOLS, validate_against_schema

ROOT = Path(__file__).resolve().parents[2]

L2_TOOLS = [
    "web_get", "web_extract", "web_batch",
    "ext_discover", "kb_discuss", "look_at_screen",
]

# 真调记录（键/类型来自那次返回，长文本已截短；bytes 保留以证明确实可被容忍）
SUCCESS_SAMPLES = {
    "web_get": {
        "ok": True,
        "status_code": 200,
        "reason": "OK",
        "headers": {"Content-Type": "text/html", "Server": "cloudflare"},
        "content_length": 1256,
        "content": b"<html>...</html>",
        "text": "<html>... Example Domain ...</html>",
        "encoding": "utf-8",
        "url": "https://example.com/",
        "elapsed": 0.123,
        "cookies": {},
        "redirect_history": [],
        "parsed": {
            "title": "Example Domain",
            "text": "This domain is for use in illustrative examples.",
            "links": [],
            "images": [],
            "meta": {"viewport": "width=device-width, initial-scale=1"},
            "headings": {},
        },
    },
    "web_extract": {   # kind="clean"
        "ok": True,
        "results": ["Hello & world"],
        "count": 1,
        "text": "Hello & world",
    },
    "web_batch": {
        "ok": True,
        "total": 2,
        "results": [
            {"ok": True, "status_code": 200, "text": "<html>...</html>",
             "url": "https://example.com/", "elapsed": 0.05},
            {"ok": False, "error": "连接失败: ..."},
        ],
    },
    "ext_discover": {   # 无 query：8 个清单数组
        "ok": True,
        "builtin_skills": [],
        "installed_skills": [],
        "builtin_mcp": [],
        "installed_mcp": [],
        "builtin_channels": [],
        "installed_channels": [],
        "resolved_channels": [],
        "local_plugins": [],
    },
    "kb_discuss": {
        "ok": True,
        "discussion_path": "data/knowledge/discussions/disc-20261124.md",
        "hint": "讨论记录已生成，可 kb_card（discussion_path）产卡",
    },
    "look_at_screen": {   # 成功分支当前不可达；形状取自包装层 + VoiceVision 声明
        "ok": True,
        "reading": {"timestamp": "2026-11-24 01:05:00", "data": [], "meta": {}},
        "text": "[10,20] Hello",
    },
}

FAILURE_SAMPLES = {
    "web_get": {"ok": False, "error": "请提供 URL"},
    "web_extract": {"ok": False, "error": "kind 必须为 'xpath'、'css' 或 'clean' 之一，收到: 'nope'"},
    "web_batch": {"ok": False, "error": "请提供 URL 列表 (urls)"},
    "ext_discover": {"ok": False, "error": "扩展市场不可用: ..."},
    "kb_discuss": {"ok": False, "error": "须提供 note_slug"},
    "look_at_screen": {"ok": False, "error": "语音/视觉模块不可用（dl.voice 未就绪）"},
}


def _doc(name: str) -> dict:
    path = ROOT / "data" / "tool_definitions" / f"{name}.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _schema(name: str) -> dict:
    schema = _doc(name).get("result_schema")
    assert isinstance(schema, dict) and schema, f"{name} 缺少 result_schema"
    return schema


@pytest.mark.parametrize("name", L2_TOOLS)
def test_l2_tool_declares_result_schema(name):
    schema = _schema(name)
    assert schema.get("type") == "object", name
    assert "ok" in (schema.get("required") or []), name
    assert schema.get("additionalProperties") is True, name


@pytest.mark.parametrize("name", L2_TOOLS)
def test_l2_tool_in_required_tuple(name):
    assert name in RESULT_SCHEMA_REQUIRED_TOOLS, (
        f"{name} 已有 result_schema 但没进 RESULT_SCHEMA_REQUIRED_TOOLS —— "
        "清单与 YAML 会各说各话")


@pytest.mark.parametrize("name", L2_TOOLS)
def test_success_sample_passes_schema(name):
    ok, errors, validator = validate_against_schema(SUCCESS_SAMPLES[name], _schema(name))
    assert ok, f"{name} 成功样本被判违约（validator={validator}）：{errors}"
    assert validator in ("jsonschema", "builtin"), validator


@pytest.mark.parametrize("name", L2_TOOLS)
def test_failure_sample_passes_schema(name):
    """失败分支只有 ok/error —— 若把 status_code 等写成必填，这里会红。"""
    ok, errors, validator = validate_against_schema(FAILURE_SAMPLES[name], _schema(name))
    assert ok, f"{name} 失败样本被判违约（validator={validator}）：{errors}"


def test_web_get_success_sample_has_undeclared_bytes_field():
    """bytes 字段（content）故意不声明；additionalProperties 必须容忍它。"""
    sample = dict(SUCCESS_SAMPLES["web_get"])
    assert isinstance(sample.get("content"), bytes)
    ok, errors, _ = validate_against_schema(sample, _schema("web_get"))
    assert ok, errors


def test_samples_are_json_serialisable_except_known_bytes():
    """样本本身要能被审计/日志序列化 —— 只有 web_get 的 content 是 bytes。"""
    for name, sample in SUCCESS_SAMPLES.items():
        payload = {k: v for k, v in sample.items() if k != "content"}
        json.dumps(payload, ensure_ascii=False)
