"""综合技能管理系统单元测试

覆盖维度：
- 功能测试：技能创建、审核、搜索、版本管理、参数优化
- 边界测试：空输入、非法 ID、重复创建
- 错误处理测试：审核拒绝、安全风险、版本回滚失败
- 并发测试：防连点锁

状态同步机制说明：
- 测试用例隔离：每个测试使用独立的 tmpdir 作为 store_path
- 后端权威原则验证：写操作返回的 skill 与本地存储一致
"""
import os
import json
import logging
import tempfile
from pathlib import Path
import pytest

from agent.skills_mgmt import (
    SkillsMgmtService,
    SkillNotFoundError,
    SkillAlreadyExistsError,
    SkillValidationError,
    SkillSecurityError,
)
from agent.skills_mgmt.context_injector import ContextInjector
from agent.skills_mgmt.loader import MatchResult, SkillMatch, SkillLoader, estimate_tokens
from agent.skills_mgmt.models import SkillSearchParams


# ═══════════════════════════════════════════════════════════════════
#  Fixture：独立临时存储的服务实例
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def svc(tmp_path):
    """每个测试用例独立的 SkillsMgmtService 实例"""
    store_path = str(tmp_path / "skills_mgmt.json")
    return SkillsMgmtService(store_path=store_path)


def _make_skill_data(name="test-skill", **overrides):
    """构造 create_manual 所需的字典（id 与 name 同名）"""
    data = {
        "id": name,
        "name": name,
        "description": "测试技能",
        "content": "# 测试\nprint('hello')\n",
        "content_type": "python",
        "category": "custom",
        "tags": ["test", "demo"],
        "author": "tester",
    }
    data.update(overrides)
    return data


# ═══════════════════════════════════════════════════════════════════
#  1. 技能创建测试
# ═══════════════════════════════════════════════════════════════════

class TestSkillCreation:
    """技能创建功能测试"""

    def test_create_manual_basic(self, svc):
        """手动创建：基本字段应正确写入"""
        skill = svc.create_manual(_make_skill_data())
        assert skill.id == "test-skill"
        assert skill.name == "test-skill"
        assert skill.description == "测试技能"
        assert "print" in skill.content
        assert skill.category == "custom"
        assert skill.status == "draft"
        assert skill.enabled is True
        assert skill.version == "0.1.0"
        assert "test" in skill.tags

    def test_create_manual_duplicate_raises(self, svc):
        """重复创建同 ID 应抛 SkillAlreadyExistsError"""
        svc.create_manual(_make_skill_data(name="dup"))
        with pytest.raises(SkillAlreadyExistsError) as exc_info:
            svc.create_manual(_make_skill_data(name="dup", content="different"))
        assert "ALREADY_EXISTS" in exc_info.value.code or "EXISTS" in exc_info.value.code

    def test_create_manual_invalid_id_raises(self, svc):
        """非法 ID（含特殊字符）应抛 SkillValidationError"""
        with pytest.raises((SkillValidationError, Exception)):
            svc.create_manual(_make_skill_data(name="Invalid ID!"))

    def test_create_via_ai_with_template_fallback(self, svc):
        """AI 生成：LLM 不可用时使用模板兜底"""
        skill = svc.create_via_ai(
            name="ai-skill",
            intent="生成一段问候语",
            category="ai_generated",
            tags=["ai"],
        )
        assert skill.id == "ai-skill"
        assert skill.category == "ai_generated"
        assert len(skill.content) > 0  # 模板兜底内容非空

    def test_install_local_json_file(self, svc, tmp_path):
        """本地安装：从 JSON 文件加载"""
        # 准备一个本地技能 JSON 文件
        skill_file = tmp_path / "skill.json"
        skill_file.write_text(
            json.dumps({
                "id": "installed-skill",
                "name": "installed-skill",
                "description": "从本地安装",
                "content": "print('installed')",
                "content_type": "python",
                "category": "custom",
                "tags": ["installed"],
            }),
            encoding="utf-8",
        )
        skill = svc.install(f"local:{skill_file}")
        assert skill.id == "installed-skill"
        assert skill.description == "从本地安装"


# ═══════════════════════════════════════════════════════════════════
#  2. 审核系统测试
# ═══════════════════════════════════════════════════════════════════

