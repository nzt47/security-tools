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
import os
import pathlib
import re

import pytest

from agent.monitoring.observability_config import OBSERVABILITY_VALIDATION_RULES
from agent.settings import registry as R

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]


# ════════════════════════════════════════════════════════════
#  〇、L5 补登记清单（2026-09-23）—— 本文件多处守护共用，故定义在最前
# ════════════════════════════════════════════════════════════
#
# 来源：docs/closeout/开关登记表_config口径审计_20260922.md（只读审计）
#   (a) 组 398 个 config_path 为空的键中，**55 个**确实读 config.yaml；
#   其中 **50 个是定值路径**（下表逐键取证），**5 个是动态族**（只能写 description）。
#
# 口径纪律（L1 原文）：补 config_path 会让本来「只读 default」的键**开始受
# config.yaml 影响**，故**禁止批量补** —— 每个键都必须能在归属模块源码里
# 复核到「env 读取点 + config.yaml 点分路径」，见 TestL5ReadSitesAreMechanicallyVerified。

#: L5 的 50 个「定值路径」键：key -> (归属模块, 声明的点分路径, env 名形态)
#:
#: env 名形态有两种（两种都由**源码**机械核对，不是手抄）：
#:   - 字面量字符串：模块源码里直接出现该 env 名（如 "CP_BUDGET_BRAKE_ENABLED"）；
#:   - ("compose", 前缀, 后缀) 三元组：模块源码里出现前缀常量与后缀字面量
#:     （如 _ENV_PREFIX = "SKILL_CLEANUP" 与 f"{_ENV_PREFIX}_ENABLED"）。
#:     ★ 这类 env 名**不存在**于代码字面量里，若按字面量断言，守护会永远失败/或被迫空转。
_L5_READ_SITES: dict = {
        "CP_BUDGET_BRAKE_ENABLED": ("agent/monitoring/cost_brake.py", "budget.enabled", "CP_BUDGET_BRAKE_ENABLED"),
        "CP_MODEL_FALLBACK_CHAIN": ("agent/observability/model_degrade.py", "llm.fallback_chain", "CP_MODEL_FALLBACK_CHAIN"),
        "CP_RETENTION_ARCHIVE_DIR": ("agent/retention/policy.py", "retention.archive_dir", "CP_RETENTION_ARCHIVE_DIR"),
        "CP_SLO_SCHEDULE_AUDIT_FILE": ("agent/monitoring/slo_report_scheduler.py", "slo_report.audit_file", ("compose", "CP_SLO_SCHEDULE", "_AUDIT_FILE")),
        "CP_SLO_SCHEDULE_OUT_DIR": ("agent/monitoring/slo_report_scheduler.py", "slo_report.out_dir", ("compose", "CP_SLO_SCHEDULE", "_OUT_DIR")),
        "CP_UTC_ANCHOR_MODEL": ("agent/observability/utc.py", "llm.model", "CP_UTC_ANCHOR_MODEL"),
        "CRITIC_EVALUATION_ENABLED": ("agent/orchestrator/orchestrator.py", "features.critic_evaluation_enabled", "CRITIC_EVALUATION_ENABLED"),
        "LEARNING_BUDGET_MAX_DAILY_TOKENS": ("agent/learning_budget.py", "learning.budget.max_daily_tokens", "LEARNING_BUDGET_MAX_DAILY_TOKENS"),
        "LEARNING_BUDGET_MAX_SINGLE_ACTION_TOKENS": ("agent/learning_budget.py", "learning.budget.max_single_action_tokens", "LEARNING_BUDGET_MAX_SINGLE_ACTION_TOKENS"),
        "LEARNING_BUDGET_MODE": ("agent/learning_budget.py", "learning.budget.mode", "LEARNING_BUDGET_MODE"),
        "LEARNING_BUDGET_RECOVERY_SECONDS": ("agent/learning_budget.py", "learning.budget.recovery_seconds", "LEARNING_BUDGET_RECOVERY_SECONDS"),
        "LEARNING_CONTEXT_ASSEMBLER_ENABLED": ("agent/orchestrator/orchestrator.py", "learning.context_assembler.enabled", "LEARNING_CONTEXT_ASSEMBLER_ENABLED"),
        "LEARNING_EVOLVER_AUDIT_FILE": ("agent/skills_mgmt/evolution_scheduler.py", "learning.evolver.audit_file", ("compose", "LEARNING_EVOLVER", "_AUDIT_FILE")),
        "LEARNING_EVOLVER_DRY_RUN": ("agent/skills_mgmt/evolution_scheduler.py", "learning.evolver.dry_run", ("compose", "LEARNING_EVOLVER", "_DRY_RUN")),
        "LEARNING_EVOLVER_ENABLED": ("agent/skills_mgmt/evolution_scheduler.py", "learning.evolver.enabled", ("compose", "LEARNING_EVOLVER", "_ENABLED")),
        "LEARNING_EVOLVER_INTERVAL_DAYS": ("agent/skills_mgmt/evolution_scheduler.py", "learning.evolver.interval_days", ("compose", "LEARNING_EVOLVER", "_INTERVAL_DAYS")),
        "LEARNING_FEEDBACK_AGENT_AUDIT_FILE": ("agent/skills_mgmt/feedback_agent.py", "learning.feedback_agent.audit_file", ("compose", "LEARNING_FEEDBACK_AGENT", "_AUDIT_FILE")),
        "LEARNING_FEEDBACK_AGENT_DRY_RUN": ("agent/skills_mgmt/feedback_agent.py", "learning.feedback_agent.dry_run", ("compose", "LEARNING_FEEDBACK_AGENT", "_DRY_RUN")),
        "LEARNING_FEEDBACK_AGENT_ENABLED": ("agent/skills_mgmt/feedback_agent.py", "learning.feedback_agent.enabled", ("compose", "LEARNING_FEEDBACK_AGENT", "_ENABLED")),
        "LEARNING_FEEDBACK_AGENT_INTERVAL_HOURS": ("agent/skills_mgmt/feedback_agent.py", "learning.feedback_agent.interval_hours", ("compose", "LEARNING_FEEDBACK_AGENT", "_INTERVAL_HOURS")),
        "LEARNING_LIFECYCLE_ARCHIVE_DAYS": ("agent/skills_mgmt/lifecycle.py", "learning.lifecycle.archive_days", ("compose", "LEARNING_LIFECYCLE", "_ARCHIVE_DAYS")),
        "LEARNING_LIFECYCLE_AUDIT_FILE": ("agent/skills_mgmt/lifecycle.py", "learning.lifecycle.audit_file", ("compose", "LEARNING_LIFECYCLE", "_AUDIT_FILE")),
        "LEARNING_LIFECYCLE_DRY_RUN": ("agent/skills_mgmt/lifecycle.py", "learning.lifecycle.dry_run", ("compose", "LEARNING_LIFECYCLE", "_DRY_RUN")),
        "LEARNING_LIFECYCLE_ENABLED": ("agent/skills_mgmt/lifecycle.py", "learning.lifecycle.enabled", ("compose", "LEARNING_LIFECYCLE", "_ENABLED")),
        "LEARNING_LIFECYCLE_INTERVAL_HOURS": ("agent/skills_mgmt/lifecycle.py", "learning.lifecycle.interval_hours", ("compose", "LEARNING_LIFECYCLE", "_INTERVAL_HOURS")),
        "LEARNING_LIFECYCLE_UNUSED_DAYS": ("agent/skills_mgmt/lifecycle.py", "learning.lifecycle.unused_days", ("compose", "LEARNING_LIFECYCLE", "_UNUSED_DAYS")),
        "LEARNING_LIFECYCLE_UPGRADE_THRESHOLD": ("agent/skills_mgmt/lifecycle.py", "skills_mgmt.scale.upgrade_threshold", ("compose", "LEARNING_LIFECYCLE", "_UPGRADE_THRESHOLD")),
        "LEARNING_PRECIPITATE_AUDIT_FILE": ("agent/skills_mgmt/precipitate.py", "learning.precipitate.audit_file", ("compose", "LEARNING_PRECIPITATE", "_AUDIT_FILE")),
        "LEARNING_PRECIPITATE_ENABLED": ("agent/skills_mgmt/precipitate.py", "learning.precipitate_enabled", ("compose", "LEARNING_PRECIPITATE", "_ENABLED")),
        "LEARNING_PRECIPITATE_INTERVAL_HOURS": ("agent/skills_mgmt/precipitate.py", "learning.precipitate.interval_hours", ("compose", "LEARNING_PRECIPITATE", "_INTERVAL_HOURS")),
        "LEARNING_REFLECTION_PERSIST": ("agent/orchestrator/orchestrator.py", "learning.reflection_persist", "LEARNING_REFLECTION_PERSIST"),
        "ORCHESTRATOR_LLM_MIN_CONFIDENCE": ("agent/orchestrator/orchestrator.py", "orchestrator.reject.llm_min_confidence", "ORCHESTRATOR_LLM_MIN_CONFIDENCE"),
        "ORCHESTRATOR_REJECT_ENABLED": ("agent/orchestrator/orchestrator.py", "orchestrator.reject.enabled", "ORCHESTRATOR_REJECT_ENABLED"),
        "ORCHESTRATOR_REJECT_THRESHOLD": ("agent/orchestrator/orchestrator.py", "orchestrator.reject.threshold", "ORCHESTRATOR_REJECT_THRESHOLD"),
        "ORCHESTRATOR_SEMANTIC_LAYER_ENABLED": ("agent/orchestrator/orchestrator.py", "orchestrator.semantic_layer.enabled", "ORCHESTRATOR_SEMANTIC_LAYER_ENABLED"),
        "ORCHESTRATOR_SEMANTIC_MIN_SCORE": ("agent/orchestrator/orchestrator.py", "orchestrator.semantic_layer.min_score", "ORCHESTRATOR_SEMANTIC_MIN_SCORE"),
        "ORCHESTRATOR_WF_LEARN_ENABLED": ("agent/orchestrator/orchestrator.py", "workflow_learning.learn_from_interaction.enabled", "ORCHESTRATOR_WF_LEARN_ENABLED"),
        "ORCHESTRATOR_WORKFLOW_LEARNING_LAYER_ENABLED": ("agent/orchestrator/orchestrator.py", "orchestrator.workflow_learning_layer.enabled", "ORCHESTRATOR_WORKFLOW_LEARNING_LAYER_ENABLED"),
        "ORCHESTRATOR_WORKFLOW_LEARNING_MIN_SCORE": ("agent/orchestrator/orchestrator.py", "orchestrator.workflow_learning_layer.min_score", "ORCHESTRATOR_WORKFLOW_LEARNING_MIN_SCORE"),
        "SKILLS_FUSION_WEIGHT_BM25": ("agent/skills_mgmt/loader.py", "skills_mgmt.retrieval.fusion.weights.bm25", "SKILLS_FUSION_WEIGHT_BM25"),
        "SKILLS_FUSION_WEIGHT_TFIDF": ("agent/skills_mgmt/loader.py", "skills_mgmt.retrieval.fusion.weights.tfidf", "SKILLS_FUSION_WEIGHT_TFIDF"),
        "SKILLS_FUSION_WEIGHT_VECTOR": ("agent/skills_mgmt/loader.py", "skills_mgmt.retrieval.fusion.weights.vector", "SKILLS_FUSION_WEIGHT_VECTOR"),
        "SKILLS_REVIEW_AUDIT_FILE": ("agent/skills_mgmt/review_gate.py", "skills_mgmt.review.audit_file", "SKILLS_REVIEW_AUDIT_FILE"),
        "SKILLS_REVIEW_ENFORCE_PUBLISH": ("agent/skills_mgmt/review_gate.py", "skills_mgmt.review.enforce_before_publish", "SKILLS_REVIEW_ENFORCE_PUBLISH"),
        "SKILL_CLEANUP_ARCHIVED_DAYS": ("agent/skills_mgmt/cleanup_scheduler.py", "skills_mgmt.cleanup.archived_days", ("compose", "SKILL_CLEANUP", "_ARCHIVED_DAYS")),
        "SKILL_CLEANUP_ENABLED": ("agent/skills_mgmt/cleanup_scheduler.py", "skills_mgmt.cleanup.enabled", ("compose", "SKILL_CLEANUP", "_ENABLED")),
        "SKILL_CLEANUP_INTERVAL_HOURS": ("agent/skills_mgmt/cleanup_scheduler.py", "skills_mgmt.cleanup.interval_hours", ("compose", "SKILL_CLEANUP", "_INTERVAL_HOURS")),
        "SKILL_CLEANUP_ORPHANS_DRY_RUN": ("agent/skills_mgmt/cleanup_scheduler.py", "skills_mgmt.cleanup.orphans_dry_run", ("compose", "SKILL_CLEANUP", "_ORPHANS_DRY_RUN")),
        "SKILL_CLEANUP_UNUSED_DAYS": ("agent/skills_mgmt/cleanup_scheduler.py", "skills_mgmt.cleanup.unused_days", ("compose", "SKILL_CLEANUP", "_UNUSED_DAYS")),
        "SKILL_CLEANUP_UNUSED_DRY_RUN": ("agent/skills_mgmt/cleanup_scheduler.py", "skills_mgmt.cleanup.unused_dry_run", ("compose", "SKILL_CLEANUP", "_UNUSED_DRY_RUN")),
}

