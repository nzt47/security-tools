"""鉴权迁移第 ① 步的验收（TASK-06 §3 第 5 步第 2 项 / 交付物 #11 / E12）

## 这一步交付什么

| 交付 | 位置 |
|---|---|
| 未配置令牌时**启动告警**（不阻断） | `app_server.py::_warn_if_auth_unconfigured` |
| 鉴权状态**只读暴露**（`/api/health/auth`） | `agent/server_routes/routes_panorama.py` |
| 聚合面增量暴露（`/api/status` 的 `auth` 键） | 同上 |
| 一键生成令牌（第 ② 步） | `scripts/gen_api_token.py` |
| 迁移路径与回退文档 | `docs/rfc/鉴权迁移.md` |

## 为什么这些断言是"迁移第 ① 步"的正确验收

E12 要求"鉴权改动**没有**一次性打挂本机 UI/脚本"。故本文件的每一条都同时钉住两件事：
① 状态**可见**（否则管理员以为端点已鉴权）；② 判定**没变**（未配令牌仍放行）。
任何"顺手改成 fail-closed"的改动都会让这里的第二类断言变红 —— 那是刻意的。

## 纪律

* 不真调 `/api/status`（它要活体 `Yunshu`）——用 `auth_status()` 与**路由注册**两个
  可判定的点来锁定，避免为测试拉起整个 app_server（那会起调度线程、写 data/**）。
* 环境变量一律 `monkeypatch`。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class TestAuthStatusIsVisible:

    def test_未配令牌时状态可见(self, monkeypatch):
        """本部署的实测状态：未配置任何令牌 ⇒ `configured=False` 且写明 fail-open"""
        import agent.server_auth as sa

        monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", False)
        monkeypatch.delenv("CP_UI_TOKENS", raising=False)
        st = sa.auth_status()
        assert st["configured"] is False
        assert st["source"] == sa.SRC_NO_TOKEN_CONFIGURED
        assert "fail-open" in st["note"], st

    def test_配了共享令牌则状态转为已配置(self, monkeypatch):
        import agent.server_auth as sa

        monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", True)
        monkeypatch.setenv("FLASK_API_TOKEN", "unit-test-token")
        st = sa.auth_status()
        assert st["configured"] is True
        assert st["shared_token"] is True
        assert st["source"] == sa.SRC_SHARED_TOKEN
        assert st["note"] == ""

    def test_状态字段齐全(self, monkeypatch):
        import agent.server_auth as sa

        st = sa.auth_status()
        for field in ("configured", "source", "shared_token", "token_map",
                      "token_map_size", "require_authoritative", "note"):
            assert field in st, f"缺字段 {field}"

    def test_状态探测绝不抛异常(self, monkeypatch):
        """取不到状态 ⇒ 降级为"未配置"，不得让端点/启动失败（D4）"""
        import agent.server_auth as sa

        monkeypatch.setattr(sa, "current_api_token",
                            lambda: (_ for _ in ()).throw(RuntimeError("env down")))
        st = sa.auth_status()
        assert st["configured"] is False


class TestJudgementIsUnchanged:
    """E12 的核心：第 ① 步**只让状态可见**，不改变任何判定"""

    def test_未配令牌仍然放行(self, monkeypatch):
        import agent.server_auth as sa

        monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", False)
        monkeypatch.delenv("CP_UI_TOKENS", raising=False)
        ok, actor, source = sa.authorize_token("")
        assert ok is True and source == sa.SRC_NO_TOKEN_CONFIGURED, \
            "第 ① 步不得把「未配令牌」改成拒绝（那会当场 401 掉本机 UI 与脚本）"

    def test_配了令牌后无令牌被拒(self, monkeypatch):
        import agent.server_auth as sa

        monkeypatch.setattr(sa, "_API_TOKEN_ENABLED", True)
        monkeypatch.setenv("FLASK_API_TOKEN", "unit-test-token")
        ok, _actor, source = sa.authorize_token("")
        assert ok is False and source == "denied"
        ok2, _a2, _s2 = sa.authorize_token("unit-test-token")
        assert ok2 is True


class TestRoutesAreRegistered:
    """"只读暴露"必须是**真实注册**的端点（本仓有过"只写进死代码、测试全绿线上 404"的教训）"""

    def _source(self) -> str:
        return (_PROJECT_ROOT / "agent/server_routes/routes_panorama.py").read_text(
            encoding="utf-8")

    def test_health_auth_端点存在(self):
        src = self._source()
        assert '@app.route("/api/health/auth")' in src
        assert "auth_status" in src

    def test_不改_api_health_的响应形状(self):
        """`/api/health` 是**数组**契约，被前端 `data.forEach` 消费 ⇒ 不得改成对象

        （若哪天要改形状，必须同步改前端与所有消费者，属独立变更 —— 本任务明确不做。）
        """
        src = self._source()
        block = src.split('@app.route("/api/health")', 1)[1].split("@app.route", 1)[0]
        assert "jsonify([r.to_dict() for r in readings])" in block, \
            "/api/health 的数组契约被改动了（会打挂状态面板）"
        assert "auth" not in block, "鉴权状态不该塞进 /api/health 的数组响应"

    def test_status_面增量暴露(self):
        src = self._source()
        block = src.split('@app.route("/api/status")', 1)[1].split("@app.route", 1)[0]
        assert 'status["auth"]' in block

    def test_启动告警存在且不阻断(self):
        src = (_PROJECT_ROOT / "app_server.py").read_text(encoding="utf-8")
        assert "_warn_if_auth_unconfigured" in src
        block = src.split("def _warn_if_auth_unconfigured", 1)[1].split("\n_warn", 1)[0]
        assert "logger.warning" in block
        assert "auth_status" in block
        # 只告警，不得 raise / sys.exit（D4：新模块失败不得阻断启动）
        assert "raise" not in block and "sys.exit" not in block


class TestTokenGenerator:
    """第 ② 步：一键生成（**默认零副作用**）"""

    def test_默认不写任何文件(self, tmp_path, monkeypatch):
        fake_env = tmp_path / ".env"
        fake_env.write_text("EXISTING=1\n", encoding="utf-8")
        r = subprocess.run(
            [sys.executable, str(_PROJECT_ROOT / "scripts" / "gen_api_token.py"), "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(_PROJECT_ROOT))
        assert r.returncode == 0, r.stderr
        data = json.loads(r.stdout)
        assert data["written"] is False
        assert data["env"]["FLASK_API_TOKEN"]
        assert len(data["env"]["FLASK_API_TOKEN"]) >= 20, "令牌太短（熵不足）"

    def test_每次生成都不同(self):
        r = subprocess.run(
            [sys.executable, str(_PROJECT_ROOT / "scripts" / "gen_api_token.py"), "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(_PROJECT_ROOT))
        r2 = subprocess.run(
            [sys.executable, str(_PROJECT_ROOT / "scripts" / "gen_api_token.py"), "--json"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(_PROJECT_ROOT))
        a = json.loads(r.stdout)["env"]["FLASK_API_TOKEN"]
        b = json.loads(r2.stdout)["env"]["FLASK_API_TOKEN"]
        assert a != b, "两次生成同一个令牌 ⇒ 不是随机源"

    def test_熵下限拒绝(self):
        r = subprocess.run(
            [sys.executable, str(_PROJECT_ROOT / "scripts" / "gen_api_token.py"),
             "--bytes", "4"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(_PROJECT_ROOT))
        assert r.returncode == 2
        assert "不得小于 16" in (r.stdout + r.stderr)

    def test_映射片段格式与解析口径一致(self):
        """`CP_UI_TOKENS` 的格式必须能被 `identity._split_entries` 解析

        【为什么必须对拍】"生成一个没人能解析的令牌文件"是典型的静默失败：
        脚本说成功、服务却把它当空表 ⇒ 又回到"未配令牌 ⇒ 不校验"。
        """
        r = subprocess.run(
            [sys.executable, str(_PROJECT_ROOT / "scripts" / "gen_api_token.py"),
             "--json", "--map", "ui,ci"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(_PROJECT_ROOT))
        raw = json.loads(r.stdout)["env"]["CP_UI_TOKENS"]
        from agent.security.identity import TokenMap
        tm = TokenMap(raw)
        assert tm.empty is False
        assert len(tm) == 2
        first_token = raw.split(",")[0].split(":")[0]
        entry = tm.lookup(first_token)
        assert entry is not None
        assert entry.actor == "ui"


class TestMigrationDocExists:
    """交付物 #11 的文档必须存在，且写明"哪一步做了、哪一步刻意没做" """

    def test_文档存在且覆盖四步(self):
        p = _PROJECT_ROOT / "docs" / "rfc" / "鉴权迁移.md"
        assert p.exists(), "缺少鉴权迁移文档（TASK-06 交付物 #11）"
        text = p.read_text(encoding="utf-8")
        for step in ("①", "②", "③", "④"):
            assert step in text, f"迁移文档缺第 {step} 步"
        assert "未接入" in text, "必须写明 multi_tenant.py 未接入这一技术债"
        assert "webhook" in text.lower(), "必须写明 webhook 场景的核查结论"
