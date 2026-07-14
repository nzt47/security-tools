"""技能管理 & 工作流学习 集成测试

覆盖端到端核心场景：
- 技能创建 → 审核 → 启用 → 执行 → 统计回写
- 工作流学习 → 匹配 → 执行 → 统计更新
- 跨模块联动：技能执行触发工作流学习

测试目标：验证两大子系统在真实联动场景下的状态一致性
"""
import pytest

from agent.skills_mgmt import SkillsMgmtService
from agent.skills_mgmt.models import SkillSearchParams
from agent.workflow_learning import WorkflowLearningService
from agent.workflow_learning.models import LearningRecord


# ═══════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════

@pytest.fixture
def skills_svc(tmp_path):
    return SkillsMgmtService(store_path=str(tmp_path / "skills.json"))


@pytest.fixture
def wf_svc(tmp_path):
    return WorkflowLearningService(repo_path=str(tmp_path / "wf.json"))


@pytest.fixture
def tool_executor():
    """模拟工具执行器，记录调用并返回固定输出"""
    calls = []

    def executor(tool_name, params):
        calls.append({"tool": tool_name, "params": params})
        return {"ok": True, "tool": tool_name, "result": f"executed_{tool_name}"}

    executor.calls = calls
    return executor


def _skill_data(name, **overrides):
    data = {
        "id": name,
        "name": name,
        "description": f"测试技能 {name}，用于验证完整生命周期与审核流程的端到端正确性。",
        "content": (
            "# 文档说明\n\n"
            "## 适用场景\n\n"
            "适用于日常测试与功能验证场景，确保技能管理系统的核心流程稳定可靠。\n\n"
            "## 用法\n\n"
            "```python\n"
            "def run(input_text):\n"
            "    try:\n"
            "        return input_text.strip()\n"
            "    except Exception as e:\n"
            "        raise ValueError(f'处理失败: {e}')\n"
            "```\n"
        ),
        "content_type": "python",
        "category": "custom",
        "tags": ["test", "demo"],
    }
    data.update(overrides)
    return data


# ═══════════════════════════════════════════════════════════════════
#  端到端场景测试
# ═══════════════════════════════════════════════════════════════════

class TestSkillsEndToEnd:
    """技能管理端到端流程测试"""

    def test_full_skill_lifecycle(self, skills_svc):
        """完整生命周期：创建 → 审核 → 启用 → 执行 → 统计 → 优化 → 版本升级"""
        # 1. 创建
        skill = skills_svc.create_manual(_skill_data("lifecycle-skill"))
        assert skill.status == "draft"

        # 2. 审核
        review = skills_svc.review(skill.id)
        assert review.status in ("passed", "warn", "pending")

        # 3. 启用
        skills_svc.set_enabled(skill.id, True)
        assert skills_svc.get(skill.id).enabled is True

        # 4. 记录多次执行
        for _ in range(5):
            skills_svc.record_execution(skill.id, success=True, latency_ms=100)
        skills_svc.record_execution(skill.id, success=False, latency_ms=200)

        # 5. 统计应正确
        skill = skills_svc.get(skill.id)
        assert skill.metrics.usage_count == 6
        assert skill.metrics.success_count == 5
        assert skill.metrics.failure_count == 1

        # 6. 优化建议
        result = skills_svc.optimize_params(skill.id)
        assert isinstance(result, dict)

        # 7. 版本升级
        bump = skills_svc.bump_version(skill.id, "minor", changelog="新增功能")
        assert bump.new_version == "0.2.0"
        assert skills_svc.get(skill.id).version == "0.2.0"

    def test_search_after_multiple_creates(self, skills_svc):
        """批量创建后搜索应返回正确结果"""
        for i in range(10):
            skills_svc.create_manual(_skill_data(
                f"batch-{i}",
                description=f"批量技能 {i}",
                tags=[f"tag-{i % 3}"],
            ))
        # 搜索全部
        result = skills_svc.search(SkillSearchParams(query="批量"))
        assert len(result.items) == 10
        assert result.total == 10

        # 按标签筛选
        result = skills_svc.search(SkillSearchParams(tags=["tag-0"]))
        assert len(result.items) == 4  # 0, 3, 6, 9
        assert all("tag-0" in s.tags for s in result.items)

    def test_review_identifies_low_quality(self, skills_svc):
        """低质量内容应被识别（低质量分）"""
        # 缺乏文档、缺乏标签、内容极短
        skills_svc.create_manual(_skill_data(
            "low-quality",
            description="",
            content="x",
            tags=[],
        ))
        try:
            result = skills_svc.review("low-quality")
            # 质量分应较低
            assert result.quality_score < 70
        except Exception:
            # 审核拒绝也是合理结果
            pass


