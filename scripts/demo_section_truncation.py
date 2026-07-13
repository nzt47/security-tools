"""章节截断逻辑本地验证脚本

构造多 H2/H3 章节且总 token 超标的 mock instruction，
验证 _split_sections / _select_sections_by_budget / inject_instruction 行为，
同时观察 logger.info 输出的章节级取舍详情。
"""
import sys
import logging
from pathlib import Path

# 让脚本可独立运行：把仓库根目录加入 sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from unittest.mock import MagicMock

from agent.skills_mgmt.context_injector import (
    ContextInjector,
    _split_sections,
    _classify_section,
    _select_sections_by_budget,
)
from agent.skills_mgmt.loader import estimate_tokens


def build_mock_instruction() -> str:
    """构造一个包含 7 个 H2/H3 章节且总 token 超预算的 instruction。"""
    overview = (
        "## 概述\n"
        "本技能用于演示章节级智能截断。\n"
        "面向需要按 H2/H3 边界保留语义完整性的场景。\n\n"
    )
    steps = (
        "## 步骤\n"
        "1. 调用 `prepare()` 准备环境\n"
        "2. 调用 `run()` 执行主流程\n"
        "3. 调用 `cleanup()` 清理资源\n\n"
    )
    usage = (
        "## 使用方法\n"
        "直接在终端运行 `python -m skill` 即可启动。\n"
        "支持 `--dry-run` 参数预演。\n\n"
    )
    examples = (
        "## 示例\n"
        "输入 hello → 输出 HELLO\n"
        "输入 world → 输出 WORLD\n\n"
    )
    notes = (
        "## 注意\n"
        "Windows 环境下需管理员权限。\n\n"
    )
    refs = "## 参考资料\n" + ("相关链接：https://example.com/doc\n" * 80) + "\n"
    related = "## 相关链接\n" + ("详见 issue #123\n" * 60) + "\n"
    return overview + steps + usage + examples + notes + refs + related


def main() -> None:
    # 开启 INFO 级别日志，观察章节级取舍详情
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    instruction = build_mock_instruction()
    full_tokens = estimate_tokens(instruction)
    print(f"\n[mock] 总字符={len(instruction)}, 总 token={full_tokens}")

    # 1) 验证 _split_sections 切分结果
    sections = _split_sections(instruction, trace_id="demo-001")
    print(f"\n[split] 切分出 {len(sections)} 个章节:")
    for i, s in enumerate(sections):
        title = s.split('\n', 1)[0]
        print(f"  #{i} pri={_classify_section(s, i)} tokens={estimate_tokens(s):4d} | {title}")

    # 2) 验证 _select_sections_by_budget 在预算 300 下的取舍
    budget = 300
    kept, dropped, used = _select_sections_by_budget(sections, budget, trace_id="demo-002")
    print(f"\n[select] budget={budget} used={used} kept={len(kept)} dropped={len(dropped)}")
    print("  kept:")
    for s in kept:
        print(f"    - {s.split(chr(10),1)[0]} (tokens={estimate_tokens(s)})")
    print("  dropped:")
    for s in dropped:
        print(f"    - {s.split(chr(10),1)[0]} (tokens={estimate_tokens(s)})")

    # 3) 端到端验证 inject_instruction
    loader = MagicMock()
    loader.load_instruction.return_value = {
        "skill_id": "demo-skill",
        "instruction": instruction,
        "estimated_tokens": full_tokens,
        "instruction_chars": len(instruction),
        "layer": 2,
    }
    injector = ContextInjector(loader=loader, instr_budget=budget)
    result = injector.inject_instruction("demo-skill")

    print(f"\n[inject] truncated={result['truncated']} tokens={result['estimated_tokens']}")
    print(f"[inject] prompt 末尾 200 字符:\n{result['prompt'][-200:]}")

    # 断言关键不变量
    assert result["truncated"] is True, "应触发截断"
    assert "## 概述" in result["prompt"], "首章节必保留"
    assert "## 步骤" in result["prompt"], "步骤章节必保留"
    assert "## 参考资料" not in result["prompt"], "参考资料应被丢弃"
    assert "更多章节未加载" in result["prompt"], "应追加省略提示"
    hint_tokens = estimate_tokens(
        "\n\n...(更多章节未加载，完整内容请查看 skill.md 文件，"
        "可调用 load_skill_instruction 获取完整说明)"
    )
    assert result["estimated_tokens"] <= budget + hint_tokens, "截断后 token 不应远超预算"
    print("\n[assert] 全部断言通过 ✓")


if __name__ == "__main__":
    main()
