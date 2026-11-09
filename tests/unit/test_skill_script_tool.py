# -*- coding: utf-8 -*-
"""技能脚本执行工具（skill_script_run）—— L3 显式执行链路 + YAML 契约（2026-11-09）。

背景：使用台账 stage=exec 建账以来一条都没有。根因不是"写入点漏了"，而是**没有调用者**：
聊天链路只做 L1 命中 → L2 注入，SkillExecutor.execute 只挂在 REST 手动端点与评估器上，
而 `scripted-selftest/skill.md` 却承诺"匹配后交由 SkillExecutor 执行"。本工具补上
「显式调用」这条链（不做命中即自动执行），并守住四件事：
  1. 没有脚本 ⇒ 回「不适用」，不是「执行失败」（另一种 0 折算）；
  2. 脚本名只认白名单（不拼路径）；
  3. 参数缺省回落 default_params，超时钳制到 [30,120]；
  4. 异常原文不回响应；YAML 与 register 的 description/schema 逐字一致。
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from agent.tools import skill_tools

_ROOT = Path(__file__).resolve().parents[2]
_YAML_PATH = _ROOT / "data" / "tool_definitions" / "skill_script_run.yaml"


class _FakeResult:
    def __init__(self, success=True, result=None, stdout='{"ok": true}', stderr="",
                 timed_out=False, error=""):
        self.success = success
        self.result = result
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = timed_out
        self.error = error
        self.exit_code = 0 if success else 1
        self.duration_ms = 12.5
        self.validation_status = "skipped"


class _FakeStore:
    def __init__(self, scripts):
        self._scripts = scripts

    def list_scripts(self, sid):
        return list(self._scripts.get(sid, []))


def _svc(scripts=None, *, enabled=True, status="approved", defaults=None, result=None,
         raises=None):
    calls = {}

    def _exec(sid, script_name="main.py", params=None, timeout=None):
        calls.update(sid=sid, script_name=script_name, params=params, timeout=timeout)
        if raises is not None:
            raise raises
        return result if result is not None else _FakeResult()

    svc = SimpleNamespace(
        get=lambda sid: SimpleNamespace(
            id=sid, enabled=enabled, status=status,
            default_params=dict(defaults or {}),
        ),
        file_store=_FakeStore(scripts or {}),
        execute_skill_script=_exec,
    )
    return svc, calls


@pytest.fixture
def patch_svc(monkeypatch):
    def _apply(svc):
        import agent.state_manager as sm
        monkeypatch.setattr(sm, "get_skills_mgmt_service", lambda: svc, raising=True)
        return svc
    return _apply


def test_missing_skill_id_is_rejected():
    out = skill_tools.skill_script_run(skill_id="")
    assert out["ok"] is False and "skill_id" in out["error"]


def test_unknown_skill_is_rejected(patch_svc):
    svc, _ = _svc()

    def _raise(sid):
        raise KeyError(sid)

    svc.get = _raise
    patch_svc(svc)
    out = skill_tools.skill_script_run(skill_id="nope")
    assert out["ok"] is False and out["skill_id"] == "nope"
    # 异常原文（KeyError 的 repr）不得出现在响应里
    assert "KeyError" not in json.dumps(out, ensure_ascii=False)


def test_disabled_skill_is_rejected(patch_svc):
    svc, _ = _svc({"s1": ["main.py"]}, enabled=False)
    patch_svc(svc)
    out = skill_tools.skill_script_run(skill_id="s1")
    assert out["ok"] is False and "停用" in out["error"]


def test_non_executable_status_is_rejected(patch_svc):
    svc, _ = _svc({"s1": ["main.py"]}, status="draft")
    patch_svc(svc)
    out = skill_tools.skill_script_run(skill_id="s1")
    assert out["ok"] is False and "draft" in out["error"]


def test_no_scripts_says_not_applicable(patch_svc):
    """没有 scripts/*.py ⇒ 「不适用」，**不能**说成执行失败（L5 口径）。"""
    svc, calls = _svc({"s1": []})
    patch_svc(svc)
    out = skill_tools.skill_script_run(skill_id="s1")
    assert out["ok"] is False
    assert out["applicable"] is False
    assert "不适用" in out["error"] and "scripts" in out["error"]
    assert calls == {}, "没有脚本时不得调用执行器"


def test_script_name_whitelist(patch_svc):
    svc, calls = _svc({"s1": ["main.py", "audit.py"]})
    patch_svc(svc)
    out = skill_tools.skill_script_run(skill_id="s1", script_name="../../../etc/passwd")
    assert out["ok"] is False
    assert out["available_scripts"] == ["main.py", "audit.py"]
    assert calls == {}, "不在白名单里一律不执行"


def test_defaults_merge_and_timeout_clamp(patch_svc):
    svc, calls = _svc({"s1": ["main.py"]}, defaults={"greeting": "hello", "count": 3},
                      result=_FakeResult(result={"ok": True}))
    patch_svc(svc)
    out = skill_tools.skill_script_run(skill_id="s1", params={"count": 9}, timeout=999)
    assert out["ok"] is True and out["result"] == {"ok": True}
    assert calls["params"] == {"greeting": "hello", "count": 9}, "调用方参数优先于 default_params"
    assert calls["timeout"] == float(skill_tools.MAX_TIMEOUT_SEC), "超时必须钳到上限"
    assert calls["script_name"] == skill_tools.DEFAULT_SCRIPT_NAME


def test_zero_timeout_falls_back_to_default(patch_svc):
    svc, calls = _svc({"s1": ["main.py"]})
    patch_svc(svc)
    skill_tools.skill_script_run(skill_id="s1", timeout=0)
    assert calls["timeout"] == float(skill_tools.DEFAULT_TIMEOUT_SEC)


def test_executor_exception_does_not_leak_text(patch_svc):
    secret = "Traceback secret-token /root/x.py"
    svc, _ = _svc({"s1": ["main.py"]}, raises=RuntimeError(secret))
    patch_svc(svc)
    out = skill_tools.skill_script_run(skill_id="s1")
    assert out["ok"] is False
    body = json.dumps(out, ensure_ascii=False)
    assert secret not in body and "RuntimeError" not in body
    assert "详见服务日志" in out["error"]


def test_failed_execution_reports_exit_and_error(patch_svc):
    svc, _ = _svc({"s1": ["main.py"]},
                  result=_FakeResult(success=False, stdout="", stderr="boom", error="脚本退出码 2"))
    patch_svc(svc)
    out = skill_tools.skill_script_run(skill_id="s1")
    assert out["ok"] is False and out["success"] is False
    assert out["exit_code"] == 1 and out["error"] == "脚本退出码 2"
    assert out["stderr_tail"] == "boom"
    assert "result" not in out, "失败时不要伪造结果字段"


def test_long_output_is_truncated(patch_svc):
    svc, _ = _svc({"s1": ["main.py"]}, result=_FakeResult(stdout="x" * 9000))
    patch_svc(svc)
    out = skill_tools.skill_script_run(skill_id="s1")
    assert len(out["stdout_tail"]) == skill_tools._STDOUT_TAIL_CHARS


# ── YAML 契约（D1：一份描述，两处逐字一致）────────────────────────
def _registered_literal() -> dict:
    """用与迁移脚本/守门测试相同的 AST 口径，抽出装饰器里的 name/description/schema。

    不引用模块常量（那样会让"注册实参"和"YAML"失去对拍意义）：抽取的就是
    `@_tools.register(...)` 里的**字面量**。
    """
    src = Path(skill_tools.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if not isinstance(dec, ast.Call):
                continue
            fn = dec.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name != "register":
                continue
            args = [ast.literal_eval(a) for a in dec.args]
            schema = None
            for kw in dec.keywords:
                if kw.arg == "schema":
                    schema = ast.literal_eval(kw.value)
            return {"name": args[0], "description": args[1], "schema": schema}
    raise AssertionError("没找到 register 装饰器（description/schema 必须是静态字面量）")


def test_yaml_matches_registered_description_and_schema():
    doc = yaml.safe_load(_YAML_PATH.read_text(encoding="utf-8"))
    reg = _registered_literal()
    assert doc["name"] == reg["name"] == skill_tools.TOOL_NAME
    assert doc["description"] == reg["description"]
    assert doc["schema"] == reg["schema"]
    assert doc["enabled"] is False, "新增可执行能力必须默认停用（先观察）"
    assert doc["category"] in {"code"}
    assert doc["effect"] == "execute" and doc["risk"] == "high"


def test_register_all_keeps_tool_registered():
    from agent import tools as t
    skill_tools.register_all(None)
    entry = t._registry.get(skill_tools.TOOL_NAME)
    assert entry is not None, "导入期注册必须生效（或 register_all 需响亮报警）"
    assert entry["description"] == _registered_literal()["description"]
