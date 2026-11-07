"""入轨助手 · 基线默认值（2026-11-08 Owner 决定，R-1）
------------------------------------------------------------------
R-1 事故：2026-11-07 23:49 的 UI 入轨把 `capability_manifest` / 盘点表 / 3 处**测试基线**
一起改了。基线是「派生面哨兵」—— 它们必须等于"干净 worktree 重派生"的结果，人才能靠
它们发现清单被手改；助手顺手改数字 = 绕过 review 挪哨兵。

判据（本次锁死）：
  ① 端点默认 `fix_baselines=False`（不传就不改基线）；
  ② 显式传 `fix_baselines: true` 时才透传 true（CLI 的 `--fix-baselines` 不受影响）；
  ③ 响应回显生效值，界面能分清"基线没变化"和"基线没开"。

用**最小 Flask app** 只挂这一个路由（app_server 全量装配要 80–100s，见
`test_envelope_batch4` 的同类做法）；`scripts.intake_skill.intake_one` 换成替身，
所以本用例只验证"视图把哪个值传下去"，不碰 git / 文件。
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.timeout(120)

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def client(monkeypatch):
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    # 未配置令牌 ⇒ require_token 直接放行（SRC_NO_TOKEN_CONFIGURED），本用例不测鉴权
    monkeypatch.delenv("FLASK_API_TOKEN", raising=False)
    monkeypatch.delenv("CP_UI_TOKENS", raising=False)
    monkeypatch.setenv("FLASK_API_TOKEN", "")

    from flask import Flask

    routes = importlib.import_module("agent.server_routes.routes_skills_mgmt")
    app = Flask(__name__)
    routes.register_routes(app, None)
    app.config["TESTING"] = True
    return app.test_client()


@pytest.fixture
def spy(monkeypatch):
    """把 intake_one 换成替身，记录收到的 kwargs。"""
    intake = importlib.import_module("scripts.intake_skill")
    seen = {}

    def _fake(skill_id, *, execute=False, fix_baselines=False, fix_meta=True, llm_sections=False):
        seen.update({"skill_id": skill_id, "execute": execute,
                     "fix_baselines": fix_baselines, "fix_meta": fix_meta,
                     "llm_sections": llm_sections})
        return {"ok": True, "skill_id": skill_id, "baseline_changes": []}

    monkeypatch.setattr(intake, "intake_one", _fake)
    return seen


def test_不传_fix_baselines_时默认不刷基线(client, spy):
    r = client.post("/api/skills-mgmt/document-skill/intake", json={"execute": True})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert spy["fix_baselines"] is False, "入轨助手又默认改基线了（R-1 事故的入口）"
    assert r.get_json()["fix_baselines"] is False, "响应要回显生效值，界面才分得清「没变化」与「没开」"


def test_只有显式传_true_才刷基线(client, spy):
    r = client.post("/api/skills-mgmt/document-skill/intake",
                    json={"execute": True, "fix_baselines": True})
    assert r.status_code == 200, r.get_data(as_text=True)
    assert spy["fix_baselines"] is True
    assert r.get_json()["fix_baselines"] is True


def test_字面量_false_字符串不会被当成真(client, spy):
    r = client.post("/api/skills-mgmt/document-skill/intake",
                    json={"execute": True, "fix_baselines": "false"})
    assert r.status_code == 200
    assert spy["fix_baselines"] is False


def test_常量与路由写法同源(client):
    """防"改了常量却没接线"：视图里必须引用 DEFAULT_FIX_BASELINES，不是再写一个 True。"""
    src = (ROOT / "agent" / "server_routes" / "routes_skills_mgmt.py").read_text(encoding="utf-8")
    assert "_as_bool(\"fix_baselines\", DEFAULT_FIX_BASELINES)" in src, (
        "入轨路由没引用 DEFAULT_FIX_BASELINES —— 改常量就失效了")
    assert "_as_bool(\"fix_baselines\", True)" not in src, "又写回默认 True 了"
