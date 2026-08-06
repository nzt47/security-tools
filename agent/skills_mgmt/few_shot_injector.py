"""Dynamic Few-shot 注入器 — 替代 SFT 微调

背景:
    架构要求"高频核心技能积累高质量 SFT 数据后做专项微调"，
    但个人 Agent 无训练数据与算力，SFT 不现实。
    改用 Dynamic Few-shot：为每个技能维护成功案例库（JSONL），
    运行时用 TF-IDF 检索最匹配的 1-2 个示例注入 prompt，实现 in-context learning。

设计:
    - 每个技能维护示例库（data/skill_few_shot/<skill_id>.jsonl，每行一个示例）
    - 运行时用 TF-IDF 余弦相似度检索最匹配的 1-2 个示例
    - 仅注入 rating>=4 的高质量示例
    - 无示例 / 无高置信匹配 / 示例数 < 3 时不注入（宁缺毋滥）

不变量(不易):
    - 不做模型微调
    - 示例库文件缺失、某行 JSON 解析失败、注入失败均不影响主流程
"""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from .loader import estimate_tokens, _tokenize
from .observability import logger, emit_metric


# ════════════════════════════════════════════════════════════
#  数据模型
# ════════════════════════════════════════════════════════════

@dataclass
class FewShotExample:
    """一条 Few-shot 示例（成功案例）

    【不变】字段与 data/skill_few_shot/<skill_id>.jsonl 每行 JSON 对齐：
        {"example_id", "intent", "input", "output", "rating", "tags", "created_at"}
    """

    example_id: str
    intent: str
    input: str
    output: str
    rating: int = 5
    tags: List[str] = field(default_factory=list)
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FewShotExample | None":
        """从字典构造；必填字段缺失或类型非法时返回 None（由调用方跳过该行）"""
        try:
            example_id = str(data.get("example_id", "")).strip()
            intent = str(data.get("intent", "")).strip()
            input_ = str(data.get("input", "")).strip()
            output = str(data.get("output", "")).strip()
            if not example_id or not intent or not input_ or not output:
                return None
            rating = int(data.get("rating", 5))
            tags = list(data.get("tags") or [])
            created_at = str(data.get("created_at", "") or "")
            return cls(
                example_id=example_id,
                intent=intent,
                input=input_,
                output=output,
                rating=rating,
                tags=tags,
                created_at=created_at,
            )
        except (TypeError, ValueError):
            return None


# ════════════════════════════════════════════════════════════
#  TF-IDF 轻量实现（无第三方依赖）
# ════════════════════════════════════════════════════════════

