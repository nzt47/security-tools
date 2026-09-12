"""TASK-S7-01 开关注册表与机械提取单测（**零缺口**守护）

【本文件守护的两条硬约束】
    1. **零缺口**：`scripts/scan_settings.py` 用 AST 从代码里机械提取全部 env 读取点，
       提取结果必须 100% 被 `agent/settings/registry.py` 覆盖（缺口即失败）；
    2. **零重造**：`agent/monitoring/observability_config.py` 的既有校验表
       （48 条 path/校验/默认/说明）必须 100% 被注册表以 `config_path` 合并，
       且默认值逐条一致（少一条即失败）。

另加"反向防漂移"：**注册表里登记的 env 名必须在代码里真的被读到**（`extra == []`），
否则会出现"UI 显示了一个没人读的开关"这种更隐蔽的谎报。
"""

from __future__ import annotations

import importlib.util
import pathlib

import pytest

from agent.monitoring.observability_config import OBSERVABILITY_VALIDATION_RULES
from agent.settings import registry as R

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _load_scanner():
    """按路径加载 `scripts/scan_settings.py`（scripts/ 不是包）

    注意：必须**先注册进 `sys.modules` 再 exec**，否则模块内 `@dataclass` 在
    Python 3.12 下会因 `sys.modules[cls.__module__] is None` 而报
    `AttributeError: 'NoneType' object has no attribute '__dict__'`（S7-01 实测踩坑）。
    """
    import sys

    path = REPO_ROOT / "scripts" / "scan_settings.py"
    spec = importlib.util.spec_from_file_location("cp_scan_settings", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:                                    # pragma: no cover
        sys.modules.pop(spec.name, None)
        raise
    return module


@pytest.fixture(scope="module")
def scan():
    """整仓扫描一次（模块级复用；扫描本身只读，无副作用）"""
    scanner = _load_scanner()
    report = scanner.scan_paths([REPO_ROOT / "agent"], REPO_ROOT)
    return scanner, report


# ════════════════════════════════════════════════════════════
#  一、零缺口（机械提取 vs 注册表）
# ════════════════════════════════════════════════════════════


class TestMechanicalZeroGap:
    def test_scan_covers_repo_sources(self, scan):
        _scanner, report = scan
        assert report.files_scanned > 300
        assert report.parse_errors == []

    def test_zero_gap_between_scan_and_registry(self, scan):
        """★ 验收核心：机械提取项零缺口"""
        scanner, report = scan
        gap = scanner.check_gaps(report)
        assert gap.missing == [], f"代码读到但注册表未覆盖：{gap.missing}"
        assert gap.unregistered_dynamic == []
        assert gap.undeclared_process_env == []
        assert gap.undeclared_passthrough == []
        assert gap.undeclared_runtime_reads == []
        assert gap.ok is True

    def test_registry_has_no_phantom_switches(self, scan):
        """反向：注册表登记的 env 名必须在代码里真的被读到（防"没人读的开关"）"""
        scanner, report = scan
        gap = scanner.check_gaps(report)
        assert gap.extra == [], f"注册表登记但代码未读到：{gap.extra}"

    def test_extracted_scale_is_disclosed(self, scan):
        """规模如实披露（任务书说的 73 是 `.env` 里配置的条数，不是代码读取点）"""
        scanner, report = scan
        gap = scanner.check_gaps(report)
        assert len(gap.extracted_names) >= 250
        assert len(gap.registered_names) == len(gap.extracted_names)

    def test_declared_exclusions_are_exact_tables(self, scan):
        """排除表/透传表/运行时名字表都是**显式声明**的，且被实际命中"""
        scanner, report = scan
        observed_process = set(report.process_env_names())
        assert observed_process
        assert observed_process <= set(scanner.PROCESS_ENV_DENYLIST)
        for name in observed_process:
            assert scanner.PROCESS_ENV_DENYLIST[name].strip()
        # 每个声明项都必须给出理由（防止"塞个空理由进白名单"）
        for name, why in scanner.PROCESS_ENV_DENYLIST.items():
            assert why.strip(), name
        for site, why in scanner.PASS_THROUGH_SITES.items():
            assert why.strip(), site
        for expr, why in scanner.RUNTIME_NAME_READS.items():
            assert why.strip(), expr

    def test_dynamic_families_match_registry_declarations(self, scan):
        """动态家族：提取到的与注册表声明的一致（两向）"""
        _scanner, report = scan
        observed = set(report.dynamic_prefixes())
        declared = set(R.dynamic_prefixes())
        assert observed == declared, (observed, declared)
        assert observed, "至少应有一个动态家族（技能评估/摘要/清理前缀族）"

    def test_scan_report_is_reproducible(self, scan):
        """两次扫描结果一致（机械提取必须确定，不能靠字典序偶然）"""
        scanner, report = scan
        second = scanner.scan_paths([REPO_ROOT / "agent"], REPO_ROOT)
        assert sorted(second.managed_names()) == sorted(report.managed_names())

    def test_two_level_family_chain_is_resolved(self, scan):
        """★ 二级转发家族必须解析出**真实开关名**（S7-02 合并后实测的漏报）

        形态：`agent/repair/policy.py` 里
            `_env_int(env, name, d)` → `_env_text(env, name)` → `key = ENV_PREFIX + name`
        名字在**内层**构造，而且**不是第一个参数**。早期提取器只认"第一个实参就是名字"，
        于是整族 `CP_REPAIR_*` 被归入 `<unresolved>`——**这些开关在 UI 里根本看不见**。
        本用例把该能力钉死：既断言名字解析正确，也断言其归类为 `helper_family`。
        """
        _scanner, report = scan
        names = report.managed_names()
        expected = {
            "CP_REPAIR_MAX_CHANGED_FILES", "CP_REPAIR_MAX_LINES_PER_FILE",
            "CP_REPAIR_BUDGET_TOKENS", "CP_REPAIR_MAX_ROUNDS",
            "CP_REPAIR_TIMEOUT_SECONDS", "CP_REPAIR_SLICE_RADIUS",
            "CP_REPAIR_HISTORY_COMMITS",
        }
        missing = expected - set(names)
        assert not missing, f"二级转发家族未解析出：{sorted(missing)}"
        for name in expected:
            kinds = {rp.kind for rp in names[name]}
            assert "helper_family" in kinds, (name, kinds)
            assert names[name][0].module == "agent/repair/policy.py"

    def test_resolved_family_names_are_independently_verifiable(self, scan):
        """★ 解析出的名字必须能在源码里**独立复核**（防"提取器自己编名字"）

        提取器是"代码读了什么"的唯一来源：它若编出一个并不存在的开关名，注册表就会
        带着"已被机械提取证实"的外观把假开关展示给用户。故此处用**另一种方法**
        （直接读源码取 `ENV_PREFIX` 常量 + 调用点字面量后缀）复核同一批名字。
        """
        _scanner, report = scan
        names = set(report.managed_names())
        source = (REPO_ROOT / "agent/repair/policy.py").read_text(encoding="utf-8")
        prefix_lines = [ln for ln in source.splitlines()
                        if ln.startswith("ENV_PREFIX")]
        assert prefix_lines, "源码里必须能直接读到 ENV_PREFIX 常量"
        prefix = prefix_lines[0].split("=", 1)[1].strip().strip('"')
        assert prefix == "CP_REPAIR_"
        resolved = sorted(n for n in names if n.startswith(prefix))
        assert resolved, "应解析出该前缀下的开关名"
        for name in resolved:
            suffix = name[len(prefix):]
            assert f'"{suffix}"' in source, (
                f"{name} 的后缀 {suffix} 未能在源码里独立复核（疑似提取器编造）")


# ════════════════════════════════════════════════════════════
#  二、零重造（合并既有 observability 校验表）
# ════════════════════════════════════════════════════════════


class TestObservabilityRuleMerge:
    def test_every_rule_path_is_merged(self):
        """★ 既有 48 条校验规则必须逐条被注册表合并（config_path 对齐）"""
        merged = {s.config_path for s in R.all_specs() if s.config_path}
        for rule in OBSERVABILITY_VALIDATION_RULES:
            assert rule.path in merged, f"未合并既有校验项：{rule.path}"

    def test_merged_defaults_are_identical(self):
        """合并项的**默认值**必须与既有表逐条一致（不重复造、也不许改数）"""
        by_path = {s.config_path: s for s in R.all_specs() if s.config_path}
        for rule in OBSERVABILITY_VALIDATION_RULES:
            spec = by_path[rule.path]
            assert spec.default == rule.default, rule.path

    def test_merged_descriptions_are_not_empty(self):
        by_path = {s.config_path: s for s in R.all_specs() if s.config_path}
        for rule in OBSERVABILITY_VALIDATION_RULES:
            spec = by_path[rule.path]
            assert spec.description.strip()
            assert rule.description in spec.description

    def test_merged_items_have_validators(self):
        for spec in R.all_specs():
            if spec.config_path:
                assert spec.validator.kind in (
                    "bool", "int", "float", "str", "enum", "path", "regex")

    def test_tracing_env_names_map_to_paths(self):
        """tracing.* 的两级读取（env 优先于配置树）在注册表里合成为一条"""
        env_spec = R.get_spec("TRACING_SAMPLER_RATIO")
        assert env_spec is not None
        assert env_spec.config_path == "tracing.sampler_ratio"
        assert env_spec.env_name == "TRACING_SAMPLER_RATIO"
        assert env_spec.env_only is False


# ════════════════════════════════════════════════════════════
#  三、注册表自身的完整性
# ════════════════════════════════════════════════════════════


class TestRegistryIntegrity:
    def test_no_duplicate_keys_or_env_names(self):
        keys = [s.key for s in R.all_specs()]
        assert len(keys) == len(set(keys))
        envs = [s.env_name for s in R.all_specs() if s.env_name]
        assert len(envs) == len(set(envs))

    def test_every_spec_has_required_metadata(self):
        for spec in R.all_specs():
            assert spec.description.strip(), spec.key
            assert spec.risk in R.RISK_LABELS, spec.key
            assert spec.category in R.CATEGORY_LABELS, spec.key
            assert spec.type in ("bool", "int", "float", "str", "path"), spec.key
            assert spec.owner_module or spec.dynamic_prefix, spec.key
            assert spec.env_name or spec.config_path or spec.dynamic_prefix, spec.key

    def test_owner_modules_point_at_real_files(self):
        """归属模块必须是真实存在的文件（UI 展示"属于哪个子系统"要能落地）"""
        for spec in R.all_specs():
            if not spec.owner_module:
                continue
            assert (REPO_ROOT / spec.owner_module).exists(), spec.owner_module

    def test_risk_counts_and_categories_sum(self):
        counts = R.counts_by_risk()
        assert sum(counts.values()) == len(R.all_specs())
        cats = R.categories()
        assert sum(c["count"] for c in cats) == len(R.all_specs())
        assert [c["id"] for c in cats] == list(R.CATEGORY_ORDER)

    def test_all_six_categories_are_used(self):
        used = {s.category for s in R.all_specs()}
        assert used == set(R.CATEGORY_ORDER)

    def test_b_level_requires_second_factor(self):
        for spec in R.all_specs():
            if spec.risk == R.RISK_B:
                assert spec.requires_second_factor is True
                assert spec.requires_dual_approval is True
            else:
                assert spec.requires_second_factor is False

    def test_c_level_secrets_never_are_editable(self):
        for spec in R.all_specs():
            if spec.risk == R.RISK_C:
                assert spec.editable is False, spec.key

    def test_env_only_flag_matches_task_requirement(self):
        """有 env_name 无 config_path → 必须能标注「仅支持环境变量」"""
        env_only = [s for s in R.all_specs() if s.env_only]
        assert env_only
        sample = R.get_spec("LOCK_PROFILE")
        assert sample is not None and sample.env_only is True

    def test_effect_vocabulary_is_exercised(self):
        effects = {s.effect for s in R.all_specs()}
        assert R.EFFECT_HOT in effects
        assert R.EFFECT_RESTART in effects
        assert R.EFFECT_NEXT_TASK in effects, "next_task 生效方式必须有真实条目"

    def test_public_dict_hides_secret_defaults(self):
        spec = R.get_spec("SMTP_PASSWORD")
        assert spec is not None and spec.secret is True
        assert spec.to_public_dict()["default"] is None

    def test_spec_rejects_missing_description(self):
        with pytest.raises(ValueError):
            R.SettingSpec(key="X_Y", category=R.CAT_OBSERVABILITY, type="bool",
                          default=False, description="", env_name="X_Y")
