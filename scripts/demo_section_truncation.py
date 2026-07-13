"""章节截断逻辑本地验证脚本

阶段1: 原 mock 验证（基础场景，budget=300）
阶段2: 真实技能描述 + DEBUG 日志（贴近生产，budget=300）
阶段3: 极端紧张预算 50 tokens — 验证策略行为
阶段4: 极端紧张预算 30 tokens — 必保留都放不下时的兜底
"""
import sys
import logging
from pathlib import Path

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
        "## 概述\n本技能用于演示章节级智能截断。\n面向需要按 H2/H3 边界保留语义完整性的场景。\n\n"
    )
    steps = "## 步骤\n1. 调用 `prepare()` 准备环境\n2. 调用 `run()` 执行主流程\n3. 调用 `cleanup()` 清理资源\n\n"
    usage = "## 使用方法\n直接在终端运行 `python -m skill` 即可启动。\n支持 `--dry-run` 参数预演。\n\n"
    examples = "## 示例\n输入 hello → 输出 HELLO\n输入 world → 输出 WORLD\n\n"
    notes = "## 注意\nWindows 环境下需管理员权限。\n\n"
    refs = "## 参考资料\n" + ("相关链接：https://example.com/doc\n" * 80) + "\n"
    related = "## 相关链接\n" + ("详见 issue #123\n" * 60) + "\n"
    return overview + steps + usage + examples + notes + refs + related


def build_realistic_instruction() -> str:
    """基于真实技能（情感表达/上下文感知）构造贴近生产的 instruction。"""
    return (
        "## 概述\n"
        "情感表达技能用于在对话中注入情感色彩，让回应更生动。\n"
        "支持识别用户情绪（开心/悲伤/愤怒/焦虑），并调整回应语气。\n\n"

        "## 步骤\n"
        "1. 调用 `_detect_emotion(text)` 检测用户情绪，返回 emotion 标签\n"
        "2. 根据 emotion 从 `emotion_map` 查询对应语气参数（warmth/formality）\n"
        "3. 调用 `_apply_tone(response, params)` 调整回应语气\n"
        "4. 如检测到负面情绪，追加安抚语句并标记 `escalated=True`\n\n"

        "## 使用方法\n"
        "- 在 orchestrator 的 post_process 阶段调用：`skill.invoke(response, context)`\n"
        "- 配置参数：`{intensity: 0.7, fallback: 'neutral'}`\n"
        "- 与上下文感知技能联动时可启用 `context_aware_mode=True`\n\n"

        "## 示例\n"
        "输入: '我今天好累'\n"
        "检测结果: emotion=sadness, intensity=0.8\n"
        "调整后回应: '听起来你今天很辛苦，要不要先休息一下？'\n\n"

        "## 注意\n"
        "- 不要在系统提示词中泄露 emotion 检测过程\n"
        "- 检测置信度 < 0.5 时不应用调整，避免误判\n"
        "- 中文场景下 warmth 参数建议范围 [0.3, 0.8]\n\n"

        "## 参考资料\n"
        + ("- 情绪词典参考: Plutchik 八情绪模型\n" * 40)
        + "- 论文: Affective Computing (Picard, 1997)\n\n"

        "## 相关链接\n"
        + ("- GitHub: example/emotion-engine\n" * 30)
        + "- API 文档: /docs/skills/emotion\n"
    )


def run_stage(label: str, instruction: str, budget: int, *, enable_debug: bool) -> None:
    """单阶段运行：split → select → inject，可选 DEBUG 日志。"""
    print("\n" + "=" * 72)
    print(f"[{label}] budget={budget} tokens  debug={enable_debug}")
    print("=" * 72)

    if enable_debug:
        logging.getLogger("agent.skills_mgmt").setLevel(logging.DEBUG)

    full_tokens = estimate_tokens(instruction)
    print(f"\n[input] 总字符={len(instruction)}, 总 token={full_tokens}")

    tid = f"{label}-{budget}"
    sections = _split_sections(instruction, trace_id=tid)
    print(f"\n[split] 切分出 {len(sections)} 个章节:")
    for i, s in enumerate(sections):
        title = s.split('\n', 1)[0]
        print(f"  #{i} pri={_classify_section(s, i)} tokens={estimate_tokens(s):4d} | {title}")

    kept, dropped, used = _select_sections_by_budget(sections, budget, trace_id=tid)
    print(f"\n[select] budget={budget} used={used} kept={len(kept)} dropped={len(dropped)}")
    print("  kept:")
    for s in kept:
        print(f"    - {s.split(chr(10),1)[0]} (tokens={estimate_tokens(s)})")
    print("  dropped:")
    for s in dropped:
        print(f"    - {s.split(chr(10),1)[0]} (tokens={estimate_tokens(s)})")

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
    print(f"[inject] prompt 全文:\n{result['prompt']}")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    mock_instr = build_mock_instruction()
    real_instr = build_realistic_instruction()

    run_stage("stage1-mock", mock_instr, budget=300, enable_debug=False)
    run_stage("stage2-real-debug", real_instr, budget=300, enable_debug=True)
    run_stage("stage3-real-50", real_instr, budget=50, enable_debug=False)
    run_stage("stage4-real-30", real_instr, budget=30, enable_debug=False)


if __name__ == "__main__":
    main()