def _term_counts(tokens: List[str]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for t in tokens:
        counts[t] = counts.get(t, 0) + 1
    return counts


def _compute_idf(docs_tokens: List[List[str]]) -> Dict[str, float]:
    """TF-IDF 的 IDF 部分 — 在示例语料上计算（平滑版）

    idf(t) = ln((1+N)/(1+df(t))) + 1，df(t) = 包含词 t 的文档数
    """
    n = len(docs_tokens)
    df: Dict[str, int] = {}
    for tokens in docs_tokens:
        for t in set(tokens):
            df[t] = df.get(t, 0) + 1
    return {t: math.log((1 + n) / (1 + df[t])) + 1 for t in df}


def _cosine_tfidf(query_tokens: List[str], doc_tokens: List[str],
                  idf: Dict[str, float]) -> float:
    """查询与文档的 TF-IDF 加权余弦相似度（量纲 [0,1]）"""
    if not query_tokens or not doc_tokens:
        return 0.0
    q_count = _term_counts(query_tokens)
    d_count = _term_counts(doc_tokens)
    dot = 0.0
    q_norm = 0.0
    d_norm = 0.0
    for term in set(q_count) | set(d_count):
        w = idf.get(term, 0.0)
        qw = q_count.get(term, 0.0) * w
        dw = d_count.get(term, 0.0) * w
        dot += qw * dw
        q_norm += qw * qw
        d_norm += dw * dw
    if q_norm == 0.0 or d_norm == 0.0:
        return 0.0
    return dot / (math.sqrt(q_norm) * math.sqrt(d_norm))


# ════════════════════════════════════════════════════════════
#  注入器
# ════════════════════════════════════════════════════════════

# 默认示例库根目录（相对项目根，与 file_store 的 data/ 约定一致）
_DEFAULT_FEW_SHOT_DIR = "data/skill_few_shot"


class FewShotInjector:
    """Dynamic Few-shot 注入器 — 替代 SFT 微调

    设计:
        - 每个技能维护示例库（JSONL）
        - 运行时用 TF-IDF 检索最匹配的 1-2 个示例
        - 仅注入 rating>=4 的高质量示例
        - 无示例或无高置信匹配时不注入（宁缺毋滥）
    """

    def __init__(self, few_shot_dir: str = _DEFAULT_FEW_SHOT_DIR):
        self.dir = Path(few_shot_dir)
        # 相对路径锚定到项目根（agent/ 的父目录），与 file_store 的默认 data/ 约定一致
        if not self.dir.is_absolute():
            self.dir = Path(__file__).parent.parent.parent / self.dir

    def _path_for(self, skill_id: str) -> Path:
        """示例库文件路径 — 防御非法 skill_id（防路径穿越）"""
        safe_id = re.sub(r"[^A-Za-z0-9_.\-]", "_", skill_id)
        return self.dir / f"{safe_id}.jsonl"

    # ──────────────────────────────────────────────
    #  加载
    # ──────────────────────────────────────────────

    def load_examples(self, skill_id: str) -> List[FewShotExample]:
        """加载技能的示例库

        【防御】文件不存在 → 返回空列表（不报错）
        【防御】某行 JSON 解析失败 / 字段非法 → 跳过该行（不阻塞）
        """
        path = self._path_for(skill_id)
        if not path.exists():
            return []
        examples: List[FewShotExample] = []
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError) as e:
            logger.warning(json.dumps({
                "module_name": "few_shot_injector",
                "action": "load_examples.read_failed",
                "skill_id": skill_id,
                "error": str(e),
            }, ensure_ascii=False))
            return []
        for line_no, line in enumerate(lines, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                logger.warning(json.dumps({
                    "module_name": "few_shot_injector",
                    "action": "load_examples.bad_line",
                    "skill_id": skill_id,
                    "line_no": line_no,
                    "reason": "invalid_json",
                }, ensure_ascii=False))
                continue
            example = FewShotExample.from_dict(data)
            if example is None:
                logger.warning(json.dumps({
                    "module_name": "few_shot_injector",
                    "action": "load_examples.bad_line",
                    "skill_id": skill_id,
                    "line_no": line_no,
                    "reason": "missing_required_field",
                }, ensure_ascii=False))
                continue
            examples.append(example)
        return examples

    # ──────────────────────────────────────────────
    #  选择
    # ──────────────────────────────────────────────

    def select_examples(self, skill_id: str, intent: str,
                        top_k: int = 2,
                        min_rating: int = 4,
                        min_score: float = 0.3) -> List[FewShotExample]:
        """选择与当前 intent 最匹配的示例

        - 仅考虑 rating >= min_rating 的高质量示例
        - 对示例 intent 做 TF-IDF 余弦相似度打分
        - score < min_score 的示例不入选（宁缺毋滥）
        - 返回按分数降序的前 top_k 个

        排查辅助（Why）: 每个阶段输出结构化日志——
            start(入参+加载数) → rating_filter(过滤结果) → score(逐示例分数)
            → done(选中列表)。"选不到示例"时可据此定位是加载、过滤还是分数不达标。
        """
        tid = uuid.uuid4().hex[:16]

        all_examples = self.load_examples(skill_id)
        logger.info(json.dumps({
            "trace_id": tid,
            "module_name": "few_shot_injector",
            "action": "select_examples.start",
            "skill_id": skill_id,
            "intent": intent[:100],
            "top_k": top_k,
            "min_rating": min_rating,
            "min_score": min_score,
            "loaded_count": len(all_examples),
        }, ensure_ascii=False))

        examples = [ex for ex in all_examples if ex.rating >= min_rating]
        logger.info(json.dumps({
            "trace_id": tid,
            "module_name": "few_shot_injector",
            "action": "select_examples.rating_filter",
            "skill_id": skill_id,
            "after_rating_filter": len(examples),
            "dropped_low_rating": len(all_examples) - len(examples),
        }, ensure_ascii=False))
        if not examples:
            logger.info(json.dumps({
                "trace_id": tid,
                "module_name": "few_shot_injector",
                "action": "select_examples.done",
                "skill_id": skill_id,
                "reason": "no_qualified_examples",
                "selected": [],
            }, ensure_ascii=False))
            return []

        query_tokens = _tokenize(intent)
        if not query_tokens:
            logger.info(json.dumps({
                "trace_id": tid,
                "module_name": "few_shot_injector",
                "action": "select_examples.done",
                "skill_id": skill_id,
                "reason": "empty_query_tokens",
                "intent": intent[:100],
                "selected": [],
            }, ensure_ascii=False))
            return []

        docs_tokens = [_tokenize(ex.intent) for ex in examples]
        idf = _compute_idf(docs_tokens)

        scored: List[tuple] = []
        for ex, doc_tokens in zip(examples, docs_tokens):
            score = _cosine_tfidf(query_tokens, doc_tokens, idf)
            passed = score >= min_score
            logger.info(json.dumps({
                "trace_id": tid,
                "module_name": "few_shot_injector",
                "action": "select_examples.score",
                "skill_id": skill_id,
                "example_id": ex.example_id,
                "example_intent": ex.intent[:100],
                "query_tokens": query_tokens,
                "doc_tokens": doc_tokens,
                "score": round(score, 4),
                "min_score": min_score,
                "passed": passed,
            }, ensure_ascii=False))
            if passed:
                scored.append((score, ex))

        scored.sort(key=lambda item: item[0], reverse=True)
        selected = [ex for _, ex in scored[:top_k]]
        logger.info(json.dumps({
            "trace_id": tid,
            "module_name": "few_shot_injector",
            "action": "select_examples.done",
            "skill_id": skill_id,
            "intent": intent[:100],
            "candidate_count": len(scored),
            "selected": [ex.example_id for ex in selected],
            "selected_count": len(selected),
            "top_score": round(scored[0][0], 4) if scored else None,
            "reason": "ok" if selected else "no_score_above_min_score",
        }, ensure_ascii=False))
        return selected

    # ──────────────────────────────────────────────
    #  注入
    # ──────────────────────────────────────────────

    def inject(self, skill_id: str, intent: str,
               max_tokens: int = 500) -> Dict[str, Any]:
        """注入 Few-shot 示例到上下文

        【防御】示例库缺失 / 解析失败 / 数据量不足 / 无高置信匹配 → 不注入
        Returns: {has_examples, prompt, estimated_tokens, examples, layer,
                  skill_id, intent}
        """
        t0 = time.time()
        tid = uuid.uuid4().hex[:16]

        empty = {
            "has_examples": False,
            "prompt": "",
            "estimated_tokens": 0,
            "examples": [],
            "layer": "2.5",
            "skill_id": skill_id,
            "intent": intent,
        }

        try:
            examples = self.load_examples(skill_id)
        except Exception as e:  # noqa: BLE001 注入失败不影响主流程
            logger.warning(json.dumps({
                "trace_id": tid,
                "module_name": "few_shot_injector",
                "action": "inject.skipped",
                "skill_id": skill_id,
                "reason": "load_failed",
                "error": str(e),
            }, ensure_ascii=False))
            return empty

        # 数据量不足：示例数 < 3 时不注入（宁缺毋滥）
        if len(examples) < 3:
            logger.info(json.dumps({
                "trace_id": tid,
                "module_name": "few_shot_injector",
                "action": "inject.skipped",
                "skill_id": skill_id,
                "reason": "insufficient_examples",
                "example_count": len(examples),
            }, ensure_ascii=False))
            return empty

        selected = self.select_examples(skill_id, intent)
        if not selected:
            logger.info(json.dumps({
                "trace_id": tid,
                "module_name": "few_shot_injector",
                "action": "inject.skipped",
                "skill_id": skill_id,
                "reason": "no_high_confidence_match",
            }, ensure_ascii=False))
            return empty

        # token 预算内贪心选取（单示例不截断）
        header = (
            f"## 技能示例（Few-shot）：{skill_id}\n\n"
            "以下为与该需求最相似的历史成功案例，请参考其处理方式"
            "（仅参考方法，禁止照抄输出）："
        )
        lines = [header]
        used = 0
        included: List[FewShotExample] = []
        for idx, ex in enumerate(selected, start=1):
            block = (
                f"\n\n### 示例 {idx}\n"
                f"- 场景: {ex.intent}\n"
                f"- 输入: {ex.input}\n"
                f"- 输出: {ex.output}"
            )
            block_tokens = estimate_tokens(block)
            if used + block_tokens > max_tokens:
                continue
            lines.append(block)
            used += block_tokens
            included.append(ex)

        if not included:
            logger.info(json.dumps({
                "trace_id": tid,
                "module_name": "few_shot_injector",
                "action": "inject.skipped",
                "skill_id": skill_id,
                "reason": "budget_too_small",
                "max_tokens": max_tokens,
            }, ensure_ascii=False))
            return empty

        prompt = "\n".join(lines)
        est_tokens = estimate_tokens(prompt)

        elapsed = (time.time() - t0) * 1000
        logger.info(json.dumps({
            "trace_id": tid,
            "module_name": "few_shot_injector",
            "action": "inject.ok",
            "duration_ms": round(elapsed, 2),
            "layer": "2.5",
            "skill_id": skill_id,
            "intent": intent[:100],
            "injected_count": len(included),
            "estimated_tokens": est_tokens,
            "budget": max_tokens,
        }, ensure_ascii=False))

        emit_metric("yunshu_skill_fewshot_inject_tokens",
                    value=est_tokens, kind="histogram",
                    labels={"skill_id": skill_id})

        return {
            "has_examples": True,
            "prompt": prompt,
            "estimated_tokens": est_tokens,
            "examples": [ex.to_dict() for ex in included],
            "layer": "2.5",
            "skill_id": skill_id,
            "intent": intent,
        }

    # ──────────────────────────────────────────────
    #  采集
    # ──────────────────────────────────────────────

    def add_example(self, skill_id: str, example: FewShotExample) -> bool:
        """新增示例（用户反馈 rating=5 时自动采集），带去重

        【去重】example_id 或 (intent, input) 已存在 → 跳过不追加
        Returns: True=已写入；False=重复被跳过
        """
        existing = self.load_examples(skill_id)
        seen_ids = {ex.example_id for ex in existing}
        seen_pairs = {(ex.intent, ex.input) for ex in existing}
        if example.example_id in seen_ids or \
                (example.intent, example.input) in seen_pairs:
            logger.info(json.dumps({
                "module_name": "few_shot_injector",
                "action": "add_example.duplicated",
                "skill_id": skill_id,
                "example_id": example.example_id,
                "reason": "duplicate_example",
            }, ensure_ascii=False))
            return False

        path = self._path_for(skill_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(example.to_dict(), ensure_ascii=False) + "\n")

        logger.info(json.dumps({
            "module_name": "few_shot_injector",
            "action": "add_example.ok",
            "skill_id": skill_id,
            "example_id": example.example_id,
            "rating": example.rating,
        }, ensure_ascii=False))
        return True
