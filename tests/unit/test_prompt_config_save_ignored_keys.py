"""L33 · 收口回归（save 路径）：整份配置保存时非 editable 节的 custom_content

为什么 update_section 的收口不够
------------------------------------------------------------------
POST /api/system-prompt/config（agent/server_routes/routes_system_prompt.py）
走的是 mgr.save(data)（整份配置），**这才是前端实际在用的入口**；
update_section 连生产调用方都没有。只收口 update_section = 把缺陷留在唯一的
真实入口上。

收口形态（本条路径允许「忽略 + 显式信号」，因为响应是 JSON，可承载信号）
------------------------------------------------------------------
save() 落盘前**丢弃**非 editable 节的 custom_content（渲染层从不读它 = 死数据），
但绝不静默，必须**同时**满足：
  1) 结构化 warning 日志（action=save.dropped_ignored_keys，含 ignored_keys）；
  2) 被丢弃的键记录在 manager.last_ignored_keys，并由路由在 JSON 响应里
     回传 ignored_keys（空列表也要回传，字段不省略）。
save() 的返回类型仍是 bool —— 未改 API。

口径差异（有意为之，见 save()._drop_dead_custom_content docstring）
------------------------------------------------------------------
update_section 对非 editable 节是「不得出现该键」（连空值也拒）；
save() 只丢弃**非空**值：注册表默认配置给每个节都写了 custom_content: ""，
前端 GET→POST 又是整包往返，必然把它带回来；丢弃空值既不减少死数据，又会
让每次保存都产生噪声并改写配置文件形态。空值原样保留（本片有测试钉住）。
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict

import pytest
from flask import Flask

from agent.server_routes.routes_system_prompt import register_routes
from agent.system_prompt_config import (
    SystemPromptConfigData,
    SystemPromptConfigManager,
    is_section_editable,
)

SENTINEL = "ZZ_L33_SAVE_DEAD_DATA_SENTINEL_ZZ"
_MOD_LOGGER_NAME = "agent.system_prompt_config"


# ══════════════════════════════════════════════════════════════════════════
#  Fixtures
# ══════════════════════════════════════════════════════════════════════════

@pytest.fixture
def config_file(tmp_path):
    """临时配置文件路径（绝不触碰真实 data/system_prompt_config.json）"""
    return str(tmp_path / "system_prompt_config.json")


@pytest.fixture
def manager(monkeypatch, config_file):
    monkeypatch.setattr("agent.system_prompt_config.CONFIG_FILE", config_file)
    return SystemPromptConfigManager()


@pytest.fixture
def mod_logs():
    """直接挂在模块 logger 上收集 record（与 L32 同技法，不受 root 重配影响）"""
    logger = logging.getLogger(_MOD_LOGGER_NAME)
    records = []

    class _Collector(logging.Handler):
        def emit(self, record):
            records.append(record)

    handler = _Collector(level=logging.DEBUG)
    old_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)


@pytest.fixture
def flask_client(monkeypatch, tmp_path):
    """真实注册的 Flask 路由 + tmp 隔离的配置/模板文件

    与 tests/unit/test_background_tasks_routes.py 同一模式：用真实
    register_routes（而非手搓），确保测的是线上那条注册路径。
    """
    config_path = str(tmp_path / "system_prompt_config.json")
    monkeypatch.setattr("agent.system_prompt_config.CONFIG_FILE", config_path)
    # 认证旁路：未配置令牌时 require_token 本就放行；这里显式关掉共享令牌开关，
    # 避免外部环境设了 FLASK_API_TOKEN 时用例 401（既有测试同款技法）
    monkeypatch.setattr("agent.server_auth._API_TOKEN_ENABLED", False, raising=False)
    # app/apply 会写运行时模板文件 —— 必须指到 tmp，绝不能碰真实 data/system_prompt.txt。
    # 这里**不做 except 兜底**：patch 不上就让它炸（宁可测试失败，也不能静默写真实文件）。
    import agent.system_prompt_manager as spm
    assert hasattr(spm, "SYSTEM_PROMPT_FILE"), "SYSTEM_PROMPT_FILE 不存在，隔离失败"
    monkeypatch.setattr(spm, "SYSTEM_PROMPT_FILE",
                        str(tmp_path / "system_prompt.txt"))

    from agent.system_prompt_config import reset_system_prompt_manager
    reset_system_prompt_manager()   # 让 _get_mgr() 用被 patch 的 CONFIG_FILE 重建单例
    app = Flask(__name__)
    app.config.update(TESTING=True)
    register_routes(app, lambda: None)
    try:
        with app.test_client() as client:
            yield client, config_path
    finally:
        reset_system_prompt_manager()


def _payload(**section_values):
    """从默认配置起一份「整包 payload」，按 {节: custom_content} 逐个塞哨兵"""
    cfg = asdict(SystemPromptConfigData())
    for key, value in section_values.items():
        cfg["sections"][key]["custom_content"] = value
    return cfg


def _read_config(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _drop_records(records):
    """结构化「丢弃」日志（log_dict 载荷）"""
    out = []
    for rec in records:
        payload = rec.msg
        if isinstance(payload, dict) and payload.get("action") == "save.dropped_ignored_keys":
            out.append(payload)
    return out


# ══════════════════════════════════════════════════════════════════════════
#  一、save() 直接行为（管理器层）
# ══════════════════════════════════════════════════════════════════════════

class TestSaveDropsDeadDataButNeverSilently:

    def test_last_ignored_keys_exists_before_any_save(self, manager):
        """防空转：属性在构造后即存在（路由读它不该 AttributeError）"""
        assert manager.last_ignored_keys == []

    def test_non_editable_dead_data_not_persisted(self, manager, config_file, mod_logs):
        """① 不落盘 ② 管理器上看得见 ③ 结构化 warning 留痕"""
        assert is_section_editable("skill_instructions") is False
        ok = manager.save(_payload(skill_instructions=SENTINEL))
        assert ok is True

        # ① 死数据没落盘（整份文件扫描）
        raw = _read_config(config_file)
        assert SENTINEL not in raw, "非 editable 节的 custom_content 竟被落盘 = 死数据"
        assert "skill_instructions.custom_content" not in raw

        # ② 管理器上有显式信号
        assert manager.last_ignored_keys == ["skill_instructions.custom_content"]

        # ③ 结构化日志
        drops = _drop_records(mod_logs)
        assert len(drops) == 1, "丢弃未产生（或重复产生）结构化日志"
        assert drops[0]["ignored_keys"] == ["skill_instructions.custom_content"]
        assert drops[0]["count"] == 1
        assert drops[0]["module_name"] == "system_prompt_config"
        assert drops[0]["level"] == "WARNING"
        assert [r for r in mod_logs if r.levelno == logging.WARNING]

    def test_editable_custom_content_still_persisted(self, manager, config_file, mod_logs):
        """合法节行为完全不变：identity/principles 的自定义内容照旧落盘"""
        ok = manager.save(_payload(identity=SENTINEL, principles=SENTINEL + "_P"))
        assert ok is True

        raw = _read_config(config_file)
        assert SENTINEL in raw
        cfg = json.loads(raw)
        assert cfg["sections"]["identity"]["custom_content"] == SENTINEL
        assert cfg["sections"]["principles"]["custom_content"] == SENTINEL + "_P"
        assert manager.last_ignored_keys == []
        assert _drop_records(mod_logs) == []

    def test_multiple_offending_sections_all_reported(self, manager, config_file):
        ok = manager.save(_payload(
            skill_instructions=SENTINEL,
            tool_status=SENTINEL,
            memory_context=SENTINEL,
        ))
        assert ok is True
        assert sorted(manager.last_ignored_keys) == [
            "memory_context.custom_content",
            "skill_instructions.custom_content",
            "tool_status.custom_content",
        ]
        assert SENTINEL not in _read_config(config_file)

    def test_other_fields_of_offending_section_survive(self, manager, config_file):
        """只丢死数据键：同节的 enabled/token_limit/label/extra_params 原样落盘"""
        payload = _payload(skill_instructions=SENTINEL)
        payload["sections"]["skill_instructions"].update({
            "enabled": False,
            "token_limit": 777,
            "label": "L33 标签",
            "extra_params": {"l33": True},
        })
        assert manager.save(payload) is True

        sec = json.loads(_read_config(config_file))["sections"]["skill_instructions"]
        assert sec["enabled"] is False
        assert sec["token_limit"] == 777
        assert sec["label"] == "L33 标签"
        assert sec["extra_params"]["l33"] is True
        assert "custom_content" not in sec

    def test_blank_custom_content_is_preserved_and_not_reported(self, manager, config_file, mod_logs):
        """口径钉死：空值**不算**丢弃（默认配置本就全空 + 整包往返必然带回）

        若改成连空值也丢，每次保存都会产生一堆噪声 ignored_keys，且会改写配置
        文件既有形态。此断言把该决定固定下来，改动它必须是有意识的。
        """
        assert manager.save(_payload(skill_instructions="")) is True
        cfg = json.loads(_read_config(config_file))
        assert cfg["sections"]["skill_instructions"]["custom_content"] == ""
        assert manager.last_ignored_keys == []
        assert _drop_records(mod_logs) == []

    def test_default_config_first_load_reports_nothing(self, manager, config_file):
        """非空转对照：默认配置（每节都有空 custom_content）首次落盘零噪声"""
        manager.load()   # 首次：写默认配置
        assert manager.last_ignored_keys == []
        cfg = json.loads(_read_config(config_file))
        assert cfg["sections"]["skill_instructions"]["custom_content"] == ""

    def test_ignored_keys_reset_between_saves(self, manager, config_file):
        """信号是「本次」的：上一次的丢弃不得残留到下一次"""
        manager.save(_payload(skill_instructions=SENTINEL))
        assert manager.last_ignored_keys != []
        manager.save(_payload(identity=SENTINEL))
        assert manager.last_ignored_keys == []

    def test_caller_dict_is_not_mutated(self, manager):
        """丢弃只发生在深拷贝上：调用方传入的 dict 不被改写"""
        payload = _payload(skill_instructions=SENTINEL)
        manager.save(payload)
        assert payload["sections"]["skill_instructions"]["custom_content"] == SENTINEL

    def test_save_without_sections_key_is_tolerated(self, manager):
        """边界：无 sections 的坏结构不抛错（保持 save 既有容错语义）"""
        assert manager.save({"version": 999, "sections": {}}) is True
        assert manager.last_ignored_keys == []


# ══════════════════════════════════════════════════════════════════════════
#  二、HTTP 面：响应必须带回显式信号
# ══════════════════════════════════════════════════════════════════════════

class TestRouteReportsIgnoredKeys:

    def test_post_config_reports_ignored_keys(self, flask_client, tmp_path):
        client, config_path = flask_client
        r = client.post("/api/system-prompt/config", json=_payload(
            identity=SENTINEL, skill_instructions=SENTINEL))
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert body["ignored_keys"] == ["skill_instructions.custom_content"], (
            "响应未回传被丢弃的键 = 接口返回成功但数据被悄悄丢掉"
        )
        raw = _read_config(config_path)
        assert SENTINEL in raw                                    # identity 的照旧落盘
        assert raw.count(SENTINEL) == 1                           # 死数据那份没落盘
        assert json.loads(raw)["sections"]["skill_instructions"].get("custom_content") is None

    def test_post_config_without_drops_still_returns_empty_list(self, flask_client):
        """字段不省略：无丢弃时回空列表，调用方好判断"""
        client, _ = flask_client
        r = client.post("/api/system-prompt/config", json=_payload(identity=SENTINEL))
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert "ignored_keys" in body, "ignored_keys 字段被省略了"
        assert body["ignored_keys"] == []

    def test_apply_route_reports_ignored_keys(self, flask_client):
        """apply 同样经 save() 落盘，故同样要回传（否则该入口仍会静默）"""
        client, config_path = flask_client
        r = client.post("/api/system-prompt/config/apply", json={
            "config": {"sections": {"tool_status": {"enabled": True, "custom_content": SENTINEL}}},
        })
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert body["ignored_keys"] == ["tool_status.custom_content"]
        assert SENTINEL not in _read_config(config_path)
