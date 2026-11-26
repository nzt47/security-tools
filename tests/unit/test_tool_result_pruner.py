"""E0-2② 工具结果裁剪：阈值 / 头尾保留 / 形状不变 / 开关 / 与 call() 的接线"""
from __future__ import annotations

import pytest

from agent.context import tool_result_pruner as pr


def test_小于阈值原样返回():
    text = "x" * 100
    out, removed = pr.prune_text(text, limit=1000)
    assert out == text and removed == 0


def test_超阈值留头尾并标注裁剪量():
    text = "A" * 3000 + "B" * 4000 + "C" * 3000
    out, removed = pr.prune_text(text, limit=1000)
    assert removed == 9000, "被裁的是中段 4000 B，加上头尾各让出的部分"
    # 头 70% / 尾 30%
    assert out.startswith("A" * 700) and out.endswith("C" * 300)
    assert "已裁剪" in out and "B" * 100 not in out


def test_prune_result_只动长文本字段且不改键名():
    big = "z" * 50_000
    src = {"ok": True, "stdout": big, "content": big, "stderr": "short", "count": 3}
    out, meta = pr.prune_result("shell_execute", src, limit=4000)
    assert meta["pruned"] is True
    assert set(out) == set(src), "不得新增/删除键"
    assert out["stderr"] == "short" and out["count"] == 3
    assert len(out["stdout"]) < len(big) and len(out["content"]) < len(big)
    assert sorted(meta["fields"]) == ["content", "stdout"]
    assert meta["removed_chars"] > 0


def test_非dict与小结果不动():
    assert pr.prune_result("x", "y" * 99999, limit=4000) == ("y" * 99999, {
        "pruned": False, "fields": [], "removed_chars": 0, "limit": 4000})
    small = {"ok": True, "stdout": "hi"}
    out, meta = pr.prune_result("x", small, limit=4000)
    assert out is small and meta["pruned"] is False


def test_开关关闭时不裁(monkeypatch):
    monkeypatch.setenv(pr.PRUNER_ENABLED_ENV, "0")
    big = {"stdout": "q" * 50_000}
    out, meta = pr.prune_result("x", big, limit=4000)
    assert out is big and meta["pruned"] is False
    assert pr.enabled() is False


def test_阈值环境变量与收敛(monkeypatch):
    monkeypatch.setenv(pr.PRUNE_CHARS_ENV, "8000")
    assert pr.prune_limit() == 8000
    monkeypatch.setenv(pr.PRUNE_CHARS_ENV, "1")
    assert pr.prune_limit() == pr.MIN_PRUNE_CHARS
    monkeypatch.setenv(pr.PRUNE_CHARS_ENV, "99999999")
    assert pr.prune_limit() == pr.MAX_PRUNE_CHARS
    monkeypatch.setenv(pr.PRUNE_CHARS_ENV, "abc")
    assert pr.prune_limit() == pr.DEFAULT_PRUNE_CHARS


def test_call_接线_超长结果被裁(monkeypatch):
    from agent import tools as T

    monkeypatch.setenv(pr.PRUNE_CHARS_ENV, "4000")
    monkeypatch.delenv(pr.PRUNER_ENABLED_ENV, raising=False)
    saved = T._registry.get("prune_probe")
    T.register("prune_probe", "test only", handler=lambda **kw: {
        "ok": True, "stdout": "L" * 20_000}, schema={"type": "object"})
    try:
        result = T.call("prune_probe", x=1)
        assert result["ok"] is True
        assert len(result["stdout"]) < 20_000
        assert "已裁剪" in result["stdout"]
    finally:
        if saved is None:
            T._registry.pop("prune_probe", None)
        else:
            T._registry["prune_probe"] = saved
        T._registry_version += 1