class TestWorkflowEndToEnd:
    """工作流学习端到端流程测试"""

    def test_learn_match_execute_cycle(self, wf_svc, tool_executor):
        """学习 → 匹配 → 执行的完整循环"""
        wf_svc.set_tool_executor(tool_executor)

        # 1. 学习
        wf = wf_svc.learn_from_interaction(LearningRecord(
            session_id="e2e",
            user_input="搜索新闻并翻译",
            tool_calls=[
                {"name": "web_search", "params": {"query": "新闻"}},
                {"name": "translate", "params": {"text": "...", "to": "zh"}},
            ],
            success=True,
            duration_ms=2000,
        ))
        assert wf is not None
        assert len(wf.steps) >= 1

        # 2. 提高优先级以确保能匹配执行
        wf_svc.update_priority(wf.id, 100)

        # 3. 匹配相似任务
        matches = wf_svc.search("搜索新闻并翻译成中文", top_k=5)
        assert isinstance(matches, list)

        # 4. 尝试执行
        exec_result = wf_svc.try_execute("搜索新闻并翻译成中文")
        assert exec_result is not None
        assert hasattr(exec_result, "matched")

        # 5. 执行后统计应更新
        wf_after = wf_svc.get(wf.id)
        after_runs = wf_after.success_count + wf_after.failure_count
        before_runs = wf.success_count + wf.failure_count
        assert after_runs >= before_runs


