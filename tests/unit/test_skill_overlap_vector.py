# -*- coding: utf-8 -*-
"""C-7 第二步：向量候选（agent/skills_mgmt/overlap_vector.py）的守卫。

锁四件事：
  1. 默认关（不设 env 就完全不加载模型、不算向量）；
  2. 开启后 provider=local / api 各自走对路径，阈值 0.25 生效（不降噪）；
  3. 任何失败都只回类名 + 空清单，不抛异常、不泄漏端点/密钥；
  4. 两个开关都在设置注册表里（「开关零缺口」+ UI 可打开）。
"""
from __future__ import annotations

import pytest

from agent.skills_mgmt import overlap_vector as ov

@pytest.fixture(autouse=True)
def _clean_cache():
    """每个用例前后清空向量缓存：否则前一条用例的假向量会被后一条命中。"""
    ov.reset_cache()
    yield
    ov.reset_cache()


SKILLS = [
    {"skill_id": "read-files", "name": "读文件",
     "trigger": ["需要读取文件内容时"], "steps": ["用 read_file 读指定文件"],
     "params": [], "guard": []},
    {"skill_id": "paint", "name": "配色",
     "trigger": ["需要给界面配色时"], "steps": ["先选主色再调对比度"],
     "params": [], "guard": []},
]
TOOLS = [
    {"id": "read_file", "name": "read_file",
     "summary": "读取文件内容，支持按行区间读取文本文件", "status": "ok"},
    {"id": "kb_capture", "name": "kb_capture",
     "summary": "收集素材入库，文本或文件路径进 inbox", "status": "ok"},
]


def test_默认关_不算也不加载模型(monkeypatch):
    monkeypatch.delenv("CP_SKILL_OVERLAP_VECTOR", raising=False)
    monkeypatch.delenv("CP_SKILL_OVERLAP_VECTOR_PROVIDER", raising=False)

    def _boom(texts):
        raise AssertionError("默认关时不许加载/调用 embedding")

    monkeypatch.setattr(ov, "_embed_local", _boom)
    monkeypatch.setattr(ov, "_embed_api", _boom)
    out = ov.vector_candidates(SKILLS, TOOLS)
    assert out["items"] == []
    assert out["stats"]["enabled"] is False
    assert out["stats"]["error"] == "disabled:CP_SKILL_OVERLAP_VECTOR=0"


def test_开启后_local_给出向量候选且阈值生效(monkeypatch):
    monkeypatch.setenv("CP_SKILL_OVERLAP_VECTOR", "1")
    monkeypatch.setenv("CP_SKILL_OVERLAP_VECTOR_PROVIDER", "local")
    seen = []

    def _fake(texts):
        seen.append(len(texts))
        # read_file 工具与 read-files 技能同向；kb_capture 与谁都不同向
        table = {
            "需要读取文件内容时\n用 read_file 读指定文件": [1.0, 0.0],
            "需要给界面配色时\n先选主色再调对比度": [0.0, 1.0],
            "读取文件内容，支持按行区间读取文本文件": [1.0, 0.0],
            "收集素材入库，文本或文件路径进 inbox": [0.0, 0.0],
        }
        return [table[t] for t in texts]

    monkeypatch.setattr(ov, "_embed_local", _fake)
    out = ov.vector_candidates(SKILLS, TOOLS)
    assert seen, "应该真的调用了 embedding"
    assert out["stats"]["provider"] == "st_bge_m3"  # 旧值 local 是别名
    assert out["stats"]["skills_embedded"] == 2
    assert out["stats"]["tools_embedded"] == 2
    items = {i["skill_id"]: i for i in out["items"]}
    assert "read-files" in items
    top = items["read-files"]["matches"][0]
    assert top["native_id"] == "read_file" and top["reason"] == "vector"
    assert top["score"] >= ov.VECTOR_THRESHOLD
    assert "paint" not in items, "正交向量（0 分）不该进候选"


def test_阈值不下调_分数不够就不报(monkeypatch):
    monkeypatch.setenv("CP_SKILL_OVERLAP_VECTOR", "1")
    monkeypatch.setenv("CP_SKILL_OVERLAP_VECTOR_PROVIDER", "local")

    def _weak(texts):
        # 全部同向但只给 0.2 的分量 → 归一化后仍是 1.0；这里改用不同长度维度制造 0.2 相似度
        return [[1.0, 0.0] if "read" in t or "读" in t else [0.2, 0.979] for t in texts]

    # 直接验证阈值参数：把阈值抬到 0.99 后，弱相关条目必须被过滤
    monkeypatch.setattr(ov, "_embed_local", _weak)
    out = ov.vector_candidates(SKILLS, TOOLS, threshold=0.99)
    for item in out["items"]:
        assert all(m["score"] >= 0.99 for m in item["matches"])