#: L5 的 5 个动态开关族（路径带**运行时后缀** ⇒ 只能写进 description，
#: 不得填 config_path —— 单值字段填了就是伪造来源）
_L5_DYNAMIC_FAMILIES: tuple = (
    "SKILLS_ASSESS_<KEY>", "SKILLS_DIGEST_<KEY>", "SKILL_CLEANUP_<NAME>",
    "CP_SLO_SCHEDULE_<KEY>", "YUNSHU_FEATURE_<NAME>",
)




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

    # ────────────────────────────────────────────────────────
    #  【2026-09-13 回归】具名集合来源的读取点**必须被披露**，不得静默丢弃
    #
    #  背景（守卫自身的漏洞）：`scan_settings.py::visit_Call` 的分支链是
    #  `if / elif / elif / else`，而第一个分支（"名字来自具名集合"）**只设置
    #  `kind_eff`/`name`、没有调用 `_record`** ⇒ 命中该分支的读取点被整体丢弃：
    #  既不在 `managed`、也不在 `dynamic`、也不在 `passthrough`。
    #  于是 `unregistered_dynamic` 与 `undeclared_passthrough` 都看不见它 ——
    #  **零缺口守卫存在一个静默漏洞**。实测被吞掉的包括**既有的**
    #  `agent/skills_mgmt/executor.py::_ENV_WHITELIST`（它在 PASS_THROUGH_SITES
    #  里躺了很久却从未被命中）与 S8-03 隔离模块的白名单。
    #  修法：该分支改为与相邻分支同款显式 `_record(kind="loop_collection")`；
    #  下列两条用例锁死"能被看见"与"声明必须真的命中"。
    # ────────────────────────────────────────────────────────

    #: 已知的"具名集合来源"读取点（至少这些必须被看见）
    _NAMED_COLLECTION_SITES = (
        "agent/skills_mgmt/executor.py::_ENV_WHITELIST",
        "agent/digestion/isolation.py::HOST_ENV_ALLOWLIST",
        "agent/digestion/isolation_worker.py::ISOLATION_ENV_WATCH",
        "agent/digestion/isolation_worker.py::names",
    )

    def test_named_collection_reads_are_disclosed_not_dropped(self, scan):
        """具名集合来源的读取点必须进 `passthrough`（已声明）或 `dynamic`（未声明）

        判据取"能被看见"这一最弱但最关键的性质：**不得消失**。
        若有人把 `_record` 从这个分支里去掉（回到旧的静默丢弃），本用例立刻变红。
        """
        scanner, report = scan
        gap = scanner.check_gaps(report)
        disclosed = set(gap.passthrough_sites) | set(gap.dynamic_families)
        for site in self._NAMED_COLLECTION_SITES:
            assert site in disclosed, (
                f"具名集合读取点被静默丢弃（既不在 passthrough 也不在 dynamic）：{site}"
                "—— 见 scan_settings.py::visit_Call 的 loop_collection 分支")

    def test_declared_passthrough_sites_are_actually_hit(self, scan):
        """已声明的透传点必须**真的被命中**（防"声明了却永不触发"掩盖漏洞）

        原实现下 `PASS_THROUGH_SITES` 的条目永远不会被命中，而
        `test_declared_exclusions_are_exact_tables` 只检查"声明项有理由"、
        不检查"声明项被命中" ⇒ 一个空转的声明可以掩盖整片缺口。
        本用例补上这一向。
        """
        scanner, report = scan
        gap = scanner.check_gaps(report)
        observed = set(gap.passthrough_sites)
        declared = set(scanner.PASS_THROUGH_SITES)
        stale = sorted(declared - observed)
        assert stale == [], (
            f"已声明但从未被扫描命中的透传点：{stale}"
            "（要么代码已改、声明该删；要么读取点被静默丢弃）")

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


