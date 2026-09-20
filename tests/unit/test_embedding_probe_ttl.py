"""Embedding 探测缓存 TTL 回归测试 —— 陈旧的探测结果不得被永久信任

【本文件守护的缺陷】
    `agent/tool_router_hybrid.py::_read_probe_cache()` 原实现只读
    `bool(data["available"])`,**完全忽略 probed_at** ⇒ 一次负结果被永久固化。
    实测生产文件 `data/.embedding_probe` 内容为
    `{"available": false, "probed_at": 1784737215.8083909}`(写于 2026-07-23),
    此后从未被复核:依赖后来装好了、或当时只是瞬时失败,Embedding 都不会再启用,
    而且日志上看不出任何异常 —— 一次静默的能力损失。

【安全约定】
    本文件**不读、不写、不删**生产 `data/.embedding_probe`:autouse fixture 把
    模块级 `_PROBE_CACHE` 指向 `tmp_path`,并在每个用例里断言该路径确实落在 tmp 下。
    其中"复刻生产内容"的用例只把**同样的内容**写进 tmp 副本。
"""
from __future__ import annotations

import json
import time

import pytest

import agent.tool_router_hybrid as mod

# data/.embedding_probe 的真实内容(2026-07-23 写入,只复刻不触碰原文件)
_PRODUCTION_PAYLOAD = {"available": False, "probed_at": 1784737215.8083909}


@pytest.fixture(autouse=True)
def probe_cache(tmp_path, monkeypatch):
    """把缓存路径重定向到 tmp_path,并清空内存缓存/环境变量覆盖"""
    path = tmp_path / ".embedding_probe"
    monkeypatch.setattr(mod, "_PROBE_CACHE", str(path))
    monkeypatch.setattr(mod, "_PROBE_RESULT", None)
    monkeypatch.delenv("AGENT_HYBRID_EMBEDDING", raising=False)
    # 安全闸:写路径必须落在 tmp_path(绝不触碰生产 data/.embedding_probe)
    assert mod._PROBE_CACHE == str(path)
    assert str(tmp_path) in mod._PROBE_CACHE
    return path


def _write_raw(path, payload) -> None:
    """按原始 JSON 写缓存文件(模拟旧格式/手工改写的文件,不走 _write_probe_cache)"""
    path.write_text(json.dumps(payload), encoding="utf-8")


