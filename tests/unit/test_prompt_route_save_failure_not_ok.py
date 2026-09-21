"""L34 · 回归：路由不得「接口报成功而实际失败」（save 返回值必须被检查）

缺陷形态（L33 报告里记录的既有隐患）
------------------------------------------------------------------
POST /api/system-prompt/config/apply 里 mgr.save(config_data) 的返回值**从未被
检查**：save() 返回 False（配置一个字都没落盘）时，路由仍一路走完并返回
{"ok": true}。这是 TASK-01「接口返回成功、数据却没落盘、且无任何痕迹」的同型
缺陷（只是这次失败发生在写盘侧），且后果更重 —— apply 的后续步骤
（build_template / 写运行时模板文件）全都基于**没保存成功**的配置。

修法（最小）
------------------------------------------------------------------
只加一处返回值检查：save() 为 False 时立刻返回 500 + 可读原因（与本文件其他
失败分支一致）；不改变成功路径的任何字节，也不改其他路由语义。
失败分支**不带** ignored_keys —— 什么都没落盘，回传「被丢弃的键」会误导
（与 POST /api/system-prompt/config 的 500 分支口径一致）。

注：POST /api/system-prompt/config 本来就有返回值检查（既有正确实现），本片
一并加测试把它**钉住**，防止将来被改回去。
"""
from __future__ import annotations

import json
from dataclasses import asdict

import pytest
from flask import Flask

from agent.server_routes.routes_system_prompt import register_routes
from agent.system_prompt_config import (
    SystemPromptConfigData,
    SystemPromptConfigManager,
    reset_system_prompt_manager,
)


@pytest.fixture
def env(monkeypatch, tmp_path):
    """真实注册的路由 + tmp 隔离的配置/模板文件 + 单例重建

    与 tests/unit/test_prompt_config_save_ignored_keys.py 同一技法：绝不触碰真实
    data/system_prompt_config.json 与 data/system_prompt.txt。
    """
    config_path = tmp_path / "system_prompt_config.json"
    template_path = tmp_path / "system_prompt.txt"
    monkeypatch.setattr("agent.system_prompt_config.CONFIG_FILE", str(config_path))
    monkeypatch.setattr("agent.server_auth._API_TOKEN_ENABLED", False, raising=False)
    import agent.system_prompt_manager as spm
    assert hasattr(spm, "SYSTEM_PROMPT_FILE"), "SYSTEM_PROMPT_FILE 不存在，隔离失败"
    monkeypatch.setattr(spm, "SYSTEM_PROMPT_FILE", str(template_path))

    reset_system_prompt_manager()
    app = Flask(__name__)
    app.config.update(TESTING=True)
    register_routes(app, lambda: None)
    try:
        with app.test_client() as client:
            yield {"client": client, "config": config_path, "template": template_path}
    finally:
        reset_system_prompt_manager()


@pytest.fixture
def save_always_fails(monkeypatch):
    """桩：save() 恒返回 False（模拟落盘失败），且不产生任何真实写入"""
    def _boom(self, config):
        self.last_ignored_keys = []
        return False

    monkeypatch.setattr(SystemPromptConfigManager, "save", _boom)
    return _boom


def _apply_payload():
    cfg = asdict(SystemPromptConfigData())
    cfg["sections"]["identity"]["custom_content"] = "L34 身份文本"
    return {"config": {"sections": cfg["sections"]}}


def _config_payload():
    return asdict(SystemPromptConfigData())


# ══════════════════════════════════════════════════════════════════════════
#  一、失败路径：save() 返回 False 时绝不得 ok:true
# ══════════════════════════════════════════════════════════════════════════

class TestSaveFailureNeverReportsSuccess:

    def test_apply_returns_500_when_save_fails(self, env, save_always_fails):
        """核心：apply 里 save 失败 ⇒ 500 + ok:False（而不是 200 + ok:true）"""
        r = env["client"].post("/api/system-prompt/config/apply", json=_apply_payload())

        assert r.status_code == 500, (
            "save() 返回 False（配置一个字都没落盘），路由却回了 "
            + str(r.status_code) + " —— 接口报成功而实际失败"
        )
        body = r.get_json()
        assert body["ok"] is False
        assert "template" not in body, "保存失败却仍回传了模板，等于掩盖失败"

    def test_apply_failure_reason_is_readable(self, env, save_always_fails):
        r = env["client"].post("/api/system-prompt/config/apply", json=_apply_payload())
        body = r.get_json()
        err = body.get("error") or ""
        assert err, "失败响应缺少可读原因"
        assert len(err) > 10, "失败原因过短，无法定位: " + repr(err)
        assert "未生效" in err, "失败原因未说明本次应用未生效: " + repr(err)

    def test_apply_failure_reports_no_ignored_keys(self, env, save_always_fails):
        """与 500 分支口径一致：什么都没落盘，不带 ignored_keys（免得误导）"""
        r = env["client"].post("/api/system-prompt/config/apply", json=_apply_payload())
        assert "ignored_keys" not in r.get_json()

    def test_apply_failure_writes_nothing(self, env, save_always_fails):
        """失败即中止：不得留下半个配置、也不得写运行时模板文件"""
        r = env["client"].post("/api/system-prompt/config/apply", json=_apply_payload())
        assert r.status_code == 500
        assert not env["config"].exists(), "保存失败却仍写了配置文件"
        assert not env["template"].exists(), (
            "保存失败却仍写了运行时模板文件（半应用状态）"
        )

    def test_config_post_returns_500_when_save_fails(self, env, save_always_fails):
        """既有正确实现回归护栏：config POST 本就检查返回值，钉住不许改回去"""
        r = env["client"].post("/api/system-prompt/config", json=_config_payload())
        assert r.status_code == 500
        body = r.get_json()
        assert body["ok"] is False
        assert body.get("error"), "失败响应缺少可读原因"
        assert not env["config"].exists()


# ══════════════════════════════════════════════════════════════════════════
#  二、成功路径：响应结构逐字不变（证明本次改动没有误伤）
# ══════════════════════════════════════════════════════════════════════════

class TestSuccessResponseShapeUnchanged:

    def test_apply_success_shape(self, env):
        r = env["client"].post("/api/system-prompt/config/apply", json=_apply_payload())
        assert r.status_code == 200
        body = r.get_json()
        assert set(body.keys()) == {
            "ok", "template", "template_length", "synced", "ignored_keys",
        }, "成功路径响应结构被改动: " + repr(sorted(body.keys()))
        assert body["ok"] is True
        assert isinstance(body["template"], str) and body["template"]
        assert body["template_length"] == len(body["template"])
        assert body["synced"] is True
        assert body["ignored_keys"] == []

    def test_config_post_success_shape(self, env):
        r = env["client"].post("/api/system-prompt/config", json=_config_payload())
        assert r.status_code == 200
        body = r.get_json()
        assert set(body.keys()) == {"ok", "ignored_keys"}
        assert body["ok"] is True
        assert body["ignored_keys"] == []

    def test_apply_success_still_reports_ignored_keys(self, env):
        """成功路径上 L33 的 ignored_keys 语义不受本次改动影响"""
        payload = _apply_payload()
        payload["config"]["sections"]["skill_instructions"]["custom_content"] = "L34 死数据"
        r = env["client"].post("/api/system-prompt/config/apply", json=payload)
        assert r.status_code == 200
        body = r.get_json()
        assert body["ignored_keys"] == ["skill_instructions.custom_content"]
        raw = env["config"].read_text(encoding="utf-8")
        assert "L34 死数据" not in raw
        assert json.loads(raw)["sections"]["identity"]["custom_content"] == "L34 身份文本"