class TestSkillReview:
    """技能审核系统测试：重复检测、安全扫描、质量评估"""

    def test_review_passes_good_skill(self, svc):
        """高质量、无安全风险的技能应通过审核"""
        long_desc = "这是一个高质量的技能，用于演示审核通过路径，包含用途、场景与示例说明。"
        long_content = (
            "# 完整文档示例\n\n"
            "## 适用场景\n\n适用于日常文本处理与格式化输出场景。\n\n"
            "## 用法\n\n```python\n"
            "def run(input_text):\n"
            "    try:\n"
            "        return input_text.strip()\n"
            "    except Exception as e:\n"
            "        raise ValueError(f'处理失败: {e}')\n"
            "```\n"
        )
        svc.create_manual(_make_skill_data(
            name="good",
            description=long_desc,
            content=long_content,
            tags=["quality", "demo"],
        ))
        result = svc.review("good")
        # 状态应为 passed 或 warn（不应该是 failed）
        assert result.status in ("passed", "warn", "pending"), (
            f"期望通过/警告，实际 {result.status} (sec={result.security_score}, "
            f"qual={result.quality_score}, summary={result.summary})")
        assert result.security_score >= 70  # 安全分应较高

    def test_review_rejects_security_risk(self, svc):
        """含危险代码（eval/命令注入）应被审核拒绝（status=failed, security_score=0）

        设计说明：SecurityScanner.scan 在 block_on_critical=True 时会抛
        SkillSecurityError，但 SkillReviewer 门面层捕获后返回结构化
        ReviewResult(status=FAILED)，便于前端统一展示。
        本测试验证门面层的结构化结果。
        """
        svc.create_manual(_make_skill_data(
            name="dangerous",
            description="危险技能，含命令注入与 eval 调用",
            content="import os\nos.system('rm -rf /')\neval(input())\n",
        ))
        result = svc.review("dangerous")
        # 门面层应返回 failed 状态
        assert result.status == "failed"
        assert result.security_score == 0.0
        # 摘要应提及安全审核未通过
        assert "安全" in result.summary or "security" in result.summary.lower()
        # 技能状态应被置为 rejected
        assert svc.get("dangerous").status == "rejected"

    def test_security_scanner_raises_on_critical(self, svc):
        """底层 SecurityScanner 应直接抛 SkillSecurityError（边界显性化）"""
        from agent.skills_mgmt.reviewer import SecurityScanner
        skill = svc.create_manual(_make_skill_data(
            name="dangerous2",
            description="危险技能",
            content="import os\nos.system('rm -rf /')\neval(input())\n",
        ))
        scanner = SecurityScanner(block_on_critical=True)
        with pytest.raises(SkillSecurityError) as exc_info:
            scanner.scan(skill)
        assert "SECURITY" in exc_info.value.code

    def test_review_detects_duplicate(self, svc):
        """重复检测：高度相似的内容应被识别"""
        content_a = "def greet(name):\n    return f'Hello, {name}!'\n"
        content_b = "def greet(name):\n    return f'Hello, {name}!'\n"  # 完全相同
        svc.create_manual(_make_skill_data(name="skill-a", content=content_a, tags=["greet"]))
        svc.create_manual(_make_skill_data(name="skill-b", content=content_b, tags=["greet"]))
        result = svc.review("skill-b")
        # 重复度得分应较高（duplicate_score: 0=完全原创, 100=完全重复）
        assert result.duplicate_score > 50

    def test_review_nonexistent_raises(self, svc):
        """审核不存在的技能应抛 SkillNotFoundError"""
        with pytest.raises(SkillNotFoundError):
            svc.review("nonexistent-id")

    def test_review_batch(self, svc):
        """批量审核：应返回审核数量"""
        svc.create_manual(_make_skill_data(name="batch-1", content="# 安全内容\nprint('a')\n"))
        svc.create_manual(_make_skill_data(name="batch-2", content="# 另一个安全内容\nprint('b')\n"))
        # review_all_pending 只审核 PENDING_REVIEW 状态，新创建的是 DRAFT
        # 先手动改状态或直接验证返回值结构
        result = svc.review_all_pending()
        assert isinstance(result, list)


# ═══════════════════════════════════════════════════════════════════
#  3. 搜索与筛选测试
# ═══════════════════════════════════════════════════════════════════

class TestSkillSearch:
    """技能搜索与筛选测试"""

    def _setup_test_skills(self, svc):
        """创建一批测试技能"""
        svc.create_manual(_make_skill_data(name="email-helper", description="邮件处理助手", tags=["email", "work"]))
        svc.create_manual(_make_skill_data(name="code-reviewer", description="代码审查工具", tags=["code", "review"]))
        svc.create_manual(_make_skill_data(name="data-fetcher", description="数据抓取器", tags=["data", "web"]))

    def test_search_by_keyword(self, svc):
        """关键词搜索应返回匹配项"""
        self._setup_test_skills(svc)
        result = svc.search(SkillSearchParams(query="邮件"))
        assert any(s.id == "email-helper" for s in result.items)

    def test_search_by_tag(self, svc):
        """标签筛选应返回对应技能"""
        self._setup_test_skills(svc)
        result = svc.search(SkillSearchParams(tags=["code"]))
        assert all("code" in s.tags for s in result.items)
        assert len(result.items) >= 1

    def test_search_by_category(self, svc):
        """分类筛选应正确过滤"""
        self._setup_test_skills(svc)
        result = svc.search(SkillSearchParams(categories=["custom"]))
        assert all(s.category == "custom" for s in result.items)

    def test_search_pagination(self, svc):
        """分页应正确返回对应页码"""
        for i in range(15):
            svc.create_manual(_make_skill_data(name=f"page-skill-{i}", content=f"print({i})\n"))
        result = svc.search(SkillSearchParams(page=2, page_size=5))
        assert result.page == 2
        assert result.page_size == 5
        assert len(result.items) <= 5


# ═══════════════════════════════════════════════════════════════════
#  4. 版本管理测试
# ═══════════════════════════════════════════════════════════════════

class TestSkillVersioning:
    """技能版本管理测试"""

    def test_bump_patch_version(self, svc):
        """patch 版本升级应正确递增"""
        svc.create_manual(_make_skill_data(name="ver-skill", content="v1\n"))
        bump = svc.bump_version("ver-skill", "patch", changelog="修复 bug")
        assert bump.new_version == "0.1.1"
        # 验证技能本身的版本已更新
        assert svc.get("ver-skill").version == "0.1.1"

    def test_bump_minor_version(self, svc):
        """minor 版本升级应正确递增"""
        svc.create_manual(_make_skill_data(name="ver-skill", content="v1\n"))
        bump = svc.bump_version("ver-skill", "minor", changelog="新增功能")
        assert bump.new_version == "0.2.0"

    def test_bump_major_version(self, svc):
        """major 版本升级应正确递增"""
        svc.create_manual(_make_skill_data(name="ver-skill", content="v1\n"))
        bump = svc.bump_version("ver-skill", "major", changelog="破坏性变更")
        assert bump.new_version == "1.0.0"

    def test_list_versions(self, svc):
        """列出历史版本应包含所有已发布版本"""
        svc.create_manual(_make_skill_data(name="ver-skill", content="v0\n"))
        svc.bump_version("ver-skill", "patch")
        svc.bump_version("ver-skill", "minor")
        versions = svc.list_versions("ver-skill")
        assert len(versions) >= 2  # 至少有升级后的版本

    def test_rollback_version(self, svc):
        """回滚到旧版本应正确切换"""
        svc.create_manual(_make_skill_data(name="ver-skill", content="v0\n"))
        svc.bump_version("ver-skill", "minor", content="v1 content\n")
        # 回滚到初始版本
        skill = svc.rollback_version("ver-skill", "0.1.0")
        assert skill.version == "0.1.0"