def _read_raw(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ════════════════════════════════════════════════════════════
#  一、新鲜缓存仍然被信任(不得因加 TTL 而失效)
# ════════════════════════════════════════════════════════════

class TestFreshCacheIsTrusted:
    @pytest.mark.parametrize("available", [True, False])
    def test_fresh_cache_returns_value(self, probe_cache, available):
        _write_raw(probe_cache, {"available": available, "probed_at": time.time()})
        assert mod._read_probe_cache() is available

    def test_just_inside_ttl_is_trusted(self, probe_cache):
        _write_raw(probe_cache, {
            "available": False,
            "probed_at": time.time() - (mod._PROBE_CACHE_TTL - 60.0),
        })
        assert mod._read_probe_cache() is False

    def test_fresh_cache_short_circuits_probe(self, probe_cache, monkeypatch):
        """新鲜缓存命中时不得起子进程探测"""
        _write_raw(probe_cache, {"available": False, "probed_at": time.time()})

        def _boom(_model):  # pragma: no cover —— 被调用即失败
            raise AssertionError("新鲜缓存不应触发重新探测")

        monkeypatch.setattr(mod, "_run_embedding_probe", _boom)
        assert mod._ensure_st_checked() is False
        assert mod._PROBE_RESULT is False

    def test_write_then_read_roundtrip(self, probe_cache):
        """_write_probe_cache 写出的 probed_at 必须是可用的时间戳"""
        mod._write_probe_cache(True)
        payload = _read_raw(probe_cache)
        assert isinstance(payload["probed_at"], float)
        assert mod._read_probe_cache() is True


# ════════════════════════════════════════════════════════════
#  二、过期缓存 ⇒ 未命中 ⇒ 重新探测并覆写新鲜值(核心回归)
# ════════════════════════════════════════════════════════════

class TestStaleCacheIsReprobed:
    def test_stale_negative_is_a_miss(self, probe_cache):
        _write_raw(probe_cache, {
            "available": False,
            "probed_at": time.time() - (mod._PROBE_CACHE_TTL + 60.0),
        })
        assert mod._read_probe_cache() is None

    def test_stale_positive_is_a_miss(self, probe_cache):
        """正结果同样过期:环境漂移后陈旧 True 会让问题推迟到起 worker 时才暴露"""
        _write_raw(probe_cache, {
            "available": True,
            "probed_at": time.time() - (mod._PROBE_CACHE_TTL + 60.0),
        })
        assert mod._read_probe_cache() is None

    def test_stale_negative_is_reprobed_and_rewritten(self, probe_cache, monkeypatch):
        """★ 核心:陈旧 false 不能再永久禁用 Embedding"""
        _write_raw(probe_cache, {
            "available": False,
            "probed_at": time.time() - (mod._PROBE_CACHE_TTL + 60.0),
        })
        calls = []

        def _probe(model_name):
            calls.append(model_name)
            return True  # 依赖此时已经可用

        monkeypatch.setattr(mod, "_run_embedding_probe", _probe)

        assert mod._ensure_st_checked() is True
        assert calls == [mod._DEFAULT_MODEL], "陈旧缓存必须触发一次真实探测"
        payload = _read_raw(probe_cache)
        assert payload["available"] is True
        assert payload["probed_at"] > time.time() - 60.0, "重探后必须写入新鲜 probed_at"

    def test_stale_positive_is_reprobed(self, probe_cache, monkeypatch):
        _write_raw(probe_cache, {
            "available": True,
            "probed_at": time.time() - (mod._PROBE_CACHE_TTL + 60.0),
        })
        monkeypatch.setattr(mod, "_run_embedding_probe", lambda _m: False)

        assert mod._ensure_st_checked() is False
        assert _read_raw(probe_cache)["available"] is False

    def test_production_shaped_stale_payload_is_reprobed(self, probe_cache, monkeypatch):
        """复刻生产文件的真实内容(available=false, 写于 2026-07-23)⇒ 判为过期

        只把同样的 payload 写进 tmp_path 副本,生产文件不被读取或修改。
        """
        _write_raw(probe_cache, _PRODUCTION_PAYLOAD)
        monkeypatch.setattr(mod, "_run_embedding_probe", lambda _m: True)

        assert mod._read_probe_cache() is None
        assert mod._ensure_st_checked() is True


# ════════════════════════════════════════════════════════════
#  三、无法判定新鲜度的缓存一律视为未命中
# ════════════════════════════════════════════════════════════

class TestUnverifiableTimestampsAreMisses:
    @pytest.mark.parametrize(
        "probed_at",
        [
            None,
            "2026-07-23T00:20:15",   # 字符串(旧格式/手写)
            True,                    # bool 是 int 子类,必须显式排除
            False,
            [],                      # 非数字类型
            {"t": 1},
        ],
    )
    def test_non_numeric_or_missing_probed_at_is_a_miss(self, probe_cache, probed_at):
        payload = {"available": True}
        if probed_at is not None:
            payload["probed_at"] = probed_at
        _write_raw(probe_cache, payload)
        assert mod._read_probe_cache() is None

    def test_explicit_null_probed_at_is_a_miss(self, probe_cache):
        _write_raw(probe_cache, {"available": True, "probed_at": None})
        assert mod._read_probe_cache() is None

    def test_legacy_file_without_probed_at_triggers_one_reprobe(self, probe_cache, monkeypatch):
        """旧格式(无 probed_at)判为过期 ⇒ 重探一次,并把文件升级为带时间戳"""
        _write_raw(probe_cache, {"available": False})
        calls = []
        monkeypatch.setattr(mod, "_run_embedding_probe",
                            lambda _m: (calls.append(1), True)[1])

        assert mod._ensure_st_checked() is True
        assert len(calls) == 1
        assert mod._read_probe_cache() is True  # 已被覆写为新鲜值

    def test_future_timestamp_is_a_miss(self, probe_cache):
        """时间戳在未来(时钟回拨/文件被手改)⇒ 不可信,重新探测"""
        _write_raw(probe_cache, {"available": True, "probed_at": time.time() + 3600})
        assert mod._read_probe_cache() is None

    def test_corrupted_file_is_a_miss(self, probe_cache):
        probe_cache.write_text("invalid json {{{", encoding="utf-8")
        assert mod._read_probe_cache() is None

    def test_missing_file_is_a_miss(self, probe_cache):
        assert mod._read_probe_cache() is None

    def test_payload_without_available_key_is_a_miss(self, probe_cache):
        _write_raw(probe_cache, {"probed_at": time.time()})
        assert mod._read_probe_cache() is None


# ════════════════════════════════════════════════════════════
#  四、不会重探风暴 + TTL 取值可辩护
# ════════════════════════════════════════════════════════════

class TestNoReprobeStorm:
    def test_memory_cache_prevents_repeated_probe(self, probe_cache, monkeypatch):
        """陈旧文件 + 内存缓存短路 ⇒ 同一进程内最多探测一次"""
        _write_raw(probe_cache, {
            "available": False,
            "probed_at": time.time() - (mod._PROBE_CACHE_TTL + 60.0),
        })
        calls = []
        monkeypatch.setattr(mod, "_run_embedding_probe",
                            lambda _m: (calls.append(1), True)[1])

        for _ in range(5):
            assert mod._ensure_st_checked() is True
        assert len(calls) == 1, "内存缓存 _PROBE_RESULT 必须挡住重复探测"

    def test_ttl_is_bounded_and_reasonable(self):
        """TTL 必须既有限、又不至于每次进程启动都重探(探测需起解释器+加载模型)"""
        assert 3600.0 <= mod._PROBE_CACHE_TTL <= 30 * 24 * 3600.0

    def test_boundary_just_outside_ttl_is_a_miss(self, probe_cache):
        _write_raw(probe_cache, {
            "available": False,
            "probed_at": time.time() - (mod._PROBE_CACHE_TTL + 1.0),
        })
        assert mod._read_probe_cache() is None
