"""
多引擎搜索结果聚合去重模块

同时调用多个搜索引擎，对结果进行去重、评分、排序后返回综合结果。
使用线程池并发调用，单个引擎超时/失败不影响整体结果。
"""

import time
import re
import json
import uuid
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional, List, Dict, Any
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

logger = logging.getLogger(__name__)


def _trace_id():
    """生成简短 trace_id"""
    return uuid.uuid4().hex[:16]

# ── 来源权重配置 ────────────────────────────────────────────────────
# 可配置：不同搜索引擎的结果在聚合时的权重
DEFAULT_SOURCE_WEIGHTS = {
    "tavily": 1.0,
    "tavily_search": 1.0,
    "firecrawl": 1.0,
    "firecrawl_search": 1.0,
    "custom": 1.0,          # 自定义 API 引擎
    "duckduckgo": 0.8,
    "sogou": 0.7,
    "so360": 0.7,
    # 通用后备（引擎名未匹配到时用）
    "__default__": 0.5,
}

# 关键词匹配加分系数
KEYWORD_BONUS_FACTOR = 0.1

# 最高评分上限
MAX_SCORE_CAP = 1.5

# ── 追踪参数列表 ────────────────────────────────────────────────────
TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_source_platform", "utm_creative_format", "utm_marketing_tactic",
    "fbclid", "gclid", "gclsrc", "dclid", "msclkid", "twclid",
    "igshid", "sc_campaign", "sc_channel", "sc_content", "sc_medium",
    "sc_source", "sc_term", "mc_cid", "mc_eid",
    "trk", "trkCampaign", "CNDID", "zanpid",
    "ref", "referrer", "source", "_ga", "_gl",
    "s_kwcid", "cmpid", "affiliate_id",
})


