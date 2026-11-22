# -*- coding: utf-8 -*-
"""Webhook 通道投递口径（2026-11-22，飞书/钉钉的「200 也可能是失败」）。

【现场】消息中心配了飞书自定义机器人 webhook，点「发送」界面回「消息已发送」，
机器人一条都没收到。实测两步就复现：
  · 云枢当时发的 {"text": ..., "source": "yunshu"} → HTTP **200** + {"code":19002,
    "msg":"params error, msg_type need"}；
  · 飞书要的 {"msg_type":"text","content":{"text":...}} → HTTP 200 + {"code":0}。
⇒ 两个缺陷：①载荷方言；②成功判据只看 HTTP 状态。

本用例钉住：三种方言的载荷形状、业务码/ok=false 判失败、可读原因能回给界面。
"""
from __future__ import annotations

import json

import pytest

from agent.extensions.channels_installer import (
    ChannelInstaller, ChannelRejected, SEND_ERR_GENERIC,
)

FEISHU = "https://open.feishu.cn/open-apis/bot/v2/hook/abc"
DINGTALK = "https://oapi.dingtalk.com/robot/send?access_token=abc"
SLACK = "https://hooks.slack.com/services/T/B/X"


class _Resp:
    def __init__(self, status=200, body="{}"):
        self.status = status
        self._body = body.encode("utf-8")

    def read(self, n=-1):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture()
def sent(monkeypatch):
    """拦 urlopen：记下请求体，回固定响应。"""
    box = {"payload": None, "url": None, "resp": _Resp()}

    def fake_urlopen(req, timeout=None):
        box["url"] = req.full_url
        box["payload"] = json.loads(req.data.decode("utf-8"))
        return box["resp"]

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return box


def test_飞书载荷形状是_msg_type_加_content_text(sent):
    sent["resp"] = _Resp(200, '{"code":0,"msg":"success"}')
    out = ChannelInstaller._handle_webhook({"webhook_url": FEISHU}, "你好")
    assert sent["payload"] == {"msg_type": "text", "content": {"text": "你好"}}
    # 通用形状里的两个键不能混进去（飞书见 msg_type 缺失就 19002）
    assert "text" not in sent["payload"] and "source" not in sent["payload"]
    assert out == "HTTP 200"


def test_钉钉载荷形状是_msgtype_加_text_content(sent):
    sent["resp"] = _Resp(200, '{"errcode":0,"errmsg":"ok"}')
    ChannelInstaller._handle_webhook({"webhook_url": DINGTALK}, "你好")
    assert sent["payload"] == {"msgtype": "text", "text": {"content": "你好"}}


def test_slack_兼容端点仍发通用形状(sent):
    sent["resp"] = _Resp(200, "ok")
    assert ChannelInstaller._handle_webhook({"webhook_url": SLACK}, "你好") == "HTTP 200"
    assert sent["payload"] == {"text": "你好", "source": "yunshu"}


def test_飞书_200_但业务码非零必须判失败(sent):
    sent["resp"] = _Resp(200, '{"code":19002,"msg":"params error, msg_type need"}')
    with pytest.raises(ChannelRejected) as ei:
        ChannelInstaller._handle_webhook({"webhook_url": FEISHU}, "你好")
    assert ei.value.code == "19002"
    # 原因要能到界面（不是「失败（原因见服务日志）」）
    assert "19002" in ei.value.message and "msg_type" in ei.value.message
    assert ChannelInstaller._send_error_reason(ei.value) == ei.value.message


def test_钉钉_200_但_errcode_非零判失败(sent):
    sent["resp"] = _Resp(200, '{"errcode":310000,"errmsg":"sign not match"}')
    with pytest.raises(ChannelRejected) as ei:
        ChannelInstaller._handle_webhook({"webhook_url": DINGTALK}, "你好")
    assert ei.value.code == "310000"
    assert "sign not match" in ei.value.message


def test_ok_false_也算拒收(sent):
    sent["resp"] = _Resp(200, '{"ok":false,"error":"channel_not_found"}')
    with pytest.raises(ChannelRejected):
        ChannelInstaller._handle_webhook({"webhook_url": SLACK}, "你好")


def test_非_json_响应体不误判(sent):
    """Slack 之类回纯文本 "ok" ⇒ 不能因为解析不出 JSON 就判失败。"""
    sent["resp"] = _Resp(200, "ok")
    assert ChannelInstaller._handle_webhook({"webhook_url": SLACK}, "x") == "HTTP 200"


def test_HTTP_4xx_转成可读原因(sent, monkeypatch):
    import urllib.error

    def boom(req, timeout=None):
        raise urllib.error.HTTPError(req.full_url, 404, "Not Found", {}, None)

    monkeypatch.setattr("urllib.request.urlopen", boom)
    with pytest.raises(ChannelRejected) as ei:
        ChannelInstaller._handle_webhook({"webhook_url": FEISHU}, "x")
    assert "404" in ei.value.message


def test_未知异常仍走固定文案不泄原文():
    """只有 ChannelRejected 回可读原因；其它异常保持「原因见服务日志」。"""
    assert ChannelInstaller._send_error_reason(RuntimeError("secret /abs/path")) == SEND_ERR_GENERIC
