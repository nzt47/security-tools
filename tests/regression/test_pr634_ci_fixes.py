"""PR #634 CI 6 项失败修复点回归测试

【用途】验证 2026-08-14 对 PR #634 CI 6 项 FAILURE 的修复均已落地且不引入新问题。
  每个修复点对应一个测试类；测试为静态/结构性验证（不依赖真实 CI 环境），
  本地 pytest 即可运行。

修复点清单（见 docs/pr_conflict_resolution.md 与 CI 失败日志）：
  1. Pact 契约测试：test_status_queries_identical 剔除时序字段 time_since_last_state_change
  2. Docker tag：l3-docker-tests.yml meta step 清洗 PR ref（refs/pull/N/merge → PR-N）
  3. 覆盖率分析：连锁失败（上游 build-image 产物缺失），修复点 2 后自动恢复
  4. 集成测试：e2e mock 补 _interaction_lock（process() 持锁递增 _interaction_count）
  5. 边界覆盖检查：boundary_config.yaml 声明 self_healing 模块 + 边界测试补齐
  6. kwarg 扫描 HIGH：test_reviewer.py 5 处 make_skill(skill_id=..., **base) 改为 dict literal 合并
  7. unit 孪生时序断言：test_task3_breaker_degrade_unify.py test_status_queries_consistent
     同剔除 time_since_last_state_change（CI Shard 4/6 失败）
  8. 缺 import pytest：test_planning_defect_d7.py 使用 @pytest.mark.xfail 但未导入
     pytest，导致 CI 分片收集 NameError（CI Shard 5/6 失败）
  9. Docker 镜像验证步骤 ENTRYPOINT 覆盖：docker run IMAGE python -c 被镜像
     ENTRYPOINT(pytest) 追加参数导致 "file or directory not found: python"（exit 4），
     需 --entrypoint python 覆盖（CI L3 Docker 构建验证失败）

运行方式：
  python -m pytest tests/regression/test_pr634_ci_fixes.py -v
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ═══════════════════════════════════════════════════════════════
#  修复点 2：Docker tag 清洗逻辑
# ═══════════════════════════════════════════════════════════════

class TestDockerTagSedLogic:
    """l3-docker-tests.yml 镜像标签生成逻辑（修复点 2）"""

    WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "l3-docker-tests.yml"

    def test_meta_step_cleans_pr_ref(self):
        """meta step 必须同时清洗 refs/heads/ 与 refs/pull/ 前缀"""
        text = self.WORKFLOW.read_text(encoding="utf-8")
        assert "refs/heads/||" in text, "缺少 refs/heads/ 前缀清洗"
        assert "refs/pull/" in text, "缺少 PR ref 清洗，PR 事件将生成非法 tag"
        assert "s|/|-|g" in text, "缺少残留 '/' 兜底替换"

    @pytest.mark.parametrize(
        "ref, expected_prefix",
        [
            ("refs/heads/master", "master"),
            ("refs/heads/develop", "develop"),
            ("refs/pull/634/merge", "PR-634"),
        ],
    )
    def test_sed_output_is_valid_docker_tag(self, ref, expected_prefix):
        """模拟 sed 清洗后 tag 前缀符合 Docker 命名规范（不含 '/'）"""
        import re

        # 与 workflow 中 sed 表达式等价：python 复刻（测试用，非生产逻辑）
        cleaned = re.sub(r"refs/heads/", "", ref)
        cleaned = re.sub(r"refs/pull/(\d+)/merge", r"PR-\1", cleaned)
        cleaned = cleaned.replace("/", "-")
        assert cleaned == expected_prefix
        assert "/" not in cleaned, "Docker tag 不允许含 '/'"


# ═══════════════════════════════════════════════════════════════
#  修复点 5：边界覆盖 self_healing 模块
# ═══════════════════════════════════════════════════════════════

class TestBoundarySelfHealing:
    """boundary_config.yaml 声明 self_healing + 边界测试补齐（修复点 5）"""

    CONFIG = PROJECT_ROOT / "tests" / "boundary_config.yaml"
    POLICY_TEST = PROJECT_ROOT / "tests" / "unit" / "test_self_healing_policy.py"

    def test_config_declares_self_healing(self):
        """config modules 必须声明 self_healing"""
        cfg = yaml.safe_load(self.CONFIG.read_text(encoding="utf-8"))
        modules = cfg["modules"]
        assert "self_healing" in modules, "boundary_config.yaml 缺少 self_healing 模块声明"
        assert set(modules["self_healing"]["required_scenes"]) >= {"empty", "invalid", "timeout"}
        assert modules["self_healing"]["min_tests"] >= 3

    def test_boundary_tests_exist(self):
        """测试函数名须含边界关键词 empty/invalid/timeout（边界扫描按函数名识别）"""
        text = self.POLICY_TEST.read_text(encoding="utf-8")
        assert "def test_get_domain_for_alert_empty(" in text
        assert "def test_get_actions_for_alert_invalid(" in text
        assert "def test_timeout_alert_maps_to_llm_timeout_domain(" in text


# ═══════════════════════════════════════════════════════════════
#  修复点 1：Pact 契约时序字段
# ═══════════════════════════════════════════════════════════════

class TestPactTimingField:
    """契约测试剔除时序字段（修复点 1）"""

    CONTRACT_TEST = PROJECT_ROOT / "tests" / "contract" / "test_contract_breaker_single_source.py"

    def test_strip_timing_in_comparison(self):
        """test_status_queries_identical 须剔除 time_since_last_state_change 后比较"""
        text = self.CONTRACT_TEST.read_text(encoding="utf-8")
        assert "time_since_last_state_change" in text
        assert "_strip_timing" in text, "缺少时序字段剔除逻辑"
        assert "assert _strip_timing(status_a) == _strip_timing(status_b)" in text


# ═══════════════════════════════════════════════════════════════
#  修复点 4：e2e mock 补 _interaction_lock
# ═══════════════════════════════════════════════════════════════

class TestE2EMockLock:
    """e2e mock 注入 _interaction_lock（修复点 4）"""

    E2E_TEST = PROJECT_ROOT / "tests" / "integration" / "test_orchestrator三层路由_e2e.py"

    def test_mock_has_interaction_lock(self):
        """_make_mock_orchestrator 必须注入 _interaction_lock"""
        text = self.E2E_TEST.read_text(encoding="utf-8")
        assert "import threading" in text, "缺少 threading import"
        assert "orch._interaction_lock = threading.Lock()" in text


# ═══════════════════════════════════════════════════════════════
#  修复点 6：test_reviewer.py kwarg 冲突
# ═══════════════════════════════════════════════════════════════

class TestReviewerKwargFix:
    """test_reviewer.py 5 处 make_skill 调用消除 HIGH（修复点 6）"""

    REVIEWER_TEST = PROJECT_ROOT / "tests" / "unit" / "test_reviewer.py"

    def _calls(self, tree):
        return [n for n in ast.walk(tree) if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Name) and n.func.id == "make_skill"]

    def test_no_spread_with_skill_id_explicit(self):
        """make_skill 不得同时出现显式 skill_id 与 **变量 展开"""
        tree = ast.parse(self.REVIEWER_TEST.read_text(encoding="utf-8"))
        for call in self._calls(tree):
            explicit = [k.arg for k in call.keywords if k.arg is not None]
            has_spread = any(k.arg is None for k in call.keywords)
            if has_spread:
                assert "skill_id" not in explicit, \
                    f"make_skill 显式 skill_id + **展开 冲突 @ L{call.lineno}"

    def test_dict_literal_merge_used(self):
        """修复后调用应使用 dict literal 合并（{**base, ...}）"""
        text = self.REVIEWER_TEST.read_text(encoding="utf-8")
        assert '**{**base, "skill_id": "tags-0", "tags": []}' in text
        assert '**{**base, "skill_id": "va-0"' in text


# ═══════════════════════════════════════════════════════════════
#  修复点 3：覆盖率连锁（上游产物）
# ═══════════════════════════════════════════════════════════════

class TestCoverageChain:
    """覆盖率 job 依赖 build-image 产物（修复点 3 连锁关系）"""

    WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "l3-docker-tests.yml"

    def test_coverage_analysis_depends_on_tests(self):
        """coverage-analysis 须 needs l3-tests，l3-tests 须 needs build-image"""
        text = self.WORKFLOW.read_text(encoding="utf-8")
        # coverage-analysis job needs l3-tests
        assert "coverage-analysis" in text
        assert "needs: l3-tests" in text, "coverage-analysis 未依赖 l3-tests"
        # l3-tests 依赖 build-image（产物 coverage-report-* 由上游生成）
        assert "needs: build-image" in text, "l3-tests 未依赖 build-image"
        assert "coverage-report-${{ matrix.test-mode }}" in text

    def test_entrypoint_overridden_in_verify_step(self):
        """镜像验证步骤须 --entrypoint python 覆盖 ENTRYPOINT(pytest)"""
        text = self.WORKFLOW.read_text(encoding="utf-8")
        assert "--entrypoint python" in text, \
            "验证步骤缺 --entrypoint python，python -c 会被镜像 ENTRYPOINT(pytest) 追加为参数"
        # 该 docker run 之后不能再跟裸 python -c（会被 ENTRYPOINT 吞掉）
        assert "docker run --rm --entrypoint python" in text


# ═══════════════════════════════════════════════════════════════
#  修复点 7：unit 层孪生时序断言（CI Shard 4/6 失败）
# ═══════════════════════════════════════════════════════════════

class TestBreakerTimingUnit:
    """unit 层 test_status_queries_consistent 剔除时序字段（修复点 7）"""

    UNIT_TEST = PROJECT_ROOT / "tests" / "unit" / "test_task3_breaker_degrade_unify.py"

    def test_strip_timing_in_unit_test(self):
        """unit 层同源断言须同样剔除 time_since_last_state_change"""
        text = self.UNIT_TEST.read_text(encoding="utf-8")
        assert "test_status_queries_consistent" in text
        assert "time_since_last_state_change" in text
        assert "_strip_timing" in text, "unit 层缺时序字段剔除逻辑"
        assert "assert _strip_timing(handler.get_circuit_breaker_status())" in text


# ═══════════════════════════════════════════════════════════════
#  修复点 8：缺 import pytest 收集错误（CI Shard 5/6 失败）
# ═══════════════════════════════════════════════════════════════

class TestPytestImportGuard:
    """使用 pytest 特性的测试文件必须 import pytest（修复点 8）"""

    D7_TEST = PROJECT_ROOT / "tests" / "unit" / "test_planning_defect_d7.py"

    def test_d7_file_imports_pytest(self):
        """@pytest.mark.xfail 所在文件须 import pytest（否则分片收集 NameError）"""
        text = self.D7_TEST.read_text(encoding="utf-8")
        assert "import pytest" in text, "使用 pytest 特性必须 import pytest"
        assert "@pytest.mark.xfail" in text
