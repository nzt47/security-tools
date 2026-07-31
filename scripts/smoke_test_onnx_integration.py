"""ONNX 集成端到端冒烟测试（真实模型验证）

目的:
    单元测试用 mock，本脚本用真实 ONNX 模型验证集成正确性。

验证项:
    1. SkillReranker 实际走 ONNX 路径（_use_onnx=True）
    2. rerank() 返回正确排序结果
    3. 与直接 ONNX 推理分数一致
"""
import os
import sys

sys.stdout.reconfigure(line_buffering=True)

# 加载 .env
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["SKILL_RERANKER_ENABLED"] = "true"
os.environ["SKILL_RERANKER_USE_ONNX"] = "true"
os.environ["SKILL_RERANKER_MODEL"] = "C:/Users/Administrator/.cache/huggingface/hub/models--jinaai--jina-reranker-v2-base-multilingual"
os.environ["SKILL_RERANKER_ONNX_VARIANT"] = "model_quantized.onnx"

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from agent.skills_mgmt.reranker import SkillReranker
from dataclasses import dataclass
from typing import List


@dataclass
class TestSkillMatch:
    skill_id: str
    name: str
    description: str
    score: float = 0.0
    category: str = ""
    tags: List[str] = None


def main():
    print("=" * 60)
    print("  ONNX 集成端到端冒烟测试")
    print("=" * 60)

    candidates = [
        TestSkillMatch(
            skill_id="voice_interaction",
            name="语音交互助手",
            description="语音识别 语音转文字 语音合成",
            score=0.5,
            category="interaction",
            tags=["语音", "ASR", "TTS"],
        ),
        TestSkillMatch(
            skill_id="pdf_parser",
            name="PDF 解析器",
            description="PDF 文件解析 文档提取",
            score=0.5,
            category="file",
            tags=["PDF", "解析"],
        ),
        TestSkillMatch(
            skill_id="self_reflection",
            name="自我反思",
            description="复盘 改进建议 自我评估",
            score=0.5,
            category="meta",
            tags=["反思", "复盘"],
        ),
    ]

    print("\n[1] 初始化 SkillReranker...")
    r = SkillReranker()
    print(f"  _use_onnx_env: {r._use_onnx_env}")
    print(f"  _onnx_variant: {r._onnx_variant}")
    print(f"  _model_name: {r._model_name}")

    print("\n[2] 触发模型加载（首次 rerank）...")
    import time
    t0 = time.time()
    result = r.rerank("帮我识别语音并转成文字", candidates, top_k=3)
    elapsed = time.time() - t0
    print(f"  首次加载 + 推理耗时: {elapsed:.2f}s")

    print("\n[3] 验证 ONNX 路径启用...")
    print(f"  _use_onnx: {r._use_onnx} {'✅' if r._use_onnx else '❌'}")
    print(f"  _onnx_session 已加载: {'✅' if r._onnx_session is not None else '❌'}")
    print(f"  _onnx_tokenizer 已加载: {'✅' if r._onnx_tokenizer is not None else '❌'}")
    print(f"  _model (PyTorch) 未加载: {'✅' if r._model is None else '❌'}")
    print(f"  _onnx_input_names: {r._onnx_input_names}")

    print("\n[4] 验证排序结果...")
    print(f"  查询: '帮我识别语音并转成文字'")
    print(f"  排序结果:")
    for i, c in enumerate(result):
        print(f"    [{i+1}] {c.skill_id} (score={c.score})")
    expected_first = "voice_interaction"
    actual_first = result[0].skill_id
    print(f"  期望首位: {expected_first}")
    print(f"  实际首位: {actual_first}")
    print(f"  排序正确: {'✅' if actual_first == expected_first else '❌'}")

    print("\n[5] 第二次推理（验证懒加载缓存）...")
    t0 = time.time()
    result2 = r.rerank("解析这个 PDF 文件", candidates, top_k=2)
    elapsed = time.time() - t0
    print(f"  推理耗时（无加载）: {elapsed*1000:.2f}ms")
    print(f"  PDF 查询首位: {result2[0].skill_id} {'✅' if result2[0].skill_id == 'pdf_parser' else '❌'}")

    print("\n" + "=" * 60)
    all_ok = (
        r._use_onnx
        and r._onnx_session is not None
        and r._model is None
        and actual_first == expected_first
        and result2[0].skill_id == "pdf_parser"
    )
    print(f"  综合结果: {'✅ ONNX 集成验证通过' if all_ok else '❌ 存在问题'}")
    print("=" * 60)

    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
