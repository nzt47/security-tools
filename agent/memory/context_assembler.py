"""ContextAssembler — 三级上下文组装 L0/L1/L2 [TLM-L3]

职责：
- L0 热数据层：从 HotnessScorer 取 Top-N 热门记忆，截断到 token 硬上限
- L1 温数据层：FTS5 全文检索 + 过滤已访问（access_count > 0）
- L2 冷数据层：向量检索命中后从 Markdown 归档懒加载原文片段

【不易】
- L0 token 硬上限 300（用 tiktoken 估算；tiktoken 不可用时降级 len//4）
- L2 冷数据懒加载必须从 MarkdownSyncer.read_fragment 读取，不查 SQLite 主表
- 返回结构契约：{L0: str, L1: list[MemoryResult], L2: list[dict]}
- assemble 是 async（依赖 adapter.search 协程）

【变易】
- max_tokens / l0_token_limit / top_n 可配
- adapter / scorer / syncer 均可选（None 时对应层级降级返回空）

【简易】
- 三层独立组装，无跨层依赖
- token 估算抽到 _estimate_tokens 单一入口，便于后续替换为精确实现
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

# tiktoken 可选依赖（不存在时降级为字符数 // 4 的粗略估算）
try:
    import tiktoken as _tiktoken
    _ENCODER = None  # 延迟初始化（首次使用时）
    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _tiktoken = None
    _ENCODER = None
    _TIKTOKEN_AVAILABLE = False


def _get_encoder():
    """延迟获取 tiktoken 编码器（cl100k_base，GPT-4/Ada 系列 tokenizer）"""
    global _ENCODER
    if not _TIKTOKEN_AVAILABLE:
        return None
    if _ENCODER is None:
        try:
            _ENCODER = _tiktoken.get_encoding("cl100k_base")
        except Exception as e:
            logger.debug("[ContextAssembler] tiktoken 编码器初始化失败: %s", e)
            return None
    return _ENCODER


def _estimate_tokens(text: str) -> int:
    """估算 text 的 token 数

    优先 tiktoken 精确计数；不可用时降级为 len(text) // 4（英文经验值，中文偏保守）
    """
    if not text:
        return 0
    enc = _get_encoder()
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            pass
    # 降级：中文 1 字符 ≈ 1 token，英文 4 字符 ≈ 1 token，取折中 len//3 偏保守
    # Why: 宁可高估避免 L0 超限（守不易约束）
    return max(1, len(text) // 3)


class ContextAssembler:
    """[TLM-L3] 三级上下文组装器

    用法:
        adapter = HolographicAdapter(db_path="...")
        scorer = HotnessScorer(adapter)
        syncer = MarkdownSyncer(adapter, output_dir="...")
        adapter.set_scorer(scorer)

        assembler = ContextAssembler(adapter, scorer, syncer)
        result = await assembler.assemble("用户查询", max_tokens=2000)
        # result = {"L0": "热门记忆摘要", "L1": [MemoryResult...], "L2": [{key, fragment}]}
    """

    # L0 token 硬上限（守不易约束）
    _DEFAULT_L0_TOKEN_LIMIT = 300
    # L0 默认取热度 Top-N
    _DEFAULT_L0_TOP_N = 5
    # L1 默认检索 top_k
    _DEFAULT_L1_TOP_K = 8
    # L2 默认检索 top_k
    _DEFAULT_L2_TOP_K = 3
    # L2 单条 fragment 最大字符数
    _DEFAULT_L2_MAX_CHARS = 500
    # 默认总 token 预算
    _DEFAULT_MAX_TOKENS = 2000

    def __init__(
        self,
        adapter: Optional[Any] = None,
        scorer: Optional[Any] = None,
        syncer: Optional[Any] = None,
        l0_token_limit: int = _DEFAULT_L0_TOKEN_LIMIT,
        l0_top_n: int = _DEFAULT_L0_TOP_N,
        l1_top_k: int = _DEFAULT_L1_TOP_K,
        l2_top_k: int = _DEFAULT_L2_TOP_K,
        l2_max_chars: int = _DEFAULT_L2_MAX_CHARS,
    ):
        """初始化 ContextAssembler

        Args:
            adapter: HolographicAdapter（L1/L2 检索依赖）
            scorer: HotnessScorer（L0 热数据依赖）
            syncer: MarkdownSyncer（L2 冷数据懒加载依赖）
            l0_token_limit: L0 token 硬上限（默认 300）
            l0_top_n: L0 取热度 Top-N
            l1_top_k: L1 检索 top_k
            l2_top_k: L2 检索 top_k
            l2_max_chars: L2 单条 fragment 最大字符数
        """
        self.adapter = adapter
        self.scorer = scorer
        self.syncer = syncer
        self.l0_token_limit = int(l0_token_limit)
        self.l0_top_n = int(l0_top_n)
        self.l1_top_k = int(l1_top_k)
        self.l2_top_k = int(l2_top_k)
        self.l2_max_chars = int(l2_max_chars)

        if self.l0_token_limit > self._DEFAULT_L0_TOKEN_LIMIT:
            logger.warning(
                log_dict({
                    "module_name": "context_assembler",
                    "action": "init.l0_limit_exceeded",
                    "msg": f"[ContextAssembler] L0 token 上限 {self.l0_token_limit} "
                           f"超过硬上限 {self._DEFAULT_L0_TOKEN_LIMIT}，已强制回退",
                })
            )
            self.l0_token_limit = self._DEFAULT_L0_TOKEN_LIMIT

        logger.info(
            log_dict({
                "module_name": "context_assembler",
                "action": "init",
                "msg": f"[ContextAssembler] 初始化完成: "
                       f"L0_limit={self.l0_token_limit}, L0_top={self.l0_top_n}, "
                       f"L1_top={self.l1_top_k}, L2_top={self.l2_top_k}, "
                       f"adapter={'on' if adapter else 'off'}, "
                       f"scorer={'on' if scorer else 'off'}, "
                       f"syncer={'on' if syncer else 'off'}",
            })
        )

    # ── 主入口 ──

    async def assemble(self, query: str, max_tokens: int = _DEFAULT_MAX_TOKENS) -> dict:
        """组装三级上下文

        Args:
            query: 用户查询（用于 L1/L2 检索）
            max_tokens: 总 token 预算（L0 受硬上限 300 约束，L1/L2 共享剩余）

        Returns:
            {
                "L0": str,                          # 热数据摘要文本
                "L1": list[MemoryResult],            # 温数据检索结果
                "L2": list[dict],                    # 冷数据 fragment（{key, fragment, source}）
                "meta": {                           # 组装元信息
                    "l0_tokens": int,
                    "l1_count": int,
                    "l2_count": int,
                    "elapsed_ms": float,
                }
            }
        """
        t0 = time.time()
        # 入口日志：查询 + 组件注入状态 + token 预算（便于排查"为何某层为空"）
        logger.debug(
            log_dict({
                "module_name": "context_assembler",
                "action": "assemble.entry",
                "msg": f"[ContextAssembler] assemble 开始: query={query!r} "
                       f"max_tokens={max_tokens} "
                       f"adapter={'on' if self.adapter else 'off'} "
                       f"scorer={'on' if self.scorer else 'off'} "
                       f"syncer={'on' if self.syncer else 'off'}",
            })
        )
        l0_text = self._build_l0()
        l1_items = await self._build_l1(query, max_tokens)
        # 单独计量 L2 耗时（便于脚本做 L2 超时断言 + 性能排查定位瓶颈层）
        l2_t0 = time.time()
        l2_items = await self._build_l2(query)
        l2_elapsed_ms = (time.time() - l2_t0) * 1000.0

        l0_tokens = _estimate_tokens(l0_text)
        elapsed_ms = (time.time() - t0) * 1000.0
        self._emit_metrics(l0_tokens, len(l1_items), len(l2_items), elapsed_ms)

        # 出口日志：三层数量 + token + 耗时（便于排查性能瓶颈与组装效果）
        logger.debug(
            log_dict({
                "module_name": "context_assembler",
                "action": "assemble.exit",
                "duration_ms": round(elapsed_ms, 2),
                "msg": f"[ContextAssembler] assemble 完成: "
                       f"L0_tokens={l0_tokens}/{self.l0_token_limit} "
                       f"L1_count={len(l1_items)} "
                       f"L2_count={len(l2_items)} "
                       f"L2_elapsed={l2_elapsed_ms:.2f}ms "
                       f"elapsed={elapsed_ms:.2f}ms",
            })
        )
        return {
            "L0": l0_text,
            "L1": l1_items,
            "L2": l2_items,
            "meta": {
                "l0_tokens": l0_tokens,
                "l1_count": len(l1_items),
                "l2_count": len(l2_items),
                "elapsed_ms": round(elapsed_ms, 2),
                "l2_elapsed_ms": round(l2_elapsed_ms, 2),
            },
        }

    # ── L0 热数据层 ──

    def _build_l0(self) -> str:
        """组装 L0 热数据层（Top-N 热门记忆摘要，截断到 token 硬上限）

        策略：
        1. scorer 未注入或无热数据 → 返回空字符串
        2. 取 Top-N，按 "key: data摘要" 拼接
        3. token 估算超过 l0_token_limit 时按条目粒度截断（不拆条目内部）
        """
        t0 = time.time()
        if self.scorer is None:
            logger.info(
                log_dict({
                    "module_name": "context_assembler",
                    "action": "l0.skip_no_scorer",
                    "msg": "[ContextAssembler] L0 跳过: scorer 未注入",
                })
            )
            return ""
        try:
            hot_records = self.scorer.get_hot_records(top_n=self.l0_top_n)
        except Exception as e:
            logger.warning(
                log_dict({
                    "module_name": "context_assembler",
                    "action": "l0.fetch_failed",
                    "msg": f"[ContextAssembler] L0 取热数据失败: {e}",
                })
            )
            return ""
        if not hot_records:
            logger.debug(
                log_dict({
                    "module_name": "context_assembler",
                    "action": "l0.empty_hot_records",
                    "msg": "[ContextAssembler] L0 为空: 无热数据记录",
                })
            )
            return ""

        lines: list[str] = []
        total_tokens = 0
        truncated_count = 0
        for rec in hot_records:
            key = rec.get("key", "")
            data = rec.get("data", "")
            # data 截断到单条最大字符（避免单条过长撑爆 L0）
            data_preview = (data[:120] + "...") if len(data) > 120 else data
            line = f"- [{key}] {data_preview}"
            line_tokens = _estimate_tokens(line)
            # 守不易：硬上限检查，超过则停止追加
            if total_tokens + line_tokens > self.l0_token_limit:
                truncated_count = len(hot_records) - len(lines)
                logger.debug(
                    log_dict({
                        "module_name": "context_assembler",
                        "action": "l0.token_limit_truncated",
                        "msg": f"[ContextAssembler] L0 token 截断: "
                               f"已用 {total_tokens}/{self.l0_token_limit} "
                               f"截断 {truncated_count} 条 "
                               f"(当前条 tokens={line_tokens})",
                    })
                )
                break
            lines.append(line)
            total_tokens += line_tokens
        elapsed_ms = (time.time() - t0) * 1000.0
        logger.debug(
            log_dict({
                "module_name": "context_assembler",
                "action": "l0.built",
                "duration_ms": round(elapsed_ms, 2),
                "msg": f"[ContextAssembler] L0 组装完成: "
                       f"records={len(hot_records)} included={len(lines)} "
                       f"truncated={truncated_count} "
                       f"tokens={total_tokens}/{self.l0_token_limit} "
                       f"elapsed={elapsed_ms:.2f}ms",
            })
        )
        return "\n".join(lines)

    # ── L1 温数据层 ──

    async def _build_l1(self, query: str, max_tokens: int) -> list:
        """组装 L1 温数据层（FTS5 检索 + 过滤已访问）

        策略：
        1. adapter 未注入或空查询 → 返回空列表
        2. adapter.search(query, top_k=l1_top_k)
        3. 过滤 access_count > 0 的（已访问过的视为温数据）
        4. 失败返回空列表（守降级）
        """
        if self.adapter is None or not query:
            logger.debug(
                log_dict({
                    "module_name": "context_assembler",
                    "action": "l1.skip_no_input",
                    "msg": f"[ContextAssembler] L1 跳过: "
                           f"adapter={'on' if self.adapter else 'off'} "
                           f"query_empty={not query}",
                })
            )
            return []
        t0 = time.time()
        try:
            results = await self.adapter.search(query, top_k=self.l1_top_k)
        except Exception as e:
            logger.warning(
                log_dict({
                    "module_name": "context_assembler",
                    "action": "l1.search_failed",
                    "msg": f"[ContextAssembler] L1 检索失败: {e}",
                })
            )
            return []
        # 过滤：已访问过的（access_count > 0）视为温数据
        # Why: L0 已包含热度最高的，L1 聚焦"用户交互过但非最热"的中间层
        filtered = []
        for r in results:
            meta = getattr(r, "metadata", None) or {}
            if not isinstance(meta, dict):
                continue
            # access_count 通过 search 命中已被 +1，>=1 表示温数据
            # 若 metadata 未携带 access_count，保守保留（避免过滤掉有效结果）
            if meta.get("access_count", 1) >= 1:
                filtered.append(r)
        elapsed_ms = (time.time() - t0) * 1000.0
        logger.debug(
            log_dict({
                "module_name": "context_assembler",
                "action": "l1.built",
                "duration_ms": round(elapsed_ms, 2),
                "msg": f"[ContextAssembler] L1 组装完成: "
                       f"search_results={len(results)} "
                       f"filtered={len(filtered)} "
                       f"top_k={self.l1_top_k} query={query!r} "
                       f"elapsed={elapsed_ms:.2f}ms",
            })
        )
        return filtered

    # ── L2 冷数据层 ──

    async def _build_l2(self, query: str) -> list[dict]:
        """组装 L2 冷数据层（向量检索命中 → Markdown 归档懒加载 fragment）

        策略：
        1. adapter/syncer 任一未注入 → 返回空列表
        2. 尝试 adapter.search_vector（向量检索命中冷数据 key）
        3. 对每个命中 key，调 syncer.read_fragment 从 .md 归档读取前 N 字符
        4. 失败返回空列表（守降级）

        不变量：L2 不查 SQLite 主表，只从 Markdown 归档读取（守不易约束）
        """
        t0 = time.time()
        # 入口日志：组件注入状态 + query（便于排查"L2 为何为空"）
        logger.debug(
            log_dict({
                "module_name": "context_assembler",
                "action": "l2.entry",
                "msg": f"[ContextAssembler] L2 开始: query={query!r} "
                       f"adapter={'on' if self.adapter else 'off'} "
                       f"syncer={'on' if self.syncer else 'off'} "
                       f"vec_available={getattr(self.adapter, '_vec_available', False) if self.adapter else False} "
                       f"top_k={self.l2_top_k} max_chars={self.l2_max_chars}",
            })
        )
        if self.adapter is None or self.syncer is None or not query:
            logger.debug(
                log_dict({
                    "module_name": "context_assembler",
                    "action": "l2.skip_no_input",
                    "msg": f"[ContextAssembler] L2 跳过: "
                           f"adapter={'on' if self.adapter else 'off'} "
                           f"syncer={'on' if self.syncer else 'off'} "
                           f"query_empty={not query}",
                })
            )
            return []
        # 向量层不可用时无法定位冷数据 key
        if not getattr(self.adapter, "_vec_available", False):
            logger.debug(
                log_dict({
                    "module_name": "context_assembler",
                    "action": "l2.skip_vec_unavailable",
                    "msg": "[ContextAssembler] L2 跳过: 向量层不可用（_vec_available=False）",
                })
            )
            return []
        # 取 query 的 embedding（adapter 若提供 _embedding_func 则用，否则跳过）
        embedding_func = getattr(self.adapter, "_embedding_func", None)
        if embedding_func is None:
            logger.debug(
                log_dict({
                    "module_name": "context_assembler",
                    "action": "l2.skip_no_embedding_func",
                    "msg": "[ContextAssembler] L2 跳过: adapter._embedding_func 未注入",
                })
            )
            return []
        try:
            query_embedding = embedding_func(query)
        except Exception as e:
            logger.debug(
                log_dict({
                    "module_name": "context_assembler",
                    "action": "l2.embedding_failed",
                    "msg": f"[ContextAssembler] L2 embedding 生成失败: {e}",
                })
            )
            return []
        if not query_embedding:
            logger.debug(
                log_dict({
                    "module_name": "context_assembler",
                    "action": "l2.empty_embedding",
                    "msg": "[ContextAssembler] L2 跳过: embedding_func 返回空向量",
                })
            )
            return []
        try:
            vec_results = await self.adapter.search_vector(query_embedding, top_k=self.l2_top_k)
        except Exception as e:
            logger.warning(
                log_dict({
                    "module_name": "context_assembler",
                    "action": "l2.search_failed",
                    "msg": f"[ContextAssembler] L2 向量检索失败: {e}",
                })
            )
            return []
        logger.debug(
            log_dict({
                "module_name": "context_assembler",
                "action": "l2.vec_results",
                "msg": f"[ContextAssembler] L2 向量检索命中: "
                       f"results={len(vec_results)} top_k={self.l2_top_k}",
            })
        )
        # 懒加载：每个命中 key 从 Markdown 归档读 fragment
        fragments: list[dict] = []
        skipped_no_key = 0
        empty_fragments = 0
        for r in vec_results:
            meta = getattr(r, "metadata", None) or {}
            key = meta.get("key") if isinstance(meta, dict) else None
            if not key:
                skipped_no_key += 1
                continue
            try:
                fragment = self.syncer.read_fragment(key, max_chars=self.l2_max_chars)
            except Exception as e:
                logger.debug(
                    log_dict({
                        "module_name": "context_assembler",
                        "action": "l2.fragment_failed",
                        "msg": f"[ContextAssembler] L2 fragment 读取失败 key={key}: {e}",
                    })
                )
                fragment = ""
            if fragment:
                fragments.append({
                    "key": key,
                    "fragment": fragment,
                    "source": "markdown_archive",
                })
            else:
                empty_fragments += 1
        elapsed_ms = (time.time() - t0) * 1000.0
        # 出口日志：命中数 / 跳过数 / 空片段数 / 耗时（便于排查 L2 命中率与 fragment 缺失）
        logger.debug(
            log_dict({
                "module_name": "context_assembler",
                "action": "l2.built",
                "duration_ms": round(elapsed_ms, 2),
                "msg": f"[ContextAssembler] L2 组装完成: "
                       f"vec_hits={len(vec_results)} fragments={len(fragments)} "
                       f"skipped_no_key={skipped_no_key} empty_fragments={empty_fragments} "
                       f"elapsed={elapsed_ms:.2f}ms",
            })
        )
        return fragments

    # ── 指标埋点 ──

    def _emit_metrics(self, l0_tokens: int, l1_count: int,
                      l2_count: int, elapsed_ms: float) -> None:
        """组装后统一上报指标（失败不影响返回值）"""
        try:
            from agent.memory.observability import (
                track_tlm_context_assembled_tokens,
                track_tlm_retrieval_latency,
            )
            track_tlm_context_assembled_tokens("L0", l0_tokens)
            # L1/L2 用 count 近似 token 体量（条目数 * 100 粗估）
            track_tlm_context_assembled_tokens("L1", l1_count * 100)
            track_tlm_context_assembled_tokens("L2", l2_count * 100)
            # 整体 assemble 延迟归一为 hybrid 策略
            track_tlm_retrieval_latency("hybrid", elapsed_ms)
        except Exception as e:
            logger.debug(
                "[ContextAssembler] 指标埋点失败（忽略）: %s", e
            )