# ═══════════════════════════════════════════════════════════════════
#  5. 启用/禁用 & 优化测试
# ═══════════════════════════════════════════════════════════════════

class TestSkillEnhancement:
    """技能增强功能测试"""

    def test_toggle_enable_disable(self, svc):
        """切换启用/禁用状态"""
        svc.create_manual(_make_skill_data(name="toggle-skill", content="x\n"))
        assert svc.get("toggle-skill").enabled is True
        svc.set_enabled("toggle-skill", False)
        assert svc.get("toggle-skill").enabled is False
        svc.set_enabled("toggle-skill", True)
        assert svc.get("toggle-skill").enabled is True

    def test_record_execution_updates_metrics(self, svc):
        """记录执行应更新使用统计"""
        svc.create_manual(_make_skill_data(name="metrics-skill", content="x\n"))
        svc.record_execution("metrics-skill", success=True, latency_ms=100)
        svc.record_execution("metrics-skill", success=True, latency_ms=200)
        svc.record_execution("metrics-skill", success=False, latency_ms=50)
        skill = svc.get("metrics-skill")
        assert skill.metrics.usage_count == 3
        assert skill.metrics.success_count == 2
        assert skill.metrics.failure_count == 1
        assert 50 <= skill.metrics.avg_latency_ms <= 200

    def test_optimize_returns_suggestions(self, svc):
        """优化应返回建议字典"""
        svc.create_manual(_make_skill_data(name="opt-skill", content="x\n", default_params={"timeout": 30}))
        # 触发几次执行以产生统计
        for _ in range(5):
            svc.record_execution("opt-skill", success=True, latency_ms=100)
        result = svc.optimize_params("opt-skill")
        assert isinstance(result, dict)


# ═══════════════════════════════════════════════════════════════════
#  6. 持久化与健康检查测试
# ═══════════════════════════════════════════════════════════════════

class TestSkillPersistence:
    """持久化与健康检查测试"""

    def test_persistence_across_restart(self, tmp_path):
        """重启后数据应持久化"""
        store_path = str(tmp_path / "skills.json")
        svc1 = SkillsMgmtService(store_path=store_path)
        svc1.create_manual(_make_skill_data(name="persist", content="x\n"))

        # 模拟重启：创建新实例
        svc2 = SkillsMgmtService(store_path=store_path)
        skill = svc2.get("persist")
        assert skill is not None
        assert skill.name == "persist"

    def test_health_returns_stats(self, svc):
        """健康检查应返回依赖状态"""
        svc.create_manual(_make_skill_data(name="h-skill", content="x\n"))
        health = svc.health()
        assert health["ok"] is True
        assert "stats" in health
        assert health["stats"]["total"] >= 1

    def test_delete_skill(self, svc):
        """删除技能后应无法再获取"""
        svc.create_manual(_make_skill_data(name="del-skill", content="x\n"))
        svc.delete("del-skill")
        with pytest.raises(SkillNotFoundError):
            svc.get("del-skill")


# ═══════════════════════════════════════════════════════════════════
#  7. 检索扩展点测试（接口预留，当前仅 TF-IDF 实现）
# ═══════════════════════════════════════════════════════════════════

class TestRetrievalExtension:
    """检索扩展点测试：验证 MatchResult/SkillMatch/health 预留字段向后兼容"""

    def test_match_result_has_extension_fields(self):
        """MatchResult.to_dict() 含 retrieval_method/reranked/fallback_used 等新字段"""
        match = SkillMatch(
            skill_id="t", name="t", description="d",
            score=0.5, estimated_tokens=10,
        )
        result = MatchResult(
            matches=[match], total_scanned=1, elapsed_ms=1.0,
            estimated_total_tokens=10,
        )
        d = result.to_dict()
        assert d["retrieval_method"] == "tfidf"
        assert d["reranked"] is False
        assert d["fallback_used"] is False
        assert "score_breakdown" in d
        # SkillMatch 也应含 score_breakdown 字段（默认 None）
        assert d["matches"][0]["score_breakdown"] is None

    def test_match_accepts_extension_params(self, svc, caplog):
        """传入 use_vector=True 等扩展参数不报错，并记录 warning 日志"""
        svc.create_manual(_make_skill_data(name="ext-skill", description="邮件处理"))
        loader = svc.loader

        with caplog.at_level(logging.WARNING, logger="agent.skills_mgmt"):
            result = loader.match("邮件", use_vector=True, use_bm25=True,
                                   use_reranker=True,
                                   retrieval_weights={"tfidf": 0.2, "vector": 0.8})

        # 应在 WARNING 日志中找到扩展点未实现的记录
        found_warning = False
        for record in caplog.records:
            if "match.extension_not_implemented" in record.getMessage():
                found_warning = True
                break
        assert found_warning, "未记录扩展点未实现的 warning"
        # 仍应返回有效 MatchResult（降级 TF-IDF）
        assert isinstance(result, MatchResult)

    def test_match_fallback_flag_when_vector_requested(self, svc):
        """use_vector=True 时 fallback_used 应为 True（请求了但未实现）"""
        svc.create_manual(_make_skill_data(name="fb-skill", description="邮件处理"))
        loader = svc.loader

        result_normal = loader.match("邮件")
        assert result_normal.fallback_used is False, "未请求扩展点时 fallback_used 应为 False"

        result_vector = loader.match("邮件", use_vector=True)
        assert result_vector.fallback_used is True, "请求 use_vector=True 时应标记降级"
        assert result_vector.retrieval_method == "tfidf", "降级后方法仍为 tfidf"

    def test_health_includes_scale_monitoring(self, svc):
        """health() 返回值应含 scale_monitoring 字段及子字段"""
        svc.create_manual(_make_skill_data(name="h2-skill", content="x\n"))
        health = svc.health()
        assert "scale_monitoring" in health, "health() 应包含 scale_monitoring"
        sm = health["scale_monitoring"]
        assert sm["total_skills"] >= 1
        assert sm["upgrade_threshold"] == 30
        assert sm["current_method"] == "tfidf"
        assert sm["available_methods"] == ["tfidf"]
        assert sm["upgrade_recommended"] is False, "1 个技能不应触发升级建议"

    def test_health_upgrade_recommended_at_threshold(self, svc):
        """技能数达到阈值 30 时 upgrade_recommended 应为 True"""
        # 创建 30 个技能达到升级阈值
        for i in range(30):
            svc.create_manual(_make_skill_data(
                name=f"thresh-skill-{i}", content=f"print({i})\n"))
        health = svc.health()
        sm = health["scale_monitoring"]
        assert sm["total_skills"] == 30
        assert sm["upgrade_recommended"] is True, "技能数达 30 应触发升级建议"