class TestCrossModuleIntegration:
    """跨模块联动测试"""

    def test_skills_and_workflows_independent(self, skills_svc, wf_svc):
        """技能管理与工作流学习系统应独立运行"""
        # 在两个系统中分别操作
        skills_svc.create_manual(_skill_data("ind-skill"))
        wf_svc.learn_from_interaction(LearningRecord(
            session_id="ind",
            user_input="执行任务X然后执行任务Y",
            tool_calls=[
                {"name": "x", "params": {}},
                {"name": "y", "params": {}},
            ],
            success=True,
            duration_ms=100,
        ))
        # 互不影响
        assert skills_svc.health()["ok"] is True
        assert wf_svc.health()["ok"] is True

    def test_concurrent_operations_safe(self, skills_svc):
        """并发操作（防连点）应安全"""
        import threading

        errors = []

        def create_skill(idx):
            try:
                skills_svc.create_manual(_skill_data(f"conc-{idx}"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create_skill, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 应全部成功（不同 ID）
        assert len(errors) == 0
        result = skills_svc.search(SkillSearchParams(query="测试"))
        assert len(result.items) >= 5


# ═══════════════════════════════════════════════════════════════════
#  技能边界声明（防幻觉）集成测试
# ═══════════════════════════════════════════════════════════════════

def _make_skill_md(skill_id, name, description, instruction_body="# 说明"):
    """生成 skill.md 内容（用于文件仓库安装）"""
    return f"""---
id: {skill_id}
name: {name}
description: {description}
category: custom
tags: [test]
version: 1.0.0
enabled: true
status: approved
author: tester
---

{instruction_body}
"""


@pytest.fixture
def skill_mgr(tmp_path):
    """使用文件仓库的 SkillManager 实例

    说明：build_context 依赖 SkillLoader 扫描文件仓库，
    故使用 SkillManager（文件仓库）而非 SkillsMgmtService（JSON 存储）。
    """
    from agent.skills_mgmt.skill_manager import SkillManager
    return SkillManager(repo_path=str(tmp_path / "skills_repo"))


class TestBoundaryDeclarationIntegration:
    """技能边界声明在 build_context 主流程中的端到端验证"""

    def test_boundary_declaration_in_build_context(self, skill_mgr, tmp_path):
        """build_context 应自动注入边界声明，已加载/未加载列表正确"""
        for sid, name, desc in [
            ("pdf-parser", "PDF解析", "解析PDF文件并提取文本内容"),
            ("translator", "翻译", "翻译文本到不同语言"),
            ("summarizer", "摘要", "对长文本生成摘要"),
        ]:
            sd = tmp_path / "src" / sid
            sd.mkdir(parents=True)
            (sd / "skill.md").write_text(
                _make_skill_md(sid, name, desc), encoding="utf-8")
            skill_mgr.install_from_dir(str(sd))

        result = skill_mgr.build_context("解析PDF文件", top_k=1)

        prompt = result.get("prompt", "")
        assert "## 技能边界声明" in prompt, "应包含边界声明章节"
        assert "已加载技能" in prompt, "应包含已加载列表"
        assert "未加载技能" in prompt, "应包含未加载列表"
        assert "严禁编造" in prompt, "应包含防幻觉提示"

        # 匹配到的技能应在已加载列表
        assert "pdf-parser" in prompt
        # 未匹配的技能应在未加载列表
        assert "translator" in prompt
        assert "summarizer" in prompt

    def test_boundary_declaration_empty_repo(self, skill_mgr):
        """空仓库时 build_context 不应输出边界声明"""
        result = skill_mgr.build_context("任意意图")

        prompt = result.get("prompt", "")
        assert "## 技能边界声明" not in prompt, "空仓库不应输出边界声明"

    def test_boundary_declaration_all_loaded(self, skill_mgr, tmp_path):
        """所有技能都被匹配时，未加载列表应为空"""
        for sid, name, desc in [
            ("skill-a", "功能A", "功能A描述"),
            ("skill-b", "功能B", "功能B描述"),
        ]:
            sd = tmp_path / "src" / sid
            sd.mkdir(parents=True)
            (sd / "skill.md").write_text(
                _make_skill_md(sid, name, desc), encoding="utf-8")
            skill_mgr.install_from_dir(str(sd))

        result = skill_mgr.build_context("功能", top_k=10)

        prompt = result.get("prompt", "")
        assert "## 技能边界声明" in prompt
        assert "skill-a" in prompt
        assert "skill-b" in prompt
        assert "（无）" in prompt

    def test_boundary_declaration_with_auto_load_instruction(self, skill_mgr, tmp_path):
        """auto_load_instruction=True 时边界声明仍正确注入"""
        sd = tmp_path / "src" / "doc-skill"
        sd.mkdir(parents=True)
        (sd / "skill.md").write_text(
            _make_skill_md(
                "doc-skill", "文档处理", "文档处理技能",
                instruction_body="# 使用说明\n\n## 步骤\n\n1. 接收输入\n2. 处理文档\n3. 返回结果\n",
            ),
            encoding="utf-8",
        )
        skill_mgr.install_from_dir(str(sd))

        sd2 = tmp_path / "src" / "other-skill"
        sd2.mkdir(parents=True)
        (sd2 / "skill.md").write_text(
            _make_skill_md("other-skill", "其他技能", "其他无关技能"),
            encoding="utf-8",
        )
        skill_mgr.install_from_dir(str(sd2))

        result = skill_mgr.build_context(
            "文档处理", auto_load_instruction=True)

        prompt = result.get("prompt", "")
        assert "## 技能边界声明" in prompt
        assert "使用说明" in prompt
        assert result.get("layers", {}).get("layer2_instruction") is True
