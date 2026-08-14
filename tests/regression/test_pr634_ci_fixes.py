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
  10. build-image job timeout 20→40min：大镜像（~3.1GB）高负载构建 22min 被
     20min 超时 cancel（CI L3 Docker build-image failure）
  11. .dockerignore 误排除顶层业务包 memory/：L3 容器内报 ModuleNotFoundError
     (memory.storage / memory.vector_store)——memory 是业务代码包（非运行时数据
     目录），COPY . . 后 /app/memory 缺失；data 才是运行时数据目录（仍排除）
  12. l3-tests 自行 rebuild 导致模型层缺失：build-image 与 l3-tests 是不同 runner，
     各自从 gha cache（blob 可能损坏）恢复；测试 job 重建镜像时模型预下载层缺失
     → 容器内 SentenceTransformer 编码器加载超时(30s) → 降级 json 后端 →
     "expected sqlite_vec, got json"。修复：build-image 导出镜像产物(docker save
     → artifact docker-image)，l3-tests 下载并 docker load 复用，不再自行 rebuild
  13. .dockerignore 误排除 scripts/：Dockerfile RUN python scripts/predownload_models.py
     在容器内找不到文件（[Errno 2]）→ 模型从未预下载 → 运行时 SentenceTransformer
     在线加载失败/超时 → 编码器 None → 降级 json（"expected sqlite_vec, got json"）。
     修复：.dockerignore 移除 scripts 排除（predownload_models.py 随 COPY . . 入镜像）

运行方式：
  python -m pytest tests/regression/test_pr634_ci_fixes.py -v