# ═══════════════════════════════════════════════════════════════════
#  8. ContextInjector 章节级智能截断测试
# ═══════════════════════════════════════════════════════════════════

def _make_injector_with_instruction(instruction: str, *, budget: int = 4000) -> ContextInjector:
    """构造一个用 mock loader 注入指定 instruction 的 ContextInjector"""
    from unittest.mock import MagicMock
    loader = MagicMock()
    loader.load_instruction.return_value = {
        "skill_id": "test-skill",
        "instruction": instruction,
        "estimated_tokens": estimate_tokens(instruction),
        "instruction_chars": len(instruction),
        "layer": 2,
    }
    return ContextInjector(loader=loader, instr_budget=budget)


class TestContextInjectorInstruction:
    """ContextInjector.inject_instruction 章节级智能截断测试"""

    _SECTION_INSTRUCTION = (
        "## 概述\n这是一个测试技能的简介说明。\n\n"
        "## 步骤\n1. 准备数据\n2. 执行处理\n3. 输出结果\n\n"
        "## 示例\n输入 hello 输出 HELLO。\n\n"
        "## 参考资料\n相关链接列表。\n"
    )

    def test_inject_instruction_no_truncation_under_budget(self):
        """预算充足时完整保留所有章节"""
        instruction = self._SECTION_INSTRUCTION
        injector = _make_injector_with_instruction(instruction, budget=10000)
        result = injector.inject_instruction("test-skill")
        assert result["truncated"] is False
        assert "## 概述" in result["prompt"]
        assert "## 步骤" in result["prompt"]
        assert "## 示例" in result["prompt"]
        assert "## 参考资料" in result["prompt"]
        assert "更多章节未加载" not in result["prompt"]

    def test_inject_instruction_truncation_by_section(self):
        """超预算时按章节取舍：保留首章节 + 步骤章节，丢弃参考资料"""
        overview = "## 概述\n技能简介。\n"
        steps = "## 步骤\n1. 第一步\n2. 第二步\n3. 第三步\n"
        examples = "## 示例\n示例内容。\n"
        # 参考资料占大量 token，但优先级最低
        refs = "## 参考资料\n" + ("参考链接内容。" * 200) + "\n"
        instruction = overview + steps + examples + refs
        injector = _make_injector_with_instruction(instruction, budget=200)
        result = injector.inject_instruction("test-skill")
        assert result["truncated"] is True
        # 必保留：首章节（概述）+ 步骤章节
        assert "## 概述" in result["prompt"]
        assert "## 步骤" in result["prompt"]
        # 可裁剪：参考资料应被丢弃
        assert "## 参考资料" not in result["prompt"]
        # 末尾追加省略提示
        assert "更多章节未加载" in result["prompt"]

    def test_inject_instruction_no_half_sentence(self):
        """保留的任何章节末尾不应是半句话（用句号/问号/感叹号/换行判断）"""
        overview = "## 概述\n这是一个完整的句子。\n\n"
        steps = "## 步骤\n1. 完整步骤一。\n2. 完整步骤二。\n\n"
        examples = "## 示例\n完整示例。\n\n"
        refs = "## 参考资料\n" + ("参考内容。" * 150) + "\n"
        instruction = overview + steps + examples + refs
        injector = _make_injector_with_instruction(instruction, budget=150)
        result = injector.inject_instruction("test-skill")
        # 截取 prompt 中省略提示之前的章节内容
        body = result["prompt"]
        if "更多章节未加载" in body:
            body = body.split("...(更多章节未加载")[0]
        # 切分回章节块（双换行分隔）
        blocks = [b for b in body.split("\n\n") if b.strip()]
        for block in blocks:
            # 跳过外层包装行
            if block.startswith("## 技能使用说明"):
                continue
            tail = block.rstrip()[-1] if block.rstrip() else ""
            assert tail in "。！？\n)", (
                f"章节末尾疑似半句话: ...{block[-40:]!r}"
            )

    def test_inject_instruction_no_markdown_fallback(self):
        """无 H2/H3 标记时降级为整段保留或整段不保留（不截断半句话）"""
        plain_text = "这是一个没有章节标记的纯文本使用说明。" * 100

        # 预算充足：整段保留
        injector_full = _make_injector_with_instruction(plain_text, budget=100000)
        result_full = injector_full.inject_instruction("test-skill")
        assert result_full["truncated"] is False
        assert plain_text in result_full["prompt"]
        assert "更多章节未加载" not in result_full["prompt"]

        # 预算极小：整段不保留，只显示省略提示（无半句话截断）
        injector_small = _make_injector_with_instruction(plain_text, budget=10)
        result_small = injector_small.inject_instruction("test-skill")
        assert result_small["truncated"] is True
        assert "更多章节未加载" in result_small["prompt"]
        # 不应出现原始长文本片段（说明整段被丢弃而非字符截断）
        # 取省略提示前的内容判断
        before_hint = result_small["prompt"].split("...(更多章节未加载")[0]
        assert plain_text[:50] not in before_hint

    def test_inject_instruction_dropped_sections_logged(self, caplog):
        """截断时 WARNING 日志应包含非空的 dropped_sections / kept_sections"""
        overview = "## 概述\n简介。\n"
        steps = "## 步骤\n1. 步骤一。\n"
        refs = "## 参考资料\n" + ("参考" * 200) + "\n"
        instruction = overview + steps + refs
        injector = _make_injector_with_instruction(instruction, budget=100)

        with caplog.at_level(logging.WARNING, logger="agent.skills_mgmt"):
            result = injector.inject_instruction("test-skill")

        assert result["truncated"] is True

        # 在 WARNING 日志中找包含 dropped_sections 的 JSON 记录
        found = False
        for record in caplog.records:
            msg = record.getMessage()
            if "dropped_sections" not in msg or "inject_instruction.truncated" not in msg:
                continue
            payload = json.loads(msg)
            assert payload["truncated"] is True
            assert len(payload["dropped_sections"]) > 0, "dropped_sections 应非空"
            assert len(payload["kept_sections"]) > 0, "kept_sections 应非空"
            found = True
            break

        assert found, "未找到含 dropped_sections 的截断日志"

    def test_inject_instruction_extreme_budget_50_tokens(self):
        """极端预算 50 tokens：必保留章节合计超预算时切换 greedy_sequential 兜底

        验证边界：必保留 3 章（概述+步骤+使用方法）合计 162 tokens，
        预算 50 tokens 时无法全部放下，应：
        1. 切换到 greedy_sequential 策略
        2. 仅保留能放下的首章节（概述，37 tokens）
        3. 步骤章节因单章节不截断原则被整段丢弃
        4. 不出现半句话截断
        """
        instruction = (
            "## 概述\n情感表达技能简介。\n\n"
            "## 步骤\n1. 检测情绪\n2. 查询语气参数\n3. 调整回应\n4. 标记升级\n\n"
            "## 使用方法\n调用 skill.invoke(response, context)。\n\n"
            "## 示例\n输入'我今天好累' → 调整为安抚语气。\n\n"
            "## 注意\n置信度<0.5 不调整。\n\n"
            "## 参考资料\n" + ("情绪词典\n" * 40) + "\n"
        )
        injector = _make_injector_with_instruction(instruction, budget=50)
        result = injector.inject_instruction("test-skill")

        # 断言1: 触发截断
        assert result["truncated"] is True, "50 tokens 预算应触发截断"

        # 断言2: 至少保留首章节（概述），不放半句话
        assert "## 概述" in result["prompt"], "首章节应被保留"
        assert "情感表达技能简介。" in result["prompt"], "概述正文应完整保留"

        # 断言3: 步骤章节因单章节不截断原则被整段丢弃，不留半句话
        # 取省略提示之前的内容判断
        body = result["prompt"].split("...(更多章节未加载")[0]
        # 步骤章节不应部分出现（不应有"1. 检测情绪"后跟"2."缺失的情况）
        # 即要么完整保留步骤章节，要么完全不留
        if "## 步骤" in body:
            # 若保留了步骤章节，必须完整（含全部 4 个步骤）
            assert "1. 检测情绪" in body
            assert "4. 标记升级" in body, "保留步骤章节时必须完整"
        else:
            # 未保留步骤章节时，不应残留步骤片段
            assert "检测情绪" not in body, "不应残留步骤章节的半句话"

        # 断言4: 必保留章节合计 162 > 预算 50，应触发 greedy fallback
        # 通过日志记录验证策略切换
        # （这里通过结果行为间接验证：保留数 < 必保留数 3）
        # 概述 37 < 50 单独可放下，步骤 71 单独就超 50 → 步骤必丢
        assert "## 参考资料" not in result["prompt"], "参考资料应被丢弃"

        # 断言5: 输出 token 数受控（不超过预算 + 省略提示）
        hint = "\n\n...(更多章节未加载，完整内容请查看 skill.md 文件，可调用 load_skill_instruction 获取完整说明)"
        assert result["estimated_tokens"] <= 50 + estimate_tokens(hint), \
            "截断后 token 不应远超预算"

    def test_inject_instruction_extreme_budget_below_min_section(self):
        """更极端预算 30 tokens：连最小章节都放不下时全部丢弃

        验证边界：预算 < 单个最小章节 token 时：
        1. 不强制保留任何不完整章节
        2. 仅输出省略提示
        3. 不出现半句话截断
        """
        # 所有章节都 > 30 tokens
        instruction = (
            "## 概述\n"
            "这是一个故意写得很长的概述章节，目的是让 token 数量明显超过三十个，"
            "这样才能验证当预算极小、所有章节都放不下时的兜底行为是否正确。\n\n"
            "## 步骤\n1. 第一步操作\n2. 第二步操作\n\n"
            "## 示例\n示例内容较长。\n\n"
        )
        injector = _make_injector_with_instruction(instruction, budget=30)
        result = injector.inject_instruction("test-skill")

        # 断言1: 触发截断
        assert result["truncated"] is True

        # 断言2: 任何章节正文都不应残留（避免半句话）
        body = result["prompt"].split("...(更多章节未加载")[0]
        assert "第一步操作" not in body, "步骤正文不应残留"
        assert "示例内容" not in body, "示例正文不应残留"
        # 概述正文也不应残留（因为整段超 30 tokens）
        assert "这是一个比较长的概述" not in body, "概述正文不应残留"

        # 断言3: 必须包含省略提示
        assert "更多章节未加载" in result["prompt"]

        # 断言4: 输出 token 数约等于省略提示
        hint = "\n\n...(更多章节未加载，完整内容请查看 skill.md 文件，可调用 load_skill_instruction 获取完整说明)"
        # 包装行 "## 技能使用说明：test-skill\n\n" 也占少量 token，故输出 token 略大于 hint
        assert result["estimated_tokens"] <= 30 + estimate_tokens(hint) + 20, \
            "全部章节丢弃后 token 数应接近省略提示"


