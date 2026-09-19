"""特性开关守卫（TASK-03 第 6 步 / E8 / E13）

【本文件守护什么】
    `agent/settings/feature_flags.py::FEATURES` 是"阶段开关族"的唯一声明处。
    手册式的约定（"记得登记"）一定会漂移，所以这里用**双向机械断言**把它钉住：

    | 方向 | 断言 | 防的是什么 |
    |---|---|---|
    | 表 → 注册表 | 每个开关的 env 前缀族必须登记在 `registry.dynamic_prefixes()` | 新增开关不登记 ⇒ 零缺口守卫变红或（更糟）静默漏掉 |
    | 表 → config.yaml | `FEATURES` 的每个 name 必须在 `config.yaml:features` 里有条目 | 声明了却没有持久化默认值 ⇒ 关掉就再也打不开 |
    | config.yaml → 表 | 反向：本段新增的 `features.*` 布尔项必须在 `FEATURES` 里 | 有人在 yaml 里塞了个没人读的开关（"幽灵开关"） |
    | 名 → 文件 | `owner` 必须指向真实存在的文件 | UI 上"属于哪个子系统"点不进去 |
    | 归一 | `name` 唯一、全小写下划线、env 名可逆 | 大小写/短横线导致的"同一个开关两个名字" |

    ⚠️ 与 `tests/unit/test_settings_registry.py` 的分工：
    那边管"**代码里读到的 env** ↔ 注册表"（零缺口）；这边管
    "**特性开关表** ↔ 注册表 ↔ config.yaml"。两者互补，不重复。
"""

from __future__ import annotations

import pathlib
import re

import pytest

from agent.settings import feature_flags as F
from agent.settings import registry as R

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

#: config.yaml 里 **TASK-03 之前就存在**的 `features:` 键。
#: Why 要显式列出：那一批（v2_lifetrace / verification_enabled / ...）由既有代码
#: 直接读 config，尚未收口到本模块；反向断言若不排除它们，会立刻误红。
#: 收口它们属于业务代码改动（越界风险见 TASK-03 E9），登记为债务
#: （docs/rfc/特性开关规范.md §6）。
_LEGACY_FEATURE_KEYS = frozenset({
    "v2_lifetrace",
    "v2_persona",
    "v2_distillation",
    "v2_lazy_loader",
    "verification_enabled",
    "schema_validation_enabled",
    "critic_evaluation_enabled",
    "failure_analysis_enabled",
})


def _config_features() -> dict:
    """读 config.yaml 的 `features:` 段（只读）"""
    import yaml

    data = yaml.safe_load((REPO_ROOT / "config.yaml").read_text(encoding="utf-8")) or {}
    section = data.get("features") or {}
    assert isinstance(section, dict), "config.yaml 的 features: 必须是映射"
    return section


# ════════════════════════════════════════════════════════════
#  一、表自身的完整性
# ════════════════════════════════════════════════════════════


class TestFeatureTableIntegrity:
    def test_table_is_not_empty(self):
        assert F.feature_names(), "阶段开关族至少要有 TASK-04~08 的预留开关"

    def test_names_are_unique_and_normalized(self):
        names = [f.name for f in F.FEATURES]
        assert len(names) == len(set(names)), "开关名重复"
        for name in names:
            assert re.fullmatch(r"[a-z][a-z0-9_]*", name), (
                f"开关名 {name!r} 必须是小写字母开头的下划线命名（env 名由它大写推导）")

    def test_every_feature_has_required_metadata(self):
        for f in F.FEATURES:
            assert f.description.strip(), f.name
            assert f.stage.strip(), f.name
            assert isinstance(f.default, bool), f.name
            assert 0 <= f.rollout_default <= 100, f.name

    def test_owner_modules_point_at_real_files(self):
        for f in F.FEATURES:
            assert (REPO_ROOT / f.owner).exists(), f"{f.name} 的 owner 不存在：{f.owner}"

    def test_env_and_config_names_are_derived_consistently(self):
        for f in F.FEATURES:
            assert f.env_name == F.ENV_PREFIX + f.name.upper(), f.name
            assert f.rollout_env_name == f.env_name + F.ROLLOUT_SUFFIX, f.name
            assert f.config_path == f"features.{f.name}", f.name


# ════════════════════════════════════════════════════════════
#  二、表 ↔ 注册表（env 前缀族）
# ════════════════════════════════════════════════════════════