class SearchAggregator:
    """多引擎搜索聚合器

    并发调用多个搜索引擎，对结果去重、评分、排序，返回聚合后的综合结果。

    用法:
        aggregator = SearchAggregator(search_engine)
        result = aggregator.aggregate_search("Python tutorial", num_results=10)
    """

    def __init__(self, search_engine, source_weights: Optional[Dict[str, float]] = None):
        """初始化聚合器

        Args:
            search_engine: SearchEngine 实例，用于执行单个引擎的搜索
            source_weights: 可选的来源权重覆写字典
        """
        self._search_engine = search_engine
        self._source_weights = dict(DEFAULT_SOURCE_WEIGHTS)
        if source_weights:
            self._source_weights.update(source_weights)
        self._lock = threading.Lock()
        self._stats = {
            "aggregations": 0,
            "total_search_calls": 0,
            "total_failures": 0,
        }
        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "search_aggregator", "action": "search_aggregator.init.success", "engine_count": len(self._source_weights)}, ensure_ascii=False))

    # ── 公共接口 ────────────────────────────────────────────────────

    def aggregate_search(
        self,
        query: str,
        num_results: int = 10,
        engines: Optional[List[str]] = None,
        timeout: float = 15.0,
    ) -> dict:
        """聚合搜索主接口

        并发调用多个搜索引擎，合并、去重、评分、排序后返回 Top N。

        Args:
            query: 搜索关键词
            num_results: 最终返回的结果数
            engines: 指定要使用的引擎列表，None 则使用优先级列表中前 3 个启用的引擎
            timeout: 单个引擎的超时秒数

        Returns:
            dict 格式:
            {
                "ok": True/False,
                "query": "...",
                "engine": "aggregate",
                "results": [{title, url, snippet, source, score, dedup_key}, ...],
                "total_estimate": int,
                "aggregated": True,
                "engines_used": [...],
                "engine_results": {engine_name: count, ...},
                "errors": {engine_name: error_msg, ...},
                "elapsed": float,
            }
        """
        start_time = time.time()
        num_results = min(max(num_results, 1), 50)

        # 确定要使用的引擎列表
        if engines is None:
            engines = self._select_engines()
        if not engines:
            return {"ok": False, "error": "没有可用的搜索引擎"}

        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "search_aggregator", "action": "search_aggregator.search.start", "query": query[:60], "engines": str(engines), "num_results": num_results}, ensure_ascii=False))

        # 并发调用各引擎
        aggregated = []       # 所有结果
        engine_results = {}   # 各引擎原始结果数
        engine_errors = {}    # 各引擎的错误信息

        with ThreadPoolExecutor(max_workers=len(engines)) as executor:
            future_to_engine = {}
            for eng in engines:
                future = executor.submit(
                    self._search_single_engine, query, eng, num_results, timeout
                )
                future_to_engine[future] = eng

            try:
                for future in as_completed(future_to_engine, timeout=timeout):
                    eng = future_to_engine[future]
                    try:
                        engine_result = future.result()
                        if engine_result.get("ok") and engine_result.get("results"):
                            for item in engine_result["results"]:
                                item["source"] = eng  # 标记真实来源
                            aggregated.extend(engine_result["results"])
                            count = len(engine_result["results"])
                            engine_results[eng] = count
                            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "search_aggregator", "action": "search_aggregator.engine.results", "engine": eng, "count": count}, ensure_ascii=False))
                        elif engine_result.get("error"):
                            engine_errors[eng] = engine_result["error"]
                            engine_results[eng] = 0
                            logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "search_aggregator", "action": "search_aggregator.engine.failed", "engine": eng, "error": engine_result["error"]}, ensure_ascii=False))
                        else:
                            engine_results[eng] = 0
                            logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "search_aggregator", "action": "search_aggregator.engine.empty", "engine": eng}, ensure_ascii=False))
                    except Exception as e:
                        engine_errors[eng] = str(e)
                        engine_results[eng] = 0
                        logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "search_aggregator", "action": "search_aggregator.engine.exception", "engine": eng, "error": str(e)}, ensure_ascii=False))
            except TimeoutError:
                # 超时：取消所有未完成的 future，记录超时错误
                logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "search_aggregator", "action": "search_aggregator.search.timeout", "timeout_sec": timeout}, ensure_ascii=False))
                for future, eng in future_to_engine.items():
                    if not future.done():
                        future.cancel()
                        if eng not in engine_results:
                            engine_errors[eng] = f"超时 (>{timeout}s)"
                            engine_results[eng] = 0
                            logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "search_aggregator", "action": "search_aggregator.engine.timeout", "engine": eng}, ensure_ascii=False))

        # 去重
        deduped = self._deduplicate(aggregated)

        # 评分（使用静态方法，避免重复逻辑）
        for item in deduped:
            item["score"] = self.score_result(item, query, self._source_weights)

        # 按评分降序排序
        deduped.sort(key=lambda x: x.get("score", 0), reverse=True)

        # 取 Top N
        top_results = deduped[:num_results]

        elapsed = time.time() - start_time

        with self._lock:
            self._stats["aggregations"] += 1
            self._stats["total_search_calls"] += len(engines)
            self._stats["total_failures"] += len(engine_errors)

        logger.info(json.dumps({"trace_id": _trace_id(), "module_name": "search_aggregator", "action": "search_aggregator.search.complete", "engines_count": len(engines), "aggregated_count": len(aggregated), "deduped_count": len(deduped), "final_count": len(top_results), "elapsed_sec": round(elapsed, 2)}, ensure_ascii=False))

        return {
            "ok": True if top_results else False,
            "query": query,
            "engine": "aggregate",
            "results": top_results,
            "total_estimate": len(deduped),
            "aggregated": True,
            "engines_used": engines,
            "engine_results": engine_results,
            "errors": engine_errors,
            "elapsed": round(elapsed, 2),
        }

    # ── 内部方法 ────────────────────────────────────────────────────

    def _select_engines(self) -> List[str]:
        """从已注册引擎中选择优先级靠前且启用的前 3 个"""
        # 优先使用默认引擎，再取优先级列表中的其他可用引擎
        selected = []
        try:
            available = self._search_engine.get_available_engines()
            # 按优先级排序
            priority_order = self._search_engine._engine_priority
            ordered_names = []
            for name in priority_order:
                if name not in ordered_names:
                    ordered_names.append(name)
            # 追加未在优先级列表中的引擎
            for eng in available:
                if eng["name"] not in ordered_names:
                    ordered_names.append(eng["name"])
            # 过滤出启用且可用（无需 key 或已配置 key）的引擎
            for name in ordered_names:
                for eng in available:
                    if eng["name"] == name and eng.get("enabled", True):
                        if not eng.get("needs_key") or eng.get("configured"):
                            selected.append(name)
                            break
                if len(selected) >= 3:
                    break
        except Exception as e:
            logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "search_aggregator", "action": "search_aggregator.select_engines.failed", "error": str(e)}, ensure_ascii=False))
            selected = ["duckduckgo", "sogou", "so360"][:3]
        if not selected:
            # 最后的回退
            selected = [e["name"] for e in self._search_engine.get_registered_engines()
                       if self._search_engine._engine_enabled.get(e["name"], True)][:3]
        return selected

    def _search_single_engine(
        self, query: str, engine: str, num_results: int, timeout: float
    ) -> dict:
        """在单个引擎上执行搜索（用于线程池并发调用）

        直接调用引擎 handler 而不经过 search() 的缓存和降级机制，
        避免并发查询时的缓存碰撞和降级逻辑冲突。
        """
        try:
            # 直接调用引擎 handler，绕过 search() 的缓存和降级机制
            # 这样每个引擎独立执行，避免并发缓存碰撞
            result = self._search_engine._search_with_engine(
                engine, query, num_results, page=1
            )
            return result
        except Exception as e:
            logger.warning(json.dumps({"trace_id": _trace_id(), "module_name": "search_aggregator", "action": "search_aggregator.engine.search_exception", "engine": engine, "error": str(e)}, ensure_ascii=False))
            return {"ok": False, "error": str(e), "engine": engine, "results": []}

    def _deduplicate(self, results: List[Dict]) -> List[Dict]:
        """按归一化 URL 去重，第一次出现的保留，后续相同的丢弃。"""
        seen_urls: set = set()
        output: List[Dict] = []

        for item in results:
            url = item.get("url", "")

            # URL 归一化
            norm_url = self.normalize_url(url)
            if norm_url and norm_url in seen_urls:
                continue

            # 添加 dedup_key
            item["dedup_key"] = norm_url

            if norm_url:
                seen_urls.add(norm_url)

            output.append(item)

        return output

    def _get_source_weight(self, source: str) -> float:
        """获取指定来源的权重分数（仅精确匹配）"""
        source_lower = source.lower() if source else ""
        return self._source_weights.get(source_lower, self._source_weights.get("__default__", 0.5))

    def _keyword_bonus(self, item: Dict, query: str) -> float:
        """计算关键词在标题和摘要中的命中加分

        Args:
            item: 结果项，包含 title 和 snippet
            query: 搜索关键词

        Returns:
            加分值 = 命中的关键词数量 * KEYWORD_BONUS_FACTOR
        """
        if not query:
            return 0.0
        title = (item.get("title") or "").lower()
        snippet = (item.get("snippet") or "").lower()
        combined = title + " " + snippet

        # 拆分查询为关键词（支持中文和英文分词）
        keywords = self._extract_keywords(query)
        hits = sum(1 for kw in keywords if kw in combined)
        return round(hits * KEYWORD_BONUS_FACTOR, 2)

    @staticmethod
    def _extract_keywords(query: str) -> List[str]:
        """从查询字符串中提取关键词

        中英文混合处理：
        - 英文：按空格和标点分词
        - 中文：按常见标点分割，也保留整体作为关键词
        """
        query = query.lower().strip()
        if not query:
            return []
        # 先按空格和常见标点分割
        segments = re.split(r'[]\s,，。！？、；：""''（）()【】]+', query)
        keywords = []
        for seg in segments:
            seg = seg.strip()
            if not seg:
                continue
            # 纯英文单词直接加入
            if re.match(r'^[a-z0-9]+$', seg):
                keywords.append(seg)
            else:
                # 中英混合或纯中文，整体作为一个关键词
                keywords.append(seg)
                # 如果包含中文且有长度，也提取 2-3 字的中文子串
                if any('一' <= c <= '鿿' for c in seg) and len(seg) >= 3:
                    for i in range(len(seg) - 1):
                        bigram = seg[i:i + 2]
                        if bigram not in keywords:
                            keywords.append(bigram)
        return list(set(keywords))  # 去重

    @staticmethod
    def normalize_url(url: str) -> str:
        """URL 归一化，用于去重

        处理步骤：
        1. 去掉协议前缀 (http://, https://)
        2. 去掉末尾斜杠
        3. 去掉常见追踪参数 (utm_*, fbclid, gclid 等)
        4. 统一小写域名和路径
        5. 去掉 www 前缀（可选，保持一致性）

        Args:
            url: 原始 URL 字符串

        Returns:
            归一化后的 URL 字符串，空字符串输入返回空字符串
        """
        if not url:
            return ""
        # 确保有协议以便 urlparse 正确解析
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        parsed = urlparse(url)

        # 去掉 www 前缀
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]

        # 去掉末尾斜杠
        path = parsed.path.rstrip("/")

        # 处理查询参数：移除追踪参数
        if parsed.query:
            params = parse_qs(parsed.query, keep_blank_values=False)
            cleaned_params = {}
            for key, values in params.items():
                key_lower = key.lower()
                if key_lower not in TRACKING_PARAMS:
                    cleaned_params[key] = values[0] if len(values) == 1 else values
            if cleaned_params:
                query_str = urlencode(cleaned_params, doseq=True)
                normalized = urlunparse(("", netloc, path, "", query_str, ""))
            else:
                normalized = urlunparse(("", netloc, path, "", "", ""))
        else:
            normalized = urlunparse(("", netloc, path, "", "", ""))

        # 去掉开头的 //
        if normalized.startswith("//"):
            normalized = normalized[2:]

        return normalized

    @staticmethod
    def score_result(item: Dict, query: str, source_weights: Optional[Dict[str, float]] = None) -> float:
        """单个结果的评分（独立静态方法，方便测试）

        Args:
            item: 结果项，包含 title, snippet, source
            query: 搜索关键词
            source_weights: 自定义来源权重，None 则使用默认值

        Returns:
            评分 (0 ~ MAX_SCORE_CAP)
        """
        weights = dict(DEFAULT_SOURCE_WEIGHTS)
        if source_weights:
            weights.update(source_weights)

        source = (item.get("source") or "").lower()
        weight = weights.get(source, weights.get("__default__", 0.5))

        # 计算关键词加分
        keywords = SearchAggregator._extract_keywords(query)
        title = (item.get("title") or "").lower()
        snippet = (item.get("snippet") or "").lower()
        combined = title + " " + snippet
        hits = sum(1 for kw in keywords if kw in combined)
        bonus = hits * KEYWORD_BONUS_FACTOR

        return min(round(weight + bonus, 2), MAX_SCORE_CAP)

    @staticmethod
    def _normalize_title(title: str) -> str:
        """归一化标题，用于去重比对"""
        if not title:
            return ""
        return re.sub(r'\s+', ' ', title.lower()).strip()

    # ── 统计 ────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """获取聚合器统计信息"""
        with self._lock:
            return dict(self._stats)
