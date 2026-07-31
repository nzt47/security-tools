"""向量检索端到端 Demo — 验证语义相似 query 能被向量检索命中

对比三种检索模式:
1. TF-IDF（use_vector=False）— 字面词频匹配，无法捕获语义
2. 向量（use_vector=True）— FakeModel 基于关键词 bag-of-words 模拟语义检索
3. RRF 融合（use_vector=True, fusion_mode="rrf"）— TF-IDF + 向量双路融合

验证用例:
- "帮我反思一下" → 期望命中 self_reflection（description: "主动检查回答合理性"）
  TF-IDF 无法命中（"反思"不在 query 字面，但 description 有"反思"），
  向量检索通过关键词匹配命中。
- "把这段对话压缩一下" → 期望命中 memory_summary
- "这个PDF怎么读取" → 期望命中 pdf_parser

设计:
- 用 FakeSentenceTransformer 避免加载真 BGE-m3 模型（11 分钟 + Windows 崩溃风险）
- FakeModel 基于关键词 bag-of-words：含相同关键词的文本向量相似度高
- 8 个技能覆盖 8 个关键词域，验证语义召回能力

运行:
    python scripts/demo_vector_retrieval.py

【不易】不修改生产代码，只读用 SkillLoader / SkillVectorAdapter
【变易】FakeModel 可替换为真 BGE-m3（需注释掉注入逻辑）
【简易】单文件脚本，无外部依赖（除项目自身模块）
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import yaml

# 加载项目模块
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.skills_mgmt.file_store import SkillFileStore
from agent.skills_mgmt.loader import SkillLoader
from agent.skills_mgmt.vector_adapter import SkillVectorAdapter


# ═══════════════════════════════════════════════════════════════════
#  FakeSentenceTransformer — 模拟 BGE-m3 语义检索行为
# ═══════════════════════════════════════════════════════════════════

class FakeSentenceTransformer:
    """模拟 SentenceTransformer — 基于关键词 bag-of-words + 同义词映射生成向量

    让包含相同关键词（或同义词）的文本向量相似度高，模拟语义检索行为。
    维度 = 关键词数，每维对应该关键词是否出现在文本中（含同义词映射）。

    【变易】关键词列表 + 同义词映射从配置文件加载（config/synonyms.json），
            支持动态调整无需改代码；加载失败时用默认值兜底
    【简易】bag-of-words + 同义词是最简语义模拟，足够验证检索流程
    """

    # 默认值（配置文件加载失败时兜底）
    DEFAULT_KEYWORDS = ["pdf", "反思", "总结", "记忆", "情绪", "安全", "建议", "上下文"]

    DEFAULT_SYNONYMS = {
        "压缩": ["总结", "记忆"],
        "不高兴": ["情绪"],
        "不合理": ["反思"],
        "危险": ["安全"],
        "读取": ["pdf"],
        "文件": ["pdf"],
        "推荐": ["建议"],
        "裁剪": ["上下文"],
    }

    def __init__(self, dim: int | None = None, config_path: str | None = None):
        """初始化 FakeModel，从配置文件加载关键词和同义词映射

        Args:
            dim: 向量维度（默认=关键词数）
            config_path: 配置文件路径，默认从环境变量 SYNONYMS_CONFIG_PATH 读取
                         守 project_memory 约束：.env 管路径，JSON 管数据
        """
        # 从环境变量读取配置路径（.env 中 SYNONYMS_CONFIG_PATH=config/synonyms.json）
        if config_path is None:
            config_path = os.environ.get("SYNONYMS_CONFIG_PATH", "config/synonyms.json")

        self.keywords, self.synonyms = self._load_config(config_path)
        self._dim = dim or len(self.keywords)

    @classmethod
    def _load_config(cls, config_path: str) -> tuple[list, dict]:
        """从 JSON 配置文件加载关键词和同义词映射

        【不易】加载失败时用默认值兜底，不抛异常（demo 脚本不应因配置缺失崩溃）
        【简易】JSON 格式简单，schema 校验最小化
        """
        try:
            path = Path(config_path)
            # 相对路径相对于项目根目录解析
            if not path.is_absolute():
                project_root = Path(__file__).resolve().parent.parent
                path = project_root / path

            if not path.exists():
                logging.warning(
                    f"synonyms config not found at {path}, using defaults"
                )
                return list(cls.DEFAULT_KEYWORDS), dict(cls.DEFAULT_SYNONYMS)

            with open(path, "r", encoding="utf-8") as f:
                cfg = json.load(f)

            keywords = cfg.get("keywords", cls.DEFAULT_KEYWORDS)
            synonyms = cfg.get("synonyms", cls.DEFAULT_SYNONYMS)

            logging.info(
                f"FakeModel loaded config from {path}: "
                f"{len(keywords)} keywords, {len(synonyms)} synonym entries"
            )
            return keywords, synonyms
        except Exception as e:
            logging.warning(
                f"Failed to load synonyms config from {config_path}: {e}, "
                f"using defaults"
            )
            return list(cls.DEFAULT_KEYWORDS), dict(cls.DEFAULT_SYNONYMS)

    def get_sentence_embedding_dimension(self) -> int:
        return self._dim

    def encode(
        self,
        texts,
        normalize_embeddings: bool = True,
        show_progress_bar: bool = False,
    ) -> np.ndarray:
        """编码文本列表为向量矩阵

        每个文本：命中关键词（或其同义词）的维度=1，否则=0；归一化后单位向量
        全零向量保持全零（不兜底）— 与任何关键词向量正交，避免无关技能误召回
        """
        vectors = []
        for text in texts:
            vec = np.zeros(self._dim, dtype=float)
            text_lower = (text or "").lower()
            for i, kw in enumerate(self.keywords[: self._dim]):
                # 直接关键词命中
                if kw.lower() in text_lower:
                    vec[i] = 1.0
                    continue
                # 同义词映射命中：检查是否有近义词映射到当前关键词
                for syn, targets in self.synonyms.items():
                    if syn.lower() in text_lower and kw in targets:
                        vec[i] = 1.0
                        break
            # 全零向量保持全零（不兜底）— 与任何关键词向量正交，不会误召回
            # 仅在归一化时跳过全零向量（避免 NaN）
            if normalize_embeddings:
                norm = np.linalg.norm(vec)
                if norm > 0:
                    vec = vec / norm
            vectors.append(vec)
        return np.array(vectors)


# ═══════════════════════════════════════════════════════════════════
#  Mock 数据集 — 8 个技能覆盖 8 个关键词域
# ═══════════════════════════════════════════════════════════════════

MOCK_SKILLS = [
    {
        "id": "pdf_parser",
        "name": "PDF文档解析",
        "description": "解析PDF文件内容，提取文本和表格结构",
        "category": "document",
        "tags": ["pdf", "parse"],
        "version": "1.0.0",
        "enabled": True,
    },
    {
        "id": "self_reflection",
        "name": "自我反思",
        # 关键：description 含"反思"，但 query "帮我反思一下" 也含"反思"
        # 更严格的语义测试：query 用"检查我的回答对不对"（不含"反思"字面）
        "description": "主动检查回答合理性，反思并改进输出质量",
        "category": "meta",
        "tags": ["反思", "improve"],
        "version": "1.0.0",
        "enabled": True,
    },
    {
        "id": "memory_summary",
        "name": "记忆总结",
        "description": "总结对话记忆，压缩历史信息减少上下文占用",
        "category": "memory",
        "tags": ["总结", "记忆"],
        "version": "1.0.0",
        "enabled": True,
    },
    {
        "id": "emotion_support",
        "name": "情绪支持",
        "description": "识别用户情绪状态，提供情绪安抚和心理支持建议",
        "category": "emotion",
        "tags": ["情绪", "support"],
        "version": "1.0.0",
        "enabled": True,
    },
    {
        "id": "security_check",
        "name": "安全检查",
        "description": "检查输出内容安全性，过滤敏感信息和有害建议",
        "category": "safety",
        "tags": ["安全", "filter"],
        "version": "1.0.0",
        "enabled": True,
    },
    {
        "id": "suggestion_engine",
        "name": "建议引擎",
        "description": "基于上下文生成个性化建议和推荐方案",
        "category": "advisory",
        "tags": ["建议", "recommend"],
        "version": "1.0.0",
        "enabled": True,
    },
    {
        "id": "context_manager",
        "name": "上下文管理",
        "description": "管理对话上下文窗口，智能裁剪历史消息保持连贯性",
        "category": "context",
        "tags": ["上下文", "window"],
        "version": "1.0.0",
        "enabled": True,
    },
    {
        "id": "code_review",
        "name": "代码审查",
        # 不含任何关键词 — 验证无关技能不会被误召回
        "description": "审查代码质量，检查风格规范和潜在缺陷",
        "category": "engineering",
        "tags": ["code", "review"],
        "version": "1.0.0",
        "enabled": True,
    },
]


# 验证用例：语义相似但字面可能不同的 query
TEST_QUERIES = [
    {
        "query": "帮我反思一下刚才的回答",
        "expected": "self_reflection",
        "reason": "query 含'反思'，description 也含'反思'，向量应高相似度",
    },
    {
        "query": "把这段对话压缩一下节省空间",
        "expected": "memory_summary",
        "reason": "query 含'总结'语义（压缩），description 含'总结''记忆'",
    },
    {
        "query": "这个PDF文件怎么读取内容",
        "expected": "pdf_parser",
        "reason": "query 含'PDF'，description 含'PDF'，向量直接命中",
    },
    {
        "query": "用户好像有点不高兴，怎么处理",
        "expected": "emotion_support",
        "reason": "query 含'情绪'语义（不高兴），description 含'情绪'",
    },
    {
        "query": "检查一下输出有没有安全问题",
        "expected": "security_check",
        "reason": "query 含'安全'，description 含'安全'",
    },
]


# ═══════════════════════════════════════════════════════════════════
#  辅助函数
# ═══════════════════════════════════════════════════════════════════

def write_skill_md(repo: Path, skill: dict) -> None:
    """写入单个 skill.md（YAML front matter + body）"""
    skill_dir = repo / skill["id"]
    skill_dir.mkdir(parents=True, exist_ok=True)
    yaml_block = yaml.safe_dump(
        skill, allow_unicode=True, default_flow_style=False, sort_keys=False,
    ).strip()
    body = f"# {skill['name']}\n\n{skill['description']}技能使用说明。"
    md = f"---\n{yaml_block}\n---\n\n{body}"
    (skill_dir / "skill.md").write_text(md, encoding="utf-8")


def build_demo_loader(tmpdir: Path) -> SkillLoader:
    """构建注入 FakeModel 的 SkillLoader（避免加载真 BGE-m3）"""
    repo = tmpdir / "skills_repo"
    repo.mkdir()
    for skill in MOCK_SKILLS:
        write_skill_md(repo, skill)

    file_store = SkillFileStore(repo_path=str(repo))

    # 创建 adapter 并禁用真模型初始化
    adapter = SkillVectorAdapter(
        file_store=file_store,
        use_sentence_transformers=False,
        use_native_chroma=False,
    )
    # 注入 FakeModel
    fake_model = FakeSentenceTransformer()
    adapter._st_backend = (fake_model, [], [], [])
    adapter._vector_store = (fake_model, [], [], [])

    loader = SkillLoader(file_store=file_store, vector_adapter=adapter)
    return loader


def print_separator(title: str) -> None:
    """打印分隔线"""
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print(f"{'=' * 70}")


def print_match_result(label: str, result, expected: str) -> None:
    """打印单个检索结果"""
    top1 = result.matches[0] if result.matches else None
    hit = top1 and top1.skill_id == expected
    marker = "✓ 命中" if hit else "✗ 未命中"

    print(f"\n  [{label}] {marker}")
    print(f"  retrieval_method = {result.retrieval_method}")
    print(f"  fallback_used    = {result.fallback_used}")
    if top1:
        print(f"  top1: {top1.skill_id} (score={top1.score:.4f})")
        print(f"        name={top1.name}")
    else:
        print(f"  top1: <空>")

    if len(result.matches) > 1:
        print(f"  其余候选:")
        for m in result.matches[1:4]:  # 最多打印 3 个
            print(f"    - {m.skill_id} (score={m.score:.4f})")


# ═══════════════════════════════════════════════════════════════════
#  主流程
# ═══════════════════════════════════════════════════════════════════

def main() -> int:
    """端到端 Demo：对比 TF-IDF vs 向量 vs RRF 融合 + 冲突场景验证"""
    # 配置日志 — INFO 级别，让 vector_adapter/loader 的日志可见
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stdout,
    )

    print_separator("向量检索端到端 Demo — TF-IDF vs 向量 vs RRF 融合")
    print(f"  Mock 数据集: {len(MOCK_SKILLS)} 个技能")
    print(f"  验证用例: {len(TEST_QUERIES)} 个语义相似 query")
    print(f"  FakeModel: 从 config/synonyms.json 加载关键词和同义词映射")

    with tempfile.TemporaryDirectory() as tmpdir_str:
        tmpdir = Path(tmpdir_str)
        loader = build_demo_loader(tmpdir)

        # 先构建向量索引（让后续检索走向量路径）
        adapter = loader._vector_adapter
        print(f"\n  构建向量索引...")
        count = adapter.ensure_indexed()
        print(f"  已索引 {count} 个技能")

        # ──────────────────────────────────────────────
        #  Part 1: 对比三种检索模式
        # ──────────────────────────────────────────────
        results_summary = []  # (query, expected, tfidf_hit, vector_hit, rrf_hit)

        for tc in TEST_QUERIES:
            query = tc["query"]
            expected = tc["expected"]

            print_separator(f"Query: '{query}'")
            print(f"  期望命中: {expected}")
            print(f"  原因: {tc['reason']}")

            # 1. TF-IDF 检索
            result_tfidf = loader.match(
                query, top_k=3, use_vector=False,
            )
            print_match_result("TF-IDF", result_tfidf, expected)
            tfidf_hit = (
                result_tfidf.matches
                and result_tfidf.matches[0].skill_id == expected
            )

            # 2. 向量检索
            result_vector = loader.match(
                query, top_k=3, use_vector=True,
            )
            print_match_result("向量", result_vector, expected)
            vector_hit = (
                result_vector.matches
                and result_vector.matches[0].skill_id == expected
            )

            # 3. RRF 融合检索
            result_rrf = loader.match(
                query, top_k=3, use_vector=True, fusion_mode="rrf",
            )
            print_match_result("RRF 融合", result_rrf, expected)
            rrf_hit = (
                result_rrf.matches
                and result_rrf.matches[0].skill_id == expected
            )

            results_summary.append((query, expected, tfidf_hit, vector_hit, rrf_hit))

        # ──────────────────────────────────────────────
        #  Part 1 汇总
        # ──────────────────────────────────────────────
        print_separator("Part 1 汇总：语义相似 query 命中率")
        header = f"  {'Query':<30} {'期望':<20} {'TF-IDF':<10} {'向量':<10} {'RRF':<10}"
        print(header)
        print(f"  {'-' * 80}")
        for query, expected, tfidf_hit, vector_hit, rrf_hit in results_summary:
            query_short = query[:28] if len(query) > 28 else query
            print(
                f"  {query_short:<30} {expected:<20} "
                f"{'✓' if tfidf_hit else '✗':<10} "
                f"{'✓' if vector_hit else '✗':<10} "
                f"{'✓' if rrf_hit else '✗':<10}"
            )

        tfidf_count = sum(1 for r in results_summary if r[2])
        vector_count = sum(1 for r in results_summary if r[3])
        rrf_count = sum(1 for r in results_summary if r[4])
        total = len(results_summary)
        print(f"\n  命中率: TF-IDF {tfidf_count}/{total} | 向量 {vector_count}/{total} | RRF {rrf_count}/{total}")

        # ──────────────────────────────────────────────
        #  Part 2: RRF 冲突场景验证
        # ──────────────────────────────────────────────
        conflict_demo(loader)

        # ──────────────────────────────────────────────
        #  结论
        # ──────────────────────────────────────────────
        print_separator("结论")
        print(f"  Part 1 (语义召回): TF-IDF {tfidf_count}/{total} | 向量 {vector_count}/{total} | RRF {rrf_count}/{total}")
        print(f"  Part 2 (冲突融合): 见上方 RRF 冲突验证详情")
        print(f"\n  日志排查要点:")
        print(f"    - rrf.paths_before_fuse: 两路原始 top3 + top1_conflict 标志")
        print(f"    - rrf.fused_detail: 融合后 top5 的 tfidf_rank/vector_rank/rrf_score")
        print(f"    - match.layer1.rrf.ok: 最终 top_k 的 skill_id + score")
        print(f"    - st_backend.sims_computed: 向量路 top1_skill_id + 相似度")

    return 0


def conflict_demo(loader: SkillLoader) -> None:
    """RRF 冲突验证 — TF-IDF 与向量 top1 分歧时的融合排序

    构造冲突场景:
    - query 含 A 的字面词（TF-IDF 字面匹配 A 为 top1）
    - query 含 B 的同义词（向量同义词映射 B 为 top1）
    - A ≠ B，两路 top1 分歧
    - RRF 融合后，两路都命中的技能分数累加，排名靠前

    验证用例:
    - query="PDF文件解析压缩"
      - TF-IDF top1 = pdf_parser（'pdf''文''件''解''析'5字面命中 > '文''压''缩'3字面）
      - 向量 top1 = memory_summary（同义词'压缩'→'总结''记忆'2维 > 'pdf'1维，向量相似度 0.816 > 0.577）
      - 两路 top1 分歧，RRF 融合后两路都命中的技能分数累加排名靠前

    设计要点（【不易】第一性原理）:
      原 query "PDF文件压缩" 失效根因：pdf_parser 与 memory_summary TF-IDF 均命中 3/5=0.6 打平。
      memory_summary description 含"上下文"→"文"被误匹配，含"压缩"→直接命中。
      加入"解析"（pdf_parser 独有字面词）打破平局，使 TF-IDF 明确偏向 pdf_parser。
    """
    print_separator("Part 2: RRF 冲突验证 — TF-IDF vs 向量 top1 分歧")

    conflict_queries = [
        {
            "query": "PDF文件解析压缩",
            "tfidf_expected_top1": "pdf_parser",
            "vector_expected_top1": "memory_summary",
            "reason": (
                "TF-IDF 字面匹配 pdf_parser（'pdf''文''件''解''析'5字 > '文''压''缩'3字），"
                "向量同义词映射 memory_summary（'压缩'→'总结''记忆'2维 > 'pdf'1维），"
                "两路 top1 分歧，RRF 融合后两路都命中的技能分数累加"
            ),
        },
    ]

    for tc in conflict_queries:
        query = tc["query"]
        print(f"\n  Query: '{query}'")
        print(f"  TF-IDF 期望 top1: {tc['tfidf_expected_top1']}")
        print(f"  向量期望 top1:   {tc['vector_expected_top1']}")
        print(f"  原因: {tc['reason']}")

        # 跑三种检索
        result_tfidf = loader.match(query, top_k=5, use_vector=False)
        result_vector = loader.match(query, top_k=5, use_vector=True)
        result_rrf = loader.match(query, top_k=5, use_vector=True, fusion_mode="rrf")

        tfidf_top1 = result_tfidf.matches[0].skill_id if result_tfidf.matches else None
        vector_top1 = result_vector.matches[0].skill_id if result_vector.matches else None
        rrf_top1 = result_rrf.matches[0].skill_id if result_rrf.matches else None

        conflict = tfidf_top1 != vector_top1
        conflict_marker = "✓ 检测到冲突" if conflict else "✗ 无冲突（两路 top1 相同）"
        print(f"\n  {conflict_marker}")
        print(f"    TF-IDF top1: {tfidf_top1}")
        print(f"    向量 top1:   {vector_top1}")

        if not conflict:
            print(f"    （跳过 RRF 融合分析 — 无冲突）")
            continue

        # 打印 RRF 融合详情
        print(f"\n  RRF 融合结果:")
        print(f"    RRF top1: {rrf_top1}")
        if result_rrf.matches:
            print(f"\n    {'Rank':<6} {'Skill ID':<20} {'RRF Score':<12} {'TF-IDF Rank':<14} {'Vector Rank':<14} {'两路命中'}")
            print(f"    {'-' * 80}")
            for i, m in enumerate(result_rrf.matches[:5], start=1):
                bd = m.score_breakdown or {}
                tfidf_rank = bd.get("tfidf_rank")
                vector_rank = bd.get("vector_rank")
                both = "✓ 是" if (tfidf_rank is not None and vector_rank is not None) else "✗ 单路"
                print(
                    f"    {i:<6} {m.skill_id:<20} {m.score:<12.4f} "
                    f"{str(tfidf_rank):<14} {str(vector_rank):<14} {both}"
                )

            # 分析 RRF 融合效果
            rrf_top1_match = result_rrf.matches[0]
            rrf_top1_bd = rrf_top1_match.score_breakdown or {}
            rrf_top1_both = (
                rrf_top1_bd.get("tfidf_rank") is not None
                and rrf_top1_bd.get("vector_rank") is not None
            )
            print(f"\n  RRF 融合分析:")
            print(f"    RRF top1 = {rrf_top1}（{'两路都命中，分数累加' if rrf_top1_both else '单路命中'}）")
            if rrf_top1_both:
                print(f"    ✓ RRF 正确提升了两路都命中的技能（{rrf_top1}）")
                print(f"      tfidf_rank={rrf_top1_bd.get('tfidf_rank')}, "
                      f"vector_rank={rrf_top1_bd.get('vector_rank')}, "
                      f"rrf_score={rrf_top1_bd.get('rrf_score')}")
            else:
                print(f"    注意: RRF top1 是单路命中，可能因另一路 rank 较低")

            # 检查冲突的两个技能是否都在 RRF top5 中
            rrf_top5_ids = [m.skill_id for m in result_rrf.matches[:5]]
            conflict_skills = {tc["tfidf_expected_top1"], tc["vector_expected_top1"]}
            both_in_top5 = conflict_skills.issubset(set(rrf_top5_ids))
            print(f"\n    冲突双方都在 RRF top5: {'✓ 是' if both_in_top5 else '✗ 否'}")
            if both_in_top5:
                print(f"    ✓ RRF 融合保留了 TF-IDF 和向量的各自 top1，未丢失召回")


if __name__ == "__main__":
    sys.exit(main())
