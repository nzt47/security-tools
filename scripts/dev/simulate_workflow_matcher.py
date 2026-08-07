"""模拟 WorkflowMatcher 相似度计算过程（复刻 agent/workflow_learning/matcher.py 核心算法）

【不易】算法与原实现逐行一致：CJK 混合分词 / 平滑 IDF(0.001 下界) /
        余弦归一化(索引时归一化, 查询点积即余弦) / 综合分 = sim * confidence * priority_factor
【简易】自包含纯标准库脚本，不依赖 agent 包，python scripts/dev/simulate_workflow_matcher.py 直接运行
"""
import math
import re
import sys

from sim_common import TEST_CASES, export_csv

# Windows GBK 控制台兼容（PowerShell 下保证中文正常显示）
sys.stdout.reconfigure(encoding="utf-8")

# ════════════════════════════════════════════════════════════
# 1. 分词（与 matcher.py L27-L31 一致）
#    CJK 混合：英文按整词 [a-zA-Z0-9_]+、中文按单字 [\u4e00-\u9fff]
# ════════════════════════════════════════════════════════════
_TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")


def _tokenize(text: str) -> list:
    return _TOKEN_RE.findall((text or "").lower())


# ════════════════════════════════════════════════════════════
# 2. 平滑 IDF（与 matcher.py L34-L42 一致）
#    加 0.001 下界：单文档退化时(N==df==1)原公式恒为 0，会导致匹配不上首个工作流
# ════════════════════════════════════════════════════════════
def _idf(n_docs: int, df: int) -> float:
    return max(math.log((n_docs + 1) / (1 + df)), 0.001)


# ════════════════════════════════════════════════════════════
# 3. TF-IDF 索引（与 matcher.py L45-L123 一致，打印中间过程）
# ════════════════════════════════════════════════════════════
class TfidfIndex:
    def __init__(self):
        self._docs: dict = {}       # doc_id -> tokens
        self._df: dict = {}         # term -> 文档频率
        self._cache: dict = {}      # doc_id -> 归一化 tf-idf 向量
        self._dirty = True

    def add(self, doc_id: str, text: str) -> None:
        tokens = _tokenize(text)
        self._docs[doc_id] = tokens
        for t in set(tokens):
            self._df[t] = self._df.get(t, 0) + 1
        self._dirty = True

    def _rebuild(self) -> None:
        """构建每个文档的归一化 tf-idf 向量（与 matcher.py L71-L87 一致）"""
        N = max(1, len(self._docs))
        print("─" * 60)
        print(f"[索引重建] 文档数 N={N}")
        self._cache = {}
        for doc_id, tokens in self._docs.items():
            tf: dict = {}
            for t in tokens:
                tf[t] = tf.get(t, 0) + 1
            # L2 范数（分母 or 1.0 防除零）
            length = math.sqrt(sum(
                (cnt / len(tokens)) ** 2 * _idf(N, self._df.get(t, 0)) ** 2
                for t, cnt in tf.items()
            )) or 1.0
            vec: dict = {}
            for t, cnt in tf.items():
                tf_val = cnt / len(tokens)          # 归一化词频
                vec[t] = tf_val * _idf(N, self._df.get(t, 0)) / length
            self._cache[doc_id] = vec
            print(f"\n文档[{doc_id}] tokens={tokens}")
            print(f"  向量(已余弦归一化): { {k: round(v, 4) for k, v in vec.items()} }")
        self._dirty = False

    def query(self, text: str, top_k: int = 5) -> list:
        """查询：向量已归一化，点积即余弦（与 matcher.py L89-L123 一致）

        Returns:
            (top_scores, all_sims)
            all_sims = {doc_id: cosine} 含 0 分文档，供 CSV 全量导出
        """
        if self._dirty:
            self._rebuild()
        q_tokens = _tokenize(text)
        if not q_tokens:
            return [], {}
        N = max(1, len(self._docs))
        tf: dict = {}
        for t in q_tokens:
            tf[t] = tf.get(t, 0) + 1
        print("\n" + "═" * 60)
        print(f"[查询] text='{text}'")
        print(f"  tokens={q_tokens}")

        # 查询向量（同样归一化）
        q_vec: dict = {}
        q_length = 0.0
        for t, cnt in tf.items():
            tf_val = cnt / len(q_tokens)
            v = tf_val * _idf(N, self._df.get(t, 0))
            q_vec[t] = v
            q_length += v * v
        q_length = math.sqrt(q_length) or 1.0
        for t in q_vec:
            q_vec[t] /= q_length
        print(f"  查询向量: { {k: round(v, 4) for k, v in q_vec.items()} }")

        all_sims: dict = {}
        scores = []
        for doc_id, vec in self._cache.items():
            # 点积 = 余弦（两向量均归一化）
            sim = sum(w * vec.get(t, 0.0) for t, w in q_vec.items())
            all_sims[doc_id] = sim
            print(f"  → 与[{doc_id}] 点积=cosine={sim:.4f}")
            if sim > 0:
                scores.append((doc_id, sim))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:top_k], all_sims


