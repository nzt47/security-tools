"""TASK-S7-02 策略与范围护栏 单元测试

覆盖（任务书 §四 验收清单）：
- **只读区改动被拒**（逐条对着 §一 边界 ② 的清单：``core/auth/`` / ``core/audit/`` /
  ``schema/`` / ``agent/audit/chain.py`` / ``agent/security/`` / ``eval/l0_anchor/``）
- **文件数/行数超限被拒**
- 改测试断言时被**机械识别**（PR 描述据此强制标注）
- 阈值可经环境变量覆盖，**非法值回退默认**并留 note
- 解析失败的补丁 **fail-closed**（不放行）
"""

from __future__ import annotations

import pytest

from repair_fixtures import simple_diff

from agent.repair import guardrails as G
from agent.repair import policy as P
from agent.repair.policy import (
    DEFAULT_MAX_CHANGED_FILES,
    DEFAULT_MAX_LINES_PER_FILE,
    READONLY_ZONE_FILES,
    READONLY_ZONE_PREFIXES,
    RepairPolicy,
    is_readonly_path,
    normalize_relpath,
    policy_from_env,
    readonly_reason,
)


# ════════════════════════════════════════════════════════════
#  只读区
# ════════════════════════════════════════════════════════════


class TestReadonlyZones:
    @pytest.mark.parametrize("path", [
        "core/auth/login.py",
        "core/auth/",
        "core/audit/chain.py",
        "schema/users.sql",
        "agent/security/actor_matrix.py",
        "agent/audit/chain.py",
        "eval/l0_anchor/cases.json",
        "eval/l0_anchor/manifest.json",
    ])
    def test_declared_zones_are_readonly(self, path):
        assert is_readonly_path(path) is True, f"{path} 应在只读区"

    @pytest.mark.parametrize("path", [
        "agent/repair/delegate.py",
        "agent/audit/facade.py",          # 同目录但**允许**作为调用方
        "agent/subagent/delegation.py",
        "tests/unit/test_repair_guards.py",
        "core/runtime/engine.py",
        "eval/l1_min/cases.json",         # L1 不在 L0 锚只读区内
    ])
    def test_non_zones_are_writable(self, path):
        assert is_readonly_path(path) is False, f"{path} 不应被判为只读区"

    def test_audit_signing_name_patterns_fail_closed(self):
        """审计/签名相关**文件名模式**命中即拒（未登记 ≠ 允许）"""
        for path in ("agent/x/audit_chain.py", "a/b/roots_signer.py",
                     "agent/foo/signing.py", "x/merkle.py"):
            assert is_readonly_path(path) is True

    def test_reason_is_human_readable(self):
        assert "只读区" in readonly_reason("core/auth/a.py")
        assert "审计链" in readonly_reason("agent/audit/chain.py")
        assert readonly_reason("agent/repair/policy.py") == ""

    def test_normalize_relpath_handles_diff_prefixes(self):
        assert normalize_relpath("b/agent/repair/policy.py") == "agent/repair/policy.py"
        assert normalize_relpath("a\\core\\auth\\x.py") == "core/auth/x.py"
        assert normalize_relpath('"./schema/a.sql"') == "schema/a.sql"

    def test_zone_lists_are_non_empty_and_documented(self):
        assert "core/auth/" in READONLY_ZONE_PREFIXES
        assert "core/audit/" in READONLY_ZONE_PREFIXES
        assert "schema/" in READONLY_ZONE_PREFIXES
        assert "agent/security/" in READONLY_ZONE_PREFIXES
        assert "agent/audit/chain.py" in READONLY_ZONE_FILES


# ════════════════════════════════════════════════════════════
#  策略与非法值回退
# ════════════════════════════════════════════════════════════


