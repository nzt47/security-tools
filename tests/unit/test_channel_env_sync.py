# -*- coding: utf-8 -*-
"""报警通道 ↔ .env 同步（2026-11-14，alarm-channel-env-sync）

Owner 原文：「在 UI 配置报警通道同时也写进 env，重新配置则修改 env，配置为空时回落到 env。
设置可以增加有多通道配置，空的频道放进范例和说明，方便以后添加。」

本文件钉住四类判据（改实现要同批改这里）：
1. **写**：非空 ⇒ 原地替换（不是追加第二份）、注释/行序不变、600 与写前备份、
   热重载 os.environ、审计只出现键名；
2. **清**：空 ⇒ **从 .env 删除该键**（不写 KEY= 空串占位），读取回落到
   旧键 / 默认值；口令留空 = 不改，清空必须显式 null；
3. **多通道**：主通道（email_smtp / webhook）映射 CP_ALERT_*；
   同类型第 2 条起只本地登记（不抢同一组键）；
4. **隔离**：测试进程直接指向仓库根 .env ⇒ 拒绝写（fail-closed）；
   配置写入失败时 .env 回滚（不留下"本地没存上、env 却改了"的分叉）。

【事故参照】2026-08-15：测试把真实 .env 写掉 1801 行。本轮实现里连**冒烟脚本**
都踩过同一个坑（裸 python 调 configure_channel 写了仓库 .env，靠 .env.backups 还原）
⇒ 这也是 apply_many 加"测试进程拒写仓库 .env"那道闸的直接原因。
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from agent.env_config_manager import (
    EnvConfigManager,
    EnvWriteRefused,
    get_env_config_manager,
    reset_env_config_manager,
)
from agent.extensions import channel_env_bridge as bridge
from agent.extensions.base import ExtensionStatus, ExtensionType
from agent.extensions.channels_installer import ChannelInstaller
from agent.extensions.store import ExtensionStore

REPO_ROOT = Path(__file__).resolve().parents[2]


# ════════════════════════════════════════════════════════════
#  夹具：临时 .env + 临时扩展台账
# ════════════════════════════════════════════════════════════

@pytest.fixture()
def env_file(tmp_path, monkeypatch):
    """把 EnvConfigManager 的目标 .env 钉到本用例的 tmp（真实文件 I/O）"""
    path = tmp_path / "isolated.env"
    path.write_text(
        "# 顶部注释：这一行必须原样保留\n"
        "EXISTING_KEY=keep-me\n"
        "\n"
        "# 报警通道段落\n"
        "CP_ALERT_SMTP_HOST=old.example.com\n"
        "SMTP_HOST=legacy.example.com\n",
        encoding="utf-8",
    )
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


def _row(store, channel_id="email_smtp"):
    return store.get(ExtensionType.CHANNEL, channel_id)


def _lines(path: Path):
    return path.read_text(encoding="utf-8").splitlines()


def _value_of(path: Path, key: str):
    found = None
    for line in _lines(path):
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            continue
        name, value = stripped.split("=", 1)
        if name.strip() == key:
            found = value.strip()
    return found


# ════════════════════════════════════════════════════════════
#  1) 映射表与范例（空通道的"照抄就能用"）
# ════════════════════════════════════════════════════════════

def test_映射表每种类型都有专用键且不重复():
    keys = []
    for ctype, pairs in bridge.ENV_KEYS_BY_TYPE.items():
        assert pairs, ctype
        for field, env_key in pairs:
            assert env_key.startswith("CP_ALERT_"), env_key
            assert field and field not in bridge.LOCAL_ONLY_FIELDS
            keys.append(env_key)
    assert len(keys) == len(set(keys)), "同一键被两个字段映射 ⇒ 会互相覆盖"
    assert set(bridge.ENV_KEYS_BY_TYPE) == set(bridge.PRIMARY_CHANNEL_BY_TYPE)


def test_空通道有范例与说明且字段可照抄():
    for ctype, pairs in bridge.ENV_KEYS_BY_TYPE.items():
        example = bridge.EXAMPLE_TEMPLATES[ctype]
        notes = bridge.EXAMPLE_NOTES[ctype]
        assert notes and all(str(n).strip() for n in notes)
        assert example.get("channel_type") == ctype
        for field, _env_key in pairs:
            assert field in example, f"{ctype} 范例缺字段 {field}"


def test_公开视图给出每个字段的来源与掩码(store, installer):
    installer.install_builtin_channel("email_smtp")
    installer.configure_channel("email_smtp", {
        "smtp_server": "smtp.example.com", "smtp_password": "pw-9x",
        "default_to": "owner@example.com",
    })
    view = bridge.public_view(_row(store))
    assert view["primary"] is True
    assert view["channel_type"] == "smtp"
    by_field = {f["field"]: f for f in view["fields"]}
    assert by_field["smtp_server"]["source"] == bridge.SOURCE_CHANNEL
    assert by_field["smtp_server"]["effective"] == "smtp.example.com"
    # 口令永久掩码，且不出现原文
    assert by_field["smtp_password"]["effective"] == bridge.MASK
    assert "pw-9x" not in json.dumps(view, ensure_ascii=False)


# ════════════════════════════════════════════════════════════
#  2) apply_many：原地替换 / 删键 / 备份 / 热重载 / 审计
# ════════════════════════════════════════════════════════════

def test_批量写是原地替换不是追加第二份(env_file):
    mgr = get_env_config_manager()
    result = mgr.apply_many(upsert={"CP_ALERT_SMTP_HOST": "new.example.com"})
    assert result["written"] == ["CP_ALERT_SMTP_HOST"]
    lines = _lines(env_file)
    assert sum(1 for l in lines if l.startswith("CP_ALERT_SMTP_HOST=")) == 1
    assert _value_of(env_file, "CP_ALERT_SMTP_HOST") == "new.example.com"
    # 注释与行序不变（新值出现在原来的位置，不是末尾）
    assert lines[0] == "# 顶部注释：这一行必须原样保留"
    assert lines.index("CP_ALERT_SMTP_HOST=new.example.com") == 4
    assert _value_of(env_file, "SMTP_HOST") == "legacy.example.com"


def test_批量写新增键追加到末尾且一次落盘(env_file):
    mgr = get_env_config_manager()
    result = mgr.apply_many(upsert={"CP_ALERT_SMTP_PORT": "465",
                                    "CP_ALERT_SMTP_TO": "a@x.com,b@x.com"})
    assert set(result["written"]) == {"CP_ALERT_SMTP_PORT", "CP_ALERT_SMTP_TO"}
    assert result["wrote_file"] is True
    assert _value_of(env_file, "CP_ALERT_SMTP_PORT") == "465"
    assert _value_of(env_file, "CP_ALERT_SMTP_TO") == "a@x.com,b@x.com"


def test_空值等于删除该键而不是写空串(env_file):
    mgr = get_env_config_manager()
    result = mgr.apply_many(upsert={"CP_ALERT_SMTP_HOST": ""})
    assert result["removed"] == [] and result["written"] == []
    result = mgr.apply_many(delete=["CP_ALERT_SMTP_HOST"])
    assert result["removed"] == ["CP_ALERT_SMTP_HOST"]
    text = env_file.read_text(encoding="utf-8")
    assert "CP_ALERT_SMTP_HOST" not in text
    assert "CP_ALERT_SMTP_HOST=" not in text
    assert "CP_ALERT_SMTP_HOST" not in os.environ


def test_重复行收敛成一行(env_file):
    env_file.write_text("CP_ALERT_SMTP_TO=a@x\nCP_ALERT_SMTP_TO=b@x\n", encoding="utf-8")
    get_env_config_manager().apply_many(upsert={"CP_ALERT_SMTP_TO": "c@x"})
    lines = [l for l in _lines(env_file) if l.startswith("CP_ALERT_SMTP_TO=")]
    assert lines == ["CP_ALERT_SMTP_TO=c@x"]


def test_值没变就不重写也不产生新备份(env_file):
    backups = env_file.parent / ".env.backups"
    before = len(list(backups.glob("env.bak.*"))) if backups.exists() else 0
    result = get_env_config_manager().apply_many(
        upsert={"CP_ALERT_SMTP_HOST": "old.example.com"})
    assert result["unchanged"] == ["CP_ALERT_SMTP_HOST"]
    assert result["wrote_file"] is False
    after = len(list(backups.glob("env.bak.*"))) if backups.exists() else 0
    assert after == before


def test_写前自动备份且备份里是旧内容(env_file):
    mgr = get_env_config_manager()
    mgr.apply_many(upsert={"CP_ALERT_SMTP_HOST": "brand-new.example.com"})
    backups = sorted((env_file.parent / ".env.backups").glob("env.bak.*"))
    assert backups, "写前必须留可回滚快照"
    assert _value_of(backups[-1], "CP_ALERT_SMTP_HOST") == "old.example.com"
    assert _value_of(env_file, "CP_ALERT_SMTP_HOST") == "brand-new.example.com"


def test_热重载写盘后进程环境立刻生效(env_file):
    mgr = get_env_config_manager()
    mgr.apply_many(upsert={"CP_ALERT_SMTP_TO": "x@y.com"})
    assert os.environ["CP_ALERT_SMTP_TO"] == "x@y.com"
    mgr.apply_many(delete=["CP_ALERT_SMTP_TO"])
    assert "CP_ALERT_SMTP_TO" not in os.environ


def test_重启后仍生效_靠启动时的reload读回(env_file):
    """验收①的"重启后仍生效"：.env 里有值 ⇒ 新进程 app_server 启动时 reload() 能读回"""
    mgr = get_env_config_manager()
    mgr.apply_many(upsert={"CP_ALERT_SMTP_TO": "restart@example.com"})
    assert os.environ["CP_ALERT_SMTP_TO"] == "restart@example.com"
    os.environ.pop("CP_ALERT_SMTP_TO", None)      # 模拟进程重启（内存里的值没了）
    mgr.reload()                                   # app_server.py 启动时就调这一个
    assert os.environ["CP_ALERT_SMTP_TO"] == "restart@example.com"


def test_审计只记键名不记值(env_file, monkeypatch):
    entries = []
    monkeypatch.setattr(EnvConfigManager, "_write_audit_entry",
                        lambda self, **kw: entries.append(kw))
    get_env_config_manager().apply_many(
        upsert={"CP_ALERT_SMTP_PASSWORD": "pw-7y"},
        delete=["SMTP_HOST"])
    assert len(entries) == 2
    by_key = {e["key"]: e for e in entries}
    secret_entry = by_key["CP_ALERT_SMTP_PASSWORD"]
    assert secret_entry["new_value"] == EnvConfigManager.REDACTED_VALUE
    assert secret_entry["old_value"] == EnvConfigManager.REDACTED_VALUE
    assert "pw-7y" not in json.dumps(entries, ensure_ascii=False)
    assert by_key["SMTP_HOST"]["new_value"] is None


# ════════════════════════════════════════════════════════════
#  3) 守卫：测试进程不得写仓库根 .env
# ════════════════════════════════════════════════════════════

def test_测试进程指向仓库env时拒写且文件不变():
    repo_env = REPO_ROOT / ".env"
    if not repo_env.exists():                       # pragma: no cover 干净 checkout
        pytest.skip("仓库根没有 .env（CI 上新检出即如此）")
    before = hashlib.sha256(repo_env.read_bytes()).hexdigest()
    mgr = EnvConfigManager(env_file_path=repo_env)
    with pytest.raises(EnvWriteRefused):
        mgr.apply_many(upsert={"CP_ALERT_SMTP_HOST": "polluted.example.com"})
    assert hashlib.sha256(repo_env.read_bytes()).hexdigest() == before


# ════════════════════════════════════════════════════════════
#  4) 回落链：本地 > CP_ALERT_* > 旧键 > 默认
# ════════════════════════════════════════════════════════════

def test_回落链逐层生效(store, installer, monkeypatch):
    installer.install_builtin_channel("email_smtp")
    # 旧键（运维手配，属于审计日报那一族）有值 ⇒ 读它
    monkeypatch.setenv("SMTP_HOST", "legacy.example.com")
    row = _row(store)
    layers = bridge._resolve_layers(row)
    assert layers["smtp_server"] == ("legacy.example.com", bridge.SOURCE_LEGACY_ENV)
    # 专用键有值 ⇒ env 优先于旧键
    monkeypatch.setenv("CP_ALERT_SMTP_HOST", "alert.example.com")
    layers = bridge._resolve_layers(row)
    assert layers["smtp_server"] == ("alert.example.com", bridge.SOURCE_ENV)
    # 本地有值 ⇒ 最高优先
    installer.configure_channel("email_smtp", {"smtp_server": "local.example.com"})
    layers = bridge._resolve_layers(_row(store))
    assert layers["smtp_server"] == ("local.example.com", bridge.SOURCE_CHANNEL)
    assert bridge.effective_config(_row(store))["smtp_server"] == "local.example.com"


def test_发送用的是有效配置而不是本地空值(store, installer, monkeypatch):
    installer.install_builtin_channel("email_smtp")
    installer.toggle_channel("email_smtp", True)
    monkeypatch.setenv("CP_ALERT_SMTP_HOST", "env.example.com")
    seen = {}

    def handler(config, message, **kwargs):
        seen.update(config)
        return "ok"

    installer.register_handler("email_smtp", handler)
    ok, _msg = installer.send_message("email_smtp", "hi")
    assert ok is True
    assert seen.get("smtp_server") == "env.example.com"


def test_非主通道不借用主通道的env(store, installer, monkeypatch):
    installer.install_channel("email_smtp_2", "邮件 2", "smtp",
                              config={"channel_type": "smtp", "smtp_server": ""})
    monkeypatch.setenv("CP_ALERT_SMTP_HOST", "env.example.com")
    row = store.get(ExtensionType.CHANNEL, "email_smtp_2")
    assert bridge.is_primary_channel(row) is False
    assert bridge.effective_config(row).get("smtp_server") in (None, "")
    plan = bridge.sync_env(row, {"smtp_server": "local2.example.com"})
    assert plan["primary"] is False and plan["written"] == []


# ════════════════════════════════════════════════════════════
#  5) 保存链路：写 .env → 写本地 → 失败回滚
# ════════════════════════════════════════════════════════════

def test_保存通道同时写env且再保存只改不追加(store, installer, env_file):
    installer.install_builtin_channel("email_smtp")
    ok, msg = installer.configure_channel("email_smtp", {
        "smtp_server": "smtp.example.com", "smtp_port": 465,
        "smtp_username": "alert@example.com", "from_address": "alert@example.com",
        "default_to": "owner@example.com",
    })
    assert ok is True and ".env" in msg
    assert _value_of(env_file, "CP_ALERT_SMTP_HOST") == "smtp.example.com"
    assert _value_of(env_file, "CP_ALERT_SMTP_PORT") == "465"
    installer.configure_channel("email_smtp", {"smtp_server": "smtp2.example.com"})
    assert _value_of(env_file, "CP_ALERT_SMTP_HOST") == "smtp2.example.com"
    assert sum(1 for l in _lines(env_file) if l.startswith("CP_ALERT_SMTP_HOST=")) == 1
    # 本地存储同样只有一个真相
    assert _row(store)["config"]["smtp_server"] == "smtp2.example.com"


def test_清空字段即从env删键并回落到旧键(store, installer, env_file, monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    installer.install_builtin_channel("email_smtp")
    installer.configure_channel("email_smtp", {"smtp_server": "smtp.example.com"})
    assert _value_of(env_file, "CP_ALERT_SMTP_HOST") == "smtp.example.com"
    # 旧键此时为空 ⇒ 清空后彻底没有主机
    monkeypatch.delenv("SMTP_HOST", raising=False)
    ok, _msg = installer.configure_channel("email_smtp", {"smtp_server": ""})
    assert ok is True
    assert "CP_ALERT_SMTP_HOST" not in env_file.read_text(encoding="utf-8")
    assert _row(store)["config"]["smtp_server"] == ""
    # 再配旧键（模拟运维手配）⇒ 读取回落到它
    monkeypatch.setenv("SMTP_HOST", "legacy.example.com")
    row = _row(store)
    assert bridge.effective_config(row)["smtp_server"] == "legacy.example.com"


def test_口令留空不改_显式null才清(store, installer, env_file):
    installer.install_builtin_channel("email_smtp")
    installer.configure_channel("email_smtp", {"smtp_password": "pw-9x"})
    assert _value_of(env_file, "CP_ALERT_SMTP_PASSWORD") == "pw-9x"
    # 界面上"没填" ⇒ 保留（否则一次误保存就把授权码删了，下次报警发不出去）
    installer.configure_channel("email_smtp", {"smtp_password": ""})
    assert _value_of(env_file, "CP_ALERT_SMTP_PASSWORD") == "pw-9x"
    installer.configure_channel("email_smtp", {"smtp_password": bridge.MASK})
    assert _value_of(env_file, "CP_ALERT_SMTP_PASSWORD") == "pw-9x"
    # 显式清空（null）：env 删键 + 本地记成空串（不残留 None，界面显示"未配置"）
    installer.configure_channel("email_smtp", {"smtp_password": None})
    assert "CP_ALERT_SMTP_PASSWORD" not in env_file.read_text(encoding="utf-8")
    assert (_row(store)["config"] or {}).get("smtp_password") == ""


def test_本地写入失败时env回滚(store, installer, env_file, monkeypatch):
    installer.install_builtin_channel("email_smtp")
    installer.configure_channel("email_smtp", {"smtp_server": "old.example.com"})

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(store, "update_config", boom)
    ok, msg = installer.configure_channel("email_smtp", {"smtp_server": "new.example.com"})
    assert ok is False and "失败" in msg
    # .env 回到旧值（没有"本地没存上、env 却改了"的分叉）
    assert _value_of(env_file, "CP_ALERT_SMTP_HOST") == "old.example.com"


def test_改配置不会把停用通道重新启用(store, installer):
    installer.install_builtin_channel("webhook")
    installer.toggle_channel("webhook", False)
    installer.configure_channel("webhook", {"webhook_url": "https://example.com/hook"})
    row = _row(store, "webhook")
    assert row["enabled"] is False
    assert row["status"] == ExtensionStatus.DISABLED.value


def test_卸载主通道同时清掉它的env键(store, installer, env_file):
    installer.install_builtin_channel("email_smtp")
    installer.configure_channel("email_smtp", {"smtp_server": "smtp.example.com",
                                               "default_to": "owner@example.com"})
    assert _value_of(env_file, "CP_ALERT_SMTP_HOST") == "smtp.example.com"
    ok, msg = installer.uninstall_channel("email_smtp")
    assert ok is True and ".env" in msg
    text = env_file.read_text(encoding="utf-8")
    assert "CP_ALERT_SMTP_HOST" not in text
    assert "CP_ALERT_SMTP_TO" not in text


def test_卸载非主通道不碰env(store, installer, env_file):
    installer.install_builtin_channel("email_smtp")
    installer.configure_channel("email_smtp", {"smtp_server": "smtp.example.com"})
    installer.install_channel("email_smtp_2", "邮件 2", "smtp",
                              config={"channel_type": "smtp", "smtp_server": "x"})
    ok, msg = installer.uninstall_channel("email_smtp_2")
    assert ok is True and ".env" not in msg
    assert _value_of(env_file, "CP_ALERT_SMTP_HOST") == "smtp.example.com"


# ════════════════════════════════════════════════════════════
#  6) 派生面：discover 的解析视图 / 探针 / 本地台账
# ════════════════════════════════════════════════════════════

def test_discover_暴露解析视图与范例(store, installer):
    installer.install_builtin_channel("email_smtp")
    payload = installer.discover_available_channels()
    rows = {r["channel_id"]: r for r in payload["resolved_channels"]}
    view = rows["email_smtp"]
    assert view["primary"] is True
    assert view["example"]["smtp_server"] == "smtp.example.com"
    assert view["notes"], "空态说明不能为空"


def test_探针用有效配置(monkeypatch):
    """探针与发送必须同源：本地留空、.env 有值 ⇒ 探针要探 .env 里的那个端点"""
    from agent.monitoring.dependency_probe import targets_from_channels
    monkeypatch.setenv("CP_ALERT_SMTP_HOST", "probe.example.com")
    monkeypatch.setenv("CP_ALERT_SMTP_PORT", "465")
    row = {"ext_id": "email_smtp",
           "config": {"channel_type": "smtp", "smtp_server": "", "smtp_port": ""}}
    targets = targets_from_channels([row], lambda r: True)
    assert [t["host"] for t in targets] == ["probe.example.com"]
    assert targets[0]["port"] == 465 and targets[0]["tls"] is True


def test_本地台账原子写且写前留备份(tmp_path):
    store = ExtensionStore(data_file=str(tmp_path / "extensions.json"))
    installer = ChannelInstaller(store)
    installer.install_builtin_channel("webhook")
    backups = list((tmp_path).glob("extensions.json.bak.*"))
    assert backups, "扩展台账（通道清单的唯一真相）写前必须留快照"
    data = json.loads((tmp_path / "extensions.json").read_text(encoding="utf-8"))
    assert any(row.get("ext_id") == "webhook" for row in data["channels"])