#!/usr/bin/env python3
"""验证 _rrf_fuse / _rrf_fuse_weighted / build_index 的 DEBUG 日志输出

设置 agent.skills_mgmt 模块日志级别为 DEBUG, 运行一次三路融合检索,
确认关键路径日志能正确输出 (用于排查排序异常).

运行:
    python scripts/verify_debug_logging.py
"""
from __future__ import annotations

import json
import logging
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.skills_mgmt.file_store import SkillFileStore
from agent.skills_mgmt.loader import SkillLoader


# 复用 verify_three_path_fusion_real.py 的技能集 (最小子集)
TEST_SKILLS = [
    {"id": "k8s_deploy", "name": "K8s部署", "description": "Kubernetes容器编排",
     "category": "devops", "tags": ["k8s", "deploy"], "version": "1.0.0", "enabled": True},
    {"id": "helm_chart", "name": "Helm Chart管理", "description": "Kubernetes包管理",
     "category": "devops", "tags": ["helm", "k8s"], "version": "1.0.0", "enabled": True},
    {"id": "memory_summary", "name": "记忆总结", "description": "对话记忆压缩",
     "category": "memory", "tags": ["memory", "总结"], "version": "1.0.0", "enabled": True},
]


class FakeAdapter:
    _st_backend = "fake"
    _native_chroma = None

    def __init__(self, fs):
        self.fs = fs

    def search(self, intent, top_k=5, enabled_only=True, min_score=0.0):
        # 模拟向量路：对 "k8s" 不敏感, 对 "总结" 敏感
        if "总结" in intent:
            return [{"skill_id": "memory_summary", "score": 0.8, "metadata": {}}]
        return []

    @property
    def is_available(self):
        return True


def main():
    # 配置日志: agent.skills_mgmt 模块 DEBUG 级别, 其他模块 WARNING 避免噪音
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)7s] %(name)-40s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("agent.skills_mgmt").setLevel(logging.DEBUG)
    logging.getLogger("agent.skills_mgmt.loader").setLevel(logging.DEBUG)
    logging.getLogger("agent.skills_mgmt.bm25_searcher").setLevel(logging.DEBUG)

    print("=" * 80)
    print("DEBUG 日志输出验证 — _rrf_fuse / _rrf_fuse_weighted / build_index")
    print("=" * 80)
    print()
    print("【测试1】query='k8s' (TF-IDF 并列 + BM25 精确匹配, 触发三路加权融合)")
    print("-" * 80)

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        repo = tmp_dir / "skills_repo"
        repo.mkdir()
        for skill in TEST_SKILLS:
            skill_dir = repo / skill["id"]
            skill_dir.mkdir(parents=True, exist_ok=True)
            yaml_block = yaml.safe_dump(
                skill, allow_unicode=True, default_flow_style=False, sort_keys=False,
            ).strip()
            (skill_dir / "skill.md").write_text(
                f"---\n{yaml_block}\n---\n\n# {skill['name']}", encoding="utf-8",
            )

        file_store = SkillFileStore(repo_path=str(repo))
        adapter = FakeAdapter(file_store)
        loader = SkillLoader(file_store=file_store, vector_adapter=adapter)

        # 触发 build_index (首次 use_bm25=True 时构建)
        # 触发 _rrf_fuse_weighted (use_bm25=True)
        result = loader.match(
            "k8s", top_k=3,
            use_vector=True, use_bm25=True,
            retrieval_weights={"tfidf": 0.2, "vector": 0.3, "bm25": 0.5},
        )

        print()
        print(f"【融合结果】top1={result.matches[0].skill_id if result.matches else '无'}")
        if result.matches:
            for i, m in enumerate(result.matches, 1):
                bd = m.score_breakdown or {}
                print(f"  {i}. {m.skill_id:<16} rrf_norm={m.score:.4f}  "
                      f"tfidf_rank={bd.get('tfidf_rank')} "
                      f"vector_rank={bd.get('vector_rank')} "
                      f"bm25_rank={bd.get('bm25_rank')}")

    print()
    print("=" * 80)
    print("【验证点】检查上方 DEBUG 日志是否包含以下 action:")
    print("  - bm25_searcher.build_index.tokenize  (每个 skill 的分词详情)")
    print("  - bm25_searcher.build_index.ok        (含耗时 + avg_doc_tokens)")
    print("  - loader._rrf_fuse_weighted.input     (active_paths + 归一化权重)")
    print("  - loader._rrf_fuse_weighted.multi_path_contrib (多路命中文档贡献)")
    print("=" * 80)


if __name__ == "__main__":
    main()
