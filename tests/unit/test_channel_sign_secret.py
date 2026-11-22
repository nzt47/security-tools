# -*- coding: utf-8 -*-
"""Webhook「加签」（sign secret）：字段 + env 键 + 开关登记 + 两种签名（2026-11-22）。

【背景】飞书/钉钉自定义机器人可以开「加签」：关掉"任何人拿到 URL 就能发"的口子。
开启后请求必须带 timestamp + sign，否则服务端回 HTTP 200 + 业务码（飞书 19021 / 钉钉 310000）。
云枢原来没有这个字段 —— 用户只能在飞书后台关掉加签，或者根本发不出去。

本文件钉住四件事（改实现要同批改这里）：
1. **字段**：webhook 通道的解析视图多一个可选的 sign_secret（secret=True、带 env_key）；
2. **env 键**：主通道写 CP_ALERT_WEBHOOK_SIGN_SECRET；留空 = 不改；显式 null = 删键；
3. **登记**：该键进了 agent/settings/registry.py（否则「开关零缺口」门禁红）；
4. **签名**：飞书签在 body、钉钉签在 URL 查询串，且**两家算法方向相反**（key/msg 互换）。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
from pathlib import Path

import pytest

from agent.env_config_manager import reset_env_config_manager
from agent.extensions import channel_env_bridge as bridge
from agent.extensions.channels_installer import ChannelInstaller
from agent.extensions.store import ExtensionStore

REPO_ROOT = Path(__file__).resolve().parents[2]
FEISHU = "https://open.feishu.cn/open-apis/bot/v2/hook/abc"
DINGTALK = "https://oapi.dingtalk.com/robot/send?access_token=tok123"
SECRET = "test-sign-secret"


@pytest.fixture()
def env_file(tmp_path, monkeypatch):
    path = tmp_path / "isolated.env"
    path.write_text("# 顶部注释\nEXISTING_KEY=keep-me\n", encoding="utf-8")
    monkeypatch.setenv("CP_ENV_FILE", str(path))
    reset_env_config_manager()
    yield path
    reset_env_config_manager()


@pytest.fixture()
def store(tmp_path):
    return ExtensionStore(data_file=str(tmp_path / "extensions.json"))


@pytest.fixture()
def installer(store):
    return ChannelInstaller(store)


def _env_value(path: Path, key: str) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    return ""


def _row(store):
    from agent.extensions.base import ExtensionType
    return store.get(ExtensionType.CHANNEL, "webhook")


# ════════════════════════════════════════════════════════════
#  1) 字段与解析视图
# ════════════════════════════════════════════════════════════

def test_解析视图里有可选的加签字段且默认掩码(store, installer):
    installer.install_builtin_channel("webhook")
    view = bridge.public_view(_row(store))
    field = next(f for f in view["fields"] if f["field"] == "sign_secret")
    assert field["env_key"] == "CP_ALERT_WEBHOOK_SIGN_SECRET"
    assert field["secret"] is True            # 永久掩码，不回显明文
    assert field["configured"] is False       # 可选：没填也算可用
    assert view["empty"] is True              # 空通道判据仍只看 webhook_url


def test_保存后进env且视图只给掩码(store, installer, env_file):
    installer.install_builtin_channel("webhook")
    ok, _msg = installer.configure_channel("webhook", {
        "webhook_url": FEISHU, "sign_secret": SECRET,
    })
    assert ok is True
    assert _env_value(env_file, "CP_ALERT_WEBHOOK_SIGN_SECRET") == SECRET

    view = bridge.public_view(_row(store))
    field = next(f for f in view["fields"] if f["field"] == "sign_secret")
    assert field["local"] == bridge.MASK and field["effective"] == bridge.MASK
    assert field["configured"] is True
    # 发送侧读到的是**真实值**（掩码只给 UI）
    assert bridge.effective_config(_row(store))["sign_secret"] == SECRET


def test_留空等于不改_显式null才清空(store, installer, env_file):
    installer.install_builtin_channel("webhook")
    installer.configure_channel("webhook", {"webhook_url": FEISHU, "sign_secret": SECRET})

    # 留空（表单没动这个字段）⇒ 不改：env 与本地都保留
    installer.configure_channel("webhook", {"sign_secret": ""})
    assert _env_value(env_file, "CP_ALERT_WEBHOOK_SIGN_SECRET") == SECRET
    assert bridge.effective_config(_row(store))["sign_secret"] == SECRET

    # 显式 null ⇒ 清空：删 env 键（不写空串占位）
    installer.configure_channel("webhook", {"sign_secret": None})
    assert _env_value(env_file, "CP_ALERT_WEBHOOK_SIGN_SECRET") == ""
    assert "CP_ALERT_WEBHOOK_SIGN_SECRET=" not in env_file.read_text(encoding="utf-8")
    assert not (bridge.effective_config(_row(store)).get("sign_secret") or "")


def test_非主通道的加签字段也不写env(store, installer, env_file):
    installer.install_channel("webhook_2", "Webhook 2", "webhook",
                              config={"channel_type": "webhook", "webhook_url": ""})
    plan = bridge.sync_env(store.get(__import__("agent.extensions.base", fromlist=["ExtensionType"]).ExtensionType.CHANNEL, "webhook_2"),
                           {"sign_secret": SECRET})
    assert plan["primary"] is False and plan["written"] == []
    assert _env_value(env_file, "CP_ALERT_WEBHOOK_SIGN_SECRET") == ""


# ════════════════════════════════════════════════════════════
#  2) 开关登记（「开关零缺口」判据）
# ════════════════════════════════════════════════════════════

def test_env键已进开关登记表():
    from agent.settings.registry import get_spec  # noqa: WPS433
    spec = get_spec("CP_ALERT_WEBHOOK_SIGN_SECRET")
    assert spec is not None, "新增 env 读取点必须同时登记 agent/settings/registry.py"
    assert spec.secret is True, "加签密钥必须按密钥类登记（值不回显）"


# ════════════════════════════════════════════════════════════
#  3) 签名（飞书 body / 钉钉 URL，方向相反）
# ════════════════════════════════════════════════════════════

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
    box = {"payload": None, "url": None, "resp": _Resp(200, '{"code":0}')}

    def fake_urlopen(req, timeout=None):
        box["url"] = req.full_url
        box["payload"] = json.loads(req.data.decode("utf-8"))
        return box["resp"]

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    return box


def _expected_feishu_sign(secret: str, ts: str) -> str:
    # 飞书官方示例：key = f"{ts}\n{secret}"，msg 为空
    mac = hmac.new(f"{ts}\n{secret}".encode("utf-8"), b"", digestmod=hashlib.sha256).digest()
    return base64.b64encode(mac).decode("utf-8")


def _expected_dingtalk_sign(secret: str, ts: str) -> str:
    # 钉钉官方示例：key = secret，msg = f"{ts}\n{secret}"
    mac = hmac.new(secret.encode("utf-8"), f"{ts}\n{secret}".encode("utf-8"),
                   digestmod=hashlib.sha256).digest()
    return base64.b64encode(mac).decode("utf-8")


def test_飞书加签签在body且算法与官方一致(sent):
    ChannelInstaller._handle_webhook({"webhook_url": FEISHU, "sign_secret": SECRET}, "hi")
    payload = sent["payload"]
    assert payload["msg_type"] == "text" and payload["content"] == {"text": "hi"}
    ts = payload["timestamp"]
    assert ts.isdigit() and len(ts) == 10          # 秒级时间戳（字符串）
    assert payload["sign"] == _expected_feishu_sign(SECRET, ts)


def test_飞书没填密钥就不带签名字段(sent):
    ChannelInstaller._handle_webhook({"webhook_url": FEISHU}, "hi")
    assert "sign" not in sent["payload"] and "timestamp" not in sent["payload"]


def test_钉钉加签签在URL查询串且保留原参数(sent):
    ChannelInstaller._handle_webhook({"webhook_url": DINGTALK, "sign_secret": SECRET}, "hi")
    url = sent["url"]
    assert "access_token=tok123" in url, "原有查询参数不能被签名覆盖"
    assert "sign=" in url and "timestamp=" in url
    assert "sign" not in sent["payload"]            # 钉钉的签不在 body
    from urllib.parse import unquote
    ts = url.split("timestamp=")[1].split("&")[0]
    sign = unquote(url.split("sign=")[1].split("&")[0])   # 查询串里是 URL 编码后的
    assert len(ts) == 13                            # 毫秒
    assert sign == _expected_dingtalk_sign(SECRET, ts)


def test_加签失败的错误文案带下一步(sent):
    from agent.extensions.channels_installer import ChannelRejected
    sent["resp"] = _Resp(200, '{"code":19021,"msg":"sign match fail or timestamp is not within one hour from current time"}')
    with pytest.raises(ChannelRejected) as ei:
        ChannelInstaller._handle_webhook({"webhook_url": FEISHU, "sign_secret": SECRET}, "hi")
    assert "19021" in ei.value.message
    assert "加签密钥" in ei.value.message            # 不是干巴巴的 code
