"""E3-4：语音输入（STT）—— OpenAI 兼容 /audio/transcriptions 的最小客户端

【为什么是"可插拔 provider + 明确未配置"】本机没有内置 ASR，也不该把某家云服务的 SDK
  钉进仓库：默认关（CP_STT_ENABLED=0），配置 CP_STT_BASE_URL / CP_STT_MODEL / CP_STT_API_KEY
  且打开开关后才会外发音频。没配置时如实回 stt_unavailable（不是 500、不是空文本）。

【边界】
  · 只接受**音频** MIME（audio/* 的前缀 + 明确白名单），单次 <= 10 MiB；
  · 音频只在内存里组装 multipart，**不落盘**；不打印、不缓存音频；
  · 返回文本截断到 4000 字符；上游错误只回稳定 code（stt_failed / stt_timeout）。
"""
from __future__ import annotations

import json
import logging
import os
import uuid
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, Optional

logger = logging.getLogger(__name__)

#: 开关（默认关；关时路由回 disabled）
ENABLED_ENV = "CP_STT_ENABLED"
#: 上游 OpenAI 兼容端点（如 https://api.openai.com/v1）
BASE_URL_ENV = "CP_STT_BASE_URL"
#: 模型名
MODEL_ENV = "CP_STT_MODEL"
#: 上游密钥（只从 env / .env 读；永不打印）
KEY_ENV = "CP_STT_API_KEY"
#: 识别语言（空 = 交给上游自动判断）
LANGUAGE_ENV = "CP_STT_LANGUAGE"

DEFAULT_MODEL = "whisper-1"
DEFAULT_LANGUAGE = "zh"
#: 单次音频上限（10 MiB）
MAX_AUDIO_BYTES = 10 * 1024 * 1024
#: 上游超时（秒）
TIMEOUT_SEC = 60
#: 识别文本上限（字符）
MAX_TEXT_CHARS = 4000
#: 允许的 MIME（前缀 + 精确表；浏览器 MediaRecorder 常见 webm/ogg/mp4）
ALLOWED_MIME_PREFIX = "audio/"
ALLOWED_MIME_EXACT = frozenset({
    "audio/webm", "audio/webm;codecs=opus", "audio/ogg", "audio/ogg;codecs=opus",
    "audio/wav", "audio/x-wav", "audio/wave", "audio/mpeg", "audio/mp3",
    "audio/mp4", "audio/x-m4a", "audio/aac", "audio/flac",
})
#: 文件名后缀白名单（只用于 multipart 的 filename 字段，不落盘）
DEFAULT_FILENAME = "audio.webm"


class SttError(Exception):
    """STT 错误（code 稳定、detail 可展示）"""

    def __init__(self, code: str, detail: str, status: int = 400):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = int(status)


def _truthy(raw: str) -> bool:
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")


def enabled() -> bool:
    return _truthy(os.environ.get(ENABLED_ENV, ""))


def _base_url() -> str:
    return str(os.environ.get(BASE_URL_ENV, "") or "").strip().rstrip("/")


def _model() -> str:
    return str(os.environ.get(MODEL_ENV, "") or "").strip() or DEFAULT_MODEL


def _language() -> str:
    return str(os.environ.get(LANGUAGE_ENV, "") or "").strip() or DEFAULT_LANGUAGE


def api_key() -> str:
    """上游密钥（只从环境读；调用方不得打印）"""
    return str(os.environ.get(KEY_ENV, "") or "").strip()


def configured() -> bool:
    """开关开 + 端点 + 密钥齐才算可用"""
    return bool(enabled() and _base_url() and api_key())


def allowed_mime(mime: str) -> bool:
    text = str(mime or "").strip().lower()
    if not text:
        return False
    return text in ALLOWED_MIME_EXACT or text.startswith(ALLOWED_MIME_PREFIX)


