"""ContextAssembler 单元测试 — 生产模块组装效果 + orchestrator 接线（D2D3 CEL 框架）

覆盖（对应 scripts/demo_context_assembler.py 验证的组装效果 + 生产特性）:
- 场景 A 简单任务：无技能命中，工作记忆注入
- 场景 B 复杂任务：技能命中 + 反思经验 + 工作流注入
- 分级注入：简单任务注入量少于复杂任务（守 token 预算）
- 预算与截断：极小预算触发 truncated
- 降级：provider 缺失/异常 → 对应层为空，assemble 不抛异常
- render_text：输出包含各层区块标记
- orchestrator 接线：enabled=false → None；enabled=true → 组装文本；异常 → None
- 配置：config.yaml learning.context_assembler 默认关闭
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from agent.context.assembler import ContextAssembler


# ═══════════════════════════════════════════════════════════════════
#  模拟数据源（与 demo 同构）
# ═══════════════════════════════════════════════════════════════════

class MockWorkingMemory:
    def get_context(self, token_limit):
        return [
            {"role": "system", "content": "[摘要] 用户聚焦智能体学习机制重构"},
            {"role": "user", "content": "根据 D2D3 方案测试 ContextAssembler"},
        ]


def _long_term_pdf(task: str) -> list:
    if "pdf" in task.lower() or "表格" in task:
        return [
            {"layer": "长期检索记忆", "title": "卡片：PDF 表格解析",
             "content": "PDF 表格解析优先使用 pdfplumber 定位表格区域，再按行提取。"},
            {"layer": "反思经验", "title": "lesson·pdf_parse",
             "content": "上次失败：直接正则提取导致列错位，应先用定位库锁定区域。"},
        ]
    return []


def _procedural_pdf(task: str):
    if "pdf" in task.lower() or "表格" in task:
        return (
            [{"skill_id": "pdf-parser", "name": "PDF 表格解析",
              "instruction": "1. 用 pdfplumber 定位表格区域\n2. 按行提取输出 CSV"}],
            {"wf_id": "wf-pdf-table-extract", "tool_sequence": ["pdf_extract", "table_to_csv", "report"]},
        )
    return [], None


def _make_assembler(**kwargs) -> ContextAssembler:
    return ContextAssembler(
        working_memory_fn=lambda: MockWorkingMemory().get_context(8000),
        long_term_fn=_long_term_pdf,
        procedural_fn=_procedural_pdf,
        **kwargs,
    )


# ═══════════════════════════════════════════════════════════════════
#  组装效果（对应 demo 3 场景验证）
# ═══════════════════════════════════════════════════════════════════

class TestAssembleEffect:
    def test_simple_task_no_skill_hit(self):
        ctx = _make_assembler().assemble("帮我总结今天的对话要点")
        assert ctx.skill_instructions == []
        assert ctx.workflow_hint is None
        assert any(s["layer"] == "工作记忆" for s in ctx.memory_sections), "简单任务仍应含工作记忆"

    def test_complex_task_hits_skill_workflow_reflection(self):
        ctx = _make_assembler().assemble("用 Python 解析这份 PDF，把表格提取成 CSV")
        assert any(s["skill_id"] == "pdf-parser" for s in ctx.skill_instructions), "应命中 pdf-parser"
        assert ctx.workflow_hint is not None, "应命中工作流"
        assert any(r["layer"] == "反思经验" for r in ctx.reflection_notes), "应注入反思经验"

    def test_failure_retry_injects_lesson(self):
        ctx = _make_assembler().assemble("上次 PDF 解析失败了，换一种方式重试")
        assert any(r["layer"] == "反思经验" for r in ctx.reflection_notes)

    def test_graded_injection_simple_less_than_complex(self):
        simple = _make_assembler().assemble("帮我总结今天的对话要点")
        complex_ = _make_assembler().assemble("用 Python 解析这份 PDF，把表格提取成 CSV")
        simple_inject = simple.layer_tokens["long_term"] + simple.layer_tokens["skills"] + simple.layer_tokens["reflections"]
        complex_inject = complex_.layer_tokens["long_term"] + complex_.layer_tokens["skills"] + complex_.layer_tokens["reflections"]
        assert simple_inject <= complex_inject, "简单任务注入应少于复杂任务"

    def test_token_budget_truncation(self):
        ctx = _make_assembler(token_budget=100).assemble("用 Python 解析这份 PDF，把表格提取成 CSV")
        assert ctx.truncated is True
        assert ctx.total_tokens <= ctx.budget, "截断后不得超预算"


# ═══════════════════════════════════════════════════════════════════
#  生产特性：组件缺失/异常 → 空层降级，不抛异常
# ═══════════════════════════════════════════════════════════════════

class TestDegradation:
    def test_no_providers(self):
        ctx = ContextAssembler().assemble("任意任务")
        assert ctx.memory_sections == []
        assert ctx.skill_instructions == []
        assert ctx.workflow_hint is None
        assert not ctx.truncated

    def test_provider_exception_degrades_to_empty(self):
        def boom(_arg=None):
            raise RuntimeError("boom")

        assembler = ContextAssembler(
            working_memory_fn=boom,
            long_term_fn=boom,
            procedural_fn=boom,
        )
        ctx = assembler.assemble("任意任务")  # 不得抛异常
        assert ctx.memory_sections == []
        assert ctx.skill_instructions == []

    def test_render_text_contains_sections(self):
        assembler = _make_assembler()
        ctx = assembler.assemble("用 Python 解析这份 PDF，把表格提取成 CSV")
        text = assembler.render_text(ctx)
        assert "【ContextAssembler 增强上下文】" in text
        assert "技能指令" in text
        assert "工作流提示" in text
        assert "上下文统计" in text


# ═══════════════════════════════════════════════════════════════════
#  orchestrator 接线（轻量实例，不初始化完整 Orchestrator）
# ═══════════════════════════════════════════════════════════════════

class TestOrchestratorWiring:
    @staticmethod
    def _make_orchestrator(enabled: bool, memory=None):
        from unittest import mock
        from agent.orchestrator.orchestrator import Orchestrator

        o = Orchestrator.__new__(Orchestrator)
        o._memory = memory
        o._memory_token_limit = 8000
        o._ctx_skills_loader = None
        o._load_context_assembler_config = mock.Mock(
            return_value={"enabled": enabled, "token_budget": 3000})
        return o

    def test_disabled_returns_none(self):
        o = self._make_orchestrator(enabled=False)
        assert o._context_assembler_extra("任意任务") is None, "默认关闭时主链路零影响"

    def test_enabled_returns_extra_text(self):
        o = self._make_orchestrator(enabled=True, memory=MockWorkingMemory())
        o._context_assembler_long_term = lambda task: [
            {"layer": "长期检索记忆", "title": "卡片：PDF 表格解析",
             "content": "PDF 表格解析优先使用 pdfplumber 定位表格区域。"}]
        o._context_assembler_procedural = lambda task: (
            [{"skill_id": "pdf-parser", "name": "PDF 表格解析",
              "instruction": "1. 用 pdfplumber 定位表格区域\n2. 按行提取输出 CSV"}], None)

        extra = o._context_assembler_extra("解析这份 PDF")
        assert extra is not None
        assert "技能指令" in extra
        assert "工作记忆" in extra

    def test_metrics_recorded_on_injection(self):
        """Prometheus 指标回归：注入成功必须递增 injected_total 并记录 token（持续观察护城河）"""
        from agent.monitoring import prometheus as _P

        o = self._make_orchestrator(enabled=True, memory=MockWorkingMemory())
        o._context_assembler_long_term = lambda task: [
            {"layer": "长期检索记忆", "title": "卡片：PDF 表格解析",
             "content": "PDF 表格解析优先使用 pdfplumber 定位表格区域。"}]
        o._context_assembler_procedural = lambda task: (
            [{"skill_id": "pdf-parser", "name": "PDF 表格解析",
              "instruction": "1. 用 pdfplumber 定位表格区域\n2. 按行提取输出 CSV"}], None)

        _inj_before = _P.context_assembler_injected_total._value.get()
        _tok_before = _P.context_assembler_injected_tokens._value.get()

        extra = o._context_assembler_extra("解析这份 PDF")
        assert extra is not None

        assert _P.context_assembler_injected_total._value.get() - _inj_before >= 1, \
            "注入成功但 injected_total 未递增（埋点失效）"
        assert _P.context_assembler_injected_tokens._value.get() > _tok_before, \
            "注入成功但 token gauge 未更新"

    def test_metrics_recorded_on_degraded(self):
        """指标回归：组装异常必须递增 degraded_total（告警源）

        说明: provider 层异常会被 assemble 内部捕获降级为"空"（不计 degraded），
        degraded 仅在 assemble 本身抛异常时触发（真实异常路径）。
        """
        from unittest import mock
        from agent.context import assembler as _asm_mod
        from agent.monitoring import prometheus as _P

        o = self._make_orchestrator(enabled=True, memory=MockWorkingMemory())
        o._context_assembler_long_term = lambda task: [
            {"layer": "长期检索记忆", "title": "卡片：PDF 表格解析",
             "content": "PDF 表格解析优先使用 pdfplumber 定位表格区域。"}]
        o._context_assembler_procedural = lambda task: (
            [{"skill_id": "pdf-parser", "name": "PDF 表格解析",
              "instruction": "1. 用 pdfplumber 定位表格区域\n2. 按行提取输出 CSV"}], None)

        _deg_before = _P.context_assembler_degraded_total._value.get()
        with mock.patch.object(_asm_mod.ContextAssembler, "assemble",
                               side_effect=RuntimeError("boom")):
            assert o._context_assembler_extra("任意") is None, "异常必须静默降级"
        assert _P.context_assembler_degraded_total._value.get() - _deg_before >= 1, \
            "降级但 degraded_total 未递增（告警源失效）"

    def test_exception_degrades_to_none(self):
        from unittest import mock

        o = self._make_orchestrator(enabled=True, memory=None)
        o._context_assembler_long_term = mock.Mock(side_effect=RuntimeError("boom"))
        o._context_assembler_procedural = mock.Mock(side_effect=RuntimeError("boom"))
        assert o._context_assembler_extra("任意") is None, "异常必须静默降级"


# ═══════════════════════════════════════════════════════════════════
#  配置约束
# ═══════════════════════════════════════════════════════════════════

class TestConfig:
    def test_context_assembler_enabled_in_observation_mode(self):
        import yaml

        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        with open(os.path.join(repo_root, "config.yaml"), encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        lc = cfg.get("learning", {}).get("context_assembler", {})
        assert lc.get("enabled") is True, "集成验证通过后观察模式已开启"
        assert int(lc.get("token_budget", 0)) > 0
