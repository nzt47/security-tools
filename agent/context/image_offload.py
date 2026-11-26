"""图片/截图 offload（E0-2③）：把 base64 图片从上下文里挪出去，只留可再读的 locator。

【为什么】`browser_screenshot` 回的是 base64 PNG，上限 500000 字符 —— 一次截图就能吃掉
十几万 token，比整个上下文窗口还大；而且**内容没坏，只是不该进上下文**。DSH 的
`compaction-image-offload` 做的就是这件事：图片落盘，结果里只留占位 + locator。

【与 spill（E0-2①）的分工】spill 面对「大文本」，把全文落盘、回预览，模型可以分段**读回原文**；
图片是二进制，原文读回对纯文本模型没有意义 —— 所以这里的「可再读」是：
  · `read_spill(locator="image:<16hex>")` 回**元数据 + 尽力 OCR 出的文字**（OCR 不可用就如实说）；
  · 落盘文件本身留在 `agent/data/images/`（运行期、不提交），需要原图时由人/其它工具取用。

【边界】
- 只处理 dict 结果里**键名像图片**（`*_base64` / `base64` / `*_image_b64`）的字符串字段，值必须是
  合法 base64 且带 PNG/JPEG/GIF/WebP 魔数 —— 否则原样不动（宁可不过滤，也不误删正文）；
- 路径由**常量目录 + 校验过的 locator** 决定（不收任意路径），见 `_LOCATOR_RE`；
- 开关 `CP_IMAGE_OFFLOAD`（默认开）关掉 = 回到把 base64 塞进上下文的旧行为；
- 任何异常都只记 debug 并**原样返回结果**：内容保全机制不能反过来让工具调用失败。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

ENV_IMAGE_OFFLOAD = "CP_IMAGE_OFFLOAD"
ENV_IMAGE_MIN_CHARS = "CP_IMAGE_OFFLOAD_MIN_CHARS"

#: locator 形状：`image:<16 hex>`（id 由内容摘要派生，不接受任意路径）
_LOCATOR_RE = re.compile(r"^image:([0-9a-f]{16})$")

_MIN_CHARS_DEFAULT = 4_000
_MIN_CHARS_MIN = 512
_MIN_CHARS_MAX = 4_000_000

#: 键名像图片的字段（只看这三种形状；其它一律不动）
_IMAGE_KEY_HINTS = ("_base64", "_image_b64", "_image_base64")

#: 魔数 ⇒ (扩展名, MIME)。PNG/JPEG/GIF/WebP 四种够覆盖截图与常见图。
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
)


def image_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "images"


def enabled() -> bool:
    raw = str(os.environ.get(ENV_IMAGE_OFFLOAD, "1") or "").strip().lower()
    return raw not in ("0", "false", "no", "off")


def min_chars() -> int:
    """多长的 base64 才值得落盘（太小的图留在结果里更省事）"""
    raw = str(os.environ.get(ENV_IMAGE_MIN_CHARS) or "").strip()
    if not raw:
        return _MIN_CHARS_DEFAULT
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        logger.warning("[image-offload] %s=%r 不是整数，回落到 %s",
                       ENV_IMAGE_MIN_CHARS, raw, _MIN_CHARS_DEFAULT)
        return _MIN_CHARS_DEFAULT
    return max(_MIN_CHARS_MIN, min(_MIN_CHARS_MAX, value))


def _is_image_key(key: Any) -> bool:
    name = str(key or "").strip().lower()
    if not name:
        return False
    return name == "base64" or any(name.endswith(h) for h in _IMAGE_KEY_HINTS)


def _decode(raw: str):
    """base64 → (bytes, 扩展名, MIME)；不是图片就返回 None。"""
    text = str(raw or "").strip()
    if not text:
        return None
    if text.startswith("data:") and "," in text:      # data URL 形态
        text = text.split(",", 1)[1]
    try:
        blob = base64.b64decode(text, validate=False)
    except (binascii.Error, ValueError):
        return None
    if len(blob) < 16:
        return None
    for magic, ext, mime in _MAGIC:
        if blob.startswith(magic):
            return blob, ext, mime
    if blob[:4] == b"RIFF" and blob[8:12] == b"WEBP":
        return blob, "webp", "image/webp"
    return None


def _png_size(blob: bytes):
    """PNG IHDR 里读宽高（纯 stdlib，不引 Pillow）；非 PNG 或长度不足回 (None, None)"""
    try:
        if not blob.startswith(b"\x89PNG\r\n\x1a\n") or len(blob) < 24:
            return None, None
        if blob[12:16] != b"IHDR":
            return None, None
        width = int.from_bytes(blob[16:20], "big")
        height = int.from_bytes(blob[20:24], "big")
        return width or None, height or None
    except Exception:  # noqa: BLE001 尺寸读不出来不影响落盘
        return None, None


def store(tool: str, blob: bytes, ext: str, mime: str,
          width: Optional[int] = None, height: Optional[int] = None) -> Dict[str, Any]:
    """把图片字节落盘并返回信封（locator 由内容摘要派生，同图同 id）"""
    directory = image_dir()
    directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(blob).hexdigest()
    image_id = digest[:16]
    path = directory / f"{image_id}.{ext}"
    if not path.exists():
        path.write_bytes(blob)
    meta = {
        "locator": f"image:{image_id}",
        "tool": str(tool),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "mime": mime,
        "ext": ext,
        "bytes": len(blob),
        "sha256_16": digest[:16],
        "width": width,
        "height": height,
        "path": str(path),
    }
    try:
        (directory / f"{image_id}.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass
    return meta


def offload_result(tool: str, result: Any) -> Optional[Dict[str, Any]]:
    """结果里有大 base64 图片时返回**改写过的新结果**；没有就返回 None。

    改法：把该字段的值换成一行占位（保留字段名与结果形状，下游 dict 契约不变），
    并在顶层补 `image_offloaded` / `image_locator` / `image_bytes` / `image_mime` /
    `image_size`，让模型一眼看出「图在磁盘上、怎么再读」。
    """
    if not enabled() or not isinstance(result, dict):
        return None
    for key in list(result.keys()):
        if not _is_image_key(key):
            continue
        value = result.get(key)
        if not isinstance(value, str) or len(value) < min_chars():
            continue
        decoded = _decode(value)
        if decoded is None:
            continue
        blob, ext, mime = decoded
        width, height = _png_size(blob)
        meta = store(tool, blob, ext, mime, width, height)
        size_text = (f"{width}x{height}" if width and height else "尺寸未知")
        result[key] = (f"<图片已 offload：{meta['locator']}，{meta['bytes']} 字节，"
                       f"{mime}，{size_text}；用 read_spill(locator='{meta['locator']}') "
                       f"取元数据与 OCR 文字>")
        result["image_offloaded"] = True
        result["image_locator"] = meta["locator"]
        result["image_bytes"] = meta["bytes"]
        result["image_mime"] = mime
        result["image_size"] = {"width": width, "height": height}
        return result
    return None


def read_image_locator(locator: Any, *, ocr: bool = True) -> Dict[str, Any]:
    """按 `image:<16hex>` 取回**元数据 + 尽力 OCR 文字**（二进制原文不进上下文）"""
    match = _LOCATOR_RE.match(str(locator or "").strip())
    if not match:
        return {"ok": False, "error": "locator 不合法（期望形如 image:<16 位十六进制>）"}
    image_id = match.group(1)
    directory = image_dir()
    meta_path = directory / f"{image_id}.json"
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"ok": False, "locator": f"image:{image_id}",
                "error": "locator 对应的图片不存在（可能已被清理）"}
    except (OSError, ValueError):
        return {"ok": False, "locator": f"image:{image_id}",
                "error": "读取图片元数据失败（详见服务日志）"}
    payload: Dict[str, Any] = {
        "ok": True,
        "locator": f"image:{image_id}",
        "kind": "image",
        "mime": meta.get("mime"),
        "bytes": meta.get("bytes"),
        "width": meta.get("width"),
        "height": meta.get("height"),
        "tool": meta.get("tool"),
        "created_at": meta.get("created_at"),
        "hint": "图片是二进制：这里只回元数据与 OCR 文字；原图留在服务端 data/images/",
    }
    if ocr:
        payload["ocr"] = ocr_text(meta.get("path"), meta.get("ext"))
    return payload


_OCR_CACHE: Dict[str, Any] = {}


def _ocr_sensor():
    """惰性构造 OCR 传感器（一次；构造失败缓存 False，避免每次重试）"""
    if "sensor" in _OCR_CACHE:
        return _OCR_CACHE["sensor"]
    sensor = None
    try:
        from sensor.ocr_sensor import OcrSensor
        candidate = OcrSensor()
        if candidate.is_available():
            sensor = candidate
    except Exception as e:  # noqa: BLE001 缺依赖/没装 OCR ⇒ 当不可用
        logger.debug("[image-offload] OCR 传感器不可用: %s", e)
    _OCR_CACHE["sensor"] = sensor
    return sensor


def ocr_text(path: Any, ext: Any = None) -> Dict[str, Any]:
    """对落盘图片尽力做一次 OCR；不可用/失败都**如实**返回 available=False。"""
    target = str(path or "")
    if not target or not os.path.isfile(target):
        return {"available": False, "note": "原图不在磁盘上（可能已被清理）"}
    sensor = _ocr_sensor()
    if sensor is None:
        return {"available": False, "note": "本机 OCR 传感器不可用（未启用或缺依赖）"}
    try:
        from PIL import Image
        import numpy as np
        with Image.open(target) as img:
            arr = np.array(img.convert("RGB"))
        text = str(sensor.recognize_text(arr) or "")[:5000]
        return {"available": True, "chars": len(text), "text": text}
    except Exception as e:  # noqa: BLE001
        logger.debug("[image-offload] OCR 失败: %s", e)
        return {"available": False, "note": "OCR 执行失败（详见服务日志）"}


def status() -> Dict[str, Any]:
    directory = image_dir()
    files = 0
    total_bytes = 0
    if directory.is_dir():
        for path in directory.iterdir():
            if path.suffix == ".json":
                continue
            files += 1
            try:
                total_bytes += path.stat().st_size
            except OSError:
                pass
    return {
        "enabled": enabled(),
        "min_chars": min_chars(),
        "dir": str(directory),
        "files": files,
        "total_bytes": total_bytes,
        "ocr_available": _ocr_sensor() is not None,
    }


__all__ = ["ENV_IMAGE_OFFLOAD", "ENV_IMAGE_MIN_CHARS", "image_dir", "enabled", "min_chars",
           "store", "offload_result", "read_image_locator", "ocr_text", "status"]
