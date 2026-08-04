#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""DST 指代消解模拟场景演示 + 向量相似度阈值数据收集

用途:
    1. 构造包含"那个呢"/"然后呢"的多轮对话，验证 DST 补全逻辑
    2. 用真实 BGE-m3 编码对照样本，为 DST_VECTOR_MIN_SIM 阈值选型提供数据

运行:
    python scripts/dst_scenario_demo.py
"""
import sys
import time
import importlib.util

sys.path.insert(0, ".")

import numpy as np

# 【不易】dialog_state 仅依赖标准库，用 importlib 直接加载模块文件，
#         绕过 agent.orchestrator.__init__ 的循环导入（lifecycle_manager↔digital_life）
_spec = importlib.util.spec_from_file_location(
    "dialog_state", "agent/orchestrator/dialog_state.py")
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
DialogState = _mod.DialogState
get_dialog_state = _mod.get_dialog_state
reset_session_state = _mod.reset_session_state


def _try_real_model():
    """尝试加载 BGE-m3，返回 encode 函数；失败返回 None"""
    import os
    # 【不易】离线模式：本地 HF cache 已有模型，避免联网 HEAD 检查（WinError 10060）
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
    t0 = time.time()
    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer("BAAI/bge-m3", device="cpu")

        def enc(q: str):
            return model.encode([q], normalize_embeddings=True)[0]

        print(f"[LOAD] BGE-m3 就绪，加载耗时 {time.time() - t0:.1f}s")
        return enc
    except Exception as e:  # noqa: BLE001
        print(f"[LOAD] BGE-m3 不可用: {type(e).__name__}: {str(e)[:100]}")
        return None


class _Adapter:
    """鸭子类型向量适配器，包装 encode 函数"""

    def __init__(self, enc_fn):
        self._enc = enc_fn

    def encode_query(self, query):
        try:
            return self._enc(query)
        except Exception:  # noqa: BLE001
            return None


def show(tag: str, sid: str, text: str) -> None:
    """运行一次 resolve 并打印补全结果 + 相似度"""
    dst = get_dialog_state(sid)
    aug = dst.resolve(text)
    sim = getattr(dst, "last_similarity", None)
    sim_s = f"{sim:.4f}" if isinstance(sim, float) else str(sim)
    print(f"  [{tag}] '{text}' -> augmented={aug!r}  similarity={sim_s}")


def run_scenarios(enc_fn):
    """任务1: 模拟多轮对话场景"""
    adapter = _Adapter(enc_fn) if enc_fn else None
    use_real = adapter is not None

    print("\n=== 场景A: PDF 转换多轮（同话题，应通过门控）===")
    reset_session_state("A")
    dstA = get_dialog_state("A", vector_adapter=adapter)
    dstA.update(intent="pdf_convert", keywords=["PDF", "转换"],
                user_input="帮我转换PDF为Word")
    show("A1", "A", "那个呢")     # 期望: 关于 PDF 转换 呢
    show("A2", "A", "然后呢")     # 期望: 继续 PDF 转换

    print("\n=== 场景B: 语义断裂（keywords 与 last_user_input 不同源，应拒绝）===")
    reset_session_state("B")
    dstB = get_dialog_state("B", vector_adapter=adapter)
    # last_user_input = PDF 话题，但 keywords 被污染为天气（模拟模板回复引入新话题）
    dstB.update(user_input="帮我转换PDF")
    dstB.last_keywords = ["天气", "预报"]
    show("B1", "B", "那个呢")     # augmented=关于 天气 预报 呢，与 PDF 跨话题

    print("\n=== 场景C: 无注入 adapter（纯止则路径，相似度=None）===")
    reset_session_state("C")
    dstC = get_dialog_state("C", vector_adapter=None)
    dstC.update(intent="pdf_convert", keywords=["PDF", "转换"],
                user_input="帮我转换PDF为Word")
    show("C1", "C", "那个呢")

    return use_real


def run_threshold_probe(enc_fn):
    """任务3: 直接编码对照样本，统计相似度分布"""
    if not enc_fn:
        print("\n[任务3] 真实模型不可用，跳过直接对照（参考已有文献数据："
              "中文同话题 0.62+，跨话题 0.22~0.39）")
        return

    print("\n=== 任务3: 直接相似度对照（augmented vs last_user_input）===")
    pairs = [
        ("帮我转换PDF为Word", "关于 PDF 转换 呢",     "同话题-PDF-指代"),
        ("帮我转换PDF为Word", "继续 PDF 转换",        "同话题-PDF-接续"),
        ("帮我转换PDF为Word", "关于 天气 预报 呢",    "跨话题-PDF↔天气"),
        ("帮我转换PDF为Word", "关于 天气 呢",          "跨话题-PDF↔天气2"),
        ("今天天气怎么样",     "关于 天气 预报 呢",     "同话题-天气-指代"),
        ("今天天气怎么样",     "继续 天气 预报",        "同话题-天气-接续"),
        ("今天天气怎么样",     "关于 PDF 转换 呢",     "跨话题-天气↔PDF"),
        ("帮我写一首诗",       "关于 诗 写 呢",         "同话题-诗-指代"),
        ("帮我写一首诗",       "关于 PDF 转换 呢",     "跨话题-诗↔PDF"),
    ]
    print(f"  {'sim':>7} | 0.15  0.20  0.40  0.50 | 场景")
    print("  " + "-" * 70)
    same_sims, cross_sims = [], []
    for a, b, label in pairs:
        va = enc_fn(a)
        vb = enc_fn(b)
        sim = float(np.dot(va, vb))
        is_same = "同话题" in label
        (same_sims if is_same else cross_sims).append(sim)
        g = lambda th: "PASS " if sim >= th else "RJCT "  # noqa: E731
        print(f"  {sim:7.4f} | {g(0.15)} {g(0.20)} {g(0.40)} {g(0.50)} | {label}")

    if same_sims and cross_sims:
        print(f"\n  同话题: min={min(same_sims):.4f} max={max(same_sims):.4f} "
              f"mean={np.mean(same_sims):.4f}")
        print(f"  跨话题: min={min(cross_sims):.4f} max={max(cross_sims):.4f} "
              f"mean={np.mean(cross_sims):.4f}")
        gap = min(same_sims) - max(cross_sims)
        print(f"  分离间隙: same_min - cross_max = {gap:.4f}")
        if gap > 0:
            print(f"  → 最优阈值区间: ({max(cross_sims):.4f}, {min(same_sims):.4f})")


def main():
    print("=" * 72)
    print(" DST 指代消解模拟场景 + 阈值数据收集")
    print("=" * 72)
    enc_fn = _try_real_model()
    use_real = run_scenarios(enc_fn)
    run_threshold_probe(enc_fn)
    print("\n[完成] adapter 模式:", "真实 BGE-m3" if use_real else "纯止则/mock")


if __name__ == "__main__":
    main()