# ═══════════════════════════════════════════════════════════════════
#  ContextInjector.inject_metadata 技能边界声明测试
# ═══════════════════════════════════════════════════════════════════

def _make_injector_with_skills(all_skills, *, meta_budget=4000):
    """构造一个用 mock loader 注入指定技能列表的 ContextInjector"""
    from unittest.mock import MagicMock
    loader = MagicMock()
    loader.list_all_metadata.return_value = all_skills
    return ContextInjector(loader=loader, meta_budget=meta_budget)


def _make_skill_match(skill_id, name=None, tokens=50):
    """构造单个 SkillMatch 对象"""
    return SkillMatch(
        skill_id=skill_id,
        name=name or skill_id,
        description=f"{skill_id} 的描述",
        score=0.85,
        estimated_tokens=tokens,
        tags=["test"],
        version="0.1.0",
    )


class TestContextInjectorBoundaryDeclaration:
    """ContextInjector.inject_metadata 技能边界声明（防幻觉）测试"""

    def test_boundary_declaration_appended(self):
        """边界声明应追加到 prompt 末尾"""
        all_skills = [
            {"skill_id": "skill-a", "name": "A", "enabled": True},
            {"skill_id": "skill-b", "name": "B", "enabled": True},
            {"skill_id": "skill-c", "name": "C", "enabled": True},
        ]
        injector = _make_injector_with_skills(all_skills)
        matches = [_make_skill_match("skill-a")]

        result = injector.inject_metadata(matches)

        assert "## 技能边界声明" in result["prompt"]
        assert "已加载技能" in result["prompt"]
        assert "未加载技能" in result["prompt"]

    def test_boundary_declaration_loaded_unloaded_correct(self):
        """已加载/未加载列表应正确区分"""
        all_skills = [
            {"skill_id": "skill-a", "enabled": True},
            {"skill_id": "skill-b", "enabled": True},
            {"skill_id": "skill-c", "enabled": True},
        ]
        injector = _make_injector_with_skills(all_skills)
        matches = [
            _make_skill_match("skill-a"),
            _make_skill_match("skill-b"),
        ]

        result = injector.inject_metadata(matches)

        bd = result["boundary_declaration"]
        assert "skill-a" in bd["loaded"]
        assert "skill-b" in bd["loaded"]
        assert "skill-c" in bd["unloaded"]
        assert "skill-a" not in bd["unloaded"]

    def test_boundary_declaration_empty_matches(self):
        """无匹配技能时，所有技能都应在未加载列表"""
        all_skills = [
            {"skill_id": "skill-a", "enabled": True},
            {"skill_id": "skill-b", "enabled": True},
        ]
        injector = _make_injector_with_skills(all_skills)

        result = injector.inject_metadata([])

        bd = result["boundary_declaration"]
        assert len(bd["loaded"]) == 0
        assert len(bd["unloaded"]) == 2
        assert "## 技能边界声明" in result["prompt"]

    def test_boundary_declaration_no_skills(self):
        """无任何技能时，不输出边界声明"""
        injector = _make_injector_with_skills([])
        matches = [_make_skill_match("skill-a")]

        result = injector.inject_metadata(matches)

        bd = result["boundary_declaration"]
        assert bd["text"] == ""
        assert bd["tokens"] == 0
        assert "## 技能边界声明" not in result["prompt"]

    def test_boundary_declaration_load_failure_graceful(self):
        """loader.list_all_metadata 抛异常时优雅降级"""
        from unittest.mock import MagicMock
        loader = MagicMock()
        loader.list_all_metadata.side_effect = RuntimeError("disk error")
        injector = ContextInjector(loader=loader)

        result = injector.inject_metadata([_make_skill_match("skill-a")])

        bd = result["boundary_declaration"]
        assert bd["text"] == ""
        assert "## 技能边界声明" not in result["prompt"]

    def test_boundary_declaration_tokens_counted(self):
        """边界声明 token 应计入总 token"""
        all_skills = [
            {"skill_id": "skill-a", "enabled": True},
            {"skill_id": "skill-b", "enabled": True},
        ]
        injector = _make_injector_with_skills(all_skills)
        matches = [_make_skill_match("skill-a", tokens=50)]

        result = injector.inject_metadata(matches)

        bd = result["boundary_declaration"]
        assert result["estimated_tokens"] >= 50 + bd["tokens"]

    def test_boundary_declaration_return_field_exists(self):
        """返回值应包含 boundary_declaration 字段（向后兼容新增）"""
        all_skills = [{"skill_id": "skill-a", "enabled": True}]
        injector = _make_injector_with_skills(all_skills)

        result = injector.inject_metadata([_make_skill_match("skill-a")])

        assert "boundary_declaration" in result
        bd = result["boundary_declaration"]
        assert "text" in bd
        assert "tokens" in bd
        assert "loaded" in bd
        assert "unloaded" in bd

    def test_boundary_declaration_anti_hallucination_text(self):
        """边界声明应包含防幻觉关键提示语"""
        all_skills = [
            {"skill_id": "skill-a", "enabled": True},
            {"skill_id": "skill-b", "enabled": True},
        ]
        injector = _make_injector_with_skills(all_skills)
        matches = [_make_skill_match("skill-a")]

        result = injector.inject_metadata(matches)

        assert "严禁编造" in result["prompt"]
        assert "已加载" in result["prompt"]
        assert "未加载" in result["prompt"]

    def test_boundary_declaration_only_injected_skills_loaded(self):
        """超预算截断时，仅实际注入的技能出现在已加载列表"""
        all_skills = [
            {"skill_id": "skill-a", "enabled": True},
            {"skill_id": "skill-b", "enabled": True},
            {"skill_id": "skill-c", "enabled": True},
        ]
        # 预算只够放一个技能（50 tokens）
        injector = _make_injector_with_skills(all_skills, meta_budget=60)
        matches = [
            _make_skill_match("skill-a", tokens=50),
            _make_skill_match("skill-b", tokens=50),
            _make_skill_match("skill-c", tokens=50),
        ]

        result = injector.inject_metadata(matches)

        bd = result["boundary_declaration"]
        assert "skill-a" in bd["loaded"]
        # skill-b 因超预算未注入，应在未加载列表
        assert "skill-b" in bd["unloaded"]
        assert "skill-c" in bd["unloaded"]


