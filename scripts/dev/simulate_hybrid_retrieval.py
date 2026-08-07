"""模拟 tool_router_hybrid 混合检索（BM25 + Embedding 双路融合）计算过程

【不易】算法与原实现逐行一致（tool_router_hybrid.py）：
        - BM25 k1=1.5, b=0.75, CJK+英文混合分词
        - Embedding 用真实模型 paraphrase-multilingual-MiniLM-L12-v2(384维, 离线缓存)
        - 子进程隔离加载模型（防 Windows 0xC0000005 原生崩溃）
        - cosine 剪枝阈值 0.2 / min-max 归一化 / final = alpha*bm25 + (1-alpha)*embed
【简易】自包含标准库脚本（子进程里才 import sentence_transformers），
        python scripts/dev/simulate_hybrid_retrieval.py 直接运行
"""
import json
import math
import os
import re
import subprocess
import sys
import time

from sim_common import TOOLS, TEST_CASES, export_csv

sys.stdout.reconfigure(encoding="utf-8")

# 融合权重漂移实验：对比三个 alpha 下的排序变化
ALPHAS = [0.3, 0.5, 0.7]

# ════════════════════════════════════════════════════════════
# 1. 分词 + BM25（与 tool_router_hybrid.py L275-L380 一致）
# ════════════════════════════════════════════════════════════
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")


def _tokenize(text):
    return _TOKEN_RE.findall((text or "").lower())


class BM25Index:
    """BM25 倒排索引（k1=1.5, b=0.75）"""

    def __init__(self, k1=1.5, b=0.75):
        self._k1 = k1
        self._b = b
        self._index = {}          # term -> [(doc_id, tf)]
        self._doc_lengths = {}    # doc_id -> token count
        self._total_docs = 0
        self._avg_doc_length = 0.0

    def add_document(self, doc_id, content):
        tokens = _tokenize(content)
        term_counts = {}
        for t in tokens:
            term_counts[t] = term_counts.get(t, 0) + 1
        for term, freq in term_counts.items():
            self._index.setdefault(term, []).append((doc_id, freq))
        self._doc_lengths[doc_id] = len(tokens)
        self._total_docs += 1
        self._avg_doc_length = (
            sum(self._doc_lengths.values()) / self._total_docs
        )

    def _compute_bm25(self, term, term_freq, doc_length):
        doc_count = len(self._index[term])
        idf = (self._total_docs - doc_count + 0.5) / (doc_count + 0.5)
        if idf <= 0:
            return 0.0
        numerator = term_freq * (self._k1 + 1)
        denominator = term_freq + self._k1 * (
            1 - self._b + self._b * doc_length / (self._avg_doc_length or 1)
        )
        return idf * numerator / denominator

    def search(self, query, top_k=10):
        query_tokens = _tokenize(query)
        print(f"[BM25] 查询 tokens={query_tokens}")
        scores = {}
        for token in query_tokens:
            if token not in self._index:
                continue
            for doc_id, freq in self._index[token]:
                doc_length = self._doc_lengths.get(doc_id, 0)
                if doc_length > 0:
                    scores[doc_id] = scores.get(doc_id, 0.0) + \
                        self._compute_bm25(token, freq, doc_length)
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        print("[BM25] 各文档得分: " + ", ".join(
            f"{d}={s:.4f}" for d, s in ranked) or "(无命中)")
        return ranked[:top_k]


# ════════════════════════════════════════════════════════════
# 2. Embedding 子进程 worker（真实模型, 离线缓存, 防原生崩溃）
# ════════════════════════════════════════════════════════════
_MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"

_WORKER_SCRIPT = r"""
import json, os, sys, time
os.environ["HF_HUB_OFFLINE"] = "1"          # 强制离线（模型已缓存）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
def main():
    model_name = sys.argv[1]
    try:
        from sentence_transformers import SentenceTransformer
        t0 = time.time()
        model = SentenceTransformer(model_name)
        print(json.dumps({"type": "ready",
                          "load_sec": round(time.time() - t0, 2)}), flush=True)
    except Exception as e:
        print(json.dumps({"type": "init_failed", "error": str(e)}), flush=True)
        sys.exit(1)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            if req.get("type") == "exit":
                break
            if req.get("type") == "encode":
                texts = req.get("texts", [])
                vecs = model.encode(texts, show_progress_bar=False)
                data = json.dumps([v.tolist() for v in vecs])
                print(json.dumps({"type": "embeddings",
                                  "data": data}), flush=True)
        except Exception as e:
            print(json.dumps({"type": "error", "error": str(e)}), flush=True)
if __name__ == "__main__":
    main()
"""