class TestRegistryCoupling:
    def test_env_prefix_family_is_registered(self):
        """★ E13 核心：特性开关的 env 前缀族必须在开关注册表里登记

        Why 只登记**家族**而不是逐个开关：代码里只有一处 `os.getenv(前缀+名字)`
        的**拼接**读取点（`feature_flags._env_text`），没有任何字面量 env 名。
        逐字面量登记会让每条都命中 `test_registry_has_no_phantom_switches`
        （"注册了但代码没读"）。详细成本量化见 docs/rfc/特性开关规范.md §5。
        """
        assert F.ENV_PREFIX in R.dynamic_prefixes(), (
            f"{F.ENV_PREFIX} 未登记进 registry.dynamic_prefixes()；"
            "新增特性开关必须先在 agent/settings/registry.py 登记该前缀族")

    def test_registered_family_spec_points_at_this_module(self):
        spec = next(s for s in R.REGISTRY if s.dynamic_prefix == F.ENV_PREFIX)
        assert spec.owner_module == "agent/settings/feature_flags.py"
        assert spec.env_name == "", "动态家族不得再占一个字面量 env 名（会造成幽灵行）"


# ════════════════════════════════════════════════════════════
#  三、表 ↔ config.yaml（双向）
# ════════════════════════════════════════════════════════════


class TestConfigCoupling:
    def test_every_feature_has_a_config_default(self):
        cfg = _config_features()
        for f in F.FEATURES:
            assert f.name in cfg, (
                f"{f.name} 在 FEATURES 表里声明，但 config.yaml 的 features: 段没有它；"
                "没有持久化默认值 ⇒ 一旦被翻过就再也回不到已知状态")
            assert isinstance(cfg[f.name], bool), f"{f.name} 必须是布尔"

    def test_config_default_matches_table_default(self):
        """两处默认值必须一致（否则"关掉"到底关成什么，取决于读哪一处）"""
        cfg = _config_features()
        for f in F.FEATURES:
            assert cfg[f.name] == f.default, (
                f"{f.name}: config.yaml={cfg[f.name]} 与 FEATURES.default={f.default} 不一致")

    def test_no_phantom_feature_keys_in_config(self):
        """反向：config.yaml 的 features: 段不得有"没人读"的开关

        只对**本任务新增的段**做强制（`_LEGACY_FEATURE_KEYS` 是既有存量，
        收口它们在 TASK-04~08 的范围里）。判据：新增键必须在 FEATURES 表里。
        """
        cfg = _config_features()
        declared = set(F.feature_names())
        unknown = {k for k in cfg if k not in declared and k not in _LEGACY_FEATURE_KEYS}
        assert not unknown, (
            f"config.yaml 的 features: 段出现未声明的键 {sorted(unknown)}；"
            "要么在 FEATURES 表登记，要么加进 _LEGACY_FEATURE_KEYS 并说明为何不收口")


# ════════════════════════════════════════════════════════════
#  四、求值语义（运行时可切 / 灰度 / fail-closed）
# ════════════════════════════════════════════════════════════