# ════════════════════════════════════════════════════════════
#  四、登记默认值 ↔ 代码事实（回归守护；裁定 D-20260922-01）
# ════════════════════════════════════════════════════════════


class TestRegisteredDefaultsMatchCodeFacts:
    """★ 登记默认值不得与代码事实**相反**（「声明与事实不符」是谎报，不是展示瑕疵）

    背景（2026-09-22 实测）：`SCHEMA_PRUNE_DEPRECATED` / `SCHEMA_PRUNE_ADDITIONAL_PROPS`
    / `FEWSHOT_ENABLED` / `EVOLUTION_ENABLED` / `EVOLUTION_LLM_GENERATE` 五项曾在注册表里
    登记 `False`，而代码真实默认是 `True` ⇒ 开关中心把「已开启」的功能显示成「关闭」。

    为什么改登记值是安全的（不是绕过）：注册表的 `default` **从不写入 os.environ** ——
    实测 `resolve_all()` 之后这几个键都不在 `os.environ` 里；`bootstrap.py` 只遍历
    **覆盖层已存在的键**；全仓唯一把 `spec.default` 落到运行态的是
    `resolver.py::restore_runtime` 的 ObservabilityConfig 回写，它受
    `_is_observability_path(spec)`（config_path 必须命中 `observability_rule_paths()`）
    把关 —— 【L1 更正】`EVOLUTION_ENABLED` / `EVOLUTION_LLM_GENERATE` 此后**已有**
    `config_path`，但两条路径都不在那 48 条里 ⇒ `_is_observability_path` 仍为 False，
    仍不触发（"无 config_path" 不再是这两项的豁免理由）。
    因此该 `default` 只影响**展示与来源判定**（`resolve()` 的 value/display_value），
    必须与代码事实一致。

    判据取**源码里真实的默认表达式**（正则机械提取），而不是再抄一份字面量：
    抄字面量等于把「我以为的默认」断言成「代码的默认」，正是本次缺陷的成因。
    """

    #: key → (归属文件, 提取「该键在代码里的默认值字面量」的正则)
    _CODE_DEFAULT_PATTERNS = {
        "SCHEMA_PRUNE_DEPRECATED": (
            "agent/tool_schema_pruner.py",
            r'SCHEMA_PRUNE_DEPRECATED\s*=\s*_env_bool\(\s*"SCHEMA_PRUNE_DEPRECATED"\s*,\s*(True|False)',
        ),
        "SCHEMA_PRUNE_ADDITIONAL_PROPS": (
            "agent/tool_schema_pruner.py",
            r'SCHEMA_PRUNE_ADDITIONAL_PROPS\s*=\s*_env_bool\(\s*"SCHEMA_PRUNE_ADDITIONAL_PROPS"\s*,\s*(True|False)',
        ),
        "FEWSHOT_ENABLED": (
            "agent/tool_fewshot_store.py",
            r'FEWSHOT_ENABLED\s*=\s*_env_bool\(\s*"FEWSHOT_ENABLED"\s*,\s*(True|False)',
        ),
        "EVOLUTION_ENABLED": (
            "agent/evolution/injector.py",
            r'"enabled":\s*_env_bool\(\s*"EVOLUTION_ENABLED"\s*,\s*'
            r'_yaml_bool\(\s*cfg\s*,\s*"enabled"\s*,\s*(True|False)\s*\)\s*\)',
        ),
        "EVOLUTION_LLM_GENERATE": (
            "agent/evolution/injector.py",
            r'"llm_generate"\s*:\s*_env_bool\(\s*"EVOLUTION_LLM_GENERATE"\s*,\s*'
            r'_yaml_bool\(\s*cfg\s*,\s*"llm_generate"\s*,\s*(True|False)\s*\)\s*\)',
        ),
    }

    def _code_default(self, key):
        """从归属模块源码里机械提取该键的默认值（找不到 → 直接失败，不许空转）"""
        import re

        rel, pattern = self._CODE_DEFAULT_PATTERNS[key]
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        found = re.search(pattern, source)
        assert found is not None, (
            f"{key} 的默认值表达式未能在 {rel} 中提取到（正则已过期或代码已改）："
            "本守护会空转，必须同步修正则，而不是删断言")
        return found.group(1) == "True"

    def test_registered_defaults_are_not_contradicted_by_code(self):
        """★ 五项曾「声明与事实相反」的开关：登记默认值必须等于代码默认值"""
        for key in self._CODE_DEFAULT_PATTERNS:
            spec = R.get_spec(key)
            assert spec is not None, f"注册表缺少 {key}"
            assert spec.default is self._code_default(key), (
                f"{key} 登记默认值 {spec.default!r} 与代码事实 "
                f"{self._code_default(key)!r} 相反（@ {self._CODE_DEFAULT_PATTERNS[key][0]}）")

    def test_probe_table_covers_every_known_offender(self):
        """守护范围必须覆盖裁定 D-20260922-01 列出的全部五项（防「漏一个就绿」）"""
        assert set(self._CODE_DEFAULT_PATTERNS) == {
            "SCHEMA_PRUNE_DEPRECATED", "SCHEMA_PRUNE_ADDITIONAL_PROPS",
            "FEWSHOT_ENABLED", "EVOLUTION_ENABLED", "EVOLUTION_LLM_GENERATE",
        }
        for key, (rel, _pattern) in self._CODE_DEFAULT_PATTERNS.items():
            assert (REPO_ROOT / rel).exists(), (key, rel)


