# -*- coding: utf-8 -*-
"""实例管理的**跨端载荷契约**（2026-11-22）。

【解决什么】§11.201–§11.203 三轮的 bug 都是同一类：前端发的字段名 ≠ 后端读的字段名
（base_url vs api_endpoint、config vs config_json、engine_type 大小写）。两侧各有用例却都绿，
因为**没有任何一条用例把两侧的字节对上**。

【做法】`yunshu-ui/src/lib/fixtures/instancePayload.json` 是唯一事实源：
- 前端用例断言「点新建/保存时发出去的就是这份字节」（instances.test.tsx）；
- 本文件把**同一份字节**喂给后端的校验/写入路径（NetworkConfigManager），断言被接受。
任一侧改字段名，另一侧的用例立刻红 —— 不再需要真机点一次 400 才发现。

【边界】只碰 tmp 配置文件（不经 app_server 的全局 manager），不写生产 network_config.json /
.env；api_key 给空串（`_save_secure` 不会触发）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.config_validation import (BUILTIN_ENGINES, normalize_engine_type,
                                    validate_search_instance)
from agent.network_config import NetworkConfigManager

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "yunshu-ui" / "src" / "lib" / "fixtures" / "instancePayload.json"


@pytest.fixture()
def payloads():
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


@pytest.fixture()
def mgr(tmp_path):
    return NetworkConfigManager(config_file=str(tmp_path / "network_config.json"))


def test_契约文件存在且四种载荷齐全(payloads):
    assert set(payloads) >= {"llm", "llm_update", "search", "search_update"}
    # 教训：api_endpoint 是后端字段名，base_url 是历史名（写了必 400）
    assert "api_endpoint" in payloads["llm"]["instance"]
    assert "base_url" not in payloads["llm"]["instance"]


def test_新建_LLM_载荷被后端接受(payloads, mgr):
    inst = payloads["llm"]["instance"]
    assert mgr.validate_llm_instance(inst) == [], (
        "前端发的新建载荷被后端校验拒绝 ⇒ 字段名/必填项又漂移了")
    saved = mgr.add_llm_instance(dict(inst))
    assert saved.get("api_endpoint") == inst["api_endpoint"]
    assert "base_url" not in saved
    rows = mgr.get_llm_instances()
    assert any(r.get("api_endpoint") == inst["api_endpoint"] for r in rows), (
        "列表回读没有 api_endpoint ⇒ 页面渲染端点会回退历史键/空")


def test_编辑_LLM_载荷被后端接受(payloads, mgr):
    saved = mgr.add_llm_instance(dict(payloads["llm"]["instance"]))
    updates = payloads["llm_update"]["updates"]
    assert "api_key" not in updates, "编辑态不许回填/回写密钥（留空=保留）"
    result = mgr.update_llm_instance(saved["id"], dict(updates))
    assert result is not None
    assert result["name"] == updates["name"]
    assert result["api_endpoint"] == updates["api_endpoint"]


def test_新建_搜索_载荷被后端接受(payloads):
    """搜索实例的增改在**路由**里（plugins/admin.py），不在 manager 上：
    这里复刻路由的两步 —— 入口归一化 + config_validation 校验 + 套默认模板。"""
    from agent.network_config import _DEFAULT_SEARCH_INSTANCE

    inst = dict(payloads["search"]["instance"])
    inst["engine_type"] = normalize_engine_type(inst["engine_type"])
    assert validate_search_instance(inst) == [], (
        "前端发的搜索载荷被 config_validation 拒绝 ⇒ 字段名/必填项又漂移了")
    new_inst = dict(_DEFAULT_SEARCH_INSTANCE)
    new_inst.update(inst)
    assert new_inst["engine_type"] == "custom"
    assert new_inst["api_endpoint"] == inst["api_endpoint"]


def test_编辑_搜索_载荷被后端接受(payloads):
    """编辑走 PUT：路由会把 engine_type 归一化、剥掉 api_key 后 inst.update(updates_clean)。"""
    from agent.network_config import _DEFAULT_SEARCH_INSTANCE

    inst = dict(_DEFAULT_SEARCH_INSTANCE)
    inst.update({"id": "s1", "name": "DuckDuckGo", "engine_type": "duckduckgo"})
    updates = dict(payloads["search_update"]["updates"])
    assert "api_key" not in updates, "编辑态不许回填/回写密钥（留空=保留）"
    updates["engine_type"] = normalize_engine_type(updates["engine_type"])
    clean = {k: v for k, v in updates.items() if k != "api_key"}
    inst.update(clean)
    assert inst["name"] == updates["name"]
    assert inst["engine_type"] == "duckduckgo"
    # 归一化后必须是词表里的值（否则后端 400「未知的内置引擎类型」）
    assert inst["engine_type"] in set(BUILTIN_ENGINES) | {"custom"}