class TestPolicy:
    def test_defaults_match_spec(self):
        policy = policy_from_env({})
        assert policy.max_changed_files == DEFAULT_MAX_CHANGED_FILES == 3
        assert policy.max_lines_per_file == DEFAULT_MAX_LINES_PER_FILE == 120
        assert policy.notes == ()

    def test_env_override(self):
        policy = policy_from_env({"CP_REPAIR_MAX_CHANGED_FILES": "5",
                                  "CP_REPAIR_MAX_LINES_PER_FILE": "10",
                                  "CP_REPAIR_MAX_ROUNDS": "4"})
        assert policy.max_changed_files == 5
        assert policy.max_lines_per_file == 10
        assert policy.max_rounds == 4

    @pytest.mark.parametrize("value", ["abc", "", "1.5", "-3", "0"])
    def test_invalid_values_fall_back_with_note(self, value):
        policy = policy_from_env({"CP_REPAIR_MAX_CHANGED_FILES": value})
        assert policy.max_changed_files == DEFAULT_MAX_CHANGED_FILES
        if value.strip() == "":
            assert policy.notes == ()
        else:
            assert any("MAX_CHANGED_FILES" in n for n in policy.notes)

    def test_env_does_not_leak_into_process_env(self):
        import os
        before = {k: v for k, v in os.environ.items() if k.startswith(P.ENV_PREFIX)}
        policy_from_env({"CP_REPAIR_BUDGET_TOKENS": "123"})
        after = {k: v for k, v in os.environ.items() if k.startswith(P.ENV_PREFIX)}
        assert before == after

    def test_case_insensitive_env_lookup(self):
        assert policy_from_env({"cp_repair_max_rounds": "7"}).max_rounds == 7

    def test_to_dict_roundtrip_fields(self):
        payload = RepairPolicy().to_dict()
        for key in ("max_changed_files", "max_lines_per_file", "budget_tokens",
                    "max_rounds", "timeout_seconds", "slice_radius"):
            assert key in payload


# ════════════════════════════════════════════════════════════
#  统一 diff 解析
# ════════════════════════════════════════════════════════════


class TestParseUnifiedDiff:
    def test_simple_diff(self):
        parsed, error = G.parse_unified_diff(simple_diff())
        assert error == ""
        assert len(parsed) == 1
        assert parsed[0].path == "agent/demo_math.py"
        assert parsed[0].added == 1 and parsed[0].removed == 1
        assert len(parsed[0].hunks) == 1
        assert parsed[0].hunks[0].old_start == 1

    def test_new_file(self):
        diff = ("diff --git a/agent/new.py b/agent/new.py\n"
                "new file mode 100644\n"
                "index 0000000..1111111\n"
                "--- /dev/null\n"
                "+++ b/agent/new.py\n"
                "@@ -0,0 +1,2 @@\n"
                "+def f():\n"
                "+    return 1\n")
        parsed, error = G.parse_unified_diff(diff)
        assert error == ""
        assert parsed[0].is_new is True
        assert parsed[0].added == 2

    def test_deleted_file(self):
        diff = ("diff --git a/agent/gone.py b/agent/gone.py\n"
                "deleted file mode 100644\n"
                "--- a/agent/gone.py\n"
                "+++ /dev/null\n"
                "@@ -1,2 +0,0 @@\n"
                "-def f():\n"
                "-    return 1\n")
        parsed, error = G.parse_unified_diff(diff)
        assert error == ""
        assert parsed[0].is_deleted is True
        assert parsed[0].removed == 2

    def test_empty_diff(self):
        parsed, error = G.parse_unified_diff("   \n")
        assert parsed == []
        assert "空补丁" in error

    def test_garbage_diff_is_rejected(self):
        parsed, error = G.parse_unified_diff("这不是 diff\n随便几行\n")
        assert error
        report = G.guard_patch("这不是 diff\n随便几行\n")
        assert report.ok is False
        assert report.parse_error

    def test_no_newline_marker_does_not_break_counts(self):
        diff = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
                "@@ -1 +1 @@\n-old\n+new\n\\ No newline at end of file\n")
        parsed, error = G.parse_unified_diff(diff)
        assert error == ""
        assert parsed[0].added == 1 and parsed[0].removed == 1