# ════════════════════════════════════════════════════════════
#  五、登记 config_path ↔ 源码真实读取点（L1 守护，2026-09-22）
# ════════════════════════════════════════════════════════════


class TestConfigPathMatchesSourceReadSite:
    """★ L1：登记 config_path 必须等于**源码里那个真实的 config.yaml 读取点**

    背景（立项见 docs/closeout/遗留问题立项_20260922.md 的 L1）：resolver.resolve()
    只在 spec.config_path 非空时才去 config.yaml 取值；登记项漏了路径，开关中心的
    source 就永远只显示 default / env —— config.yaml 里显式写了值也照样显示代码
    默认值。这是**谎报**（面板显示的不是真实生效值），不是展示瑕疵。

    反向同样是谎报：给一个**不读** config.yaml 的键补上 config_path，UI 会声称该值
    来自 config.yaml，而代码里没有任何人读它。故本守护**两个方向都钉**：

      1. **正向**：_CODE_CONFIG_PATH_PATTERNS 里的键，登记路径必须等于从其归属模块
         源码里机械提取的「段 + 叶子」；
      2. **反向**：_NO_CONFIG_READER_KEYS 里的键必须保持 config_path == ""，且其归属
         模块源码里**没有任何** config.yaml 读取。

    判据全部来自源码（正则机械提取），**不另抄一份字面量** —— 抄字面量等于把
    「我以为的路径」断言成「代码的路径」，正是 L1 缺陷的成因（同
    TestRegisteredDefaultsMatchCodeFacts 的纪律）。
    """

    #: novelty_hooks._cfg_value() 里 `learning.sensor_learning` 段的表达式
    #: （sec1=learning、sec2=sensor_learning，由源码提取而非手写）
    _SENSOR_SECTION = (
        r'\(\(cfg\.get\("(?P<sec1>[a-z_]+)",\s*\{\}\)\s*or\s*\{\}\)'
        r'\.get\("(?P<sec2>[a-z_]+)",\s*\{\}\)'
    )

    #: key → (归属文件, 提取式)。正则里的**具名组按出现顺序以 . 连接**即为点分路径。
    #: 每组都同时锚定「该 env 名」与「它落的 config.yaml 键」，故路径被改到别处即红。
    _CODE_CONFIG_PATH_PATTERNS = {
        # ── 进化：injector.get_evolution_config() 读 config.yaml 顶层 evolution: 段 ──
        "EVOLUTION_ENABLED": (
            "agent/evolution/injector.py",
            r'data\.get\("(?P<sec>[a-z_]+)"\)[\s\S]*?'
            r'"enabled":\s*_env_bool\(\s*"EVOLUTION_ENABLED"\s*,\s*'
            r'_yaml_bool\(\s*cfg\s*,\s*"(?P<leaf>[a-z_]+)"',
        ),
        "EVOLUTION_LLM_GENERATE": (
            "agent/evolution/injector.py",
            r'data\.get\("(?P<sec>[a-z_]+)"\)[\s\S]*?'
            r'"llm_generate":\s*_env_bool\(\s*"EVOLUTION_LLM_GENERATE"\s*,\s*'
            r'_yaml_bool\(\s*cfg\s*,\s*"(?P<leaf>[a-z_]+)"',
        ),
        "EVOLUTION_STORAGE_PATH": (
            "agent/evolution/injector.py",
            r'data\.get\("(?P<sec>[a-z_]+)"\)[\s\S]*?'
            r'"storage_path":\s*os\.environ\.get\(\s*"EVOLUTION_STORAGE_PATH"\s*,\s*'
            r'str\(cfg\.get\("(?P<leaf>[a-z_]+)"',
        ),
        # ── 感知侧学习：novelty_hooks._cfg_value() 读 learning.sensor_learning.<key> ──
        "SENSOR_LEARNING_ENABLED": (
            "agent/learning/novelty_hooks.py",
            _SENSOR_SECTION
            + r'[\s\S]*?os\.environ\.get\("SENSOR_LEARNING_ENABLED"\)[\s\S]*?'
            r'_cfg_value\("(?P<leaf>[a-z_]+)"',
        ),
        "SENSOR_LEARNING_DRIFT_THRESHOLD": (
            "agent/learning/novelty_hooks.py",
            _SENSOR_SECTION
            + r'[\s\S]*?os\.environ\.get\("SENSOR_LEARNING_DRIFT_THRESHOLD"\)[\s\S]*?'
            r'float\(_cfg_value\("(?P<leaf>[a-z_]+)"',
        ),
        "SENSOR_LEARNING_BASELINE_RETENTION_WEEKS": (
            "agent/learning/novelty_hooks.py",
            _SENSOR_SECTION
            + r'[\s\S]*?os\.environ\.get\("SENSOR_LEARNING_BASELINE_RETENTION_WEEKS"\)[\s\S]*?'
            r'int\(_cfg_value\("(?P<leaf>[a-z_]+)"',
        ),
        "SENSOR_LEARNING_DRAFT_DIR": (
            "agent/learning/novelty_hooks.py",
            _SENSOR_SECTION
            + r'[\s\S]*?os\.environ\.get\("SENSOR_LEARNING_DRAFT_DIR"\)[\s\S]*?'
            r'_cfg_value\("(?P<leaf>[a-z_]+)"',
        ),
        "SENSOR_LEARNING_AUDIT_FILE": (
            "agent/learning/novelty_hooks.py",
            _SENSOR_SECTION
            + r'[\s\S]*?os\.environ\.get\("SENSOR_LEARNING_AUDIT_FILE"\)[\s\S]*?'
            r'_cfg_value\("(?P<leaf>[a-z_]+)"',
        ),
        "SENSOR_LEARNING_MEMORY_DIR": (
            "agent/learning/novelty_hooks.py",
            _SENSOR_SECTION
            + r'[\s\S]*?os\.environ\.get\("SENSOR_LEARNING_MEMORY_DIR"\)[\s\S]*?'
            r'_cfg_value\("(?P<leaf>[a-z_]+)"',
        ),
    }

    #: 反向守护：这些键的归属模块**不读** config.yaml（FEWSHOT_* 见
    #: agent/tool_fewshot_store.py:31-48 的纯 _env_bool/_env_int；SCHEMA_* 见
    #: agent/tool_schema_pruner.py:63-79 的纯 _env_bool/_env_int），
    #: 故必须保持"仅 env / 默认"，**不得**补 config_path（补了就是制造新的假来源）。
    _NO_CONFIG_READER_KEYS = (
        "FEWSHOT_ENABLED", "FEWSHOT_PER_TOOL", "FEWSHOT_WINDOW_DAYS",
        "FEWSHOT_MAX_INPUT_LEN", "FEWSHOT_MAX_OUTPUT_LEN",
        "SCHEMA_PRUNE_DEPRECATED", "SCHEMA_PRUNE_ADDITIONAL_PROPS",
        "SCHEMA_DESC_MAX_LEN", "SCHEMA_PROP_DESC_MAX_LEN",
    )

    #: 反向判据：归属模块源码里出现下列任一，即视为"该模块会读 config.yaml"
    _CONFIG_READER_MARKERS = (
        r"config\.yaml", r"yaml\.safe_load", r"yaml\.load",
    )

    #: 「声明了 config.yaml 文件路径」的完整清单（= 全部非 ObservabilityConfig
    #: 运行态路径的 config_path）。其中 12 项在 L1 之前就登记了，L1 只保证它们不被
    #: **悄悄**增删；另外 9 项是 L1 依据读取点补上的。
    #: 新增 / 删除一个"受 config.yaml 驱动"的开关时必须同步本清单，
    #: 并为其在 _CODE_CONFIG_PATH_PATTERNS 里加一条源码提取式。
    _DECLARED_FILE_CONFIG_PATH_KEYS = frozenset({
        # L1 之前既有（12）
        "AUTONOMY_DEFAULT_LEVEL",
        "CP_RETENTION_CLASSES", "CP_RETENTION_DAY_OF_WEEK",
        "CP_RETENTION_DELETE_SOURCE", "CP_RETENTION_DRY_RUN",
        "CP_RETENTION_ENABLED", "CP_RETENTION_HOUR", "CP_RETENTION_MINUTE",
        "CP_SLO_SCHEDULE_ENABLED",
        "PLANNING_WIRE_ENABLED", "PLANNING_WIRE_MIN_COMPLEXITY",
        "PLANNING_WIRE_TIMEOUT_SECONDS",
        # L1 依据读取点补登记（9）
        "EVOLUTION_ENABLED", "EVOLUTION_LLM_GENERATE", "EVOLUTION_STORAGE_PATH",
        "SENSOR_LEARNING_ENABLED", "SENSOR_LEARNING_DRIFT_THRESHOLD",
        "SENSOR_LEARNING_BASELINE_RETENTION_WEEKS", "SENSOR_LEARNING_DRAFT_DIR",
        "SENSOR_LEARNING_AUDIT_FILE", "SENSOR_LEARNING_MEMORY_DIR",
        # L5 依据读取点补登记（50）—— 逐键取证见本文件顶部的 _L5_READ_SITES
    } | frozenset(_L5_READ_SITES))

    # ── 机械提取 ──

    def _source_config_path(self, key):
        """从归属模块源码里提取该键真实读的 config.yaml 点分路径

        找不到匹配 → **直接失败**（正则过期 / 代码已改），不许静默放行：
        一个会空转的守护比没有守护更危险。
        """
        import re

        rel, pattern = self._CODE_CONFIG_PATH_PATTERNS[key]
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        compiled = re.compile(pattern)
        found = compiled.search(source)
        assert found is not None, (
            f"{key} 的 config.yaml 读取点未能在 {rel} 中提取到"
            "（正则已过期或读取代码已改）：本守护会空转，"
            "必须同步正则/路径，而不是删断言")
        order = [name for name, _idx in
                 sorted(compiled.groupindex.items(), key=lambda kv: kv[1])]
        parts = [found.group(name) for name in order]
        assert all(parts), (key, parts)
        return ".".join(parts)

    def _reads_config_yaml(self, rel):
        """归属模块源码里是否存在 config.yaml 读取"""
        import re

        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        return any(re.search(marker, source)
                   for marker in self._CONFIG_READER_MARKERS)

    # ── 正向：登记路径 == 源码读取点 ──

    def test_registered_config_path_matches_source_read_site(self):
        """★ L1 核心：登记路径必须与源码读取点逐字一致（漂移即红）"""
        for key in self._CODE_CONFIG_PATH_PATTERNS:
            spec = R.get_spec(key)
            assert spec is not None, f"注册表缺少 {key}"
            expected = self._source_config_path(key)
            assert spec.config_path == expected, (
                f"{key} 登记 config_path={spec.config_path!r} 与源码读取点 "
                f"{expected!r} 不一致（@ {self._CODE_CONFIG_PATH_PATTERNS[key][0]}）："
                "路径漂移会让开关中心谎报来源")

    def test_probe_table_covers_every_l1_key(self):
        """守护范围必须覆盖 L1 认定的全部 9 项（防「漏一个就绿」）"""
        assert set(self._CODE_CONFIG_PATH_PATTERNS) == {
            "EVOLUTION_ENABLED", "EVOLUTION_LLM_GENERATE",
            "EVOLUTION_STORAGE_PATH",
            "SENSOR_LEARNING_ENABLED", "SENSOR_LEARNING_DRIFT_THRESHOLD",
            "SENSOR_LEARNING_BASELINE_RETENTION_WEEKS",
            "SENSOR_LEARNING_DRAFT_DIR", "SENSOR_LEARNING_AUDIT_FILE",
            "SENSOR_LEARNING_MEMORY_DIR",
        }
        for key, (rel, _pattern) in self._CODE_CONFIG_PATH_PATTERNS.items():
            assert (REPO_ROOT / rel).exists(), (key, rel)

    # ── 反向：不读 config.yaml 的键不得被"补"出假来源 ──

    def test_keys_without_config_reader_keep_empty_config_path(self):
        """★ 不读 config.yaml 的键必须保持 config_path == ""（两个方向都钉）"""
        for key in self._NO_CONFIG_READER_KEYS:
            spec = R.get_spec(key)
            assert spec is not None, f"注册表缺少 {key}"
            assert not self._reads_config_yaml(spec.owner_module), (
                f"{key} 的归属模块 {spec.owner_module} 现在**会**读 config.yaml："
                "本反向表已过期，须重新核实该键的 config_path，"
                "而不是让它继续留在「不读 config」表里")
            assert spec.config_path == "", (
                f"{key} 被补上了 config_path={spec.config_path!r}，但其归属模块 "
                f"{spec.owner_module} 里没有任何 config.yaml 读取 —— "
                "这是新的假来源（UI 会声称值来自 config.yaml）")

    # ── 读取点唯一性：env 名只能有一个读取模块 ──

    def test_config_backed_env_names_have_one_reading_module(self):
        """★ 登记路径的前提是"该值只有这一个读取模块"，否则单一路径仍是谎报"""
        import re

        keys = set(self._CODE_CONFIG_PATH_PATTERNS)
        hits = {key: set() for key in keys}
        pattern = re.compile(r'"(' + "|".join(sorted(keys)) + r')"')
        for path in sorted((REPO_ROOT / "agent").rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            if rel.startswith("agent/settings/"):
                continue          # 注册表/解析器自身，不是读取点
            found = set(pattern.findall(path.read_text(encoding="utf-8",
                                                        errors="ignore")))
            for key in found:
                hits[key].add(rel)
        for key in sorted(keys):
            owner = self._CODE_CONFIG_PATH_PATTERNS[key][0]
            assert hits[key] == {owner}, (
                f"{key} 的读取点不止 {owner}：实测 {sorted(hits[key])}；"
                "登记的单一 config_path 无法代表全部读取点，须重新核实")

    # ── 文件型 config_path 的完整清单（防悄悄增删）──

    def test_declared_file_config_paths_are_exactly_this_inventory(self):
        """声明了 config.yaml 文件路径的键集合必须与本清单**逐字**相等

        注意：ObservabilityConfig 的运行态路径（observability_rule_paths() 的 48 条）
        由 TestObservabilityRuleMerge 守护，不在本清单里。
        """
        actual = {spec.key for spec in R.all_specs()
                  if spec.config_path
                  and spec.config_path not in R.observability_rule_paths()}
        assert actual == set(self._DECLARED_FILE_CONFIG_PATH_KEYS), (
            "声明 config.yaml 文件路径的开关集合变了："
            f"新增={sorted(actual - set(self._DECLARED_FILE_CONFIG_PATH_KEYS))} "
            f"消失={sorted(set(self._DECLARED_FILE_CONFIG_PATH_KEYS) - actual)}；"
            "新增一项必须在 _CODE_CONFIG_PATH_PATTERNS（或 L5 的 _L5_READ_SITES）"
            "里补一条**源码提取式**（不许只手写路径），消失一项须说明原因")

# ════════════════════════════════════════════════════════════
#  六、L5：50 个定值路径键的逐键机械核对 + 登记只影响显示（2026-09-23）
# ════════════════════════════════════════════════════════════


def _production_scan():
    """整仓**生产根**扫一次（模块级缓存；只读，无副作用）

    扫描根取自 scripts/scan_settings.py::DEFAULT_ROOTS（与本文件的零缺口守卫同源），
    因此「这些键只有一个读取模块」的判据与机械提取器的口径**永远一致**。
    """
    cached = getattr(_production_scan, "_cache", None)
    if cached is None:
        scanner = _load_scanner()
        report = scanner.scan_paths(
            [REPO_ROOT / p for p in scanner.DEFAULT_ROOTS], REPO_ROOT)
        cached = (scanner, report)
        _production_scan._cache = cached
    return cached


class TestL5ReadSitesAreMechanicallyVerified:
    """★ L5：补登记的 config_path 必须能在**归属模块源码**里机械复核

    审计（docs/closeout/开关登记表_config口径审计_20260922.md）认定 55 个键
    「模块确实读 config.yaml，而登记表 config_path 为空」。L1 的口径纪律是
    「逐键核实读取点，**禁止批量补**」—— 本类把这句纪律变成可执行判据：

      1. 登记路径 / 归属模块与取证表逐字一致（防手滑、防事后漂移）；
      2. 归属模块源码里**真的读到**该 env 名（字面量，或前缀常量 + 后缀字面量）；
      3. 点分路径的**每一段**都以字面量出现在归属模块里，且该模块确实读 config.yaml
         （否则这个路径是编的 —— 那是**新的假来源**，比漏登记更坏）；
      4. 该 env 名在生产根里**只有一个读取模块**（多模块 ⇒ 单一路径表达不了全部
         读取点，登记即部分谎报）。
    """

    def test_table_covers_every_l5_key(self):
        assert len(_L5_READ_SITES) == 50, (
            "L5 的 50 个定值路径键必须逐个取证（漏一个 = 守护空转）："
            f"实测 {len(_L5_READ_SITES)}")
        for key in _L5_READ_SITES:
            assert R.get_spec(key) is not None, f"注册表缺少 {key}"

    def test_registered_path_and_owner_match_the_table(self):
        for key, (module, path, _env) in sorted(_L5_READ_SITES.items()):
            spec = R.get_spec(key)
            assert spec.owner_module == module, (key, spec.owner_module, module)
            assert spec.config_path == path, (
                f"{key} 登记 config_path={spec.config_path!r} 与取证表 {path!r} 不一致")

    def test_owner_module_reads_its_env_name(self):
        for key, (module, _path, env) in sorted(_L5_READ_SITES.items()):
            source = (REPO_ROOT / module).read_text(encoding="utf-8")
            if isinstance(env, tuple):
                _tag, prefix, suffix = env
                assert prefix in source, (
                    f"{key} 的 env 名前缀常量 {prefix!r} 不在 {module} 里")
                assert suffix.lstrip("_") in source, (
                    f"{key} 的 env 名后缀 {suffix!r} 不在 {module} 里："
                    "前缀拼接形态已改，须同步取证表")
            else:
                assert env in source, (
                    f"{key} 的读取点已不在 {module}（env 名字面量消失）："
                    "补登记的路径失去依据，必须重新核实而不是留着")

    def test_declared_path_segments_are_source_literals(self):
        for key, (module, path, _env) in sorted(_L5_READ_SITES.items()):
            source = (REPO_ROOT / module).read_text(encoding="utf-8")
            assert "config.yaml" in source, (
                f"{key} 的归属模块 {module} 不读 config.yaml ⇒ 登记路径即假来源")
            for part in path.split("."):
                assert (f'"{part}"' in source) or (f"'{part}'" in source), (
                    f"{key} 的路径段 {part!r} 不在 {module} 里："
                    f"声明的 {path!r} 无法在源码里复核")

    def test_single_production_module_reads_each_env_name(self):
        _scanner, report = _production_scan()
        names = report.managed_names()
        for key, (module, _path, _env) in sorted(_L5_READ_SITES.items()):
            points = names.get(key) or []
            modules = sorted({p.module for p in points})
            assert modules == [module], (
                f"{key} 的生产读取模块不止 {module}：实测 {modules}；"
                "单一 config_path 表达不了多个读取点，须重新核实")


class TestDynamicFamiliesDeclarePathsInDescription:
    """★ L5 的动态族：路径模板只能写进 description，config_path 必须为空

    动态族的 config 路径带**运行时后缀**（如 skills_mgmt.assess.<key>），
    而 SettingSpec.config_path 是**单值字段**：填任何固定路径都是**伪造来源**
    （UI 会声称值来自那个路径，而代码从不读它）。故这 5 条双向钉死。
    """

    def test_registry_declares_exactly_five_families(self):
        declared = {s.key for s in R.all_specs() if s.dynamic_prefix}
        assert declared == set(_L5_DYNAMIC_FAMILIES), (
            f"动态族清单变了：{sorted(declared)}")

    def test_each_family_keeps_config_path_empty(self):
        for key in _L5_DYNAMIC_FAMILIES:
            spec = R.get_spec(key)
            assert spec is not None and spec.dynamic_prefix, key
            assert spec.config_path == "", (
                f"{key} 的路径带运行时后缀，config_path 是单值字段："
                "填固定路径即伪造来源")

    def test_each_family_documents_path_template_and_reason(self):
        for key in _L5_DYNAMIC_FAMILIES:
            spec = R.get_spec(key)
            text = spec.description or ""
            assert "config_path 为空" in text, (
                f"{key} 的 description 必须写明「路径模板 + 为什么留空」：{text!r}")
            assert re.search(r"[a-z_]+[.][a-z_]+", text), (
                f"{key} 的 description 里看不到点分路径模板：{text!r}")


class TestConfigPathDrivesDisplayNotRuntime:
    """★ L5 的**逐键对拍**：补登记只改「显示来源」，不改任何模块的生效值

    对拍两条（逐键，不是抽样）：
      ① 配置层**不提供**该路径时，resolve() 的来源必须是 default
         —— 与补登记**前**的行为完全一致（补登记不凭空改变生效值）；
      ② 配置层**提供**该路径时，来源变为 config，且取值逐字等于配置里的值
         —— 与模块自己读 config.yaml 的结果一致（修好之后不再谎报）。

    生效值不变的结构性证据（第 3 条用例）：这些键的归属模块**从不 import
    agent.settings**（也不引用 spec.config_path / SettingSpec），因此登记表里
    多一个 config_path 在物理上无法影响模块的取值路径。

    本用例组**不写 data/**：覆盖层指向 tmp_path 里的空 store；
    config.yaml 只取 mtime，取值来自内存合成的 dict（见 _with_synthetic_config）。
    """

    @pytest.fixture(autouse=True)
    def _restore_resolver_cache(self):
        from agent.settings import resolver as RS

        saved = dict(RS._CONFIG_CACHE)
        yield
        RS._CONFIG_CACHE.clear()
        RS._CONFIG_CACHE.update(saved)

    @staticmethod
    def _store(tmp_path):
        from agent.settings.overrides import OverrideStore

        return OverrideStore(tmp_path / "ui_settings.json")

    @staticmethod
    def _with_synthetic_config(pairs):
        """把合成配置塞进 resolver 的 mtime 缓存（**不碰磁盘上的 config.yaml**）"""
        from agent.settings import resolver as RS

        data = {}
        for dotted, value in pairs.items():
            node = data
            parts = dotted.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
            node[parts[-1]] = value
        path = RS.config_yaml_path()
        try:
            mtime = path.stat().st_mtime if path.exists() else -1.0
        except OSError:
            mtime = -1.0
        RS._CONFIG_CACHE["mtime"] = mtime
        RS._CONFIG_CACHE["data"] = data
        return RS

    @staticmethod
    def _probe(spec):
        if spec.type == "bool":
            return (not spec.default) if isinstance(spec.default, bool) else True
        if spec.type == "int":
            return 4242
        if spec.type == "float":
            return 42.5
        return f"parity-probe:{spec.key}"

    @classmethod
    def _declared(cls):
        obs = R.observability_rule_paths()
        return {s.key: s for s in R.all_specs()
                if s.config_path and s.config_path not in obs}

    def test_declared_paths_are_not_empty(self):
        declared = self._declared()
        assert len(declared) >= 71, (
            f"声明了 config.yaml 文件路径的键只有 {len(declared)} 个")

    def test_no_declared_key_is_env_pinned_here(self):
        """★ 对拍用例的 SKIP 分支必须**真的没被走到**（否则逐键对拍等于空转）"""
        pinned = sorted(k for k, s in self._declared().items()
                        if s.env_name and s.env_name in os.environ)
        assert pinned == [], (
            f"本进程环境里已设置这些开关的 env：{pinned} —— "
            "此时 env 层遮蔽 config 层，逐键对拍无意义（须在干净环境跑）")

    def test_every_declared_path_flips_default_to_config(self, tmp_path):
        from agent.settings import masking

        store = self._store(tmp_path)
        for key, spec in sorted(self._declared().items()):
            probe = self._probe(spec)
            RS = self._with_synthetic_config({})
            before = RS.resolve(key, store=store)
            assert before is not None and before.source == RS.SOURCE_DEFAULT, (
                f"{key} 在配置层缺位时来源不是 default：{before and before.source}")
            if spec.risk == R.RISK_C:
                # C 级不回明文（value 被抹成 None）⇒ 用**指纹**证明取值等于默认值。
                # 注意用 mask_display(...) 而不是 fingerprint(...)：default 为 None 时
                # 前者的指纹是空串（未配置），后者会给 None 算出一个假指纹。
                expected_fp = masking.mask_display(spec.default)["fingerprint"]
                assert before.fingerprint == expected_fp, (key, before.fingerprint)
            else:
                assert before.value == spec.default, (key, before.value, spec.default)
            RS = self._with_synthetic_config({spec.config_path: probe})
            after = RS.resolve(key, store=store)
            assert after.source == RS.SOURCE_CONFIG, (
                f"{key} 在配置层提供 {spec.config_path} 时来源不是 config：{after.source}")
            if spec.risk == R.RISK_C:
                # C 级按设计不回明文（resolver 把 value 抹成 None）⇒ 用指纹比对
                assert after.fingerprint == masking.fingerprint(probe), key
            else:
                assert after.value == probe, (key, after.value, probe)

    def test_owner_modules_do_not_consume_the_registry(self):
        """★ 生效值不变的结构性证据：归属模块与开关注册表**零耦合**"""
        markers = ("agent.settings", "settings.registry", "spec.config_path",
                   "SettingSpec")
        owners = {}
        for key, spec in self._declared().items():
            owners.setdefault(spec.owner_module, []).append(key)
        assert owners, "没有任何键声明 config.yaml 文件路径？"
        for module, keys in sorted(owners.items()):
            source = (REPO_ROOT / module).read_text(encoding="utf-8")
            hits = [m for m in markers if m in source]
            assert hits == [], (
                f"{module}（{len(keys)} 个键的归属模块）现在引用了开关注册表 {hits}："
                "「补登记只影响显示」的前提不再成立，必须重新做逐键对拍")

    def test_resolve_all_does_not_write_environment(self, tmp_path):
        """resolve_all() 不得把登记值写进 os.environ（否则登记值会**真的**生效）"""
        from agent.settings import resolver as RS

        before = dict(os.environ)
        self._with_synthetic_config({})
        RS.resolve_all(store=self._store(tmp_path))
        assert dict(os.environ) == before, "resolve_all() 改动了进程环境变量"


