"""三层分层检索引擎 — Agent Skill 核心机制

文章描述的三层架构:
    第一层（元数据层）: 所有技能基础信息统一存放在 skill.md 的 front matter，
        单条约 100 TOKEN，几乎不占资源。用于快速匹配用户意图。
    第二层（使用说明层）: 匹配到技能后，才读取 skill.md 的完整 body（操作步骤），
        实现按需加载，而非全程占用上下文。
    第三层（工具资源层）: 技能 scripts/ 目录下的 Python 脚本。执行任务时，
        代码不在对话中传输，由后台直接运行，只将结果传给模型。

本模块实现:
    - match(intent, top_k): 第一层匹配 — 基于元数据索引，返回候选技能（不加载 body）
    - load_instruction(skill_id): 第二层 — 按需加载使用说明
    - get_script_paths(skill_id): 第三层 — 按需获取脚本路径
    - estimate_tokens(text): token 估算（用于预算管理）
    - get_layer_summary(): 三层架构统计信息

设计原则:
    - 按需加载: 只在需要时加载对应层的数据，大幅节省上下文
    - 可观测: 每层加载输出结构化日志（trace_id, module_name, action, duration_ms, layer, tokens）
    - 边界显性化: 匹配失败/技能不存在 → 抛出带业务码的 Error
    - token 预算: match() 返回预估 token 数，调用方可据此决定加载策略
"""

from __future__ import annotations

import json
import math
import re
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

from .file_store import SkillFileStore
from .observability import logger, emit_metric, traced_action
from .exceptions import SkillNotFoundError, SkillMgmtError


def _trace_id() -> str:
    return uuid.uuid4().hex[:16]


# ════════════════════════════════════════════════════════════
#  Token 估算
# ════════════════════════════════════════════════════════════

# 经验值：中文约 1.5 字符/token，英文约 4 字符/token
# 使用简化估算：中文按 1.5，英文按 4
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")


def estimate_tokens(text: str) -> int:
    """估算文本的 token 数量（粗略，无需第三方依赖）

    中文: 约 1.5 字符/token
    英文: 约 4 字符/token
    """
    if not text:
        return 0
    cjk_chars = len(_CJK_RE.findall(text))
    other_chars = len(text) - cjk_chars
    # 中文部分 + 英文部分
    tokens = math.ceil(cjk_chars / 1.5) + math.ceil(other_chars / 4)
    return tokens


def _meta_to_meta_text(meta: Dict[str, Any]) -> str:
    """将元数据字典转为用于匹配的文本（第一层）"""
    parts = [
        meta.get("name", ""),
        meta.get("description", ""),
        " ".join(meta.get("tags", []) or []),
        meta.get("category", ""),
    ]
    return " ".join(p for p in parts if p)


# ════════════════════════════════════════════════════════════
#  分词与匹配（第一层）
# ════════════════════════════════════════════════════════════

_WORD_RE = re.compile(r"[a-zA-Z0-9_]+|[\u4e00-\u9fff]")


def _tokenize(text: str) -> List[str]:
    """混合分词：英文按词，中文按字"""
    return _WORD_RE.findall((text or "").lower())


def _match_score(meta_text: str, query_tokens: List[str]) -> float:
    """计算查询与元数据文本的匹配分"""
    if not query_tokens:
        return 0.0
    meta_tokens = _tokenize(meta_text)
    if not meta_tokens:
        return 0.0
    hits = sum(1 for t in query_tokens if t in meta_tokens)
    return hits / len(query_tokens)  # 命中率


# ════════════════════════════════════════════════════════════
#  匹配结果数据模型
# ════════════════════════════════════════════════════════════

class SkillMatch:
    """单个技能匹配结果"""

    def __init__(self, skill_id: str, name: str, description: str,
                 score: float, estimated_tokens: int,
                 category: str = "", tags: Optional[List[str]] = None,
                 version: str = "", enabled: bool = True,
                 # 以下为预留扩展字段，向后兼容（默认 None 不影响现有调用）
                 score_breakdown: Optional[Dict[str, Any]] = None):
        self.skill_id = skill_id
        self.name = name
        self.description = description
        self.score = round(score, 4)
        self.estimated_tokens = estimated_tokens
        self.category = category
        self.tags = tags or []
        self.version = version
        self.enabled = enabled
        # 预留：未来多路检索（tfidf/vector/bm25）的分项得分
        # 示例: {"tfidf": 0.8, "vector": 0.9}，当前 TF-IDF 不填充
        self.score_breakdown = score_breakdown

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "score": self.score,
            "estimated_tokens": self.estimated_tokens,
            "category": self.category,
            "tags": self.tags,
            "version": self.version,
            "enabled": self.enabled,
            "score_breakdown": self.score_breakdown,
        }