# ════════════════════════════════════════════════════════════
#  护栏硬闸
# ════════════════════════════════════════════════════════════


class TestGuardPatch:
    def test_clean_patch_passes(self):
        report = G.guard_patch(simple_diff())
        assert report.ok is True
        assert report.changed_files == 1
        assert report.violations == []

    def test_readonly_zone_rejected(self):
        report = G.guard_patch(simple_diff("agent/audit/chain.py"))
        assert report.ok is False
        assert "agent/audit/chain.py" in report.readonly_hits
        assert any("只读区" in v for v in report.violations)

    def test_schema_and_security_rejected(self):
        for path in ("schema/users.sql", "agent/security/actor_matrix.py",
                     "core/auth/token.py", "eval/l0_anchor/cases.json"):
            report = G.guard_patch(simple_diff(path))
            assert report.ok is False, f"{path} 应被拒"
            assert report.readonly_hits

    def test_too_many_files_rejected(self):
        diff = "".join(simple_diff(f"agent/m{i}.py") for i in range(4))
        report = G.guard_patch(diff)
        assert report.changed_files == 4
        assert report.ok is False
        assert any("改动文件数超限" in v for v in report.violations)

    def test_file_count_at_limit_passes(self):
        diff = "".join(simple_diff(f"agent/m{i}.py") for i in range(3))
        report = G.guard_patch(diff)
        assert report.changed_files == 3
        assert report.ok is True

    def test_too_many_lines_rejected(self):
        added = "".join(f"+line{i}\n" for i in range(200))
        diff = ("diff --git a/agent/big.py b/agent/big.py\n--- a/agent/big.py\n"
                "+++ b/agent/big.py\n@@ -1,1 +1,201 @@\n-old\n" + added)
        report = G.guard_patch(diff)
        assert report.max_file_changed_lines == 201
        assert report.ok is False
        assert any("单文件改动行数超限" in v for v in report.violations)

    def test_policy_thresholds_are_honored(self):
        diff = "".join(simple_diff(f"agent/m{i}.py") for i in range(3))
        strict = RepairPolicy(max_changed_files=2)
        assert G.guard_patch(diff, policy=strict).ok is False
        assert G.guard_patch(diff, policy=RepairPolicy(max_changed_files=3)).ok is True

    def test_test_assertion_change_is_flagged_not_rejected(self):
        """改测试断言**不拒**，但必须被机械识别（供 PR 描述强制标注）"""
        report = G.guard_patch(simple_diff("tests/unit/test_demo_math.py"))
        assert report.ok is True
        assert report.test_assertions_touched == ["tests/unit/test_demo_math.py"]
        assert report.files[0].is_test is True

    def test_test_change_plus_readonly_still_rejected(self):
        diff = (simple_diff("tests/unit/test_x.py") + simple_diff("core/audit/ledger.py"))
        report = G.guard_patch(diff)
        assert report.ok is False
        assert report.test_assertions_touched == ["tests/unit/test_x.py"]

    def test_only_readonly_detected_per_file(self):
        diff = (simple_diff("agent/repair/policy.py") + simple_diff("agent/security/x.py"))
        report = G.guard_patch(diff)
        by_path = {f.path: f for f in report.files}
        assert by_path["agent/repair/policy.py"].readonly_hit is False
        assert by_path["agent/security/x.py"].readonly_hit is True

    def test_markdown_report_mentions_test_change(self):
        report = G.guard_patch(simple_diff("tests/unit/test_demo_math.py"))
        md = G.guard_report_markdown(report)
        assert "修改了测试文件" in md
        assert "人工重点审" in md

    def test_markdown_report_lists_violations(self):
        report = G.guard_patch(simple_diff("core/audit/a.py"))
        md = G.guard_report_markdown(report)
        assert "拒绝" in md
        assert "只读区" in md