class TestEvaluationSemantics:
    def test_undeclared_feature_is_fail_closed(self):
        """未声明的开关必须返回 False 且说清原因（不能"猜一个默认值"）"""
        state = F.feature_state("no_such_feature")
        assert state["declared"] is False
        assert state["enabled"] is False
        assert "fail-closed" in state["reason"]
        assert F.feature_enabled("no_such_feature") is False

    def test_env_override_wins_and_needs_no_restart(self, monkeypatch):
        """★ E8 核心：`运行时可切` —— 同一进程内改变量即时生效，无需重启"""
        name = "capability_registry"
        monkeypatch.delenv(F.get_spec(name).env_name, raising=False)
        assert F.feature_enabled(name) is False, "默认应为关闭"
        monkeypatch.setenv(F.get_spec(name).env_name, "true")
        assert F.feature_enabled(name) is True, "env 覆盖未即时生效"
        monkeypatch.setenv(F.get_spec(name).env_name, "false")
        assert F.feature_enabled(name) is False, "翻回 false 未即时生效（一键回退失效）"

    def test_env_parsing_matches_existing_legacy_dialect(self, monkeypatch):
        """与既有 `YUNSHU_FEATURE_SANDBOX` 的口径一致：只有 'true' 才算真

        Why 单独断言：若这里改成 `bool(raw)`，`YUNSHU_FEATURE_X=false` 会被
        判成 **True** —— 一个"关掉反而打开"的开关是安全事故级缺陷。
        """
        spec = F.get_spec("capability_registry")
        for raw, expected in (("true", True), ("True", True), ("TRUE", True),
                              ("false", False), ("0", False), ("", False), ("no", False)):
            monkeypatch.setenv(spec.env_name, raw)
            assert F.feature_enabled("capability_registry") is expected, raw

    def test_rollout_is_deterministic_and_independent(self):
        """灰度分桶必须稳定，且不同开关之间不相关"""
        a = [F.rollout_bucket("capability_registry", f"s{i}") for i in range(200)]
        b = [F.rollout_bucket("capability_registry", f"s{i}") for i in range(200)]
        assert a == b, "同一 (开关, 主体) 的桶必须稳定"
        c = [F.rollout_bucket("tool_confirm_levels", f"s{i}") for i in range(200)]
        assert a != c, "不同开关的分桶必须独立（否则命中一个就命中全部）"
        assert all(0 <= v < 100 for v in a)

    def test_rollout_boundary_is_respected(self, monkeypatch):
        """0% 全不开、100% 全开（边界不能靠抽样碰巧）"""
        name = "capability_non_llm_entry"
        spec = F.get_spec(name)
        monkeypatch.setenv(spec.env_name, "true")

        monkeypatch.setenv(spec.rollout_env_name, "0")
        assert all(not F.feature_state(name, subject=f"s{i}")["enabled"] for i in range(200))

        monkeypatch.setenv(spec.rollout_env_name, "100")
        assert all(F.feature_state(name, subject=f"s{i}")["enabled"] for i in range(200))

    def test_rollout_approximates_configured_percentage(self, monkeypatch):
        """10% 灰度在 2000 个主体上的实际命中率应落在合理区间（分桶不能偏）"""
        name = "capability_non_llm_entry"
        spec = F.get_spec(name)
        monkeypatch.setenv(spec.env_name, "true")
        monkeypatch.setenv(spec.rollout_env_name, "10")
        hits = sum(1 for i in range(2000) if F.feature_state(name, subject=f"subject-{i}")["enabled"])
        assert 120 <= hits <= 280, f"10% 灰度的实际命中 {hits}/2000 偏离过大（分桶不均匀）"

    def test_state_reports_why_not_just_whether(self, monkeypatch):
        """状态必须能回答"为什么是 false"（排查时不能只能靠猜）"""
        name = "capability_registry"
        spec = F.get_spec(name)
        monkeypatch.delenv(spec.env_name, raising=False)
        state = F.feature_state(name)
        assert state["source"] in ("default", f"env:{spec.env_name}") or state["source"].startswith(
            ("config:", "override:"))
        assert state["reason"]
        assert state["effect"] == F.EFFECT_HOT

    def test_feature_enabled_never_raises_when_audit_unavailable(self, monkeypatch):
        """审计不可用时读路径**不得抛错**（D4：降级而非阻断）"""
        monkeypatch.setenv(F.get_spec("capability_registry").env_name, "true")

        import builtins

        real_import = builtins.__import__

        def _boom(name, *a, **kw):
            if name.startswith("agent.audit"):
                raise RuntimeError("audit chain down")
            return real_import(name, *a, **kw)

        monkeypatch.setattr(builtins, "__import__", _boom)
        assert F.feature_enabled("capability_registry", audit=True) is True

    def test_list_features_covers_the_table(self):
        listed = {row["name"] for row in F.list_features()}
        assert listed == set(F.feature_names())


# ════════════════════════════════════════════════════════════
#  五、阶段归属披露（供 TASK-04~08 使用）
# ════════════════════════════════════════════════════════════


class TestStagePlan:
    def test_every_stage_has_at_least_one_switch(self):
        """TASK-04~08 每个阶段都必须有可一键回退的开关（v1.4 第 14 章要求）"""
        stages = {f.stage for f in F.FEATURES}
        for task in ("TASK-04", "TASK-05", "TASK-06", "TASK-07", "TASK-08"):
            assert task in stages, f"{task} 没有登记阶段开关 ⇒ 该阶段无法一键回退"

    def test_stage_switches_default_to_off(self):
        """阶段开关默认必须关闭：合并重构代码不等于改变现网行为"""
        for f in F.FEATURES:
            if f.stage != "builtin":
                assert f.default is False, f"{f.name} 默认开启会让重构一合并就生效"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