class EmbeddingClient:
    """子进程隔离的 Embedding 客户端（编码文本 → 真实 384 维向量）"""

    def __init__(self):
        self._proc = None

    def _ensure_worker(self):
        if self._proc is not None and self._proc.poll() is None:
            return True
        env = dict(os.environ)
        env["HF_HUB_OFFLINE"] = "1"
        self._proc = subprocess.Popen(
            [sys.executable, "-c", _WORKER_SCRIPT, _MODEL_NAME],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, cwd=os.getcwd(), env=env,
        )
        # 等待 ready 信号（模型首次加载约 2-3 秒）
        deadline = time.time() + 120
        while time.time() < deadline:
            line = self._proc.stdout.readline()
            if not line:
                raise RuntimeError("embedding worker 意外退出")
            msg = json.loads(line)
            if msg["type"] == "ready":
                print(f"[Embedding] 模型加载完成: {_MODEL_NAME} "
                      f"(load={msg['load_sec']}s, 离线缓存)")
                return True
            if msg["type"] == "init_failed":
                raise RuntimeError(f"模型加载失败: {msg['error']}")
        raise RuntimeError("embedding worker ready 超时")

    def encode(self, texts):
        self._ensure_worker()
        self._proc.stdin.write(json.dumps({"type": "encode", "texts": texts}) + "\n")
        self._proc.stdin.flush()
        line = self._proc.stdout.readline()
        if not line:
            raise RuntimeError("embedding encode 失败")
        msg = json.loads(line)
        if msg["type"] != "embeddings":
            raise RuntimeError(f"embedding 响应异常: {msg}")
        return json.loads(msg["data"])

    def close(self):
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.stdin.write(json.dumps({"type": "exit"}) + "\n")
                self._proc.stdin.flush()
                self._proc.wait(timeout=5)
            except Exception:
                self._proc.kill()


# ════════════════════════════════════════════════════════════
# 3. min-max 归一化（与 tool_router_hybrid.py L942-L955 一致）
# ════════════════════════════════════════════════════════════
def _min_max_normalize(scores):
    if not scores:
        return []
    values = [s for _, s in scores]
    min_v, max_v = min(values), max(values)
    if max_v - min_v < 1e-9:
        return [(d, 1.0) for d, _ in scores]
    return [(d, (v - min_v) / (max_v - min_v)) for d, v in scores]


