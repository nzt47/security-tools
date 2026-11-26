# -*- coding: utf-8 -*-
"""E0-2③ 图片/截图 offload（2026-11-26）

为什么：browser_screenshot 回 base64 PNG（上限 50 万字符），一次截图就能吃掉十几万 token。
图片内容没坏，只是不该进上下文 ⇒ 落盘 + 占位 + 可再读（元数据 + 尽力 OCR）。

本文件钉住：
  1. 只有**带图片魔数**的长 base64 字段才被换成占位（普通长文本/坏 base64 一律不动）；
  2. 落盘路径由常量目录 + 校验过的 locator 决定（image:<16 hex>，不收任意路径）；
  3. read_spill(image:...) 分派到图片读数：只回元数据与 OCR，不回二进制；
  4. 开关 CP_IMAGE_OFFLOAD 关掉即回到旧行为（base64 原样进上下文）。
"""
from __future__ import annotations

import base64
import json

import pytest

from agent.context import image_offload
from agent.context import spill


def _png_blob(width=640, height=480, pad=3000):
    """构造一个带 PNG 魔数 + IHDR 的假图（够长以越过 offload 阈值）"""
    header = b"\x89PNG\r\n\x1a\n"
    ihdr_len = (13).to_bytes(4, "big")
    body = b"IHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big")
    return header + ihdr_len + body + b"\x08\x02\x00\x00\x00" + b"\x00" * pad


@pytest.fixture(autouse=True)
def _tmp_image_dir(tmp_path, monkeypatch):
    """落盘目录换成 tmp（不往 agent/data/images 里塞测试垃圾）"""
    target = tmp_path / "images"
    monkeypatch.setattr(image_offload, "image_dir", lambda: target)
    return target


class TestOffloadResult:
    def test_长base64图片被换成占位并可再读(self, _tmp_image_dir):
        blob = _png_blob()
        b64 = base64.b64encode(blob).decode("ascii")
        result = {"ok": True, "screenshot_base64": b64}
        got = image_offload.offload_result("browser_screenshot", result)
        assert got is not None
        assert got["image_offloaded"] is True
        assert got["image_mime"] == "image/png"
        assert got["image_bytes"] == len(blob)
        assert got["image_size"] == {"width": 640, "height": 480}
        locator = got["image_locator"]
        assert locator.startswith("image:") and len(locator) == len("image:") + 16
        # 字段名与结果形状不变，值换成一行占位（下游 dict 契约不破）
        assert got["ok"] is True
        assert isinstance(got["screenshot_base64"], str)
        assert locator in got["screenshot_base64"]
        assert len(got["screenshot_base64"]) < 200, "占位必须远小于原 base64"
        # 原文落在常量目录下（文件名 = locator id）
        saved = _tmp_image_dir / (locator.split(":", 1)[1] + ".png")
        assert saved.read_bytes() == blob

    def test_同一张图重复截图复用同一个locator(self):
        b64 = base64.b64encode(_png_blob()).decode("ascii")
        first = image_offload.offload_result("browser_screenshot",
                                            {"ok": True, "screenshot_base64": b64})
        second = image_offload.offload_result("browser_screenshot",
                                             {"ok": True, "screenshot_base64": b64})
        assert first["image_locator"] == second["image_locator"], "按内容摘要派生，同图同 id"

    def test_非图片的base64不动(self):
        result = {"ok": True, "data_base64": base64.b64encode(b"not an image" * 500).decode()}
        assert image_offload.offload_result("t", result) is None
        assert result["data_base64"] and "image_offloaded" not in result

    def test_坏base64不动(self):
        assert image_offload.offload_result("t", {"ok": True, "x_base64": "!!!not base64!!!" * 500}) is None

    def test_短图不动(self):
        b64 = base64.b64encode(_png_blob(pad=1)).decode("ascii")   # 远小于 4000 字符
        assert image_offload.offload_result("t", {"ok": True, "screenshot_base64": b64}) is None

    def test_开关关掉即回到旧行为(self, monkeypatch):
        b64 = base64.b64encode(_png_blob()).decode("ascii")
        monkeypatch.setenv(image_offload.ENV_IMAGE_OFFLOAD, "0")
        assert image_offload.enabled() is False
        assert image_offload.offload_result("t", {"ok": True, "screenshot_base64": b64}) is None

    def test_非dict结果直接跳过(self):
        assert image_offload.offload_result("t", ["x"]) is None


class TestReadBack:
    def test_按locator回元数据与OCR字段(self):
        blob = _png_blob()
        meta = image_offload.store("browser_screenshot", blob, "png", "image/png", 640, 480)
        got = image_offload.read_image_locator(meta["locator"], ocr=False)
        assert got["ok"] is True and got["kind"] == "image"
        assert got["width"] == 640 and got["height"] == 480
        assert got["bytes"] == len(blob)
        assert "二进制" in got["hint"]
        assert "ocr" not in got

    def test_OCR不可用时如实说不可用(self, monkeypatch):
        blob = _png_blob()
        meta = image_offload.store("browser_screenshot", blob, "png", "image/png", 640, 480)
        monkeypatch.setattr(image_offload, "_ocr_sensor", lambda: None)
        got = image_offload.read_image_locator(meta["locator"])
        assert got["ocr"]["available"] is False
        assert got["ocr"]["note"]

    def test_非法locator被拒(self):
        for bad in ("", "spill:0123456789abcdef", "image:../../etc/passwd",
                    "image:zzzzzzzzzzzzzzzz", "../image:0123456789abcdef"):
            got = image_offload.read_image_locator(bad)
            assert got["ok"] is False, bad
            assert "locator" in got["error"]

    def test_不存在的locator如实报错(self):
        got = image_offload.read_image_locator("image:0123456789abcdef")
        assert got["ok"] is False
        assert "不存在" in got["error"]

    def test_read_spill分派到图片(self):
        blob = _png_blob()
        meta = image_offload.store("browser_screenshot", blob, "png", "image/png", 640, 480)
        got = spill.read_locator(meta["locator"])
        assert got["ok"] is True and got["kind"] == "image"


class TestStatus:
    def test_状态如实计数与开关(self):
        image_offload.store("t", _png_blob(), "png", "image/png", 640, 480)
        st = image_offload.status()
        assert st["enabled"] is True
        assert st["files"] == 1
        assert st["total_bytes"] > 0

    def test_尺寸解析对非PNG回None(self):
        assert image_offload._png_size(b"RIFF....WEBP") == (None, None)

