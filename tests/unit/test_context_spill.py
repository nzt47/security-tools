"""E0-2 大输出 spill 的契约测试。

覆盖：开关/阈值/预览的解析与钳制、spill 信封（ok 保留 / locator 形状 / 头尾预览）、
按 locator 分段读取（offset/limit/next_offset）、非法 locator、禁写自身（防递归）、
禁用时回到不落盘、非 dict 不处理。
"""
from __future__ import annotations

import re

import pytest

from agent.context import spill

_LOCATOR_RE = re.compile(r"^spill:[0-9a-f]{16}$")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for key in (spill.SPILL_ENABLED_ENV, spill.SPILL_THRESHOLD_ENV, spill.SPILL_PREVIEW_ENV):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture()
def isolated_dir(tmp_path, monkeypatch):
    directory = tmp_path / "spill"
    monkeypatch.setattr(spill, "spill_dir", lambda: directory)
    return directory


# ── 开关与阈值 ────────────────────────────────────────────────


def test_默认开启():
    assert spill.enabled() is True


@pytest.mark.parametrize("raw", ["0", "false", "no", "off", "OFF", " False "])
def test_关闭写法(monkeypatch, raw):
    monkeypatch.setenv(spill.SPILL_ENABLED_ENV, raw)
    assert spill.enabled() is False


def test_阈值默认与钳制(monkeypatch):
    assert spill.threshold_bytes() == 100_000
    monkeypatch.setenv(spill.SPILL_THRESHOLD_ENV, "1")
    assert spill.threshold_bytes() == 4_096
    monkeypatch.setenv(spill.SPILL_THRESHOLD_ENV, "99999999")
    assert spill.threshold_bytes() == 1_048_576
    monkeypatch.setenv(spill.SPILL_THRESHOLD_ENV, "not-a-number")
    assert spill.threshold_bytes() == 100_000


def test_预览默认与钳制(monkeypatch):
    assert spill.preview_bytes() == 8_000
    monkeypatch.setenv(spill.SPILL_PREVIEW_ENV, "1")
    assert spill.preview_bytes() == 512
    monkeypatch.setenv(spill.SPILL_PREVIEW_ENV, "99999999")
    assert spill.preview_bytes() == 65_536


def test_生产者上限随开关变化(monkeypatch):
    assert spill.capture_limit(102400) == 1_048_576
    monkeypatch.setenv(spill.SPILL_ENABLED_ENV, "0")
    assert spill.capture_limit(102400) == 102400


# ── spill 信封 ────────────────────────────────────────────────


def test_小结果不落盘(isolated_dir):
    assert spill.spill_result("shell_execute", {"ok": True, "stdout": "hi"}) is None
    assert not isolated_dir.exists()


def test_大结果落盘并保留_ok(isolated_dir, monkeypatch):
    monkeypatch.setenv(spill.SPILL_THRESHOLD_ENV, "4096")
    big = "x" * 20_000
    envelope = spill.spill_result("shell_execute", {"ok": True, "stdout": big})
    assert envelope is not None
    assert envelope["ok"] is True and envelope["spilled"] is True
    assert _LOCATOR_RE.match(envelope["locator"]), envelope["locator"]
    assert envelope["bytes"] > 4096
    assert "中间省略" in envelope["preview"]
    assert envelope["preview"].startswith("{\"ok\"")  # 预览是落盘文本（JSON 序列化）的头
    assert "xxxx" in envelope["preview"]
    spill_id = envelope["locator"].split(":", 1)[1]
    assert (isolated_dir / f"{spill_id}.txt").is_file()
    assert (isolated_dir / f"{spill_id}.json").is_file()


def test_失败结果落盘保留_ok_false(isolated_dir, monkeypatch):
    monkeypatch.setenv(spill.SPILL_THRESHOLD_ENV, "4096")
    envelope = spill.spill_result("git", {"ok": False, "error": "boom", "stdout": "y" * 9000})
    assert envelope is not None and envelope["ok"] is False
    assert envelope["error"] == "boom"


def test_预览预算(isolated_dir, monkeypatch):
    monkeypatch.setenv(spill.SPILL_THRESHOLD_ENV, "4096")
    monkeypatch.setenv(spill.SPILL_PREVIEW_ENV, "1000")
    envelope = spill.spill_result("read_file", {"ok": True, "content": "z" * 10_000})
    # 预览 = 头 800 + 提示 + 尾 200；给提示留 200 字符余量
    assert len(envelope["preview"]) <= 1000 + 200


def test_read_spill_自身不落盘(isolated_dir, monkeypatch):
    monkeypatch.setenv(spill.SPILL_THRESHOLD_ENV, "4096")
    assert spill.spill_result("read_spill", {"ok": True, "content": "q" * 20_000}) is None


def test_非字典结果不处理(isolated_dir, monkeypatch):
    monkeypatch.setenv(spill.SPILL_THRESHOLD_ENV, "4096")
    assert spill.spill_result("x", ["a" * 20_000]) is None


def test_禁用时不落盘(isolated_dir, monkeypatch):
    monkeypatch.setenv(spill.SPILL_ENABLED_ENV, "0")
    monkeypatch.setenv(spill.SPILL_THRESHOLD_ENV, "4096")
    assert spill.spill_result("shell_execute", {"ok": True, "stdout": "w" * 20_000}) is None


# ── 读取 ──────────────────────────────────────────────────────


def test_分段读取(isolated_dir):
    text = "".join(chr(ord("a") + i % 26) for i in range(5000))
    meta = spill.spill_text("read_file", text)
    first = spill.read_locator(meta["locator"], offset=0, limit=1000)
    assert first["ok"] is True and first["content"] == text[:1000]
    assert first["chars_total"] == 5000 and first["next_offset"] == 1000
    last = spill.read_locator(meta["locator"], offset=4900, limit=1000)
    assert last["content"] == text[4900:] and last["next_offset"] is None


@pytest.mark.parametrize("locator", ["", "spill:", "spill:XYZ", "../../etc/passwd",
                                     "spill:0123456789abcdef0", "0123456789abcdef"])
def test_非法_locator(isolated_dir, locator):
    result = spill.read_locator(locator)
    assert result["ok"] is False and "locator" in result["error"]


def test_locator_不存在(isolated_dir):
    result = spill.read_locator("spill:0123456789abcdef")
    assert result["ok"] is False and "不存在" in result["error"]


def test_读取参数钳制(isolated_dir):
    meta = spill.spill_text("x", "abc" * 100)
    result = spill.read_locator(meta["locator"], offset=-5, limit=10 ** 9)
    assert result["ok"] is True and result["offset"] == 0
    assert result["limit"] == spill._READ_LIMIT_MAX


def test_status_形状(isolated_dir):
    spill.spill_text("x", "hello")
    payload = spill.status()
    assert payload["enabled"] is True
    assert payload["files"] == 1
    assert payload["total_bytes"] == 5
    assert payload["threshold_bytes"] == 100_000