def _multipart(data: bytes, mime: str, filename: str) -> tuple:
    boundary = "----yunshu" + uuid.uuid4().hex
    lines = []
    for field, value in (("model", _model()), ("language", _language())):
        if not value:
            continue
        lines.append(("--%s\r\n" % boundary).encode())
        lines.append(('Content-Disposition: form-data; name="%s"\r\n\r\n' % field).encode())
        lines.append(value.encode("utf-8") + b"\r\n")
    lines.append(("--%s\r\n" % boundary).encode())
    lines.append(('Content-Disposition: form-data; name="file"; filename="%s"\r\n'
                  % filename).encode())
    lines.append(("Content-Type: %s\r\n\r\n" % mime).encode())
    lines.append(data + b"\r\n")
    lines.append(("--%s--\r\n" % boundary).encode())
    return b"".join(lines), "multipart/form-data; boundary=" + boundary


def _http_post(url: str, headers: Dict[str, str], body: bytes,
               timeout: float) -> tuple:
    """默认传输：返回 (status, body_bytes)。测试可注入同签名的 transport。"""
    req = urllib.request.Request(url, data=body, method="POST")
    for key, value in headers.items():
        req.add_header(key, value)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return int(resp.status), resp.read()
    except urllib.error.HTTPError as exc:
        try:
            return int(exc.code), exc.read()
        except Exception:  # noqa: BLE001
            return int(exc.code), b""


def transcribe(data: bytes, mime: str, filename: str = DEFAULT_FILENAME,
               transport: Optional[Callable[..., tuple]] = None) -> Dict[str, Any]:
    """把一段音频转成文本。

    Raises:
        SttError: disabled / stt_unavailable / empty_audio / too_large / bad_mime /
                  stt_failed / stt_timeout
    """
    if not enabled():
        raise SttError("disabled", "语音输入未启用（CP_STT_ENABLED=0）", 200)
    if not data:
        raise SttError("empty_audio", "音频为空", 400)
    if len(data) > MAX_AUDIO_BYTES:
        raise SttError("too_large", "音频超过 %d MB 上限" % (MAX_AUDIO_BYTES // (1024 * 1024)), 413)
    if not allowed_mime(mime):
        raise SttError("bad_mime", "只接受音频（audio/*）", 415)
    base = _base_url()
    key = api_key()
    if not base or not key:
        raise SttError("stt_unavailable",
                       "语音识别未配置（需要 CP_STT_BASE_URL 与 CP_STT_API_KEY）", 503)
    body, content_type = _multipart(data, mime, filename)
    post = transport or _http_post
    try:
        status, raw = post(base + "/audio/transcriptions",
                           {"Authorization": "Bearer " + key,
                            "Content-Type": content_type}, body, TIMEOUT_SEC)
    except TimeoutError:
        raise SttError("stt_timeout", "语音识别超时", 504) from None
    except OSError:
        raise SttError("stt_failed", "语音识别请求失败", 502) from None
    if int(status) >= 400:
        logger.warning("[stt] 上游失败 status=%s", int(status))
        raise SttError("stt_failed", "语音识别服务返回错误", 502)
    try:
        payload = json.loads((raw or b"").decode("utf-8", "replace"))
    except ValueError:
        raise SttError("stt_failed", "语音识别返回不是 JSON", 502) from None
    text = str((payload or {}).get("text") or "").strip() if isinstance(payload, dict) else ""
    if not text:
        raise SttError("stt_failed", "语音识别没有返回文本", 502)
    return {"text": text[:MAX_TEXT_CHARS], "model": _model(),
            "truncated": len(text) > MAX_TEXT_CHARS}


__all__ = [
    "ALLOWED_MIME_EXACT", "ALLOWED_MIME_PREFIX", "BASE_URL_ENV", "DEFAULT_LANGUAGE",
    "DEFAULT_MODEL", "ENABLED_ENV", "KEY_ENV", "LANGUAGE_ENV", "MAX_AUDIO_BYTES",
    "MAX_TEXT_CHARS", "MODEL_ENV", "SttError", "TIMEOUT_SEC", "allowed_mime",
    "api_key", "configured", "enabled", "transcribe",
]