"""

from __future__ import annotations

import ast
import re
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

    def test_build_timeout_has_headroom(self):
        """build-image job timeout 须 ≥30min（大镜像构建高负载实测 22min 被 20min 超时 cancel）"""
        text = self.WORKFLOW.read_text(encoding="utf-8")
        m = re.search(r"build-image:\s*\n(?:.*\n)*?.*timeout-minutes:\s*(\d+)", text)
        assert m, "未找到 build-image job 的 timeout-minutes"
        assert int(m.group(1)) >= 30, \
            f"build-image timeout={m.group(1)}min 过紧，高负载下构建会被 cancel"


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


# ═══════════════════════════════════════════════════════════════
#  修复点 11：.dockerignore 误排除顶层业务包 memory/
# ═══════════════════════════════════════════════════════════════

class TestDockerIgnoreGuard:
    """.dockerignore 不得排除顶层业务包 memory/（修复点 11）"""

    DOCKERIGNORE = PROJECT_ROOT / ".dockerignore"

    def _exclude_lines(self):
        return [
            line.strip()
            for line in self.DOCKERIGNORE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]

    def test_memory_package_not_excluded(self):
        """.dockerignore 排除 memory 会致 L3 容器内 ModuleNotFoundError"""
        excluded = self._exclude_lines()
        assert "memory" not in excluded, \
            "memory 是顶层业务包（storage/vector_store/memory_manager），" \
            "被 .dockerignore 排除后 COPY . . 缺 /app/memory，L3 容器内测试收集报 ModuleNotFoundError"
        # memory 是包目录，排除规则也不得以 memory/* 前缀形式存在
        assert not any(p.startswith("memory") for p in excluded), \
            f"memory 前缀排除规则误伤业务包: {[p for p in excluded if p.startswith('memory')]}"

    def test_runtime_data_still_excluded(self):
        """data 是运行时数据目录，必须保持排除（防镜像膨胀）"""
        excluded = self._exclude_lines()
        assert "data" in excluded, "data 运行时数据目录应保持排除"

    def test_scripts_not_excluded(self):
        """.dockerignore 不得排除 scripts/（Dockerfile 依赖 scripts/predownload_models.py）"""
        excluded = self._exclude_lines()
        assert "scripts" not in excluded, \
            "scripts 被排除后容器内无 predownload_models.py，模型不预下载 → 编码器加载失败降级 json"
        assert not any(p.startswith("scripts") for p in excluded), \
            f"scripts 前缀排除规则误伤模型预下载脚本: {[p for p in excluded if p.startswith('scripts')]}"

    def test_storage_module_importable_in_repo(self):
        """memory.storage / memory.vector_store 源码须存在于仓库（防模块真缺失误判）"""
        assert (PROJECT_ROOT / "memory" / "storage.py").is_file()
        assert (PROJECT_ROOT / "memory" / "vector_store" / "__init__.py").is_file()
        init_text = (PROJECT_ROOT / "memory" / "__init__.py").read_text(encoding="utf-8")
        assert "from .storage import Storage" in init_text
        assert "from .vector_store import VectorStore" in init_text


# ═══════════════════════════════════════════════════════════════
#  修复点 12：l3-tests 复用 build-image 镜像产物
# ═══════════════════════════════════════════════════════════════

class TestDockerImageArtifactReuse:
    """l3-tests 不得自行 rebuild，须 load build-image 导出产物（修复点 12）"""

    WORKFLOW = PROJECT_ROOT / ".github" / "workflows" / "l3-docker-tests.yml"

    def _slice(self, start_marker: str, end_marker: str) -> str:
        text = self.WORKFLOW.read_text(encoding="utf-8")
        start = text.index(start_marker)
        end = text.index(end_marker, start)
        return text[start:end]

    def test_build_image_exports_artifact(self):
        """build-image 须 docker save + 上传 docker-image artifact"""
        sec = self._slice("build-image:", "l3-tests:")
        assert "导出镜像产物" in sec, "build-image 缺少导出镜像步骤"
        assert "docker save" in sec, "导出步骤须 docker save"
        assert "gzip > docker-image.tar.gz" in sec
        assert "name: docker-image" in sec, "缺少 docker-image artifact 上传"

    def test_l3_tests_loads_artifact_not_rebuild(self):
        """l3-tests 须下载并 load 镜像产物，不得自行 build-push（防 gha cache 模型层缺失）"""
        sec = self._slice("l3-tests:", "coverage-analysis:")
        assert "name: docker-image" in sec, "l3-tests 缺少下载镜像产物步骤"
        assert "docker load" in sec, "l3-tests 缺少 docker load"
        assert "build-push-action" not in sec, \
            "l3-tests 不得自行 rebuild（cache-from: type=gha 恢复时模型层缺失 → 降级 json）"
        assert "cache-from" not in sec, "l3-tests 段不应出现 cache-from（重建镜像特征）"


# ═══════════════════════════════════════════════════════════════
#  修复点 14：VOLUME /app/.hf_cache 遮蔽镜像层模型 + 预下载统计路径
# ═══════════════════════════════════════════════════════════════

class TestHfCacheVolumeGuard:
    """Dockerfile 不得声明 VOLUME /app/.hf_cache（修复点 14）

    build 阶段 predownload_models.py 将模型写入镜像层；若再声明 VOLUME，
    docker run（无显式卷挂载）会以匿名卷遮蔽镜像层内容（docker save/load
    复用后尤为明显）→ 容器内 _is_model_fully_cached 返回 False → 在线加载
    失败 → 降级 json 后端（"expected sqlite_vec, got json"）。
    """

    DOCKERFILE = PROJECT_ROOT / "Dockerfile.linux-test"

    def test_no_volume_declaration_for_hf_cache(self):
        """不得存在 VOLUME /app/.hf_cache 声明"""
        text = self.DOCKERFILE.read_text(encoding="utf-8")
        assert "VOLUME /app/.hf_cache" not in text, \
            "VOLUME /app/.hf_cache 会遮蔽镜像层预下载模型（docker run 匿名卷）→ 编码器降级 json"
        assert "VOLUME /app/.hf_cache" not in text.replace(" ", ""), "带多余空格亦应拦截"

    def test_hf_cache_env_points_to_app_dir(self):
        """缓存根语义统一：HF_HOME=根，TRANSFORMERS/SENTENCE_TRANSFORMERS=根/hub

        【不易】snapshot_download(cache_dir=X) 把 X 直接当 hub 根（模型落 X/models--，
        不再拼 hub/）；_is_model_fully_cached 检查 {HF_HOME}/hub/models--。显式 cache_dir
        必须指向 {HF_HOME}/hub，否则模型落盘与检查路径 miss → 误判无缓存 → 降级 json。
        """
        text = self.DOCKERFILE.read_text(encoding="utf-8")
        assert "ENV HF_HOME=/app/.hf_cache" in text
        assert "ENV TRANSFORMERS_CACHE=/app/.hf_cache/hub" in text, \
            "TRANSFORMERS_CACHE 须指向 {HF_HOME}/hub（与 HF_HUB_CACHE 语义一致）"
        assert "ENV SENTENCE_TRANSFORMERS_HOME=/app/.hf_cache/hub" in text, \
            "SENTENCE_TRANSFORMERS_HOME 须指向 {HF_HOME}/hub（否则模型落盘 miss 检查路径）"


class TestPredownloadCachePathGuard:
    """predownload_models.py 缓存路径须含 hub/ 子目录（修复点 14）"""

    SCRIPT = PROJECT_ROOT / "scripts" / "predownload_models.py"

    def test_list_uses_hub_subdir(self):
        """list_cached_models 必须走 {HF_HOME}/hub/models--（HF 实际缓存结构）"""
        text = self.SCRIPT.read_text(encoding="utf-8")
        assert "hub" in text, "缓存统计缺失 hub/ 子目录，已下载模型被误报为无模型"
        # list_cached_models 内不能再用裸 models--（缺 hub）
        assert "cache_dir / \"hub\" / \"models--\"" in text, \
            "list_cached_models 须检查 {HF_HOME}/hub/models-- 目录"

    def test_download_size_stat_uses_hub_subdir(self):
        """_download_fn 的大小统计须含 hub/ 子目录（否则 build 日志 0.0MB 误导）"""
        text = self.SCRIPT.read_text(encoding="utf-8")
        assert "cache_dir / \"hub\" / \"models--\" / model_name" in text, \
            "_download_fn 缓存大小统计缺 hub/ 子目录 → 日志 0.0MB 误导排查"

    def test_set_cache_env_hub_aligned(self):
        """_set_cache_env 的显式 cache_dir（TRANSFORMERS/SENTENCE_TRANSFORMERS）须指向 cache_dir/hub

        【不易】snapshot_download(cache_dir=X) 直接以 X 为 hub 根；若脚本把这两个
        env 设回 cache_dir（不带 hub/），会覆盖 Dockerfile 的正确值并再次把模型
        落盘到与 _is_model_fully_cached 检查路径不一致的位置。
        """
        text = self.SCRIPT.read_text(encoding="utf-8")
        assert 'os.environ["TRANSFORMERS_CACHE"] = str(hub_cache)' in text, \
            "TRANSFORMERS_CACHE 必须指向 hub_cache（cache_dir/hub）"
        assert 'os.environ["SENTENCE_TRANSFORMERS_HOME"] = str(hub_cache)' in text, \
            "SENTENCE_TRANSFORMERS_HOME 必须指向 hub_cache（cache_dir/hub）"
        assert 'hub_cache = cache_dir / "hub"' in text, "_set_cache_env 须定义 hub_cache = cache_dir / \"hub\""


class TestEncoderLoadErrorVisible:
    """_get_shared_encoder 加载异常必须可见（修复点 16）

    此前 `except Exception: return None` 静默吞掉 SentenceTransformer(model_name)
    的加载异常：L3 容器内模型缓存完整（_is_model_fully_cached=True）但编码器仍
    降级 json（"expected sqlite_vec, got json"），日志无任何 traceback，无法定位
    根因。修复：except 分支须输出异常详情 + exc_info=True 完整堆栈。
    """

    VSTORE = PROJECT_ROOT / "memory" / "vector_store" / "vector_store.py"

    def test_except_logs_exception_details(self):
        text = self.VSTORE.read_text(encoding="utf-8")
        assert "logger.warning" in text, "except 分支必须输出 warning 日志"
        assert "exc_info=True" in text, \
            "except 分支必须带 exc_info=True（否则无完整堆栈，异常细节仍不可见）"
        assert "编码器加载失败" in text, "日志须含可检索的失败标记"

    def test_except_returns_none_after_logging(self):
        """加日志不得破坏原降级语义：异常后仍返回 None（调用方降级 json）"""
        text = self.VSTORE.read_text(encoding="utf-8")
        start = text.index("except Exception as e:")
        end = text.index("return None", start)
        segment = text[start:end]
        assert "logger.warning" in segment, "except 分支内必须先记录日志"


class TestEncoderMockModuleGuardRetained:
    """模块级 Mock 检测保留 + 类级 Mock 检测移除（修复点 18）

    根因（2026-08-15 L3 容器 8 ERROR 终态）：_get_shared_encoder 的类级 Mock
    检测 `hasattr(SentenceTransformer, "mock_calls")` 把测试合法用法
    `patch('sentence_transformers.SentenceTransformer', return_value=mock_encoder)`
    误判为污染——patch 后的类必为 MagicMock（有 mock_calls）→ 提前 return None
    → mock_vector_store fixture 的 mock encoder 无效 → 后端降级 json。
    修复：仅保留模块级检测（防 reranker 模块级 MagicMock 残留污染）；移除类级检测。
    """

    VSTORE = PROJECT_ROOT / "memory" / "vector_store" / "vector_store.py"

    def test_module_level_mock_guard_retained(self):
        """模块级检测（hasattr(_st_mod, "mock_calls")）必须保留，防 reranker 污染回归"""
        text = self.VSTORE.read_text(encoding="utf-8")
        assert 'if hasattr(_st_mod, "mock_calls"):' in text, \
            "模块级 Mock 检测被误删（reranker 模块级 MagicMock 残留 → 坏编码器缓存风险）"

    def test_class_level_mock_guard_removed(self):
        """类级检测（hasattr(SentenceTransformer, "mock_calls")）必须移除

        否则 patch('...SentenceTransformer', return_value=encoder) 被误判污染 →
        return None → 集成测试降级 json → "expected sqlite_vec, got json"。
        注：按可执行语句（带 if 前缀）匹配，注释中描述根因的字面量不拦截。
        """
        text = self.VSTORE.read_text(encoding="utf-8")
        assert 'if hasattr(SentenceTransformer, "mock_calls"):' not in text, \
            "类级 Mock 检测必须移除：误伤测试合法 patch（patch 后类必为 MagicMock）"


class TestStMockOnlyOnWindows:
    """sentence_transformers mock 占位仅限 Windows（修复点 17）

    根因（2026-08-15 L3 容器 8 ERROR）：test_vector_store_sqlite_vec.py 的
    _enable_st_module_for_patch（autouse fixture）在 sys.modules 无真实 ST 模块时
    置为 MagicMock——该防护仅对 Windows 0xC0000005（torch C 扩展崩溃）必需。
    容器内（Linux，docker run 不继承 CI env → _HAS_ST=True 不 skip）同样触发
    mock → VectorStore._get_shared_encoder 的 Mock 检测分支提前 return None →
    mock_vector_store fixture 的 patch 无效 → 降级 json → "expected sqlite_vec,
    got json"。修复：mock 条件必须限定 sys.platform.startswith('win')。
    """

    TESTFILE = PROJECT_ROOT / "tests" / "unit" / "test_vector_store_sqlite_vec.py"

    def test_mock_gated_on_windows(self):
        text = self.TESTFILE.read_text(encoding="utf-8")
        seg_start = text.index("def _enable_st_module_for_patch")
        seg_end = text.index("def _make_mock_encoder", seg_start) \
            if "def _make_mock_encoder" in text[seg_start:] else len(text)
        segment = text[seg_start:seg_end]
        assert "sys.platform.startswith('win')" in segment, \
            "mock 占位必须限定 Windows（0xC0000005 为 Windows 特有崩溃码），否则 Linux 容器集成测试降级 json"

    def test_windows_crash_guard_retained(self):
        """Windows 0xC0000005 防护不得被误删：Mock 占位赋值逻辑仍保留"""
        text = self.TESTFILE.read_text(encoding="utf-8")
        assert 'sys.modules["sentence_transformers"] = MagicMock()' in text, \
            "Windows mock 占位赋值被误删（全量顺序 vector_store_sqlite_vec 12 失败回归风险）"