# ════════════════════════════════════════════════════════════
# 4. 模拟 WorkflowMatcher（含综合分，与 matcher.py L159-L192 一致）
# ════════════════════════════════════════════════════════════
class FakeWorkflow:
    """最小工作流对象（只含匹配所需字段）"""

    def __init__(self, wf_id, name, description, task_signature,
                 trigger_patterns, tags, confidence, priority, enabled=True):
        self.id = wf_id
        self.name = name
        self.description = description
        self.task_signature = task_signature
        self.trigger_patterns = trigger_patterns
        self.tags = tags
        self.confidence = confidence
        self.priority = priority
        self.enabled = enabled


class WorkflowMatcher:
    def __init__(self, min_similarity=0.3, min_confidence=0.4):
        self.min_similarity = min_similarity
        self.min_confidence = min_confidence
        self._index = TfidfIndex()
        self._workflows = {}

    def register(self, wf):
        # 索引文本 = 名称 + 描述 + 任务签名 + 触发模式 + 标签（与 L139-L142 一致）
        text = " ".join([
            wf.name, wf.description, wf.task_signature,
            " ".join(wf.trigger_patterns), " ".join(wf.tags),
        ])
        self._index.add(wf.id, text)
        self._workflows[wf.id] = wf

    def match(self, task_text, top_k=5):
        print("\n" + "█" * 60)
        print(f"[WorkflowMatcher.match] task='{task_text}'")
        print(f"  阈值: min_similarity={self.min_similarity}, "
              f"min_confidence={self.min_confidence}")
        candidates, all_sims = self._index.query(task_text, top_k=top_k)
        # 记录全量扫描明细（含被过滤文档），供 CSV 导出
        self._last_scan = []   # (wf, sim, passed, combined)
        results = []
        for wf_id, sim in all_sims.items():
            wf = self._workflows.get(wf_id)
            if not wf or not wf.enabled:
                self._last_scan.append((wf, sim, False, None))
                continue
            passed = True
            combined = None
            if sim < self.min_similarity:
                print(f"  ✗ [{wf.id}] sim={sim:.4f} < {self.min_similarity} 被过滤")
                passed = False
            elif wf.confidence < self.min_confidence:
                print(f"  ✗ [{wf.id}] confidence={wf.confidence} < "
                      f"{self.min_confidence} 被过滤")
                passed = False
            else:
                priority_factor = 0.5 + wf.priority / 200.0   # 0.5 ~ 1.0
                combined = sim * wf.confidence * priority_factor
                print(f"  ✓ [{wf.id}] sim={sim:.4f} × conf={wf.confidence} × "
                      f"prio_factor={priority_factor:.2f} = {combined:.4f}")
                results.append((wf, combined))
            self._last_scan.append((wf, sim, passed, combined))
        results.sort(key=lambda x: x[1], reverse=True)
        return results


# 统一的 6 组中文测试用例来自 sim_common.TEST_CASES（与 hybrid 脚本、动画面板共用）


def main():
    # ── 构造 4 个工作流（索引文本 = name + description + signature + triggers + tags）──
    workflows = [
        FakeWorkflow(
            wf_id="wf-pdf-parse", name="PDF解析", description="解析PDF文件提取文字内容",
            task_signature="pdf解析", trigger_patterns=["解析pdf", "提取pdf文本"],
            tags=["pdf", "文档"], confidence=0.9, priority=80),
        FakeWorkflow(
            wf_id="wf-translate-en", name="翻译英文", description="把中文翻译成英文",
            task_signature="翻译英文", trigger_patterns=["翻译成英文", "英文翻译"],
            tags=["翻译", "英文"], confidence=0.85, priority=70),
        FakeWorkflow(
            wf_id="wf-img-ocr", name="图片文字识别", description="识别图片中的文字",
            task_signature="图片识别", trigger_patterns=["识别图片文字", "ocr"],
            tags=["图片", "ocr"], confidence=0.8, priority=60),
        FakeWorkflow(
            wf_id="wf-sql-query", name="数据库查询", description="执行SQL查询数据库",
            task_signature="sql查询", trigger_patterns=["查数据库", "执行sql"],
            tags=["sql", "数据库"], confidence=0.95, priority=90),
    ]
    matcher = WorkflowMatcher()
    for wf in workflows:
        matcher.register(wf)

    # ── 遍历 5 组用例（首用例触发索引重建，后续只做查询）──
    rows: list = []
    for i, case in enumerate(TEST_CASES, 1):
        print("\n" + "#" * 60)
        print(f"# 用例{i}: '{case}'")
        print("#" * 60)
        matcher.match(case)
        # 导出本用例全量扫描明细（含被过滤文档）
        for wf, sim, passed, combined in matcher._last_scan:
            rows.append({
                "case_id": i,
                "query": case,
                "tool": wf.id,
                "tfidf_sim": round(sim, 6),
                "combined_score": round(combined, 6) if combined is not None else "",
                "hit": "yes" if passed else "no",
            })

    # ── 导出 CSV（统一走 sim_common.export_csv 可复用函数）──
    export_csv(rows, "tfidf_results.csv",
               ["case_id", "query", "tool", "tfidf_sim",
                "combined_score", "hit"])


if __name__ == "__main__":
    main()
