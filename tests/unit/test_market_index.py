# -*- coding: utf-8 -*-
"""E4-4：扩展市场索引契约（`agent/extensions/market_index.py`）+ 抓取硬化。

判据（对应 roadmap E4-4 与 §9.5 差距分析）：
  1. 单/复数类型键与扁平 `items` 三种形状归一后一致 —— 修复"复数键整类被过滤"；
  2. 字段白名单：`command`/`args` 之类安装参数不进入索引条目（投毒面）；
  3. `source` 只允许已知前缀；非法来源整条丢弃；
  4. bundle 只允许 plugin、未知/异类引用被过滤、空 bundle 丢弃；
  5. 上限：条目数 / bundle 项数 / schema_version；
  6. 抓取：非 https 拒、SSRF 拒、超字节拒、禁跟随 3xx、归一后写缓存；
  7. 推荐位默认关（search/recommend 为空），开后 featured 优先。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from agent.extensions import market_index as MI
from agent.extensions.market import ExtensionMarket

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "market" / "index_v1.json"


def _raw() -> dict:
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


# ════════════════════════════════════════════════════════════
#  一、归一化契约
# ════════════════════════════════════════════════════════════

def test_复数类型键被正确归一_修复旧bug():
    """旧实现按单数键比较 ⇒ `plugins` 整类被过滤；归一后必须都在。"""
    index = MI.normalize_index(_raw())
    ids = {e["id"] for e in index["items"]}
    assert {"alpha", "beta", "cmd", "s1"} <= ids
    assert {e["type"] for e in index["items"]} == {"plugin", "skill"}


def test_单数键与扁平items形状等价():
    raw = _raw()
    singular = {
        "schema_version": 1,
        "plugin": raw["plugins"],
        "skill": raw["skills"],
        "bundles": raw["bundles"],
    }
    flat = {
        "schema_version": 1,
        "items": [{"type": "plugin", **e} for e in raw["plugins"]]
        + [{"type": "skill", **e} for e in raw["skills"]],
        "bundles": raw["bundles"],
    }
    a = [(e["type"], e["id"]) for e in MI.normalize_index(raw)["items"]]
    b = [(e["type"], e["id"]) for e in MI.normalize_index(singular)["items"]]
    c = [(e["type"], e["id"]) for e in MI.normalize_index(flat)["items"]]
    assert a == b == c


def test_字段白名单丢掉安装参数():
    index = MI.normalize_index(_raw())
    cmd = next(e for e in index["items"] if e["id"] == "cmd")
    assert "command" not in cmd and "args" not in cmd
    assert set(cmd) <= MI.ENTRY_FIELDS | {"type"}


def test_非法source整条丢弃():
    index = MI.normalize_index(_raw())
    assert "poison" not in {e["id"] for e in index["items"]}
    assert index["dropped"] >= 1


def test_bundle只引用plugin且未知项被过滤():
    index = MI.normalize_index(_raw())
    bundles = {b["id"]: b for b in index["bundles"]}
    assert list(bundles) == ["starter"]
    assert bundles["starter"]["items"] == ["alpha", "beta"]   # missing 被过滤
    assert bundles["starter"]["featured"] is True
    assert "bad" not in bundles                                # 只含 skill ⇒ 空 ⇒ 丢弃


def test_schema版本不符直接拒绝():
    with pytest.raises(MI.IndexSchemaError):
        MI.normalize_index({"schema_version": 2, "plugins": []})


def test_顶层非对象也拒绝():
    with pytest.raises(MI.IndexSchemaError):
        MI.normalize_index("not-an-index")


def test_条目数上限():
    raw = {"plugins": [{"id": "p%d" % i, "name": "p", "source": "github:o/r"}
                       for i in range(MI.MAX_ENTRIES + 10)]}
    index = MI.normalize_index(raw)
    assert index["count"] == MI.MAX_ENTRIES


def test_bundle项数上限():
    raw = {
        "plugins": [{"id": "p%d" % i, "name": "p"} for i in range(MI.MAX_BUNDLE_ITEMS + 5)],
        "bundles": [{"id": "b", "items": ["p%d" % i for i in range(MI.MAX_BUNDLE_ITEMS + 5)]}],
    }
    index = MI.normalize_index(raw)
    assert len(index["bundles"][0]["items"]) == MI.MAX_BUNDLE_ITEMS


def test_featured投影():
    index = MI.normalize_index(_raw())
    assert [e["id"] for e in MI.featured_items(index, "plugin")] == ["alpha"]
    assert [b["id"] for b in MI.featured_bundles(index, "plugin")] == ["starter"]
    assert MI.featured_bundles(index, "skill") == []


# ════════════════════════════════════════════════════════════
#  二、市场取数（缓存 + 推荐位默认关）
# ════════════════════════════════════════════════════════════

def test_搜索按类型过滤_复数键不再漏():
    m = ExtensionMarket()
    m._cache = MI.normalize_index(_raw())
    plugins = m.search_community("", "plugin")
    skills = m.search_community("", "skill")
    assert {e["id"] for e in plugins} >= {"alpha", "beta"}
    assert "s1" not in {e["id"] for e in plugins}
    assert [e["id"] for e in skills] == ["s1"]


def test_关键词命中名称描述标签():
    m = ExtensionMarket()
    m._cache = MI.normalize_index(_raw())
    assert {e["id"] for e in m.search_community("first", "plugin")} == {"alpha"}
    assert {e["id"] for e in m.search_community("ui", "plugin")} == {"alpha"}


def test_推荐位默认关(monkeypatch):
    monkeypatch.delenv("CP_EXTENSION_MARKET_RECOMMEND_ENABLED", raising=False)
    m = ExtensionMarket()
    m._cache = MI.normalize_index(_raw())
    out = m.search_all("", "plugin", include_github=False)
    assert out["recommend_enabled"] is False
    assert out["featured"] == [] and out["bundles"] == []
    assert m.get_recommendations("plugin") == []
    assert m.get_recommendations("plugin", force=True)[0]["id"] == "alpha"


def test_推荐位打开后featured优先(monkeypatch):
    monkeypatch.setenv("CP_EXTENSION_MARKET_RECOMMEND_ENABLED", "1")
    m = ExtensionMarket()
    m._cache = MI.normalize_index(_raw())
    out = m.search_all("", "plugin", include_github=False)
    assert out["recommend_enabled"] is True
    assert [e["id"] for e in out["featured"]] == ["alpha"]
    assert [b["id"] for b in out["bundles"]] == ["starter"]


# ════════════════════════════════════════════════════════════
#  三、抓取硬化（不联网：全部打桩）
# ════════════════════════════════════════════════════════════

class _FakeResp:
    def __init__(self, payload: bytes):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, limit: int = -1):
        return self._payload if limit < 0 else self._payload[:limit]


class _FakeOpener:
    def __init__(self, payload: bytes):
        self.payload = payload
        self.calls = 0

    def open(self, req, timeout=None):
        self.calls += 1
        return _FakeResp(self.payload)


def _patch_fetch(monkeypatch, payload: bytes, tmp_path):
    from agent.extensions import market as M

    opener = _FakeOpener(payload)
    monkeypatch.setattr(M.urllib.request, "build_opener", lambda *a, **k: opener)
    monkeypatch.setattr(M, "_ssrf_block_reason", lambda url: None)
    monkeypatch.setattr(M, "_LOCAL_INDEX_CACHE", tmp_path / "cache.json")
    return opener


def test_非https直接拒绝(monkeypatch, tmp_path):
    from agent.extensions import market as M

    opener = _patch_fetch(monkeypatch, b"{}", tmp_path)
    monkeypatch.setenv(M.ENV_INDEX_URL, "http://example.com/index.json")
    assert ExtensionMarket().fetch_community_index() is None
    assert opener.calls == 0


def test_ssrf拒绝(monkeypatch, tmp_path):
    from agent.extensions import market as M

    opener = _patch_fetch(monkeypatch, b"{}", tmp_path)
    monkeypatch.setattr(M, "_ssrf_block_reason", lambda url: "内网地址")
    monkeypatch.setenv(M.ENV_INDEX_URL, "https://127.0.0.1/index.json")
    assert ExtensionMarket().fetch_community_index() is None
    assert opener.calls == 0


def test_超字节上限拒绝(monkeypatch, tmp_path):
    from agent.extensions import market as M

    _patch_fetch(monkeypatch, b"x" * (MI.MAX_INDEX_BYTES + 1), tmp_path)
    monkeypatch.setenv(M.ENV_INDEX_URL, "https://example.com/index.json")
    assert ExtensionMarket().fetch_community_index() is None
    assert not (tmp_path / "cache.json").exists()


def test_合法索引归一后写缓存(monkeypatch, tmp_path):
    from agent.extensions import market as M

    payload = _FIXTURE.read_bytes()
    _patch_fetch(monkeypatch, payload, tmp_path)
    monkeypatch.setenv(M.ENV_INDEX_URL, "https://example.com/index.json")
    index = ExtensionMarket().fetch_community_index()
    assert index["schema_version"] == 1 and index["count"] == 4
    assert (tmp_path / "cache.json").exists()
    assert json.loads((tmp_path / "cache.json").read_text(encoding="utf-8"))["count"] == 4


def test_版本不符的远程索引拒绝(monkeypatch, tmp_path):
    from agent.extensions import market as M

    _patch_fetch(monkeypatch, b'{"schema_version": 9}', tmp_path)
    monkeypatch.setenv(M.ENV_INDEX_URL, "https://example.com/index.json")
    assert ExtensionMarket().fetch_community_index() is None


def test_禁止跟随3xx():
    from agent.extensions.market import _NoRedirect

    assert _NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil") is None