class MatchResult:
    """匹配结果集合"""

    def __init__(self, matches: List[SkillMatch], total_scanned: int,
                 elapsed_ms: float, estimated_total_tokens: int,
                 # 以下为预留扩展字段，均为关键字参数且有默认值，向后兼容
                 *, retrieval_method: str = "tfidf",
                 score_breakdown: Optional[Dict[str, List[float]]] = None,
                 reranked: bool = False,
                 fallback_used: bool = False):
        self.matches = matches
        self.total_scanned = total_scanned
        self.elapsed_ms = round(elapsed_ms, 2)
        self.estimated_total_tokens = estimated_total_tokens
        # 预留扩展：检索方法标识 tfidf | vector | bm25 | fused
        self.retrieval_method = retrieval_method
        # 预留扩展：分路得分汇总 {"tfidf": [...], "vector": [...]}
        self.score_breakdown = score_breakdown
        # 预留扩展：是否经过 Reranker 二次排序
        self.reranked = reranked
        # 预留扩展：是否降级（向量检索失败回退 TF-IDF 时为 True）
        self.fallback_used = fallback_used

    def to_dict(self) -> Dict[str, Any]:
        return {
            "matches": [m.to_dict() for m in self.matches],
            "total_scanned": self.total_scanned,
            "match_count": len(self.matches),
            "elapsed_ms": self.elapsed_ms,
            "estimated_total_tokens": self.estimated_total_tokens,
            "layer": 1,
            "retrieval_method": self.retrieval_method,
            "score_breakdown": self.score_breakdown,
            "reranked": self.reranked,
            "fallback_used": self.fallback_used,
        }


# ════════════════════════════════════════════════════════════
#  三层检索引擎
# ════════════════════════════════════════════════════════════

