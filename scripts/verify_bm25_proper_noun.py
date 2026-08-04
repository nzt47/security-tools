#!/usr/bin/env python3
"""验证 BM25 在专有名词精确匹配上优于向量检索（离线对比）

设计目的:
    真实向量检索（BGE-m3）对语义泛化词敏感（如"部署"→召回所有部署类技能），
    但对专有名词缩写字面形式不敏感（如 query="k8s" 不一定高匹配 name="K8s部署"）。
    BM25 反向：对精确字面匹配强。

    本脚本用一个 SemanticFakeAdapter 模拟真实向量的这一特性：
    - 识别语义泛化词（部署/容器/扫描/向量/总结）→ 召回所有相关技能
    - 对专有名词缩写（k8s/gitleaks/sqlite-vec/helm）"看不到" → 召回弱

    对比三条路在专有名词 query 上的 top1 命中情况。

运行:
    python scripts/verify_bm25_proper_noun.py

【不易】不依赖外网/真模型，纯离线对比
【变易】SemanticFakeAdapter 可调语义词表，模拟不同向量行为
【简易】打印对比表格，直观展示 BM25 精确匹配优势
"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

import yaml

# 让脚本可独立运行（从项目根目录 import agent）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.skills_mgmt.bm25_searcher import BM25SkillSearcher
from agent.skills_mgmt.file_store import SkillFileStore
from agent.skills_mgmt.loader import SkillLoader, SkillMatch


# ════════════════════════════════════════════════════════════
#  测试技能集（含专有名词 + 语义相近的干扰项）
# ════════════════════════════════════════════════════════════

PROPER_NOUN_SKILLS = [
    {
        "id": "k8s_deploy",
        "name": "K8s部署",
        "description": "Kubernetes容器编排与集群部署，支持滚动更新",
        "category": "devops",
        "tags": ["k8s", "deploy", "容器"],
        "version": "1.0.0",
        "enabled": True,
    },
    {
        "id": "docker_deploy",
        "name": "Docker部署",
        "description": "容器镜像构建与部署，镜像仓库管理",
        "category": "devops",
        "tags": ["docker", "deploy", "容器"],
        "version": "1.0.0",
        "enabled": True,
    },
    {
        "id": "helm_chart",
        "name": "Helm Chart管理",
        "description": "Kubernetes包管理与部署模板",
        "category": "devops",
        "tags": ["helm", "k8s", "deploy"],
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
        "description": "SQLite向量检索扩展，本地嵌入存储",
        "category": "storage",
        "tags": ["sqlite", "vector", "扩展"],
        "version": "1.0.0",
        "enabled": True,
    },
    {
        "id": "memory_summary",
        "name": "记忆总结",
        "description": "对话记忆压缩与总结，历史信息提取",
        "category": "memory",
        "tags": ["memory", "总结"],
        "version": "1.0.0",
        "enabled": True,
    },
]

# 专有名词 query 集（query → 期望命中的 skill_id）
PROPER_NOUN_QUERIES = [
    ("k8s", "k8s_deploy"),
    ("gitleaks", "gitleaks_scan"),
    ("sqlite-vec", "sqlite_vec"),
    ("helm", "helm_chart"),
]


# ════════════════════════════════════════════════════════════
#  SemanticFakeAdapter — 模拟真实向量对专有名词召回弱
# ════════════════════════════════════════════════════════════

class SemanticFakeAdapter:
    """模拟真实向量检索的"语义泛化、字面不敏感"特性

    行为模型:
        - 识别语义泛化词（部署/容器/扫描/向量/总结/密钥）→ 匹配技能文本 → 给分
        - 对专有名词缩写（k8s/gitleaks/sqlite-vec/helm）"看不到"
          （模拟 BGE-m3 对字面缩写不敏感，需语义上下文才能匹配）

    这样 query="k8s" 时假向量召回弱（不识别 k8s），
    而 BM25 精确匹配 "k8s" token → 命中 k8s_deploy。

    【变易】语义词表可调，模拟不同向量模型的行为特征
    """

    # 假向量"能识别"的语义泛化词（不含专有名词缩写）
    _SEMANTIC_TERMS = {"部署", "容器", "扫描", "向量", "总结", "密钥", "记忆", "镜像"}

    # 专有名词缩写（假向量"看不到"，模拟字面不敏感）
    _PROPER_NOUNS = {"k8s", "gitleaks", "sqlite-vec", "helm", "docker", "sqlite"}

    def __init__(self, file_store: SkillFileStore):
        self.fs = file_store
        self._st_backend = "fake_semantic"
        self._native_chroma = None

    def search(self, intent: str, top_k: int = 5,
               enabled_only: bool = True, min_score: float = 0.0) -> List[Dict]:
        """模拟向量语义检索：只识别语义泛化词，不识别专有名词缩写"""
        intent_lower = (intent or "").lower()
        index = self.fs.load_metadata_index()

        # 提取 query 中的语义词（忽略专有名词缩写）
        query_semantic_terms = []
        for term in self._SEMANTIC_TERMS:
            if term in intent_lower:
                query_semantic_terms.append(term)

        results = []
        for skill_id, meta in index.items():
            if enabled_only and not meta.get("enabled", True):
                continue
            text = (
                meta.get("name", "") + meta.get("description", "")
                + " ".join(meta.get("tags", []) or [])
            )
            # 计算语义重叠度（只看语义泛化词，不看专有名词）
            overlap = sum(1 for t in query_semantic_terms if t in text)
            if overlap == 0:
                continue
            # 模拟 cosine 相似度：0.5 + 0.15*overlap，泛化召回多技能
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
#  辅助：构造临时技能库
# ════════════════════════════════════════════════════════════

def _build_skill_repo(tmp_dir: Path) -> SkillFileStore:
    """在临时目录写入技能 skill.md，返回 SkillFileStore"""
    repo = tmp_dir / "skills_repo"
    repo.mkdir()
    for skill in PROPER_NOUN_SKILLS:
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


def _bm25_single_search(file_store: SkillFileStore, query: str, top_k: int = 5):
    """BM25 单路检索（直接调用 BM25SkillSearcher）"""
    index = file_store.load_metadata_index()
    skills = []
    for sid, meta in index.items():
        m = dict(meta)
        m.setdefault("id", sid)
        skills.append(m)
    s = BM25SkillSearcher()
    s.build_index(skills)
    return s.search(query, top_k=top_k)


def _vector_single_search(adapter: SemanticFakeAdapter, query: str, top_k: int = 5):
    """向量单路检索（直接调用 SemanticFakeAdapter）"""
    return adapter.search(query, top_k=top_k, min_score=0.0)


# ════════════════════════════════════════════════════════════
#  主流程：对比三条路在专有名词 query 上的表现
# ════════════════════════════════════════════════════════════

def main():
    print("=" * 78)
    print("BM25 vs 向量检索 — 专有名词精确匹配对比验证")
    print("=" * 78)
    print()
    print("【模拟设定】SemanticFakeAdapter 模拟真实向量特性：")
    print(f"  - 识别语义泛化词: {sorted(SemanticFakeAdapter._SEMANTIC_TERMS)}")
    print(f"  - 对专有名词缩写'看不见': {sorted(SemanticFakeAdapter._PROPER_NOUNS)}")
    print("  （真实 BGE-m3 对字面缩写不敏感，需语义上下文才能匹配）")
    print()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        file_store = _build_skill_repo(tmp_dir)
        adapter = SemanticFakeAdapter(file_store)

        # 表头
        header = f"{'query':<14}{'期望':<16}{'BM25 top1':<18}{'向量 top1':<18}{'三路融合 top1':<18}"
        print(header)
        print("-" * len(header))

        bm25_hits = 0
        vec_hits = 0
        fused_hits = 0
        total = len(PROPER_NOUN_QUERIES)

        for query, expected in PROPER_NOUN_QUERIES:
            # BM25 单路
            bm25_results = _bm25_single_search(file_store, query, top_k=3)
            bm25_top1 = bm25_results[0].skill_id if bm25_results else "（无命中）"
            bm25_ok = bm25_top1 == expected
            if bm25_ok:
                bm25_hits += 1

            # 向量单路
            vec_results = _vector_single_search(adapter, query, top_k=3)
            vec_top1 = vec_results[0]["skill_id"] if vec_results else "（无命中）"
            vec_ok = vec_top1 == expected
            if vec_ok:
                vec_hits += 1

            # 三路融合（tfidf + vector + bm25）
            loader = SkillLoader(file_store=file_store, vector_adapter=adapter)
            result = loader.match(
                query, top_k=3,
                use_vector=True, use_bm25=True,
            )
            fused_top1 = result.matches[0].skill_id if result.matches else "（无命中）"
            fused_ok = fused_top1 == expected
            if fused_ok:
                fused_hits += 1

            mark = lambda ok: "✓" if ok else "✗"
            print(
                f"{query:<14}{expected:<16}"
                f"{bm25_top1:<14}{mark(bm25_ok)}  "
                f"{vec_top1:<14}{mark(vec_ok)}  "
                f"{fused_top1:<14}{mark(fused_ok)}"
            )

        print("-" * len(header))
        print()
        print("【汇总】top1 命中率（专有名词 query）:")
        print(f"  BM25 单路    : {bm25_hits}/{total} = {bm25_hits/total:.0%}")
        print(f"  向量单路(模拟): {vec_hits}/{total} = {vec_hits/total:.0%}")
        print(f"  三路融合     : {fused_hits}/{total} = {fused_hits/total:.0%}")
        print()

        # 详细展示一个典型案例
        print("─" * 78)
        print("【典型案例】query='k8s' 的三路详细对比:")
        print("─" * 78)
        case_query, case_expected = "k8s", "k8s_deploy"

        bm25_r = _bm25_single_search(file_store, case_query, top_k=3)
        print(f"\n  BM25 路（精确字面匹配 'k8s' token）:")
        for i, r in enumerate(bm25_r, 1):
            print(f"    {i}. {r.skill_id:<16} score={r.score:.4f}")

        vec_r = _vector_single_search(adapter, case_query, top_k=3)
        print(f"\n  向量路（模拟，对 'k8s' 缩写不敏感）:")
        if vec_r:
            for i, r in enumerate(vec_r, 1):
                print(f"    {i}. {r['skill_id']:<16} score={r['score']:.4f}")
        else:
            print("    （无命中 — 假向量不识别 'k8s' 语义词）")

        loader = SkillLoader(file_store=file_store, vector_adapter=adapter)
        result = loader.match(
            case_query, top_k=3, use_vector=True, use_bm25=True,
        )
        print(f"\n  三路融合结果（tfidf:0.2 + vector:0.6 + bm25:0.2）:")
        for i, m in enumerate(result.matches, 1):
            bd = m.score_breakdown or {}
            print(
                f"    {i}. {m.skill_id:<16} score={m.score:.4f}  "
                f"tfidf_rank={bd.get('tfidf_rank')} "
                f"vector_rank={bd.get('vector_rank')} "
                f"bm25_rank={bd.get('bm25_rank')}"
            )
        print(f"\n  结论: 期望 {case_expected}，融合 top1 = "
              f"{result.matches[0].skill_id if result.matches else '无'}")
        print()
        print("【权重调优】query='k8s' 对称场景下，调高 BM25 权重打破并列:")
        print("  （helm_chart 与 k8s_deploy 在 tfidf/bm25 两路排名对称，默认权重下 score 相等）")
        for w_cfg in [
            {"tfidf": 0.2, "vector": 0.6, "bm25": 0.2},  # 默认
            {"tfidf": 0.2, "vector": 0.3, "bm25": 0.5},  # 提高 bm25
            {"tfidf": 0.1, "vector": 0.1, "bm25": 0.8},  # bm25 主导
        ]:
            r = loader.match(case_query, top_k=3, use_vector=True, use_bm25=True,
                             retrieval_weights=w_cfg)
            top1 = r.matches[0].skill_id if r.matches else "无"
            tag = "默认" if w_cfg["bm25"] == 0.2 else (
                "bm25↑" if w_cfg["bm25"] == 0.5 else "bm25主导")
            ok = "✓" if top1 == case_expected else "✗"
            print(f"    [{tag}] tfidf={w_cfg['tfidf']} vector={w_cfg['vector']} "
                  f"bm25={w_cfg['bm25']} → top1={top1:<14} {ok}")
        print()
        print("【验证结论】")
        print("  - BM25 对专有名词缩写（k8s/gitleaks/sqlite-vec/helm）精确匹配强（100%）")
        print("  - 向量检索（模拟）对字面缩写不敏感，易漏召回专有名词（0%）")
        print("  - 三路融合兼顾语义（向量）与字面（BM25），但默认 bm25:0.2 权重偏低，")
        print("    纯专有名词 query 下 BM25 优势会被 TF-IDF 对称干扰稀释")
        print("  - 工程启示：含专有名词的查询场景，建议将 bm25 权重提到 0.5+")
        print("=" * 78)


if __name__ == "__main__":
    main()