# ════════════════════════════════════════════════════════════
# 4. 混合检索主流程（复刻 _query_locked + 打印中间步骤）
#    工具语料/测试用例来自 sim_common（与 TF-IDF 脚本、动画面板共用）
# ════════════════════════════════════════════════════════════
def main():
    # ── 构建双索引 ──
    bm25 = BM25Index()
    rows: list = []
    print("═" * 60)
    print("[索引构建] 工具语料（BM25 内容 = name + description；Embedding 内容 = description）")
    for t in TOOLS:
        bm25_content = t["name"] + " " + t["description"]
        bm25.add_document(t["name"], bm25_content)
        print(f"  [{t['name']}] bm25='{bm25_content}'")

    embed = EmbeddingClient()
    doc_embeddings = None
    try:
        t0 = time.time()
        doc_embeddings = embed.encode([t["description"] for t in TOOLS])
        print(f"[Embedding] 文档编码完成: 4 条 × {len(doc_embeddings[0])}维 "
              f"({(time.time()-t0)*1000:.0f}ms)")
        print("[Embedding] 文档向量前4维预览:")
        for i, t in enumerate(TOOLS):
            print(f"  {t['name']:<8} {[round(v, 3) for v in doc_embeddings[i][:4]]}")

        # ── 逐用例查询 ──
        for ci, query in enumerate(TEST_CASES, 1):
            print("\n" + "#" * 60)
            print(f"# 用例{ci}: '{query}'")
            print("#" * 60)

            # (1) BM25 路
            bm25_results = bm25.search(query, top_k=10)

            # (2) Embedding 路（打印余弦计算中间步骤）
            print(f"[Embedding] 查询编码 → {len(doc_embeddings[0])}维向量")
            t0 = time.time()
            q_vec = embed.encode([query])[0]
            q_norm = math.sqrt(sum(v * v for v in q_vec))
            print(f"[Embedding] 查询向量前4维: {[round(v, 3) for v in q_vec[:4]]}, "
                  f"||q||={q_norm:.4f}")
            embed_all = []      # 全部文档的余弦（含 <0.2，供 CSV/绘图）
            embed_kept = []     # 过 0.2 剪枝（进入融合）
            for i, t in enumerate(TOOLS):
                d_vec = doc_embeddings[i]
                d_norm = math.sqrt(sum(v * v for v in d_vec))
                dot = sum(a * b for a, b in zip(q_vec, d_vec))
                cosine = dot / (q_norm * d_norm) if (q_norm * d_norm) else 0.0
                embed_all.append((t["name"], cosine))
                flag = "✓" if cosine >= 0.2 else "✗"
                print(f"  [cosine] {t['name']:<8} dot={dot:.4f} / "
                      f"(||q||={q_norm:.4f} × ||d||={d_norm:.4f}) "
                      f"→ {cosine:.4f} {flag}(<0.2 剪枝)")
                if cosine >= 0.2:
                    embed_kept.append((t["name"], cosine))
            print(f"[Embedding] 编码耗时 {(time.time()-t0)*1000:.0f}ms")

            # (3) min-max 归一化（打印）
            bm25_norm = _min_max_normalize(bm25_results)
            embed_norm = _min_max_normalize(embed_kept)
            print(f"[归一化] BM25 {[(d, round(s,4)) for d, s in bm25_norm]}")
            print(f"[归一化] Embed {[(d, round(s,4)) for d, s in embed_norm]}")

            # (4) alpha 漂移（融合公式与 L1142-L1151 一致，遍历 ALPHAS）
            bm25_raw_map = dict(bm25_results)
            bm25_map = dict(bm25_norm)
            embed_cos_map = dict(embed_all)
            embed_map = dict(embed_norm)
            print("[alpha 漂移] 融合排序 top3 (final = alpha*bm25 + (1-alpha)*embed):")
            for alpha in ALPHAS:
                fused = []
                for t in TOOLS:
                    doc = t["name"]
                    b_s = bm25_map.get(doc, 0.0)
                    e_s = embed_map.get(doc, 0.0)
                    final = b_s if not embed_kept else alpha * b_s + (1 - alpha) * e_s
                    fused.append((doc, final))
                fused.sort(key=lambda x: x[1], reverse=True)
                print(f"  alpha={alpha}: " + " > ".join(
                    f"{d}({s:.3f})" for d, s in fused[:3]))
                # 收集 CSV 行：全 4 文档落盘（含被剪枝的 embed_cosine, 供绘图对比）
                # rank 只对有分文档编号, 0 分文档留空
                rank_map = {d: r for r, (d, s) in enumerate(fused, 1) if s > 0}
                for doc, final in fused:
                    rows.append({
                        "case_id": ci, "query": query, "alpha": alpha,
                        "tool": doc,
                        "bm25_raw": round(bm25_raw_map.get(doc, 0.0), 6),
                        "bm25_norm": round(bm25_map.get(doc, 0.0), 6),
                        "embed_cosine": round(embed_cos_map.get(doc, 0.0), 6),
                        "embed_norm": round(embed_map.get(doc, 0.0), 6),
                        "fused_score": round(final, 6),
                        "rank": rank_map.get(doc, ""),
                    })
    finally:
        embed.close()

    # ── 导出 CSV（统一走 sim_common.export_csv 可复用函数）──
    export_csv(rows, "hybrid_results.csv", [
        "case_id", "query", "alpha", "tool",
        "bm25_raw", "bm25_norm", "embed_cosine", "embed_norm",
        "fused_score", "rank"])


if __name__ == "__main__":
    main()
