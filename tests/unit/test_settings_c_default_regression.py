"""回归测试：C 级开关的默认值永不外泄（2026-09-13 实测缺陷）。

**缺陷复盘**：`GET /api/cp/settings` 在真实环境下**必现 500**。
根因链：
1. `SettingSpec.to_public_dict()` 的 `default` 只对 `secret=True` 屏蔽，
   **路径类 C 级项（`secret=False`，如 `ERROR_REPORTING_FILE_PATH`）漏了** → 默认值明文外泄；
2. 当 `.env` 里的值恰好等于该默认值（按默认配置填写的常见情形）时，
   `masking.assert_no_plaintext` 在投影里命中该字符串；
3. 守卫按 fail-closed 设计抛错 → 整个开关中心 API 500。

修复：C 级（只读脱敏）**一律**不投影 default 原文（返回 None）。

本文件锁定三件事：
- C 级 default 一律 None（不论是否 secret）；
- A/B 级 default 照常投影（防止过度屏蔽）；
- 端到端：构造"C 级 default == env 值"场景时，`_build_index()` **不得抛错**。
"""

from __future__ import annotations

import pytest

from agent.settings.registry import CAT_OBSERVABILITY, REGISTRY, RISK_C


# ── 投影层 ────────────────────────────────────────────────
def test_c_level_path_default_is_not_projected():
    """C 级路径类（secret=False）的 default 不得投影原文。"""
    spec = next(
        s for s in REGISTRY
        if s.risk == RISK_C and not getattr(s, "secret", False)
        and getattr(s, "default", None)
    )
    assert spec.to_public_dict()["default"] is None, (
        f"{spec.key} 的 default 明文外泄：{spec.to_public_dict()['default']!r}")


def test_all_c_level_defaults_are_none():
    """全表：任何 C 级条目的 public default 都必须是 None。"""
    leaked = [s.key for s in REGISTRY
              if s.risk == RISK_C and s.to_public_dict().get("default") is not None]
    assert leaked == [], f"以下 C 级条目仍投影 default：{leaked}"


def test_ab_level_defaults_still_projected():
    """A/B 级可编辑项仍应投影 default（防止过度屏蔽让 UI 失去参照）。"""
    specs = [s for s in REGISTRY
             if s.risk != RISK_C and getattr(s, "default", None) is not None]
    assert specs, "注册表里应存在带 default 的 A/B 级条目"
    assert any(s.to_public_dict()["default"] is not None for s in specs)


# ── 端到端：守卫不得因 default 误报 ────────────────────────
def test_build_index_does_not_raise_when_c_default_equals_env(monkeypatch):
    """回归：C 级项的 env 值恰等于其 default 时，`_build_index()` 不得抛错。"""
    from agent.settings import registry as reg

    spec = next(
        s for s in REGISTRY
        if s.risk == RISK_C and getattr(s, "default", None)
        and getattr(s, "env_name", "")
    )
    monkeypatch.setenv(spec.env_name, str(spec.default))

    from agent.server_routes.routes_settings import _build_index

    payload = _build_index()          # 修复前此处抛 AssertionError
    assert payload["ok"] is True
    # 精确断言：C 级项的 default 一律为 None。
    # 【为什么不用"全响应体不含该短字符串"这种粗断言】短值（如 'jsonl'、'memory'）
    # 会与**其他条目的合法元数据**撞车（例如同为一项的 config 选项、另一项的
    # default），而这正是本次缺陷误报的成因（详见同目录 masking 守卫的修正说明）。
    c_items = [it for it in payload.get("items", []) if it.get("risk") == "C"]
    assert c_items, "响应体应含 C 级项"
    leaked = [it.get("key") for it in c_items if it.get("default") is not None]
    assert leaked == [], f"C 级项仍投影 default：{leaked}"


def test_settings_switches_are_registered(monkeypatch):
    """SLO 周报的 env 读取点必须在注册表内（零缺口门要求）。"""
    keys = {s.key for s in REGISTRY}
    expected = {
        "CP_SLO_SCHEDULE_ENABLED",
        "CP_SLO_SCHEDULE_OUT_DIR",
        "CP_SLO_SCHEDULE_AUDIT_FILE",
        "CP_UTC_CALIBRATION_FILE",
    }
    assert expected <= keys, f"未注册：{sorted(expected - keys)}"
    assert any(getattr(s, "dynamic_prefix", "") == "CP_SLO_SCHEDULE_"
               for s in REGISTRY), "CP_SLO_SCHEDULE_ 动态家族未声明"
    assert CAT_OBSERVABILITY in {s.category for s in REGISTRY}
