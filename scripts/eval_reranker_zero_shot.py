"""零样本评估脚本:验证 bge-reranker-v2-m3 在 12 个 xfail case 上的效果

【不易】不修改现有 HybridRetriever / SkillReranker 代码,纯评估脚本
【变易】子进程加载 CrossEncoder,避免主进程因原生崩溃(0xC0000005 / SIGILL)退出
【简易】单文件脚本,JSON Lines 通信协议,输出 before/after 对比表

执行流程:
    1. 主进程:加载 tool_index.json,跑 BM25 召回 top-20(Stage 1)
    2. 子进程:加载 CrossEncoder,通过 stdin/stdout 接收 pairs 返回 scores
    3. 主进程:按 rerank_score 排序取 top-5,评估 xfail 转 PASS 情况

用法:
    cd c:\\Users\\Administrator\\agent
    $env:PYTHONIOENCODING='utf-8'
    $env:AGENT_HYBRID_EMBEDDING='0'
    python scripts/eval_reranker_zero_shot.py

可选参数:
    --top-k 5            精排后返回数量(默认 5)
    --candidate-k 20     Stage 1 召回数量(默认 20)
    --min-score 0.05     rerank_score 阈值(默认 0.05)
    --model BAAI/bge-reranker-v2-m3  Cross-Encoder 模型名

预期输出:
    - BM25 baseline: 0/12 PASS(12 个 xfail 全部失败)
    - Reranker zero-shot: X/12 PASS(目标 ≥ 6/12 = 50%)
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

# 项目根目录(脚本位于 scripts/ 下)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# 关键路径
_TOOL_INDEX_PATH = _PROJECT_ROOT / "data" / "tool_index.json"
_NEGATIVE_SAMPLES_PATH = _PROJECT_ROOT / "data" / "tool_negative_samples.json"

# 12 个 xfail case(对齐 docs/reports/xfail_root_cause_analysis_20260720.md §1.1)
# (group_id, query, expected_positive, negative_list, failure_type)
_XFAIL_CASES = [
    ("G1_q00", "在百度上搜索 Python 教程", "web_search", ["web_get", "fetch_news"], "召回缺失"),
    ("G1_q01", "抓取 https://example.com 的 HTML 内容", "web_get", ["web_search", "fetch_news"], "负样本泄漏"),
    ("G4_q07", "列出 /home/user 下的所有文件", "list_directory", ["list_processes", "list_async_tasks"], "召回缺失"),
    ("G4_q09", "查看提交的后台任务列表", "list_async_tasks", ["list_directory", "list_processes"], "召回缺失"),
    ("G6_q13", "提交一个后台数据处理任务", "submit_task", ["schedule_task", "cancel_task"], "负样本泄漏"),
    ("G6_q14", "创建每天凌晨 3 点执行的定时任务", "schedule_task", ["submit_task", "cancel_task"], "召回缺失"),
    ("G6_q15", "取消任务 ID 为 abc123 的后台任务", "cancel_task", ["submit_task", "schedule_task"], "负样本泄漏"),
    ("G7_q16", "把 logs 文件夹压缩成 zip", "compress", ["decompress"], "方向性混淆"),
    ("G7_q17", "解压 archive.tar.gz 到当前目录", "decompress", ["compress"], "负样本泄漏"),
    ("G8_q18", "把 config.json 转换成 yaml 格式", "json_to_yaml", ["yaml_to_json"], "负样本泄漏"),
    ("G8_q19", "读取 data.yaml 转成 JSON 对象", "yaml_to_json", ["json_to_yaml"], "负样本泄漏"),
    ("G9_q20", "在 Google 上搜索 Python 异步教程", "web_search", ["search_memory", "search_lifetrace"], "召回缺失"),
]


# ════════════════════════════════════════════════════════════
#  工具描述加载
# ════════════════════════════════════════════════════════════

def load_tool_descriptions() -> dict[str, str]:
    """从 tool_index.json 加载 tool_name -> description 映射

    【变易】description 为空时用 name 兜底(与 SkillReranker 一致)
    """
    with open(_TOOL_INDEX_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    descriptions = {}
    for tool in data.get("tools", []):
        name = tool.get("name", "")
        if not name:
            continue
        desc = tool.get("description", "") or name
        # 加入 parameter_names 增强语义(与 BM25 索引内容一致)
        params = tool.get("parameter_names", []) or []
        if params:
            desc = f"{desc} 参数: {', '.join(params)}"
        descriptions[name] = desc
    return descriptions


# ════════════════════════════════════════════════════════════
#  BM25 召回(Stage 1)
# ════════════════════════════════════════════════════════════

def run_bm25_recall(query: str, top_k: int = 20) -> list[tuple[str, float]]:
    """用 HybridRetriever 跑 BM25 单路召回

    Returns:
        [(tool_name, bm25_score)] 列表(按分数降序)
    """
    # 延迟导入,避免脚本启动时加载 HybridRetriever
    from agent.tool_router_hybrid import get_hybrid_retriever, reset_hybrid_retriever

    # 确保禁用 Embedding(走纯 BM25)
    os.environ.setdefault("AGENT_HYBRID_EMBEDDING", "0")
    reset_hybrid_retriever()
    retriever = get_hybrid_retriever()
    if retriever is None or not retriever.available:
        raise RuntimeError("HybridRetriever 不可用,检查 tool_index.json")

    results = retriever.query(query, top_k=top_k)
    if results is None:
        return []
    return results


# ════════════════════════════════════════════════════════════
#  Cross-Encoder 精排(Stage 2,子进程隔离)
# ════════════════════════════════════════════════════════════

# 子进程脚本:加载 CrossEncoder,循环读取 pairs,返回 scores
_WORKER_SCRIPT = '''
import json
import os
import sys

# 【变易】HF 镜像(国内下载稳定,与 SkillReranker 一致)
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("ANONYMIZED_TELEMETRY", "False")

def main():
    model_name = sys.argv[1] if len(sys.argv) > 1 else "BAAI/bge-reranker-v2-m3"
    max_length = int(sys.argv[2]) if len(sys.argv) > 2 else 512

    try:
        from sentence_transformers import CrossEncoder
        import time
        t0 = time.time()
        # 优先从本地缓存加载
        from pathlib import Path
        repo_dir = model_name.replace("/", "--")
        hf_root = Path.home() / ".cache" / "huggingface" / "hub" / f"models--{repo_dir}" / "snapshots"
        load_source = model_name
        if hf_root.exists():
            for sub in hf_root.iterdir():
                if sub.is_dir() and (sub / "config.json").exists():
                    load_source = str(sub)
                    break
        model = CrossEncoder(load_source, max_length=max_length)
        load_time = time.time() - t0
        # 就绪信号
        print(json.dumps({"type": "ready", "load_time_sec": round(load_time, 2),
                          "load_source": load_source}), flush=True)
    except Exception as e:
        print(json.dumps({"type": "init_failed", "error": str(e)[:500]}), flush=True)
        return

    # 循环处理 pairs
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            if req.get("type") == "predict":
                pairs = req.get("pairs", [])
                scores = model.predict(pairs).tolist()
                print(json.dumps({"type": "scores", "scores": scores}), flush=True)
            elif req.get("type") == "exit":
                break
        except Exception as e:
            print(json.dumps({"type": "error", "error": str(e)[:300]}), flush=True)

if __name__ == "__main__":
    main()
'''


class RerankerWorker:
    """子进程 CrossEncoder 推理 worker

    【不易】主进程崩溃时子进程自动终止,不影响系统
    【变易】JSON Lines 协议,易扩展
    【简易】单子进程,生命周期由主进程管理
    """

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3", max_length: int = 512):
        self.model_name = model_name
        self.max_length = max_length
        self._proc: Optional[subprocess.Popen] = None
        self._init_failed = False

    def start(self) -> bool:
        """启动子进程并加载模型,返回是否就绪"""
        if self._init_failed:
            return False
        try:
            self._proc = subprocess.Popen(
                [sys.executable, "-c", _WORKER_SCRIPT, self.model_name, str(self.max_length)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                cwd=str(_PROJECT_ROOT),
            )
            # 等待就绪信号(最多 60s)
            ready_line = self._proc.stdout.readline()
            if not ready_line:
                err = self._proc.stderr.read()
                print(f"[Worker] 子进程启动失败: {err[:500]}", file=sys.stderr)
                self._init_failed = True
                return False
            msg = json.loads(ready_line)
            if msg.get("type") == "ready":
                load_time = msg.get("load_time_sec", 0)
                source = msg.get("load_source", "?")
                print(f"[Worker] 模型加载完成 ({load_time}s, source={source})")
                return True
            elif msg.get("type") == "init_failed":
                print(f"[Worker] 模型加载失败: {msg.get('error', '')[:300]}", file=sys.stderr)
                self._init_failed = True
                return False
        except Exception as e:
            print(f"[Worker] 启动异常: {e}", file=sys.stderr)
            self._init_failed = True
            return False
        return False

    def predict(self, pairs: list[tuple[str, str]]) -> Optional[list[float]]:
        """对 (query, doc) pairs 打分"""
        if self._init_failed or self._proc is None or self._proc.poll() is not None:
            return None
        try:
            req = json.dumps({"type": "predict", "pairs": pairs}, ensure_ascii=False)
            self._proc.stdin.write(req + "\n")
            self._proc.stdin.flush()
            resp_line = self._proc.stdout.readline()
            if not resp_line:
                return None
            resp = json.loads(resp_line)
            if resp.get("type") == "scores":
                return resp.get("scores", [])
            elif resp.get("type") == "error":
                print(f"[Worker] 推理错误: {resp.get('error', '')[:200]}", file=sys.stderr)
                return None
        except Exception as e:
            print(f"[Worker] 通信异常: {e}", file=sys.stderr)
            return None
        return None

    def close(self):
        """关闭子进程"""
        if self._proc is None:
            return
        try:
            if self._proc.poll() is None:
                self._proc.stdin.write(json.dumps({"type": "exit"}) + "\n")
                self._proc.stdin.flush()
                self._proc.wait(timeout=5)
        except Exception:
            pass
        finally:
            try:
                self._proc.terminate()
            except Exception:
                pass
            self._proc = None


# ════════════════════════════════════════════════════════════
#  评估逻辑
# ════════════════════════════════════════════════════════════

def evaluate_case(
    case: tuple,
    tool_descs: dict[str, str],
    worker: Optional[RerankerWorker],
    candidate_k: int = 20,
    top_k: int = 5,
    min_score: float = 0.05,
) -> dict:
    """评估单个 case

    Returns:
        {
            "case_id": str,
            "query": str,
            "expected": str,
            "negative": list[str],
            "failure_type": str,
            "bm25_top5": list[str],
            "bm25_pass": bool,
            "rerank_top5": list[str],  # None 表示 reranker 不可用
            "rerank_scores": list[float],
            "rerank_pass": bool,
            "improved": bool,  # BM25 fail → Reranker pass
        }
    """
    case_id, query, expected, negative, failure_type = case

    # Stage 1: BM25 召回 top-N
    bm25_results = run_bm25_recall(query, top_k=candidate_k)
    bm25_top5 = [t for t, _ in bm25_results[:top_k]]
    bm25_pass = _check_pass(bm25_top5, expected, negative)

    # Stage 2: Cross-Encoder 精排
    rerank_top5: Optional[list[str]] = None
    rerank_scores: list[float] = []
    rerank_pass = False
    scores: Optional[list[float]] = None
    elapsed_ms: Optional[float] = None

    if worker is not None and bm25_results:
        # 构造 (query, doc) pairs
        pairs = []
        for tool_name, _ in bm25_results:
            doc = tool_descs.get(tool_name, tool_name)
            pairs.append((query, doc))

        t0 = time.time()
        scores = worker.predict(pairs)
        elapsed_ms = (time.time() - t0) * 1000

        if scores is not None:
            # 按 rerank_score 降序排序
            indexed = list(enumerate(scores))
            indexed.sort(key=lambda x: -x[1])
            # 阈值过滤 + top_k 截断
            filtered = []
            for idx, score in indexed:
                if score < min_score:
                    continue
                filtered.append((bm25_results[idx][0], float(score)))
                if len(filtered) >= top_k:
                    break
            rerank_top5 = [t for t, _ in filtered]
            rerank_scores = [round(s, 4) for _, s in filtered]
            rerank_pass = _check_pass(rerank_top5, expected, negative)

    return {
        "case_id": case_id,
        "query": query,
        "expected": expected,
        "negative": negative,
        "failure_type": failure_type,
        "bm25_top5": bm25_top5,
        "bm25_pass": bm25_pass,
        "rerank_top5": rerank_top5,
        "rerank_scores": rerank_scores,
        "rerank_pass": rerank_pass,
        "improved": (not bm25_pass) and rerank_pass,
        "rerank_latency_ms": round(elapsed_ms, 2) if elapsed_ms is not None else None,
    }


def _check_pass(top5: list[str], expected: str, negative: list[str]) -> bool:
    """检查 top-5 是否满足:expected 在 top-5 且 negative 不在 top-5"""
    top5_set = set(top5)
    if expected not in top5_set:
        return False
    if set(negative) & top5_set:
        return False
    return True


# ════════════════════════════════════════════════════════════
#  报告输出
# ════════════════════════════════════════════════════════════

def print_report(results: list[dict], reranker_available: bool):
    """输出对比报告"""
    print("\n" + "=" * 80)
    print("Cross-Encoder Reranker 零样本评估报告")
    print("=" * 80)

    # ── BM25 Baseline ──
    print("\n── BM25 Baseline(Stage 1 only)──")
    bm25_pass_count = sum(1 for r in results if r["bm25_pass"])
    for r in results:
        status = "✓ PASS" if r["bm25_pass"] else "✗ FAIL"
        print(f"  [{status}] {r['case_id']:10s} {r['failure_type']:8s} | {r['query'][:40]}")
        print(f"           BM25 top-5: {r['bm25_top5']}")
    print(f"\n  BM25 通过率: {bm25_pass_count}/{len(results)}")

    # ── Cross-Encoder Reranker ──
    if not reranker_available:
        print("\n── Cross-Encoder Reranker:不可用(子进程加载失败)──")
        print("\n=== 总结 ===")
        print(f"BM25 baseline: {bm25_pass_count}/{len(results)} PASS")
        print("Reranker zero-shot: N/A(模型加载失败)")
        return

    print("\n── Cross-Encoder Reranker(零样本)──")
    rerank_pass_count = sum(1 for r in results if r["rerank_pass"])
    improved_count = sum(1 for r in results if r["improved"])

    for r in results:
        status = "✓ PASS" if r["rerank_pass"] else "✗ FAIL"
        improved_flag = " ⭐ improved" if r["improved"] else ""
        print(f"  [{status}] {r['case_id']:10s} {r['failure_type']:8s}{improved_flag} | {r['query'][:40]}")
        print(f"           Rerank top-5: {r['rerank_top5']}")
        if r["rerank_scores"]:
            print(f"           scores: {r['rerank_scores']}")
        if r.get("rerank_latency_ms"):
            print(f"           latency: {r['rerank_latency_ms']}ms")

    # ── 总结 ──
    print("\n" + "=" * 80)
    print("=== 总结 ===")
    print(f"BM25 baseline:        {bm25_pass_count}/{len(results)} PASS")
    print(f"Reranker zero-shot:   {rerank_pass_count}/{len(results)} PASS")
    print(f"改善 case 数:         {improved_count}/{len(results)}")
    target = 6
    print(f"目标(≥{target}/{len(results)} = 50%): {'✓ 达标' if rerank_pass_count >= target else '✗ 未达标'}")

    # ── 性能统计 ──
    latencies = [r["rerank_latency_ms"] for r in results if r.get("rerank_latency_ms")]
    if latencies:
        print(f"\n性能:平均 {sum(latencies)/len(latencies):.1f}ms, "
              f"最大 {max(latencies):.1f}ms, 最小 {min(latencies):.1f}ms")

    # ── 改善明细 ──
    if improved_count > 0:
        print("\n── 改善 case 明细(BM25 FAIL → Reranker PASS)──")
        for r in results:
            if r["improved"]:
                print(f"  ⭐ {r['case_id']} ({r['failure_type']}): {r['query'][:50]}")
                print(f"     BM25: {r['bm25_top5']}")
                print(f"     Rerank: {r['rerank_top5']}")

    # ── 仍未解决 ──
    still_fail = [r for r in results if not r["rerank_pass"]]
    if still_fail:
        print(f"\n── 仍未解决({len(still_fail)} 个,需 Phase 2 微调)──")
        for r in still_fail:
            print(f"  ✗ {r['case_id']} ({r['failure_type']}): {r['query'][:50]}")
            print(f"     Rerank top-5: {r['rerank_top5']}")


# ════════════════════════════════════════════════════════════
#  主入口
# ════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(description="Cross-Encoder Reranker 零样本评估")
    parser.add_argument("--top-k", type=int, default=5, help="精排后返回数量(默认 5)")
    parser.add_argument("--candidate-k", type=int, default=20, help="Stage 1 召回数量(默认 20)")
    parser.add_argument("--min-score", type=float, default=0.05, help="rerank_score 阈值(默认 0.05)")
    parser.add_argument("--model", default="BAAI/bge-reranker-v2-m3", help="Cross-Encoder 模型名")
    parser.add_argument("--max-length", type=int, default=512, help="max_length(默认 512)")
    parser.add_argument("--no-reranker", action="store_true", help="仅跑 BM25 baseline,不加载 Reranker")
    args = parser.parse_args()

    # 设置环境变量
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    os.environ.setdefault("AGENT_HYBRID_EMBEDDING", "0")

    print(f"[Config] model={args.model}")
    print(f"[Config] candidate_k={args.candidate_k}, top_k={args.top_k}, min_score={args.min_score}")
    print(f"[Config] AGENT_HYBRID_EMBEDDING={os.environ.get('AGENT_HYBRID_EMBEDDING')}")

    # 加载工具描述
    print("\n[1/3] 加载 tool_index.json...")
    tool_descs = load_tool_descriptions()
    print(f"  加载 {len(tool_descs)} 个工具描述")

    # 启动 Reranker worker
    worker: Optional[RerankerWorker] = None
    reranker_available = False
    if not args.no_reranker:
        print(f"\n[2/3] 启动 Cross-Encoder worker(子进程隔离)...")
        worker = RerankerWorker(model_name=args.model, max_length=args.max_length)
        reranker_available = worker.start()
        if not reranker_available:
            print("  ⚠️  Reranker 不可用,仅评估 BM25 baseline")
            print("  ℹ️  可能原因:模型加载崩溃(0xC0000005)/ 依赖缺失 / 超时")
    else:
        print("\n[2/3] 跳过 Reranker(--no-reranker)")

    # 评估所有 case
    print(f"\n[3/3] 评估 {len(_XFAIL_CASES)} 个 xfail case...")
    results = []
    for i, case in enumerate(_XFAIL_CASES, 1):
        print(f"  ({i}/{len(_XFAIL_CASES)}) {case[0]}: {case[1][:40]}", end="", flush=True)
        r = evaluate_case(
            case, tool_descs, worker,
            candidate_k=args.candidate_k,
            top_k=args.top_k,
            min_score=args.min_score,
        )
        results.append(r)
        # 实时输出
        bm25_flag = "✓" if r["bm25_pass"] else "✗"
        rerank_flag = "✓" if r["rerank_pass"] else ("✗" if r["rerank_top5"] else "-")
        print(f" | BM25={bm25_flag} Rerank={rerank_flag}")

    # 输出报告
    print_report(results, reranker_available)

    # 关闭 worker
    if worker is not None:
        worker.close()

    # 退出码:reranker 达标(≥6)返回 0,否则返回 1
    if reranker_available:
        rerank_pass_count = sum(1 for r in results if r["rerank_pass"])
        sys.exit(0 if rerank_pass_count >= 6 else 1)
    else:
        sys.exit(2)


if __name__ == "__main__":
    main()


