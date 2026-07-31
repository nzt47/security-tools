#!/usr/bin/env python3
"""端到端验证: .env 驱动的 BM25 权重在 match() 中生效

链路: .env (SKILLS_FUSION_WEIGHT_BM25=0.5)
      → load_dotenv() 加载到 os.environ
      → SkillLoader._get_default_weights() 读取
      → _try_rrf_match 调用 (不传 retrieval_weights, 走默认路径)
      → match() 三路融合结果

验证场景: query='k8s' (默认权重下 top1=helm_chart 错误, bm25=0.5 下 top1=k8s_deploy 正确)

注意: BM25 在极小数据集(2个文档)上, 若 query 词在所有文档中出现(df=N),
      IDF 会变负导致分数被过滤。故使用 9 个技能集模拟真实场景。
"""
import os
import sys
import tempfile
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 加载 .env (生产环境由 docker-compose/shell 负责)
from dotenv import load_dotenv
load_dotenv()

from agent.skills_mgmt.file_store import SkillFileStore
from agent.skills_mgmt.loader import SkillLoader

# 复用 verify_three_path_fusion_real.py 的 9 技能集（保证 BM25 IDF 为正）
TEST_SKILLS = [
    {"id": "k8s_deploy", "name": "K8s部署", "description": "Kubernetes容器编排与集群部署, 支持滚动更新",
     "category": "devops", "tags": ["k8s", "deploy", "容器"], "version": "1.0.0", "enabled": True},
    {"id": "helm_chart", "name": "Helm Chart管理", "description": "Kubernetes包管理与部署模板, Chart仓库",
     "category": "devops", "tags": ["helm", "k8s", "chart"], "version": "1.0.0", "enabled": True},
    {"id": "docker_deploy", "name": "Docker部署", "description": "容器镜像构建与部署, 镜像仓库管理",
     "category": "devops", "tags": ["docker", "deploy", "容器"], "version": "1.0.0", "enabled": True},
    {"id": "gitleaks_scan", "name": "gitleaks扫描", "description": "扫描代码仓库密钥泄露与凭证检测",
     "category": "security", "tags": ["security", "gitleaks", "scan"], "version": "1.0.0", "enabled": True},
    {"id": "sqlite_vec", "name": "sqlite-vec向量", "description": "SQLite向量检索扩展, 本地嵌入存储",
     "category": "storage", "tags": ["sqlite", "vector", "vec"], "version": "1.0.0", "enabled": True},
    {"id": "chromadb_store", "name": "ChromaDB存储", "description": "向量数据库ChromaDB本地存储与检索",
     "category": "storage", "tags": ["chromadb", "vector", "db"], "version": "1.0.0", "enabled": True},
    {"id": "bge_reranker", "name": "BGE-reranker精排", "description": "BGE-m3 Cross-Encoder重排序模型",
     "category": "ml", "tags": ["bge", "reranker", "m3"], "version": "1.0.0", "enabled": True},
    {"id": "onnx_infer", "name": "ONNX推理", "description": "ONNX运行时模型推理加速",
     "category": "ml", "tags": ["onnx", "infer", "accelerate"], "version": "1.0.0", "enabled": True},
    {"id": "memory_summary", "name": "记忆总结", "description": "对话记忆压缩与总结, 历史信息提取",
     "category": "memory", "tags": ["memory", "总结"], "version": "1.0.0", "enabled": True},
]


class FakeAdapter:
    """模拟向量路: 对专有名词缩写不敏感"""
    _st_backend = "fake"
    _native_chroma = None

    def __init__(self, fs):
        self.fs = fs

    def search(self, intent, top_k=5, enabled_only=True, min_score=0.0):
        # 模拟向量路对 "k8s" 缩写不敏感
        return []

    @property
    def is_available(self):
        return True


def main():
    print("=" * 70)
    print("端到端验证: .env 驱动的 BM25 权重在 match() 中生效")
    print("=" * 70)

    # 显示 .env 配置
    bm25_env = os.environ.get("SKILLS_FUSION_WEIGHT_BM25")
    print(f"\n【.env 配置】SKILLS_FUSION_WEIGHT_BM25 = {bm25_env!r}")

    weights = SkillLoader._get_default_weights()
    print(f"【_get_default_weights()】{weights}")
    total = sum(weights.values())
    normalized = {k: round(v / total, 4) for k, v in weights.items()}
    print(f"【归一化后】{normalized} (total={total})")

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

        # 调用 match() 不传 retrieval_weights → 走 _get_default_weights() → 读 .env
        result = loader.match(
            "k8s", top_k=3,
            use_vector=True, use_bm25=True,
            # 不传 retrieval_weights, 验证 .env 驱动
        )

        top1 = result.matches[0].skill_id if result.matches else None
        print(f"\n【match() 结果】query='k8s' (不传 retrieval_weights, 走 .env 默认)")
        print(f"  retrieval_method = {result.retrieval_method}")
        print(f"  top1 = {top1}")
        if result.matches:
            for i, m in enumerate(result.matches, 1):
                bd = m.score_breakdown or {}
                print(f"  {i}. {m.skill_id:<16} rrf_norm={m.score:.4f}  "
                      f"tfidf_rank={bd.get('tfidf_rank')} bm25_rank={bd.get('bm25_rank')}")

        print()
        if top1 == "k8s_deploy":
            print("✓ 验证通过: .env 中 bm25=0.5 让 match() 正确召回 k8s_deploy")
            print("  (默认 bm25=0.2 时 top1 会错排为 helm_chart, 见 verify_three_path_fusion_real.py)")
        else:
            print(f"✗ 验证失败: top1={top1}, 期望 k8s_deploy")
            print("  可能 .env 未加载或权重未生效")
            sys.exit(1)

    print()
    print("=" * 70)
    print("结论: .env 驱动的权重配置端到端生效")
    print("  .env (SKILLS_FUSION_WEIGHT_BM25=0.5)")
    print("  → _get_default_weights() 读取")
    print("  → _try_rrf_match 应用 (不传 retrieval_weights 时走此路径)")
    print("  → match() 返回正确结果 (k8s_deploy 而非 helm_chart)")
    print("=" * 70)


if __name__ == "__main__":
    main()