def test_api_未配端点_如实报错且不泄漏(monkeypatch):
    monkeypatch.setenv("CP_SKILL_OVERLAP_VECTOR", "1")
    monkeypatch.setenv("CP_SKILL_OVERLAP_VECTOR_PROVIDER", "api")
    monkeypatch.delenv("CP_SKILL_OVERLAP_EMBED_API_BASE", raising=False)
    monkeypatch.setenv("CP_SKILL_OVERLAP_EMBED_API_KEY", "sk-should-not-leak")
    out = ov.vector_candidates(SKILLS, TOOLS)
    assert out["items"] == []
    assert out["stats"]["provider"] == "api"
    assert out["stats"]["error"] == "RuntimeError"
    assert "sk-should-not-leak" not in str(out)
    assert "未配置" not in str(out), "面向调用方的字段只放类名，不放异常原文"


def test_三种_embedding_是排它单选(monkeypatch):
    """同一个时刻只可能拿到一个 provider；别名/大小写/拼错都不破坏「三选一」。"""
    monkeypatch.setenv("CP_SKILL_OVERLAP_VECTOR_PROVIDER", "openai-ish")
    assert ov.provider() == ov.PROVIDERS[0] == "st_bge_m3"
    monkeypatch.setenv("CP_SKILL_OVERLAP_VECTOR_PROVIDER", "API")
    assert ov.provider() == "api"
    monkeypatch.setenv("CP_SKILL_OVERLAP_VECTOR_PROVIDER", "local")
    assert ov.provider() == "st_bge_m3"
    monkeypatch.delenv("CP_SKILL_OVERLAP_VECTOR_PROVIDER")
    assert ov.provider() == "st_bge_m3"


@pytest.mark.parametrize("prov,label", [
    ("st_bge_m3", "BAAI/bge-m3"),
    ("native_minilm", "all-MiniLM-L6-v2 (onnx)"),
    ("api", "text-embedding-3-small"),
])
def test_同一时刻只有选中的那个后端被调用(monkeypatch, prov, label):
    """排它的行为面：另外两个后端**一次都不许被调用**（维度不同，混用分数不可比）。"""
    monkeypatch.setenv("CP_SKILL_OVERLAP_VECTOR", "1")
    monkeypatch.setenv("CP_SKILL_OVERLAP_VECTOR_PROVIDER", prov)
    calls = []

    def _mk(name):
        def _fn(texts):
            calls.append(name)
            return [[1.0, 0.0] for _ in texts]
        return _fn

    monkeypatch.setattr(ov, "_embed_local", _mk("st_bge_m3"))
    monkeypatch.setattr(ov, "_embed_minilm", _mk("native_minilm"))
    monkeypatch.setattr(ov, "_embed_api", _mk("api"))
    if prov == "api":
        monkeypatch.setenv("CP_SKILL_OVERLAP_EMBED_API_BASE", "https://example.invalid/v1")
    out = ov.vector_candidates(SKILLS, TOOLS)
    # 技能侧与工具侧各一次批量嵌入 ⇒ 允许两次，但**只允许**选中的那一个后端
    assert set(calls) == {prov}, calls
    assert calls.count(prov) >= 1
    assert out["stats"]["provider"] == prov
    assert label in ov.model_name() or prov == "api"


def test_两个开关都进了设置注册表():
    from agent.settings import registry as reg

    keys = {s.key for s in reg._REGISTRY_ROWS}
    assert "CP_SKILL_OVERLAP_VECTOR" in keys
    assert "CP_SKILL_OVERLAP_VECTOR_PROVIDER" in keys
    assert "CP_SKILL_OVERLAP_EMBED_API_KEY" in keys
    secret = next(s for s in reg._REGISTRY_ROWS
                  if s.key == "CP_SKILL_OVERLAP_EMBED_API_KEY")
    assert secret.secret is True
    spec = next(s for s in reg._REGISTRY_ROWS
                if s.key == "CP_SKILL_OVERLAP_VECTOR_PROVIDER")
    assert spec.validator is not None and tuple(spec.validator.choices) == ov.PROVIDERS


def test_面板载荷带向量段且默认关(monkeypatch):
    monkeypatch.delenv("CP_SKILL_OVERLAP_VECTOR", raising=False)
    from agent.skills_mgmt import native_functions as nf

    payload = nf.native_functions()
    assert payload["vector_overlaps"] == []
    assert payload["vector"]["enabled"] is False
    assert payload["vector"]["threshold"] == ov.VECTOR_THRESHOLD
