"""E3-4：语音输入（STT）与思考过程翻译（CoT）—— 两条独立端点，各自开关默认关

| 端点 | 开关 | 语义 |
|---|---|---|
| POST /api/voice/transcribe（multipart audio） | CP_STT_ENABLED | 音频 -> 文本（OpenAI 兼容上游；未配置回 503 stt_unavailable） |
| POST /api/context/translate（JSON {text,target}） | CP_COT_TRANSLATE_ENABLED | 把思考/步骤文本译成 zh/en（内存缓存；不改对话） |

【为什么两条都默认关】STT 会把音频外发到配置的上游；CoT 翻译会额外花 token。
  两者都是"旁路增强"，关掉即完全回落原文（回滚 = 关开关）。
"""
from __future__ import annotations

import logging
import os
import re

from flask import jsonify, request

from agent.context import cot_translate as _cot
from agent.server_auth import require_token
from agent.server_routes.tracing_decorator import trace_route
from agent.voice import stt as _stt

logger = logging.getLogger(__name__)

STT_ENABLED_ENV = _stt.ENABLED_ENV
COT_ENABLED_ENV = _cot.ENABLED_ENV
#: 上传文件名只取 basename + 白名单字符（只进 multipart 的 filename 字段，不落盘）
_NAME_RE = re.compile(r"[^0-9A-Za-z._-]")


def _safe_name(raw: str) -> str:
    base = os.path.basename(str(raw or "").replace("\\", "/"))
    cleaned = _NAME_RE.sub("_", base)[:64].lstrip(".")
    return cleaned or _stt.DEFAULT_FILENAME


def _disabled(code: str, detail: str):
    return jsonify({"ok": False, "code": code, "error": detail})


def _error(code: str, detail: str, status: int):
    return jsonify({"ok": False, "code": code, "error": detail}), status


def register_routes(app, state=None):
    @app.route("/api/voice/transcribe", methods=["POST"])
    @trace_route("VoiceTranscribe")
    @require_token
    def api_voice_transcribe():
        """音频 -> 文本（默认关；音频只在内存里，不落盘）"""
        if not _stt.enabled():
            return _disabled("disabled", "语音输入未启用（CP_STT_ENABLED=0）")
        upload = request.files.get("audio")
        if upload is None:
            return _error("missing_audio", "缺少音频（form 字段名 audio）", 400)
        mime = str(upload.mimetype or "").strip()
        data = upload.stream.read(_stt.MAX_AUDIO_BYTES + 1)
        try:
            result = _stt.transcribe(data, mime, filename=_safe_name(upload.filename))
        except _stt.SttError as exc:
            return _error(exc.code, exc.detail, exc.status)
        except Exception:  # noqa: BLE001 上游异常原文不回给客户端
            logger.exception("[voice] 转写失败")
            return _error("voice_failed", "语音转写失败（详见服务日志）", 500)
        return jsonify({"ok": True, **result})

    @app.route("/api/context/translate", methods=["POST"])
    @trace_route("CotTranslate")
    @require_token
    def api_context_translate():
        """思考/步骤文本翻译（默认关；只读二次加工，不改对话）"""
        if not _cot.enabled():
            return _disabled("disabled", "思考过程翻译未启用（CP_COT_TRANSLATE_ENABLED=0）")
        body = request.get_json(silent=True) or {}
        try:
            result = _cot.translate(body.get("text"), body.get("target") or "zh")
        except _cot.TranslateError as exc:
            return _error(exc.code, exc.detail, exc.status)
        except Exception:  # noqa: BLE001
            logger.exception("[cot-translate] 翻译失败")
            return _error("translate_failed", "翻译失败（详见服务日志）", 500)
        return jsonify({"ok": True, **result})


__all__ = ["COT_ENABLED_ENV", "STT_ENABLED_ENV", "register_routes"]
