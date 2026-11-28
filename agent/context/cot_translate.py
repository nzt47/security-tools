"""E3-4：CoT 翻译 —— 把模型的思考/步骤文本按需译成中文或英文

【为什么单开一个端点而不是在对话里翻译】思考过程是"旁路展示"：翻译它是**只读的二次加工**，
  不该改变对话本身（不改 messages、不进上下文）。所以它是一个纯函数 + 一个独立端点，
  开关默认关（CP_COT_TRANSLATE_ENABLED=0），关掉即完全回落原文。

【边界】
  · 只翻译**给定文本**（<=2000 字符），不做总结/改写；
  · 需要正文 ⇒ 关 thinking（口径 §6：deepseek-flash 的 reasoning 会吃掉全部预算）；
  · 内存 LRU 缓存 64 条（键 = sha256(target|text)），不落盘、不进日志（文本可能含用户内容）；
  · 模型不可用 ⇒ llm_unavailable（503），不是 500、不是空串。
"""
from __future__ import annotations

import hashlib
import logging
import os
import threading
from collections import OrderedDict
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

#: 开关（默认关）
ENABLED_ENV = "CP_COT_TRANSLATE_ENABLED"
#: 输入/输出上限（字符）
MAX_INPUT_CHARS = 2000
MAX_OUTPUT_CHARS = 4000
#: 支持的目标语言（中文/英文）
TARGETS = ("zh", "en")
_LANG_NAMES = {"zh": "中文", "en": "英文"}
#: 缓存条数
CACHE_SIZE = 64
#: 模型超时（秒）与输出预算
LLM_TIMEOUT_SEC = 45
MAX_OUTPUT_TOKENS = 1200

SYSTEM_PROMPT = (
    "你是翻译器。把用户提供的文本翻译成{tlang}。只输出译文："
    "不要解释、不要保留原文、不要加引号或「译文：」这类标签。"
)

_CACHE: "OrderedDict[str, str]" = OrderedDict()
_CACHE_LOCK = threading.Lock()


class TranslateError(Exception):
    """翻译错误（code 稳定、detail 可展示）"""

    def __init__(self, code: str, detail: str, status: int = 400):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.status = int(status)


def _truthy(raw: str) -> bool:
    return str(raw or "").strip().lower() in ("1", "true", "yes", "on")


def enabled() -> bool:
    return _truthy(os.environ.get(ENABLED_ENV, ""))


def cache_key(text: str, target: str) -> str:
    raw = "%s\x1f%s" % (target, text)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:32]


def _cache_get(key: str) -> Optional[str]:
    with _CACHE_LOCK:
        value = _CACHE.get(key)
        if value is not None:
            _CACHE.move_to_end(key)
        return value


def _cache_put(key: str, value: str) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = value
        _CACHE.move_to_end(key)
        while len(_CACHE) > CACHE_SIZE:
            _CACHE.popitem(last=False)


def reset_cache_for_tests() -> None:
    with _CACHE_LOCK:
        _CACHE.clear()


def _strip_labels(raw: str) -> str:
    text = str(raw or "").strip()
    for prefix in ("译文：", "译文:", "翻译：", "翻译:", "Translation:", "translation:"):
        if text.startswith(prefix):
            text = text[len(prefix):].strip()
    return text.strip().strip('"').strip("'").strip("“”‘’").strip()


def translate(text: Any, target: str = "zh", llm: Any = None) -> Dict[str, Any]:
    """把 text 译成 target（zh|en）；返回 {text, target, cached}

    Raises:
        TranslateError: disabled / bad_target / bad_text / llm_unavailable / translate_failed
    """
    if not enabled():
        raise TranslateError("disabled", "思考过程翻译未启用（CP_COT_TRANSLATE_ENABLED=0）", 200)
    lang = str(target or "zh").strip().lower()
    if lang not in TARGETS:
        raise TranslateError("bad_target", "target 只支持 zh / en", 400)
    source = str(text or "").strip()
    if not source:
        raise TranslateError("bad_text", "没有可翻译的文本", 400)
    source = source[:MAX_INPUT_CHARS]
    key = cache_key(source, lang)
    cached = _cache_get(key)
    if cached is not None:
        return {"text": cached, "target": lang, "cached": True}
    engine = llm
    if engine is None:
        try:
            from agent.context import session_title as _title

            engine = _title.build_llm(timeout=LLM_TIMEOUT_SEC)
        except Exception as exc:  # noqa: BLE001 构建失败按不可用处理
            logger.warning("[cot-translate] LLM 构建失败: %s", type(exc).__name__)
            engine = None
    if engine is None:
        raise TranslateError("llm_unavailable",
                             "翻译不可用（没有可用的 LLM 配置）", 503)
    try:
        reply = engine.chat(
            [{"role": "user", "content": source}],
            system_prompt=SYSTEM_PROMPT.format(tlang=_LANG_NAMES[lang]),
            max_tokens=MAX_OUTPUT_TOKENS,
            temperature=0.1,
            extra_body={"thinking": {"type": "disabled"}},
        )
    except Exception as exc:  # noqa: BLE001 上游失败只回稳定码
        logger.warning("[cot-translate] 上游失败: %s", type(exc).__name__)
        raise TranslateError("translate_failed", "翻译服务返回错误", 502) from None
    output = _strip_labels(str(reply or ""))
    if not output:
        raise TranslateError("translate_failed", "翻译没有返回文本", 502)
    output = output[:MAX_OUTPUT_CHARS]
    _cache_put(key, output)
    return {"text": output, "target": lang, "cached": False}


__all__ = [
    "CACHE_SIZE", "ENABLED_ENV", "LLM_TIMEOUT_SEC", "MAX_INPUT_CHARS", "MAX_OUTPUT_CHARS",
    "SYSTEM_PROMPT", "TARGETS", "TranslateError", "cache_key", "enabled",
    "reset_cache_for_tests", "translate",
]
