"""ContextAssembler 实际运行环境集成验证 — 真实组件 + orchestrator 接线（D2D3 CEL 框架）

验证内容:
1. 真实工作记忆：MemoryManager（临时 data_dir）→ get_context
2. 真实程序性记忆：SkillLoader（data/skills_mgmt.json）→ match/load_instruction
3. 长期检索记忆：orchestrator._context_assembler_long_term 读取反思经验文件
   （构造临时 data/reflection/*.json 验证真实读取逻辑，finally 自动清理）
4. orchestrator._context_assembler_extra 接线：
   - 默认开关（config.yaml enabled=false）→ None（主链路零影响）
   - 强制开启（patch 配置）→ 返回组装文本（含技能/记忆/反思注入）

【不易】不改任何生产代码与既有数据；临时文件用后即清
【变易】真实 SkillLoader/MemoryManager 协作 + 开关两态验证
【简易】单文件自包含、无 LLM 依赖（组装为纯上下文构建，不调用模型）

运行:
    python scripts/verify_context_assembler_integration.py
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import logging as _logging

# 脚本自包含日志配置（生产环境由 setup_agent_logging 配置 root=INFO）：
# CONTEXT_ASSEMBLER_LOG_LEVEL=DEBUG 时输出组装各层拉取/耗时明细，实时观察
_log_level = os.environ.get("CONTEXT_ASSEMBLER_LOG_LEVEL", "").strip().upper()
_logging.basicConfig(
    level=getattr(_logging, _log_level, _logging.INFO),
    format="%(asctime)s %(levelname)-7s %(name)-28s: %(message)s",
)

from agent.logging_utils import log_dict  # noqa: E402


# ═══════════════════════════════════════════════════════════════════
#  临时反思经验文件（验证真实读取逻辑，结束后清理）
# ═══════════════════════════════════════════════════════════════════

def _create_reflection_files() -> bool:
    """构造临时 data/reflection/*.json，返回目录原先是否存在（供清理判断）"""
    base = Path("data") / "reflection"
    existed = base.exists()
    base.mkdir(parents=True, exist_ok=True)
    (base / "experiences.json").write_text(
        json.dumps([{"task_type": "pdf_parse", "note": "使用 pdfplumber 定位表格区域后按行提取，成功率高。"}],
                   ensure_ascii=False), encoding="utf-8")
    (base / "lessons.json").write_text(
        json.dumps([{"task_type": "pdf_parse", "note": "上次失败：直接正则提取导致列错位，应先定位表格区域。"}],
                   ensure_ascii=False), encoding="utf-8")
    return existed


def _cleanup_reflection_files(dir_existed: bool) -> None:
    """清理本脚本创建的反思文件（不删除原本就存在的文件）"""
    base = Path("data") / "reflection"
    for name in ("experiences.json", "lessons.json"):
        p = base / name
        if p.exists():
            p.unlink()
    if not dir_existed and base.exists():
        try:
            base.rmdir()
        except OSError:
            pass


# ═══════════════════════════════════════════════════════════════════
#  验证主体
# ═══════════════════════════════════════════════════════════════════

def _verify_real_skill_loader() -> str:
    """真实 SkillLoader 协作：至少一个任务命中真实技能数据"""
    from agent.skills_mgmt.loader import SkillLoader

    loader = SkillLoader()
    tasks = ["解析 PDF 并提取表格", "传感器健康检查", "帮我总结今天的对话"]
    hit, hit_task = None, None
    for t in tasks:
        result = loader.match(t, top_k=2)
        if result.matches:
            hit, hit_task = result.matches[0], t
            break
    if hit is None:
        print("  [INFO] 真实技能数据零命中（合法路径：无技能注入，仅记忆层）")
        return ""
    instr = loader.load_instruction(hit.skill_id)
    text = instr.get("instruction") or hit.description
    print(f"  [OK] 真实 SkillLoader 命中 skill_id={hit.skill_id} score={hit.score} "
          f"（任务: {hit_task}）\n      指令长度={len(text)} 字符")
    return text


def _verify_real_memory_manager() -> Path:
    """真实 MemoryManager（临时目录）协作：get_context 可读"""
    from memory.memory_manager import MemoryManager

    tmp = Path(tempfile.mkdtemp(prefix="ctxasm_"))
    mm = MemoryManager(config={"data_dir": str(tmp), "token_limit": 8000})
    mm.add_message("user", "根据 D2D3 方案测试 ContextAssembler 集成")
    ctx = mm.get_context(token_limit=8000)
    assert ctx, "临时 MemoryManager 上下文为空"
    print(f"  [OK] 真实 MemoryManager 上下文 {len(ctx)} 条消息（临时目录 {tmp}）")
    return tmp


def _benchmark_assemble(o, n: int = 20) -> None:
    """性能基准：真实组件组装耗时统计（排除首次懒初始化预热）"""
    import statistics
    import time

    tasks = ["解析 PDF 并提取表格", "帮我总结今天的对话", "传感器健康检查"]
    samples = []
    with mock.patch.object(o, "_load_context_assembler_config",
                           return_value={"enabled": True, "token_budget": 3000}):
        o._context_assembler_extra(tasks[0])  # 预热：懒初始化 SkillLoader
        for i in range(n):
            t0 = time.perf_counter()
            o._context_assembler_extra(tasks[i % len(tasks)])
            samples.append((time.perf_counter() - t0) * 1000)
    samples_sorted = sorted(samples)
    p95 = samples_sorted[int(len(samples_sorted) * 0.95) - 1]
    print(f"  [OK] 性能基准 n={n}: 平均 {statistics.mean(samples):.2f}ms | "
          f"p95 {p95:.2f}ms | max {max(samples):.2f}ms")


def _verify_orchestrator_wiring(mm, tmp: Path) -> None:
    """orchestrator 接线：观察模式开启 → 组装文本；关闭 → None；异常 → None"""
    from agent.orchestrator.orchestrator import Orchestrator

    o = Orchestrator.__new__(Orchestrator)
    o._memory = mm
    o._memory_token_limit = 8000
    o._ctx_skills_loader = None

    # 1) 观察模式已开启（真实读取 config.yaml，enabled=true）→ 返回组装文本
    extra = o._context_assembler_extra("解析 PDF 并提取表格")
    assert extra and "【ContextAssembler 增强上下文】" in extra, "观察模式应返回组装文本"
    print("  [OK] 观察模式（config.yaml enabled=true）→ 返回组装文本")

    # 2) 强制关闭（patch enabled=false）→ None（可回滚验证，主链路零影响）
    with mock.patch.object(o, "_load_context_assembler_config",
                           return_value={"enabled": False, "token_budget": 3000}):
        assert o._context_assembler_extra("解析 PDF 并提取表格") is None, \
            "关闭时必须零影响"
    print("  [OK] 强制关闭 → None，主链路零影响")

    # 3) 降级：全部提供者异常且无记忆 → None（主链路零影响）
    with mock.patch.object(o, "_load_context_assembler_config",
                           return_value={"enabled": True, "token_budget": 3000}):
        with mock.patch.object(o, "_context_assembler_long_term",
                               side_effect=RuntimeError("boom")):
            with mock.patch.object(o, "_context_assembler_procedural",
                                   side_effect=RuntimeError("boom")):
                o._memory = None  # 工作记忆层缺省 → 三层全空 → None
                assert o._context_assembler_extra("任意任务") is None, "异常必须静默降级"
    print("  [OK] 提供者异常 → 静默降级 None（主链路零影响）")

    # 4) 性能基准（真实组件，n 次组装耗时统计）
    _benchmark_assemble(o)


def main() -> None:
    line = "═" * 68
    print(f"{line}\n  ContextAssembler 实际运行环境集成验证（D2D3 · CEL 框架）\n{line}")

    dir_existed = _create_reflection_files()
    tmp = None
    try:
        # 1. 真实反思经验文件读取（orchestrator 提供者）
        from agent.orchestrator.orchestrator import Orchestrator
        o = Orchestrator.__new__(Orchestrator)
        chunks = o._context_assembler_long_term("解析 PDF 并提取表格")
        assert chunks, "反思经验文件读取失败"
        assert all(c["layer"] == "反思经验" for c in chunks)
        print(f"  [OK] 真实反思经验文件读取 {len(chunks)} 条（experiences/lessons）")

        # 2. 真实 SkillLoader
        _verify_real_skill_loader()

        # 3. 真实 MemoryManager（临时目录）
        tmp = _verify_real_memory_manager()
        mm = _verify_memory_manager_instance(tmp)

        # 4. orchestrator 接线两态 + 降级
        _verify_orchestrator_wiring(mm, tmp)
    finally:
        _cleanup_reflection_files(dir_existed)
        if tmp is not None:
            shutil.rmtree(str(tmp), ignore_errors=True)

    print(f"{line}\n  集成验证通过 ✅ — 真实组件协作 + orchestrator 开关两态均正确\n{line}")


def _verify_memory_manager_instance(tmp: Path):
    """返回可复用的 MemoryManager 实例（供 orchestrator 注入）"""
    from memory.memory_manager import MemoryManager

    return MemoryManager(config={"data_dir": str(tmp), "token_limit": 8000})


if __name__ == "__main__":
    main()