class SkillLoader:
    """三层分层检索引擎

    核心理念:
        - 第一层匹配只读元数据（~100 token/技能），不加载完整内容
        - 第二层只在匹配后按需加载使用说明
        - 第三层只在执行时按需加载脚本

    用法:
        loader = SkillLoader()
        result = loader.match("帮我解析PDF文件")  # 第一层
        for m in result.matches:
            instruction = loader.load_instruction(m.skill_id)  # 第二层
            scripts = loader.list_scripts(m.skill_id)  # 第三层
    """

    def __init__(self, file_store: Optional[SkillFileStore] = None):
        self.fs = file_store or SkillFileStore()

    # ──────────────────────────────────────────────
    #  第一层：元数据匹配
    # ──────────────────────────────────────────────

    def match(self, intent: str, *, top_k: int = 5,
              enabled_only: bool = True,
              min_score: float = 0.01,
              # 以下为预留扩展点，当前不实现，仅占位（默认 False 保证向后兼容）
              use_vector: bool = False,
              use_bm25: bool = False,
              use_reranker: bool = False,
              retrieval_weights: Optional[Dict[str, float]] = None,
              ) -> MatchResult:
        """第一层匹配 — 当前仅 TF-IDF，接口已预留向量/BM25/Reranker 扩展点

        只读取 skill.md 的 front matter（约 100 token/技能），
        不加载 body 或脚本，大幅节省上下文成本。

        Args:
            intent: 用户意图文本（如"帮我解析PDF文件"）
            top_k: 返回前 K 个匹配
            enabled_only: 是否只匹配启用状态的技能
            min_score: 最低匹配分阈值
            use_vector: 预留扩展：未来启用向量检索（当前忽略，记录 warning）
            use_bm25: 预留扩展：未来启用 BM25（当前忽略，记录 warning）
            use_reranker: 预留扩展：未来启用 Reranker（当前忽略，记录 warning）
            retrieval_weights: 预留扩展：未来多路融合（RRF）权重（当前忽略）

        Returns: MatchResult
        """
        t0 = time.time()
        tid = _trace_id()

        # 扩展点防御：请求未实现的检索方式时记录 warning 并降级 TF-IDF
        fallback_used = False
        if use_vector or use_bm25 or use_reranker:
            logger.warning(json.dumps({
                "trace_id": tid,
                "module_name": "loader",
                "action": "match.extension_not_implemented",
                "intent": intent[:100],
                "use_vector": use_vector,
                "use_bm25": use_bm25,
                "use_reranker": use_reranker,
                "retrieval_weights": retrieval_weights,
                "fallback": "tfidf",
            }, ensure_ascii=False))
            fallback_used = True

        # 加载元数据索引（第一层，只读 front matter）
        index = self.fs.load_metadata_index()
        query_tokens = _tokenize(intent)

        candidates: List[SkillMatch] = []
        for skill_id, meta in index.items():
            # 过滤禁用技能
            if enabled_only and not meta.get("enabled", True):
                continue

            # 计算匹配分
            meta_text = _meta_to_meta_text(meta)
            score = _match_score(meta_text, query_tokens)

            if score < min_score:
                continue

            # 估算元数据 token 数（第一层成本）
            meta_str = json.dumps(meta, ensure_ascii=False)
            est_tokens = estimate_tokens(meta_str)

            candidates.append(SkillMatch(
                skill_id=skill_id,
                name=meta.get("name", skill_id),
                description=meta.get("description", ""),
                score=score,
                estimated_tokens=est_tokens,
                category=meta.get("category", ""),
                tags=meta.get("tags", []),
                version=meta.get("version", ""),
                enabled=meta.get("enabled", True),
            ))

        # 按匹配分降序排列
        candidates.sort(key=lambda m: m.score, reverse=True)
        top = candidates[:top_k]

        elapsed = (time.time() - t0) * 1000
        total_tokens = sum(m.estimated_tokens for m in top)

        logger.info(json.dumps({
            "trace_id": tid,
            "module_name": "loader",
            "action": "match.layer1.ok",
            "duration_ms": round(elapsed, 2),
            "layer": 1,
            "intent": intent[:100],
            "total_scanned": len(index),
            "match_count": len(top),
            "estimated_tokens": total_tokens,
            "retrieval_method": "tfidf",
            "fallback_used": fallback_used,
        }, ensure_ascii=False))

        emit_metric("yunshu_skill_match_latency_ms",
                    value=elapsed, kind="histogram",
                    labels={"layer": "1", "success": "true"})
        emit_metric("yunshu_skill_match_count",
                    value=len(top), kind="gauge",
                    labels={"layer": "1"})

        return MatchResult(
            matches=top,
            total_scanned=len(index),
            elapsed_ms=elapsed,
            estimated_total_tokens=total_tokens,
            retrieval_method="tfidf",
            fallback_used=fallback_used,
        )

    def list_all_metadata(self, *, enabled_only: bool = False) -> List[Dict[str, Any]]:
        """列出所有技能的元数据（第一层，不加载 body）

        用于 UI 展示技能列表，只读 front matter。
        """
        index = self.fs.load_metadata_index(refresh=True)
        result = []
        for skill_id, meta in index.items():
            if enabled_only and not meta.get("enabled", True):
                continue
            meta["skill_id"] = skill_id
            meta["scripts"] = self.fs.list_scripts(skill_id)
            result.append(meta)
        return result

    # ──────────────────────────────────────────────
    #  第二层：按需加载使用说明
    # ──────────────────────────────────────────────

    def load_instruction(self, skill_id: str) -> Dict[str, Any]:
        """第二层 — 按需加载技能的完整使用说明

        只在第一层匹配到技能后才调用。
        Returns: {skill_id, instruction, estimated_tokens, layer}
        """
        t0 = time.time()
        tid = _trace_id()

        body = self.fs.load_instruction(skill_id)
        est_tokens = estimate_tokens(body)

        elapsed = (time.time() - t0) * 1000
        logger.info(json.dumps({
            "trace_id": tid,
            "module_name": "loader",
            "action": "load_instruction.layer2.ok",
            "duration_ms": round(elapsed, 2),
            "layer": 2,
            "skill_id": skill_id,
            "instruction_chars": len(body),
            "estimated_tokens": est_tokens,
        }, ensure_ascii=False))

        emit_metric("yunshu_skill_instruction_tokens",
                    value=est_tokens, kind="histogram",
                    labels={"skill_id": skill_id, "layer": "2"})

        return {
            "skill_id": skill_id,
            "instruction": body,
            "estimated_tokens": est_tokens,
            "instruction_chars": len(body),
            "layer": 2,
        }

    # ──────────────────────────────────────────────
    #  第三层：按需获取脚本路径
    # ──────────────────────────────────────────────

    def list_scripts(self, skill_id: str) -> List[Dict[str, Any]]:
        """第三层 — 列出技能的所有脚本（不加载代码内容）

        Returns: [{name, path, size_bytes}]
        """
        t0 = time.time()
        tid = _trace_id()

        script_names = self.fs.list_scripts(skill_id)
        result = []
        for name in script_names:
            try:
                path = self.fs.get_script_path(skill_id, name)
                result.append({
                    "name": name,
                    "path": str(path),
                    "size_bytes": path.stat().st_size,
                })
            except Exception as e:
                logger.warning(json.dumps({
                    "trace_id": tid,
                    "module_name": "loader",
                    "action": "list_scripts.skip",
                    "skill_id": skill_id,
                    "script": name,
                    "error": str(e),
                }, ensure_ascii=False))

        elapsed = (time.time() - t0) * 1000
        logger.info(json.dumps({
            "trace_id": tid,
            "module_name": "loader",
            "action": "list_scripts.layer3.ok",
            "duration_ms": round(elapsed, 2),
            "layer": 3,
            "skill_id": skill_id,
            "script_count": len(result),
        }, ensure_ascii=False))

        return result

    def list_temp_files(self, skill_id: str) -> List[Dict[str, Any]]:
        """第三层 — 列出技能的业务模板文件"""
        temp_names = self.fs.list_temp_files(skill_id)
        result = []
        for name in temp_names:
            try:
                path = self.fs.get_temp_path(skill_id, name)
                result.append({
                    "name": name,
                    "path": str(path),
                    "size_bytes": path.stat().st_size,
                })
            except Exception:
                pass
        return result

    # ──────────────────────────────────────────────
    #  三层统计
    # ──────────────────────────────────────────────

    def get_layer_summary(self) -> Dict[str, Any]:
        """获取三层架构统计信息"""
        index = self.fs.load_metadata_index()

        # 第一层统计
        layer1_tokens = sum(
            estimate_tokens(json.dumps(m, ensure_ascii=False))
            for m in index.values()
        )

        # 第三层统计
        total_scripts = 0
        total_temp_files = 0
        for skill_id in index:
            total_scripts += len(self.fs.list_scripts(skill_id))
            total_temp_files += len(self.fs.list_temp_files(skill_id))

        return {
            "layer1_metadata": {
                "skill_count": len(index),
                "estimated_tokens_per_skill": (
                    layer1_tokens / len(index) if index else 0
                ),
                "estimated_total_tokens": layer1_tokens,
                "description": "元数据层（front matter），约 100 token/技能",
            },
            "layer2_instruction": {
                "description": "使用说明层（skill.md body），按需加载",
                "on_demand": True,
            },
            "layer3_tools": {
                "total_scripts": total_scripts,
                "total_temp_files": total_temp_files,
                "description": "工具资源层（scripts/ + temp/），后台执行",
                "on_demand": True,
            },
            "total_skills": len(index),
        }

    # ──────────────────────────────────────────────
    #  全量加载（调试用，不推荐生产环境）
    # ──────────────────────────────────────────────

    def load_full(self, skill_id: str) -> Dict[str, Any]:
        """加载技能完整信息（三层全部加载，仅调试用）

        生产环境应分层加载以节省上下文。
        """
        meta, body, scripts, temp_files = self.fs.read(skill_id)
        return {
            "skill_id": skill_id,
            "metadata": meta,
            "instruction": body,
            "instruction_tokens": estimate_tokens(body),
            "scripts": scripts,
            "temp_files": temp_files,
            "layer": "all",
        }
