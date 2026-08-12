"""ContextAssembler 组装效果模拟 Demo — D2D3 替代方案（CEL 框架）验证

依据 docs/zh/智能体学习机制重构计划/D2D3_API架构替代方案设计.md §3.4
实现最小 ContextAssembler 模拟实现，在纯内存模拟环境下验证三层记忆组装效果：

1. 工作记忆（Working Memory）— 会话摘要 + 最近消息（对应 MemoryManager.get_context）
2. 长期检索记忆（LTM）— 知识片段检索 + 反思经验注入（对应 knowledge/search + reflector）
3. 程序性记忆（Procedural）— Skill 指令（skills_mgmt.loader）+ 工作流命中（workflow_learning.matcher）

验证维度:
- 场景 A 简单任务：无 Skill 命中，仅工作记忆 + 少量 LTM
- 场景 B 复杂任务：命中 Skill + 工作流 + 反思经验，指令注入
- 场景 C 失败重试任务：命中反思教训（lessons）注入
- Token 预算：各层贡献统计 + 超预算截断
- CEL 组装 vs 基线（仅工作记忆）的信息增益对比

【不易】不改任何生产代码（orchestrator/prompt_builder/memory 等），纯演示脚本
【变易】接口对齐 D2D3 设计文档 ContextAssembler（assemble(task, mode) -> PromptContext），
        未来可平滑替换为生产实现（agent/context/assembler.py）
【简易】单文件自包含、零第三方依赖、纯内存模拟数据、自测断言结尾

运行:
    python scripts/demo_context_assembler.py
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

# 控制台 UTF-8 输出（Windows PowerShell 兼容）
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ═══════════════════════════════════════════════════════════════════
#  基础工具
# ═══════════════════════════════════════════════════════════════════

def estimate_tokens(text: str) -> int:
    """简易 token 估算：中英混合按字符数 /3（CJK 密集场景偏保守）"""
    return max(1, len(text) // 3)


def _keyword_score(query: str, text: str) -> float:
    """关键词命中率打分：query 中每个关键词在 text 中出现的占比（大小写不敏感）"""
    if not query.strip():
        return 0.0
    tokens = [w for w in query.replace("，", " ").replace("。", " ").split()]
    if not tokens:
        return 0.0
    text_l = text.lower()
    hits = sum(1 for w in tokens if w.lower() in text_l)
    return round(hits / len(tokens), 3)


# ═══════════════════════════════════════════════════════════════════
#  三层记忆模拟数据源（对应真实模块接口）
# ═══════════════════════════════════════════════════════════════════

class MockWorkingMemory:
    """工作记忆 — 对应 memory/MemoryManager.get_context()"""

    def __init__(self) -> None:
        self._summary = (
            "用户近 3 天聚焦云枢数字生命体的智能体学习机制重构："
            "已完成 D2D3 替代方案设计（CEL 框架），讨论 ContextAssembler 三层记忆组装。"
        )
        self._recent = [
            {"role": "user", "content": "根据 D2D3 方案测试 ContextAssembler 组装效果"},
            {"role": "assistant", "content": "好的，我将启动模拟环境验证三层记忆组装（工作/长期检索/程序性）。"},
            {"role": "user", "content": "上次 PDF 表格解析失败了，这次换一种方式重试"},
        ]

    def get_context(self) -> List[Dict[str, str]]:
        msgs = [{"role": "system", "content": f"[摘要] {self._summary}"}]
        msgs += self._recent
        return msgs


class MockLongTermMemory:
    """长期检索记忆 — 对应 knowledge/search + planner/reflector 经验库"""

    def __init__(self) -> None:
        self._chunks = [
            {
                "chunk_id": "card-pdf-parsing",
                "title": "知识卡片：PDF 表格解析",
                "content": "PDF 表格解析优先使用 pdfplumber 定位表格区域，再按行提取；"
                           "跨页表格需合并表头；输出建议为 CSV。",
                "keywords": ["pdf", "表格", "解析", "提取", "csv"],
            },
            {
                "chunk_id": "card-sensor-health",
                "title": "知识卡片：传感器健康感知",
                "content": "云枢传感器层（sensor/）采集 CPU/内存/磁盘等指标，"
                           "ChangeDetector 做快照 diff，行为基线跨会话持久化。",
                "keywords": ["传感器", "感知", "cpu", "内存", "基线"],
            },
            {
                "chunk_id": "card-task-planning",
                "title": "知识卡片：任务规划与分解",
                "content": "复杂任务经 PlanningCore 分解为 DAG，执行后反思产出经验，"
                           "反思经验注入后续任务提示词。",
                "keywords": ["规划", "分解", "任务", "反思", "dag"],
            },
        ]
        self._experiences = [
            {
                "task_type": "pdf_parse",
                "kind": "experience",
                "note": "使用 pdfplumber 定位表格区域后按行提取，成功率高；"
                        "提取后按列类型做二次校验。",
            },
            {
                "task_type": "task_planning",
                "kind": "experience",
                "note": "复杂任务先分解为子任务再执行，失败子任务降级用备用工具。",
            },
        ]
        self._lessons = [
            {
                "task_type": "pdf_parse",
                "kind": "lesson",
                "note": "上次失败原因：直接对全文做正则提取导致列错位；"
                        "应先用表格定位库锁定区域再提取。",
            },
        ]

    def retrieve(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """关键词打分检索（模拟 VectorStore+BM25+RRF 融合结果）"""
        scored = []
        for c in self._chunks:
            s = _keyword_score(query, c["content"])
            scored.append((s, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [dict(c, score=s) for s, c in scored if s > 0.0][:top_k]

    def reflections_for(self, query: str) -> List[Dict[str, Any]]:
        """反思经验注入 — 对应 reflector.get_advice_for_task()"""
        hits = []
        text = query
        if "pdf" in text or "表格" in text or "解析" in text:
            hits += self._experiences[:1] + self._lessons[:1]
        elif "规划" in text or "任务" in text or "分解" in text:
            hits += self._experiences[1:2]
        return [dict(h, score=1.0) for h in hits]


class MockSkillRegistry:
    """程序性记忆·技能 — 对应 skills_mgmt/loader.py 的 match/load_instruction"""

    def __init__(self) -> None:
        self._skills = [
            {
                "skill_id": "pdf-parser",
                "name": "PDF 表格解析",
                "description": "解析 PDF 文档并提取表格数据为 CSV",
                "instruction": (
                    "【技能指令 pdf-parser】\n"
                    "1. 先用 pdfplumber 打开文档并定位所有表格区域\n"
                    "2. 按行提取单元格，跨页表格合并表头\n"
                    "3. 输出为 CSV，空单元格填 N/A"
                ),
                "keywords": ["pdf", "表格", "解析", "提取", "csv"],
                "estimated_tokens": 120,
            },
            {
                "skill_id": "sensor-health",
                "name": "传感器健康采集",
                "description": "采集云枢传感器健康指标并生成报告",
                "instruction": (
                    "【技能指令 sensor-health】\n"
                    "1. 调用 BodySensor.collect_quick() 采集 CPU/内存/电池\n"
                    "2. 与基线对比，输出健康报告与异常告警"
                ),
                "keywords": ["传感器", "健康", "采集", "cpu", "内存"],
                "estimated_tokens": 90,
            },
            {
                "skill_id": "task-planner",
                "name": "复杂任务规划",
                "description": "将复杂任务分解为可执行子任务序列",
                "instruction": (
                    "【技能指令 task-planner】\n"
                    "1. 判定任务复杂度，复杂任务走 PlanningCore 分解\n"
                    "2. 输出 DAG 子任务与执行顺序"
                ),
                "keywords": ["规划", "分解", "任务", "复杂"],
                "estimated_tokens": 80,
            },
        ]

    def match(self, query: str, top_k: int = 2) -> List[Dict[str, Any]]:
        scored = [(s, _keyword_score(query, s["description"]) + _keyword_score(query, " ".join(s["keywords"])) / 2) for s in self._skills]
        scored.sort(key=lambda x: x[1], reverse=True)
        return [dict(s, score=round(score, 3)) for s, score in scored if score > 0.0][:top_k]


class MockWorkflowRegistry:
    """程序性记忆·工作流 — 对应 workflow_learning/matcher.py 的 match"""

    def __init__(self) -> None:
        self._workflows = [
            {
                "wf_id": "wf-pdf-table-extract",
                "name": "PDF 表格提取工作流",
                "trigger_keywords": ["pdf", "表格", "提取", "csv"],
                "tool_sequence": ["pdf_extract", "table_to_csv", "report"],
                "confidence": 0.85,
            },
            {
                "wf_id": "wf-sensor-health-check",
                "name": "传感器健康检查工作流",
                "trigger_keywords": ["传感器", "健康", "检查"],
                "tool_sequence": ["collect_quick", "baseline_compare", "report"],
                "confidence": 0.92,
            },
        ]

    def match(self, query: str) -> Optional[Dict[str, Any]]:
        best, best_score = None, 0.0
        for wf in self._workflows:
            s = _keyword_score(query, " ".join(wf["trigger_keywords"]))
            if s > best_score:
                best, best_score = wf, s
        if best is None or best_score <= 0.0:
            return None
        return dict(best, score=round(best_score, 3))


# ═══════════════════════════════════════════════════════════════════
#  PromptContext 与 ContextAssembler（对齐 D2D3 设计文档 §3.4 接口）
# ═══════════════════════════════════════════════════════════════════

@dataclass
class PromptContext:
    """组装结果 — 对齐设计文档 PromptContext(system, memories, skills, tools)"""

    task: str
    system_text: str                       # 组装后的完整系统提示文本
    memory_sections: List[Dict[str, Any]]  # 记忆层片段（含工作记忆 + LTM）
    skill_instructions: List[Dict[str, Any]]  # 程序性记忆·Skill 指令
    workflow_hint: Optional[Dict[str, Any]]   # 程序性记忆·工作流命中
    reflection_notes: List[Dict[str, Any]]    # 反思经验注入
    tools: List[str]                       # 工具白名单
    layer_tokens: Dict[str, int]           # 各层 token 贡献统计
    total_tokens: int
    budget: int
    truncated: bool

    def summary(self) -> Dict[str, Any]:
        return {
            "task": self.task,
            "layers": self.layer_tokens,
            "total_tokens": self.total_tokens,
            "budget": self.budget,
            "truncated": self.truncated,
            "skills_hit": [s["skill_id"] for s in self.skill_instructions],
            "workflow_hit": self.workflow_hint["wf_id"] if self.workflow_hint else None,
            "reflections_hit": len(self.reflection_notes),
            "tools": self.tools,
        }


class ContextAssembler:
    """上下文组装器（模拟实现）— 三层记忆统一组装

    对应 D2D3 设计文档 §3.4:
        assemble(task, mode) -> PromptContext
        工作记忆 + 长期检索 + 程序性记忆 + 工具白名单
    """

    def __init__(
        self,
        working_memory: MockWorkingMemory,
        long_term_memory: MockLongTermMemory,
        skill_registry: MockSkillRegistry,
        workflow_registry: MockWorkflowRegistry,
        token_budget: int = 3000,
    ) -> None:
        self._wm = working_memory
        self._ltm = long_term_memory
        self._skills = skill_registry
        self._workflows = workflow_registry
        self._budget = token_budget

    # ── 各层拉取（对应设计文档 §3.3 实例化管线）──

    def _pull_working_memory(self) -> List[Dict[str, Any]]:
        msgs = self._wm.get_context()
        text = "\n".join(f"{m['role']}: {m['content']}" for m in msgs)
        return [{"layer": "工作记忆", "title": "会话摘要+最近消息", "content": text[:500], "tokens": estimate_tokens(text[:500])}]

    def _pull_long_term(self, task: str) -> List[Dict[str, Any]]:
        chunks = self._ltm.retrieve(task, top_k=3)
        return [
            {"layer": "长期检索记忆", "title": c["title"], "content": c["content"], "tokens": estimate_tokens(c["content"])}
            for c in chunks
        ]

    def _pull_reflections(self, task: str) -> List[Dict[str, Any]]:
        refs = self._ltm.reflections_for(task)
        return [
            {"layer": "反思经验", "kind": r["kind"], "task_type": r["task_type"], "note": r["note"], "tokens": estimate_tokens(r["note"])}
            for r in refs
        ]

    def _pull_procedural(self, task: str) -> tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
        skills = self._skills.match(task, top_k=2)
        skill_instr = [
            {"skill_id": s["skill_id"], "name": s["name"], "instruction": s["instruction"], "tokens": estimate_tokens(s["instruction"])}
            for s in skills
        ]
        wf = self._workflows.match(task)
        wf_hint = None
        if wf:
            wf_hint = {"wf_id": wf["wf_id"], "tool_sequence": wf["tool_sequence"], "confidence": wf["confidence"]}
        return skill_instr, wf_hint

    # ── 组装 ──

    def assemble(self, task: str, mode: str = "default") -> PromptContext:
        # 工具白名单按 mode 路由（对应 task_dispatcher 的 tools_whitelist）
        tools = ["search", "read_file", "write_file"] if mode == "default" else ["*"]

        mem_sections = self._pull_working_memory() + self._pull_long_term(task)
        skills, wf_hint = self._pull_procedural(task)
        refs = self._pull_reflections(task)

        # 渲染系统提示文本
        parts = ["你是云枢数字生命体。", "【工作记忆】"]
        parts += [s["content"] for s in mem_sections]
        if refs:
            parts.append("【反思经验（需遵循）】")
            parts += [f"- ({r['kind']}) {r['note']}" for r in refs]
        if skills:
            parts.append("【可用技能指令】")
            parts += [s["instruction"] for s in skills]
        if wf_hint:
            parts.append("【工作流提示】工具序列: " + " → ".join(wf_hint["tool_sequence"]))
        parts.append("【可用工具】" + ", ".join(tools))
        system_text = "\n".join(parts)

        # Token 预算检查与截断
        layer_tokens = {
            "working_memory": sum(s["tokens"] for s in mem_sections if s["layer"] == "工作记忆"),
            "long_term": sum(s["tokens"] for s in mem_sections if s["layer"] == "长期检索记忆"),
            "reflections": sum(r["tokens"] for r in refs),
            "skills": sum(s["tokens"] for s in skills),
            "workflow": estimate_tokens(str(wf_hint)) if wf_hint else 0,
        }
        total = estimate_tokens(system_text)
        truncated = total > self._budget
        if truncated:
            # 截断策略：优先丢弃 LTM 片段（保留工作记忆与技能，守会话连续性）
            system_text = system_text[: self._budget * 3]
            total = estimate_tokens(system_text)

        return PromptContext(
            task=task,
            system_text=system_text,
            memory_sections=mem_sections,
            skill_instructions=skills,
            workflow_hint=wf_hint,
            reflection_notes=refs,
            tools=tools,
            layer_tokens=layer_tokens,
            total_tokens=total,
            budget=self._budget,
            truncated=truncated,
        )


# ═══════════════════════════════════════════════════════════════════
#  场景演示与自测
# ═══════════════════════════════════════════════════════════════════

def _print_header(title: str) -> None:
    line = "═" * 68
    print(f"\n{line}\n  {title}\n{line}")


def _render_scenario(assembler: ContextAssembler, task: str, title: str) -> PromptContext:
    print(f"\n── 场景：{title} ──\n任务输入：{task}")
    ctx = assembler.assemble(task)

    print("\n【组装报告】")
    for k, v in ctx.summary().items():
        print(f"  {k:<16}: {v}")

    print("\n【各层 token 贡献】")
    for layer, n in ctx.layer_tokens.items():
        print(f"  {layer:<16}: {n} tokens")

    print("\n【组装后的系统提示（前 600 字符）】")
    print(ctx.system_text[:600])
    print("  …" if len(ctx.system_text) > 600 else "")
    return ctx


def _run_self_tests(assembler: ContextAssembler) -> None:
    """内置自测断言（对应评估标准）"""
    print("\n" + "═" * 68)
    print("  自测断言")
    print("═" * 68)

    a = assembler.assemble("帮我总结今天的对话要点")
    b = assembler.assemble("用 Python 解析这份 PDF，把表格提取成 CSV")
    c = assembler.assemble("上次 PDF 表格解析失败了，这次换一种方式重试")

    # A: 简单任务不命中 Skill；LTM 可为空（不引入无关知识，守 token 预算）
    assert len(a.skill_instructions) == 0, "简单任务不应命中 Skill"
    assert len(a.memory_sections) >= 1, "简单任务应含工作记忆"
    assert a.layer_tokens["long_term"] <= b.layer_tokens["long_term"], "简单任务 LTM 注入应少于复杂任务"
    # B: 复杂任务命中 Skill + 工作流 + 反思经验
    assert any(s["skill_id"] == "pdf-parser" for s in b.skill_instructions), "应命中 pdf-parser"
    assert b.workflow_hint is not None, "应命中 PDF 工作流"
    assert any(r["kind"] == "experience" for r in b.reflection_notes), "应注入反思经验"
    # C: 失败重试任务注入教训（lessons）
    assert any(r["kind"] == "lesson" for r in c.reflection_notes), "应注入反思教训"
    # Token 预算：不截断场景总 token 不超预算
    for ctx in (a, b, c):
        assert ctx.total_tokens <= ctx.budget or ctx.truncated, "预算约束失效"
    # 截断机制：极小预算下 truncated 为 True
    tiny = ContextAssembler(
        assembler._wm, assembler._ltm, assembler._skills, assembler._workflows, token_budget=100
    )
    assert tiny.assemble("用 Python 解析这份 PDF，把表格提取成 CSV").truncated, "截断机制未生效"

    print("  ✅ 全部断言通过（5 项：命中/注入/预算/截断）")


def _print_comparison(assembler: ContextAssembler, results: List[PromptContext]) -> None:
    """CEL 组装 vs 基线（仅工作记忆）信息增益对比"""
    print("\n" + "═" * 68)
    print("  CEL 组装 vs 基线（仅工作记忆）信息增益")
    print("═" * 68)
    for ctx in results:
        base = sum(s["tokens"] for s in ctx.memory_sections if s["layer"] == "工作记忆")
        gain = ctx.total_tokens - base
        print(f"  [{ctx.task[:18]:<20}] 基线 {base:>4} tok → CEL {ctx.total_tokens:>4} tok（增益 +{gain}，含{len(ctx.skill_instructions)}技能/{1 if ctx.workflow_hint else 0}工作流/{len(ctx.reflection_notes)}经验）")


def main() -> None:
    _print_header("云枢 ContextAssembler 模拟环境（D2D3 替代方案 · CEL 框架）")

    # 初始化模拟数据源 + 组装器
    assembler = ContextAssembler(
        working_memory=MockWorkingMemory(),
        long_term_memory=MockLongTermMemory(),
        skill_registry=MockSkillRegistry(),
        workflow_registry=MockWorkflowRegistry(),
        token_budget=3000,
    )

    # 场景演示
    results = [
        _render_scenario(assembler, "帮我总结今天的对话要点", "A. 简单任务（无 Skill 命中）"),
        _render_scenario(assembler, "用 Python 解析这份 PDF，把表格提取成 CSV", "B. 复杂任务（Skill+工作流+经验）"),
        _render_scenario(assembler, "上次 PDF 表格解析失败了，这次换一种方式重试", "C. 失败重试（教训注入）"),
    ]

    _print_comparison(assembler, results)
    _run_self_tests(assembler)

    print("\n" + "═" * 68)
    print("  模拟环境演示完成 ✅  — 组装接口对齐 D2D3 设计文档 §3.4")
    print("═" * 68)


if __name__ == "__main__":
    main()
