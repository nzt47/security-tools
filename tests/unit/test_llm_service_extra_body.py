"""守卫：`LLMService.chat` 的 `extra_body` 透传与空 content 兜底。

【为什么需要这个文件】
    2026-11-05 实测：本机模型 `deepseek-flash` 是**推理模型**，同一段提示词的
    reasoning token 在 0–8000+ 之间抖动；抖满就出现 `finish_reason=length` 且
    `message.content` 为 **None/空**。技能卡起草（`transform/rewrite.py`）遇到这种情况
    会静默"零条通过"，看起来像"技能没内容"，实际是模型没产出。

    修法两条，本用例锁死：
      1. `chat(..., extra_body=...)` 必须把额外请求体透传给 OpenAI 兼容端
         （起草用 `{"thinking": {"type": "disabled"}}` 关掉推理，7.7s 出 30 条且 30/30 过校验）；
      2. `content` 为 None 时返回空串，不许抛 `AttributeError`（否则重试 3 次全废、
         调用方只能看到"LLM 调用失败"这种看不出根因的报错）。
"""
from __future__ import annotations

import types

_VALID_KEY = "sk-test-valid-key-12345"  # 满足 MIN_API_KEY_LENGTH=10


class _FakeOpenAI:
    """只实现 `chat.completions.create`：记录入参、回放给定 content。"""

    def __init__(self, recorder: dict, content):
        self.chat = types.SimpleNamespace(
            completions=types.SimpleNamespace(create=self._create))
        self._recorder = recorder
        self._content = content

    def _create(self, **kwargs):
        self._recorder.update(kwargs)
        message = types.SimpleNamespace(content=self._content)
        return types.SimpleNamespace(choices=[types.SimpleNamespace(message=message)])


def _service(recorder: dict, content):
    import memory.llm_service as M

    svc = M.LLMService(provider="deepseek", api_key=_VALID_KEY, model="deepseek-flash")
    svc._client = _FakeOpenAI(recorder, content)
    return svc


def test_chat_透传_extra_body():
    recorder: dict = {}
    svc = _service(recorder, '{"ok": true}')
    out = svc.chat([{"role": "user", "content": "hi"}], system_prompt="s",
                   max_tokens=100, temperature=0.1,
                   extra_body={"thinking": {"type": "disabled"}})
    assert out == '{"ok": true}'
    assert recorder["extra_body"] == {"thinking": {"type": "disabled"}}
    assert recorder["max_tokens"] == 100 and recorder["temperature"] == 0.1


def test_未传_extra_body_时不下发该键():
    recorder: dict = {}
    svc = _service(recorder, "ok")
    svc.chat([{"role": "user", "content": "hi"}], max_tokens=50)
    assert "extra_body" not in recorder, "空值不许变成 extra_body=None 发给上游"


def test_content_为_None_返回空串():
    recorder: dict = {}
    svc = _service(recorder, None)
    assert svc.chat([{"role": "user", "content": "hi"}], max_tokens=50) == ""