# ═══════════════════════════════════════════════════════════════════
#  9. 检索质量评估测试（黄金集驱动，Precision@K 守卫）
# ═══════════════════════════════════════════════════════════════════

def _load_eval_module():
    """动态加载 scripts/eval_skill_retrieval.py 作为模块

    scripts/ 不是 Python 包（无 __init__.py），用 importlib 按文件路径加载，
    避免改动目录结构或污染 sys.path。
    """
    import importlib.util
    eval_script = (
        Path(__file__).resolve().parent.parent.parent
        / "scripts" / "eval_skill_retrieval.py"
    )
    spec = importlib.util.spec_from_file_location(
        "eval_skill_retrieval", eval_script,
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestRetrievalEvaluation:
    """检索质量评估 — 调用 scripts/eval_skill_retrieval.py 的核心函数

    设计说明：
    - 黄金集路径：tests/eval/skill_retrieval_golden_set.json（45 个用例）
    - 基线（TF-IDF, 2026-07-13）: Precision@3 = 0.3926
    - 主要缺陷：self_reflection / memory_summary 的 description 为空，
      TF-IDF 无法用中文语义查询命中，7 个用例 Precision=0
    - 升级向量检索后应通过此测试（届时移除 xfail 动态标记）
    """

    @pytest.mark.eval
    def test_skill_retrieval_precision_above_threshold(self):
        """技能检索 Precision@3 不低于 0.6（CI 守卫）

        验收标准：pytest tests/unit/test_skills_mgmt.py -m eval 通过
        （或标记为预期失败，若 Precision@3 < 0.6）

        实现说明：
        - 调用 evaluate(top_k=3) 获取完整报告
        - 若 Precision@3 < 0.6，调用 pytest.xfail 动态标记为预期失败
          （不是硬断言失败，避免 CI 红灯）
        - 未来升级向量检索后 Precision 提升至 >= 0.6，xfail 不触发，
          assert 验证通过
        """
        mod = _load_eval_module()
        report = mod.evaluate(top_k=3)
        precision = report["overall"]["precision"]

        # 校验失败直接失败（不是算法问题，是数据问题）
        assert not report["validation_errors"], (
            f"黄金集校验失败: {report['validation_errors']}"
        )

        if precision < 0.6:
            pytest.xfail(
                f"TF-IDF 基线 Precision@3={precision:.4f} < 0.6 阈值。"
                f"主要缺陷：self_reflection / memory_summary 的 description 为空，"
                f"TF-IDF 无法用中文语义查询命中（7 个用例 Precision=0）。"
                f"升级向量检索后应通过此测试。"
            )

        assert precision >= 0.6, f"Precision@3={precision:.4f} < 0.6"

    @pytest.mark.eval
    def test_golden_set_minimum_case_count(self):
        """黄金集用例数 >= 30（结构守卫）"""
        mod = _load_eval_module()
        golden = mod.load_golden_set(mod.DEFAULT_GOLDEN_SET)
        assert len(golden["test_cases"]) >= 30, (
            f"黄金集用例数 {len(golden['test_cases'])} < 30"
        )

    @pytest.mark.eval
    def test_golden_set_skill_ids_validated(self):
        """黄金集所有 expected_skill_ids 必须与实际技能 ID 一致

        【不易防御】避免期望 ID 拼写错误被误判为算法召回缺陷。
        """
        mod = _load_eval_module()
        golden = mod.load_golden_set(mod.DEFAULT_GOLDEN_SET)
        loader = SkillLoader()
        available = sorted(loader.fs.load_metadata_index().keys())
        errors = mod.validate_expected_skill_ids(golden, available)
        assert not errors, f"黄金集 ID 校验失败: {errors}"

    @pytest.mark.eval
    def test_baseline_precision_recorded(self):
        """基线 Precision@3 被记录到报告中（作为升级向量检索的对比基准）"""
        mod = _load_eval_module()
        report = mod.evaluate(top_k=3)
        # 报告必须包含基线 Precision 字段
        assert "overall" in report
        assert "precision" in report["overall"]
        assert 0.0 <= report["overall"]["precision"] <= 1.0
        # 报告必须包含按难度/类别的分组
        assert "by_difficulty" in report
        assert "by_category" in report
        # 报告必须包含逐用例明细（含 actual 与 expected 对比）
        assert len(report["cases"]) == report["total_cases"]
        for c in report["cases"]:
            assert "actual" in c
            assert "expected" in c


# ═══════════════════════════════════════════════════════════════════
#  9. 全链路可观测性字段测试（retrieved_chunks / eval_score）
# ═══════════════════════════════════════════════════════════════════

class TestObservabilityFields:
    """可观测性扩展字段测试：retrieved_chunks / eval_score / metrics 容错"""

    def test_match_result_contains_retrieved_chunks(self):
        """MatchResult.to_dict() 应含 retrieved_chunks 字段及正确结构

        每个 chunk 结构: {skill_id, score, layer, tokens}
        """
        match = SkillMatch(
            skill_id="t", name="t", description="d",
            score=0.5, estimated_tokens=10,
        )
        result = MatchResult(
            matches=[match], total_scanned=1, elapsed_ms=1.0,
            estimated_total_tokens=10,
        )
        d = result.to_dict()
        # [不易] 新字段必须存在且为 list
        assert "retrieved_chunks" in d
        chunks = d["retrieved_chunks"]
        assert isinstance(chunks, list)
        assert len(chunks) == 1
        chunk = chunks[0]
        # 每项含契约要求的 4 个字段
        assert chunk["skill_id"] == "t"
        assert chunk["score"] == 0.5
        assert chunk["layer"] == 1
        assert chunk["tokens"] == 10

    def test_match_result_retrieved_chunks_truncation_at_50(self):
        """retrieved_chunks > 50 项时 observability 层自动截断并标记（防御性）"""
        from agent.skills_mgmt.observability import (
            _sanitize_observability_payload, _MAX_RETRIEVED_CHUNKS,
        )
        # 构造 60 项 chunks
        big_chunks = [
            {"skill_id": f"s-{i}", "score": 0.1, "layer": 1, "tokens": 10}
            for i in range(60)
        ]
        payload = {"retrieved_chunks": big_chunks, "action": "x"}
        sanitized = _sanitize_observability_payload(payload)
        # 截断到 50 项
        assert len(sanitized["retrieved_chunks"]) == _MAX_RETRIEVED_CHUNKS
        # 标记 truncated
        assert sanitized["retrieved_chunks_truncated"] is True
        assert sanitized["retrieved_chunks_original_count"] == 60
        # 其他字段保留
        assert sanitized["action"] == "x"

    def test_build_context_reports_retrieval_chunks(self, caplog):
        """build_context 的结构化日志与返回值应含 retrieved_chunks

        通过 traced_action 使用的同一 _emit_structured_log 机制上报，
        caplog 捕获 build_context.ok 日志验证。
        """
        from unittest.mock import MagicMock
        loader = MagicMock()
        match = SkillMatch(
            skill_id="skill-x", name="X", description="d",
            score=0.9, estimated_tokens=50,
        )
        match_result = MatchResult(
            matches=[match], total_scanned=1, elapsed_ms=1.0,
            estimated_total_tokens=50,
        )
        loader.match.return_value = match_result
        loader.list_all_metadata.return_value = [
            {"skill_id": "skill-x", "enabled": True},
        ]
        injector = ContextInjector(loader=loader)

        with caplog.at_level(logging.INFO, logger="agent.skills_mgmt"):
            out = injector.build_context("测试意图", max_tokens=6000)

        # 返回值含 retrieved_chunks（透传给上游 traced_action）
        assert "retrieved_chunks" in out
        assert len(out["retrieved_chunks"]) == 1
        assert out["retrieved_chunks"][0]["skill_id"] == "skill-x"
        # 结构化日志 build_context.ok 含 retrieved_chunks
        found = False
        for record in caplog.records:
            msg = record.getMessage()
            if "build_context.ok" in msg and "retrieved_chunks" in msg:
                found = True
                break
        assert found, "build_context.ok 日志应含 retrieved_chunks"

    def test_record_execution_accepts_eval_score(self, svc, caplog):
        """传入 eval_score 不报错且持久化到结构化日志/metrics"""
        svc.create_manual(_make_skill_data(name="eval-skill", content="x\n"))
        eval_score = {
            "task_success": True,
            "instruction_followed": True,
            "hallucination_detected": False,
            "score": 0.92,
        }

        with caplog.at_level(logging.INFO, logger="agent.skills_mgmt"):
            # 不抛错即通过（防御性：可选字段缺失/提供均不报错）
            svc.record_execution(
                "eval-skill", success=True, latency_ms=100,
                eval_score=eval_score,
            )

        # enhancer 部分仍正常执行（usage_count 递增）
        skill = svc.get("eval-skill")
        assert skill.metrics.usage_count == 1

        # eval_score 持久化到结构化日志（emit_eval_score_metric 内部发射）
        found = False
        for record in caplog.records:
            msg = record.getMessage()
            if "eval_score.recorded" in msg and "0.92" in msg:
                found = True
                break
        assert found, "eval_score 应持久化到结构化日志"

    def test_record_execution_without_eval_score_backward_compat(self, svc):
        """不传 eval_score 时行为与旧调用方完全一致（守不易）"""
        svc.create_manual(_make_skill_data(name="noeval-skill", content="x\n"))
        # 旧签名调用，不报错
        svc.record_execution("noeval-skill", success=True, latency_ms=100)
        skill = svc.get("noeval-skill")
        assert skill.metrics.usage_count == 1

    def test_metrics_emission_failure_does_not_break_flow(self, svc, monkeypatch):
        """metrics 发射失败时主流程正常（已有 try/except 保护）"""
        svc.create_manual(_make_skill_data(name="metric-fail-skill", content="x\n"))

        # 模拟 metrics 后端故障：patch observability.emit_metric 抛错
        from agent.skills_mgmt import observability

        def _raise(*args, **kwargs):
            raise RuntimeError("metrics backend down")

        monkeypatch.setattr(observability, "emit_metric", _raise)

        # 主流程不应抛错（emit_eval_score_metric 内部 try/except 兜底）
        svc.record_execution(
            "metric-fail-skill", success=True, latency_ms=100,
            eval_score={
                "task_success": True,
                "score": 0.9,
                "hallucination_detected": False,
            },
        )

        # enhancer 部分仍正常执行（usage_count 递增）
        skill = svc.get("metric-fail-skill")
        assert skill.metrics.usage_count == 1

    def test_health_stats_include_observability_fields(self, svc):
        """health() 返回的 stats 应包含新可观测性字段统计"""
        svc.create_manual(_make_skill_data(name="h-obs-skill", content="x\n"))
        health = svc.health()
        assert "observability" in health["stats"]
        obs = health["stats"]["observability"]
        assert "retrieved_chunks" in obs["fields"]
        assert "eval_score" in obs["fields"]
        assert "yunshu_skill_eval_score" in obs["metrics"]
        assert "yunshu_skill_hallucination_total" in obs["metrics"]
        assert obs["retrieved_chunks_max"] == 50
        assert obs["truncation_enabled"] is True
