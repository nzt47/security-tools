#!/usr/bin/env python3
"""真实专有名词三路融合检索实际输出验证

聚焦任务1: 构造包含真实专有名词的本地测试用例, 运行三路融合检索查看实际输出.
与 verify_bm25_proper_noun.py 的区别:
    - 前者: 对比 BM25 vs 向量单路的命中率 (表格汇总)
    - 本脚本: 展示三路融合的【完整实际输出】— 各路原始排名 + RRF 融合计算过程
              + score_breakdown 透出字段 + 默认权重 vs bm25:0.5 对比

真实专有名词场景 (来自项目记忆):
    k8s / gitleaks / sqlite-vec / helm / chromadb / BGE-m3 / ONNX / RRF

运行:
    python scripts/verify_three_path_fusion_real.py

【不易】纯离线, 不依赖外网/真模型 (SemanticFakeAdapter 模拟向量路)
【变易】query 集与权重配置可扩展, 支持命令行 --weights 自定义
【简易】逐 query 打印融合详情, 直观展示 RRF 排序机制
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

# 让脚本可独立运行 (从项目根目录 import agent)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.skills_mgmt.bm25_searcher import BM25SkillSearcher
from agent.skills_mgmt.file_store import SkillFileStore
from agent.skills_mgmt.loader import SkillLoader, SkillMatch


# ════════════════════════════════════════════════════════════
#  真实专有名词技能集 (含语义相近干扰项, 验证 RRF 区分度)
# ════════════════════════════════════════════════════════════

REAL_PROPER_NOUN_SKILLS = [
    {
        "id": "k8s_deploy",
        "name": "K8s部署",
        "description": "Kubernetes容器编排与集群部署, 支持滚动更新",
        "category": "devops",
        "tags": ["k8s", "deploy", "容器"],
        "version": "1.0.0",
        "enabled": True,
    },
    {
        "id": "helm_chart",
        "name": "Helm Chart管理",
        "description": "Kubernetes包管理与部署模板, Chart仓库",
        "category": "devops",
        "tags": ["helm", "k8s", "chart"],
        "version": "1.0.0",
        "enabled": True,
    },
    {
        "id": "docker_deploy",
        "name": "Docker部署",
        "description": "容器镜像构建与部署, 镜像仓库管理",
        "category": "devops",
        "tags": ["docker", "deploy", "容器"],
        "version": "1.0.0",
        "enabled": True,
    },
    {
        "id": "gitleaks_scan",
        "name": "gitleaks扫描",
        "description": "扫描代码仓库密钥泄露与凭证检测",
        "category": "security",
        "tags": ["security", "gitleaks", "scan"],
        "version": "1.0.0",
        "enabled": True,
    },
    {
        "id": "sqlite_vec",
        "name": "sqlite-vec向量",
        "description": "SQLite向量检索扩展, 本地嵌入存储",
        "category": "storage",
        "tags": ["sqlite", "vector", "vec"],
        "version": "1.0.0",
        "enabled": True,
    },
    {
        "id": "chromadb_store",
        "name": "ChromaDB存储",
        "description": "向量数据库ChromaDB本地存储与检索",
        "category": "storage",
        "tags": ["chromadb", "vector", "db"],
        "version": "1.0.0",
        "enabled": True,
    },
    {
        "id": "bge_reranker",
        "name": "BGE-reranker精排",
        "description": "BGE-m3 Cross-Encoder重排序模型",
        "category": "ml",
        "tags": ["bge", "reranker", "m3"],
        "version": "1.0.0",
        "enabled": True,
    },
    {
        "id": "onnx_infer",
        "name": "ONNX推理",
        "description": "ONNX运行时模型推理加速",
        "category": "ml",
        "tags": ["onnx", "infer", "accelerate"],
        "version": "1.0.0",
        "enabled": True,
    },
    {
        "id": "memory_summary",
        "name": "记忆总结",
        "description": "对话记忆压缩与总结, 历史信息提取",
        "category": "memory",
        "tags": ["memory", "总结"],
        "version": "1.0.0",
        "enabled": True,
    },
]

# 真实专有名词 query 集 (query → 期望命中的 skill_id)
REAL_QUERIES: List[Tuple[str, str]] = [
    ("k8s", "k8s_deploy"),
    ("helm", "helm_chart"),
    ("gitleaks", "gitleaks_scan"),
    ("sqlite-vec", "sqlite_vec"),
    ("chromadb", "chromadb_store"),
    ("BGE-m3", "bge_reranker"),
    ("ONNX", "onnx_infer"),
]


# ════════════════════════════════════════════════════════════
#  SemanticFakeAdapter — 模拟真实向量对专有名词召回弱
# ════════════════════════════════════════════════════════════

class SemanticFakeAdapter:
    """模拟真实向量检索的"语义泛化、字面不敏感"特性

    行为模型:
        - 识别语义泛化词 (部署/容器/扫描/向量/总结/密钥/推理/存储) → 匹配技能文本 → 给分
        - 对专有名词缩写 (k8s/gitleaks/sqlite-vec/helm/chromadb/BGE-m3/ONNX) "看不到"
          (模拟 BGE-m3 对字面缩写不敏感, 需语义上下文才能匹配)

    【变易】语义词表可调, 模拟不同向量模型的行为特征
    """

    _SEMANTIC_TERMS = {
        "部署", "容器", "扫描", "向量", "总结", "密钥",
        "记忆", "镜像", "推理", "存储", "精排", "加速",
    }

    def __init__(self, file_store: SkillFileStore):
        self.fs = file_store
        # 非 None 标识, 避免 _try_vector_match 的 BM25-fallback 检测误判
        self._st_backend = "fake_semantic"
        self._native_chroma = None

    def search(self, intent: str, top_k: int = 5,
               enabled_only: bool = True, min_score: float = 0.0) -> List[Dict]:
        intent_lower = (intent or "").lower()
        index = self.fs.load_metadata_index()

        # 提取 query 中的语义词 (忽略专有名词缩写)
        query_semantic_terms = [t for t in self._SEMANTIC_TERMS if t in intent_lower]

        results = []
        for skill_id, meta in index.items():
            if enabled_only and not meta.get("enabled", True):
                continue
            text = (
                meta.get("name", "") + meta.get("description", "")
                + " ".join(meta.get("tags", []) or [])
            )
            overlap = sum(1 for t in query_semantic_terms if t in text)
            if overlap == 0:
                continue
            # 模拟 cosine: 0.5 + 0.15*overlap, 泛化召回多技能
            score = 0.5 + 0.15 * overlap
            if score < min_score:
                continue
            results.append({"skill_id": skill_id, "score": score, "metadata": meta})

        results.sort(key=lambda r: r["score"], reverse=True)
        return results[:top_k]

    @property
    def is_available(self) -> bool:
        return True


# ════════════════════════════════════════════════════════════
#  辅助函数
# ════════════════════════════════════════════════════════════

def _build_skill_repo(tmp_dir: Path) -> SkillFileStore:
    """在临时目录写入技能 skill.md, 返回 SkillFileStore"""
    repo = tmp_dir / "skills_repo"
    repo.mkdir()
    for skill in REAL_PROPER_NOUN_SKILLS:
        skill_dir = repo / skill["id"]
        skill_dir.mkdir(parents=True, exist_ok=True)
        yaml_block = yaml.safe_dump(
            skill, allow_unicode=True, default_flow_style=False, sort_keys=False,
        ).strip()
        body = f"# {skill['name']}\n\n{skill['description']}"
        (skill_dir / "skill.md").write_text(
            f"---\n{yaml_block}\n---\n\n{body}", encoding="utf-8",
        )
    return SkillFileStore(repo_path=str(repo))


def _single_path_tfidf(loader: SkillLoader, intent: str, top_k: int = 5) -> List[SkillMatch]:
    """TF-IDF 单路检索 (复用 loader 内部分词, 保证与融合路同尺度)"""
    from agent.skills_mgmt.loader import _meta_to_meta_text, _match_score, _tokenize, estimate_tokens
    import json as _json
    index = loader.fs.load_metadata_index()
    query_tokens = _tokenize(intent)
    matches: List[SkillMatch] = []
    for skill_id, meta in index.items():
        if not meta.get("enabled", True):
            continue
        meta_text = _meta_to_meta_text(meta)
        score = _match_score(meta_text, query_tokens)
        if score <= 0:
            continue
        meta_str = _json.dumps(meta, ensure_ascii=False)
        matches.append(SkillMatch(
            skill_id=skill_id,
            name=meta.get("name", skill_id),
            description=meta.get("description", ""),
            score=score,
            estimated_tokens=estimate_tokens(meta_str),
            category=meta.get("category", ""),
            tags=meta.get("tags", []),
            version=meta.get("version", ""),
            enabled=True,
        ))
    matches.sort(key=lambda m: m.score, reverse=True)
    return matches[:top_k]


def _single_path_bm25(file_store: SkillFileStore, query: str, top_k: int = 5):
    """BM25 单路检索"""
    index = file_store.load_metadata_index()
    skills = []
    for sid, meta in index.items():
        m = dict(meta)
        m.setdefault("id", sid)
        skills.append(m)
    s = BM25SkillSearcher()
    s.build_index(skills)
    return s.search(query, top_k=top_k)


def _single_path_vector(adapter: SemanticFakeAdapter, query: str, top_k: int = 5):
    """向量单路检索"""
    return adapter.search(query, top_k=top_k, min_score=0.0)


def _fmt_rank_list(items: List[Any], get_id, get_score, n: int = 3) -> str:
    """格式化排名列表 (top-n)"""
    if not items:
        return "  (无命中)"
    lines = []
    for i, item in enumerate(items[:n], 1):
        sid = get_id(item)
        sc = get_score(item)
        lines.append(f"    {i}. {sid:<18} score={sc:.4f}")
    return "\n".join(lines)


# ════════════════════════════════════════════════════════════
#  主流程: 逐 query 展示三路融合实际输出
# ════════════════════════════════════════════════════════════

def run_single_query(
    file_store: SkillFileStore,
    adapter: SemanticFakeAdapter,
    loader: SkillLoader,
    query: str,
    expected: str,
    weights: Dict[str, float],
) -> Dict[str, Any]:
    """运行单个 query 的三路检索, 打印详情, 返回统计"""
    print(f"\n┌─ query={query!r}  期望={expected}  权重={weights}")
    print("│")

    # ── TF-IDF 路 ──
    tfidf_matches = _single_path_tfidf(loader, query, top_k=5)
    print(f"│ [TF-IDF 路] (权重={weights.get('tfidf', 0.2)})")
    print(_fmt_rank_list(tfidf_matches, lambda m: m.skill_id, lambda m: m.score))

    # ── 向量路 ──
    vec_results = _single_path_vector(adapter, query, top_k=5)
    print(f"│ [向量路(模拟)] (权重={weights.get('vector', 0.6)})")
    print(_fmt_rank_list(vec_results, lambda r: r["skill_id"], lambda r: r["score"]))

    # ── BM25 路 ──
    bm25_results = _single_path_bm25(file_store, query, top_k=5)
    print(f"│ [BM25 路] (权重={weights.get('bm25', 0.2)})")
    print(_fmt_rank_list(bm25_results, lambda r: r.skill_id, lambda r: r.score))

    # ── 三路融合 ──
    result = loader.match(
        query, top_k=5,
        use_vector=True, use_bm25=True,
        retrieval_weights=weights,
    )
    print("│ [三路融合结果] (RRF 加权, k=60)")
    if not result.matches:
        print("│   (无融合结果)")
        fused_top1 = None
    else:
        for i, m in enumerate(result.matches, 1):
            bd = m.score_breakdown or {}
            ranks_str = (
                f"tfidf_rank={bd.get('tfidf_rank')} "
                f"vector_rank={bd.get('vector_rank')} "
                f"bm25_rank={bd.get('bm25_rank')}"
            )
            print(
                f"│   {i}. {m.skill_id:<18} rrf_norm={m.score:.4f}  "
                f"{ranks_str}  rrf_raw={bd.get('rrf_score')}"
            )
        fused_top1 = result.matches[0].skill_id

    ok = fused_top1 == expected
    mark = "✓" if ok else "✗"
    print(f"│")
    print(f"└─→ 融合 top1={fused_top1}  期望={expected}  {mark}")

    return {
        "query": query,
        "expected": expected,
        "fused_top1": fused_top1,
        "hit": ok,
        "retrieval_method": result.retrieval_method,
        "fused_count": len(result.matches),
    }


def main():
    parser = argparse.ArgumentParser(description="三路融合检索实际输出验证")
    parser.add_argument(
        "--weights", type=str, default=None,
        help='权重 JSON, 例: \'{"tfidf":0.2,"vector":0.3,"bm25":0.5}\'',
    )
    args = parser.parse_args()

    custom_weights = None
    if args.weights:
        try:
            custom_weights = json.loads(args.weights)
        except json.JSONDecodeError as e:
            print(f"[ERROR] --weights JSON 解析失败: {e}", file=sys.stderr)
            sys.exit(1)

    print("=" * 80)
    print("真实专有名词三路融合检索 — 实际输出验证")
    print("=" * 80)
    print()
    print(f"技能库 ({len(REAL_PROPER_NOUN_SKILLS)} 个, 含真实专有名词):")
    for s in REAL_PROPER_NOUN_SKILLS:
        print(f"  - {s['id']:<16} {s['name']:<14} tags={s['tags']}")
    print()
    print(f"专有名词 query 集 ({len(REAL_QUERIES)} 个):")
    for q, expected in REAL_QUERIES:
        print(f"  - {q:<14} → 期望 {expected}")
    print()
    print("SemanticFakeAdapter 模拟设定:")
    print(f"  - 识别语义泛化词: {sorted(SemanticFakeAdapter._SEMANTIC_TERMS)}")
    print(f"  - 对专有名词缩写'看不见' (模拟 BGE-m3 字面不敏感)")
    print()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        file_store = _build_skill_repo(tmp_dir)
        adapter = SemanticFakeAdapter(file_store)
        loader = SkillLoader(file_store=file_store, vector_adapter=adapter)

        # ── 阶段1: 默认权重 (bm25=0.2) ──
        default_weights = {"tfidf": 0.2, "vector": 0.6, "bm25": 0.2}
        print("═" * 80)
        print(f"【阶段1】默认权重 bm25=0.2  (权重={default_weights})")
        print("═" * 80)
        default_results = []
        for query, expected in REAL_QUERIES:
            r = run_single_query(
                file_store, adapter, loader, query, expected, default_weights,
            )
            default_results.append(r)

        default_hits = sum(1 for r in default_results if r["hit"])
        print(f"\n【阶段1 汇总】默认权重命中率: {default_hits}/{len(REAL_QUERIES)} "
              f"= {default_hits/len(REAL_QUERIES):.0%}")

        # ── 阶段2: bm25=0.5 (用户要求验证的调整) ──
        tuned_weights = custom_weights or {"tfidf": 0.2, "vector": 0.3, "bm25": 0.5}
        print()
        print("═" * 80)
        print(f"【阶段2】bm25=0.5 权重  (权重={tuned_weights})")
        print("═" * 80)
        tuned_results = []
        for query, expected in REAL_QUERIES:
            r = run_single_query(
                file_store, adapter, loader, query, expected, tuned_weights,
            )
            tuned_results.append(r)

        tuned_hits = sum(1 for r in tuned_results if r["hit"])
        print(f"\n【阶段2 汇总】bm25=0.5 命中率: {tuned_hits}/{len(REAL_QUERIES)} "
              f"= {tuned_hits/len(REAL_QUERIES):.0%}")

        # ── 对比汇总 ──
        print()
        print("═" * 80)
        print("【对比汇总】默认权重 vs bm25=0.5")
        print("═" * 80)
        header = f"{'query':<14}{'期望':<18}{'默认 top1':<18}{'bm25↑ top1':<18}{'改善':<6}"
        print(header)
        print("-" * len(header))
        improved = 0
        regressed = 0
        for d, t in zip(default_results, tuned_results):
            d_mark = "✓" if d["hit"] else "✗"
            t_mark = "✓" if t["hit"] else "✗"
            # 改善: 默认未命中→调优命中; 退化: 默认命中→调优未命中
            if (not d["hit"]) and t["hit"]:
                change = "↑"
                improved += 1
            elif d["hit"] and (not t["hit"]):
                change = "↓"
                regressed += 1
            else:
                change = "—"
            print(
                f"{d['query']:<14}{d['expected']:<18}"
                f"{d['fused_top1'] or '(无)':<16}{d_mark}  "
                f"{t['fused_top1'] or '(无)':<16}{t_mark}  "
                f"{change}"
            )
        print("-" * len(header))
        print(f"默认权重命中率: {default_hits}/{len(REAL_QUERIES)} "
              f"= {default_hits/len(REAL_QUERIES):.0%}")
        print(f"bm25=0.5 命中率: {tuned_hits}/{len(REAL_QUERIES)} "
              f"= {tuned_hits/len(REAL_QUERIES):.0%}")
        print(f"改善: {improved}  退化: {regressed}  持平: {len(REAL_QUERIES)-improved-regressed}")
        print()
        if tuned_hits > default_hits:
            print("【结论】提高 bm25 权重到 0.5 改善了专有名词召回, 建议采用.")
        elif tuned_hits == default_hits:
            print("【结论】命中率持平, 但 rrf_score 区分度可能变化 (见上方融合详情).")
        else:
            print("【结论】提高 bm25 权重反而退化, 不建议调整 (可能引入字面噪声).")
        print("=" * 80)


if __name__ == "__main__":
    main()
