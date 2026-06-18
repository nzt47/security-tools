"""
搜索引擎集成 — DuckDuckGo / Bing / Google / Brave / Tavily / Baidu / Sogou / 360

对接多个搜索引擎 API 和 HTML 搜索，支持自动降级机制，统一搜索结果格式。
支持动态注册新搜索引擎。
"""

import re
import json
import time
import logging
from typing import Optional, List, Dict, Any, Callable
from urllib.parse import quote_plus, urlencode

logger = logging.getLogger(__name__)


def _json_get(obj, path: str):
    """按点号分隔的键路径获取值，如 'data.items' -> obj['data']['items']"""
    if not path:
        return obj
    parts = path.strip(".").split(".")
    current = obj
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif isinstance(current, list):
            try:
                idx = int(part)
                current = current[idx]
            except (ValueError, IndexError):
                return None
        else:
            return None
    return current


class SearchEngine:
    """搜索引擎集成 — 搜索、分页、结果结构化、自动降级

    内置引擎：
    - duckduckgo: DuckDuckGo HTML 搜索（无需 API Key）
    - bing: Bing 网页搜索（需 API Key）
    - google: Google 自定义搜索（需 API Key, CX）
    - brave: Brave 搜索（需 API Key）
    - tavily: Tavily 搜索 API（需 API Key）
    - baidu: 百度 HTML 搜索（无需 API Key，中文）
    - sogou: 搜狗 HTML 搜索（无需 API Key，中文）
    - so360: 360 搜索（无需 API Key，中文）

    特性：
    - 动态引擎注册：支持运行时添加新的搜索引擎
    - 自动降级机制：主引擎失败时自动切换到备用引擎
    - 搜索引擎优先级配置
    - 详细的引擎切换日志
    - 统一的请求参数和响应格式
    """

    def __init__(self, config: Optional[dict] = None):
        self._config = config or {}
        self._default_engine = self._config.get("default_engine", "duckduckgo")
        self._http_client = None

        # 引擎注册表：name -> {name, label, description, needs_key, handler}
        self._engine_registry: Dict[str, Dict[str, Any]] = {}

        # API Keys（动态字典，任何引擎名都可作为键）
        self._api_keys: Dict[str, str] = {}

        # 从配置加载已知的 API Key
        self._load_api_keys_from_config()

        # 搜索引擎优先级列表（按优先级从高到低）
        self._engine_priority = self._config.get(
            "engine_priority",
            ["duckduckgo", "tavily", "bing", "brave", "google"]
        )

        # 各引擎启用状态
        self._engine_enabled = self._config.get(
            "engine_enabled",
            {
                "duckduckgo": True,
                "tavily": True,
                "bing": True,
                "google": True,
                "brave": True,
            }
        )

        # 搜索超时时间（秒）
        self._timeout = self._config.get("timeout", 30)

        # 统计信息（动态初始化，后续由 register_engine 填充）
        self._stats = {
            "searches": 0,
            "total_results": 0,
            "cached_hits": 0,
            "fallback_count": 0,
            "engine_usage": {},
            "engine_timing": {},
        }

        # 降级历史记录（用于问题排查）
        self._fallback_history = []

        # 缓存
        self._cache: Dict[str, dict] = {}
        self._cache_ttl = self._config.get("cache_ttl", 300)

        # 注册内置搜索引擎
        self._register_builtin_engines()

        logger.info("SearchEngine 已初始化 (默认引擎: %s, 优先级: %s)",
                   self._default_engine, self._engine_priority)

    # ── 动态引擎注册系统 ────────────────────────────────────────────

    def register_engine(self, name: str, label: str, handler: Callable,
                        needs_key: bool = False, description: str = "") -> None:
        """注册一个新的搜索引擎到引擎注册表

        Args:
            name: 引擎内部名称（如 "baidu"），全小写英文字母
            label: 引擎显示名称（如 "百度"），用于日志和界面
            handler: 搜索处理函数，签名与 _search_* 方法一致
            needs_key: 是否需要 API Key
            description: 引擎描述
        """
        self._engine_registry[name] = {
            "name": name,
            "label": label,
            "description": description,
            "needs_key": needs_key,
            "handler": handler,
        }
        # 确保引擎在优先级列表中
        if name not in self._engine_priority:
            self._engine_priority.append(name)
        # 确保引擎已启用
        if name not in self._engine_enabled:
            self._engine_enabled[name] = True
        # 确保有统计条目
        if name not in self._stats["engine_usage"]:
            self._stats["engine_usage"][name] = 0
        if name not in self._stats["engine_timing"]:
            self._stats["engine_timing"][name] = {
                "total": 0, "count": 0, "avg": 0,
                "min": float('inf'), "max": 0,
            }
        # 确保有 API Key 条目（留空）
        if name not in self._api_keys:
            self._api_keys[name] = ""
        logger.info("[搜索引擎] 已注册: %s (%s), 需API Key: %s",
                    name, label, needs_key)

    def remove_engine(self, name: str) -> bool:
        """从引擎注册表中移除一个搜索引擎

        Args:
            name: 引擎内部名称

        Returns:
            bool: 是否成功移除
        """
        if name not in self._engine_registry:
            logger.warning("[搜索引擎] 移除失败，引擎不存在: %s", name)
            return False
        del self._engine_registry[name]
        if name in self._engine_priority:
            self._engine_priority.remove(name)
        if name in self._engine_enabled:
            del self._engine_enabled[name]
        if name in self._stats["engine_usage"]:
            del self._stats["engine_usage"][name]
        if name in self._stats["engine_timing"]:
            del self._stats["engine_timing"][name]
        self._api_keys.pop(name, None)
        logger.info("[搜索引擎] 已移除: %s", name)
        return True

    def set_default_engine(self, name: str):
        """设置默认搜索引擎"""
        if not name:
            self._default_engine = self._config.get("default_engine", "duckduckgo")
            logger.info("[搜索引擎] 默认引擎已重置为: %s", self._default_engine)
            return
        if name not in self._engine_registry:
            raise ValueError(f"引擎 {name} 未注册")
        self._default_engine = name
        logger.info("[搜索引擎] 默认引擎已设为: %s", self._default_engine)

    def _load_api_keys_from_config(self):
        """从配置中加载所有已知的 API Key

        从 self._config 中查找以 _api_key 结尾或特殊命名的键，
        加载到 self._api_keys 字典中。
        """
        for key, value in self._config.items():
            if key.endswith("_api_key") and value:
                engine_name = key.replace("_api_key", "")
                self._api_keys[engine_name] = value
        # 特殊处理 google_cx
        cx = self._config.get("google_cx", "")
        if cx:
            self._api_keys["google_cx"] = cx

    def _register_builtin_engines(self):
        """注册所有内置搜索引擎"""
        # 需要 API Key 的引擎
        self.register_engine("tavily", "Tavily", self._search_tavily,
                             needs_key=True, description="Tavily 搜索 API")
        self.register_engine("firecrawl", "Firecrawl", self._search_firecrawl,
                             needs_key=True, description="Firecrawl 搜索 API")
        self.register_engine("bing", "Bing", self._search_bing,
                             needs_key=True, description="Bing 网页搜索 API")
        self.register_engine("google", "Google", self._search_google,
                             needs_key=True, description="Google 自定义搜索 API")
        self.register_engine("brave", "Brave", self._search_brave,
                             needs_key=True, description="Brave 搜索 API")
        # 无需 API Key 的引擎
        self.register_engine("duckduckgo", "DuckDuckGo", self._search_duckduckgo,
                             needs_key=False, description="DuckDuckGo HTML 搜索")
        self.register_engine("baidu", "百度", self._search_baidu,
                             needs_key=False, description="百度 HTML 搜索（中文）")
        self.register_engine("sogou", "搜狗", self._search_sogou,
                             needs_key=False, description="搜狗 HTML 搜索（中文）")
        self.register_engine("so360", "360搜索", self._search_so360,
                             needs_key=False, description="360 搜索（中文）")

    def get_registered_engines(self) -> List[Dict[str, Any]]:
        """获取所有已注册的引擎信息"""
        return [
            {
                "name": name,
                "label": info["label"],
                "description": info.get("description", ""),
                "needs_key": info["needs_key"],
            }
            for name, info in self._engine_registry.items()
        ]

    def set_http_client(self, client):
        """设置 HTTP 客户端"""
        self._http_client = client

    def set_engine_priority(self, priority: List[str]):
        """设置搜索引擎优先级"""
        self._engine_priority = priority
        logger.info("[搜索引擎] 优先级已更新: %s", priority)

    def set_engine_enabled(self, engine: str, enabled: bool):
        """设置搜索引擎启用/禁用状态"""
        if engine in self._engine_enabled:
            self._engine_enabled[engine] = enabled
            logger.info("[搜索引擎] %s 已%s", engine, "启用" if enabled else "禁用")

    def set_timeout(self, timeout: int):
        """设置搜索超时时间"""
        self._timeout = timeout
        logger.info("[搜索引擎] 超时时间已设置为 %d 秒", timeout)

    # ── 主搜索接口（带降级机制） ────────────────────────────────────

    def search(self, query: str, engine: str = "", num_results: int = 10, page: int = 1, **kwargs) -> dict:
        """执行搜索（支持自动降级）

        Args:
            query: 搜索关键词
            engine: 搜索引擎（duckduckgo / baidu / sogou / so360 / tavily / bing / google / brave），
                    为空时使用优先级列表自动选择
            num_results: 每页结果数（最大 50）
            page: 页码（从 1 开始）
            **kwargs: 传递给具体引擎的额外参数

        Returns:
            dict: {ok, query, engine, results: [{title, url, snippet, source}], 
                  total_estimate, page, fallback_used, fallback_reason, ...}
        """
        num_results = min(max(num_results, 1), 50)
        start_time = time.time()

        # ── [日志] 确定要使用的引擎列表 ──
        if engine:
            # 用户指定了特定引擎
            engines_to_try = [engine]
            logger.info("=" * 80)
            logger.info("[搜索引擎] 【搜索开始】用户指定引擎: %s, 查询: %s", engine, query[:50])
            logger.info("=" * 80)
        else:
            # 使用优先级列表中启用的引擎
            engines_to_try = [e for e in self._engine_priority if self._engine_enabled.get(e, True)]
            logger.info("=" * 80)
            logger.info("[搜索引擎] 【搜索开始】自动选择引擎")
            logger.info("[搜索引擎]   查询: %s", query[:50])
            logger.info("[搜索引擎]   引擎优先级: %s", self._engine_priority)
            logger.info("[搜索引擎]   引擎启用状态: %s", self._engine_enabled)
            logger.info("[搜索引擎]   将尝试的引擎列表: %s", engines_to_try)
            logger.info("=" * 80)

        if not engines_to_try:
            logger.error("[搜索引擎] 【错误】没有可用的搜索引擎！")
            return {"ok": False, "error": "没有可用的搜索引擎"}

        # 缓存检查（基于第一个引擎）
        cache_key = f"any:{query}:{num_results}:{page}"
        cached = self._check_cache(cache_key)
        if cached:
            logger.info("[搜索引擎] 【缓存命中】直接从缓存返回结果")
            return cached

        # ── [日志] 尝试各个引擎（带降级） ──
        last_error = None
        fallback_history = []

        for idx, current_engine in enumerate(engines_to_try):
            logger.info("-" * 80)
            logger.info("[搜索引擎] 【尝试引擎 #%d/%d】%s", idx + 1, len(engines_to_try), current_engine.upper())
            logger.info("[搜索引擎]   配置信息:")
            logger.info("[搜索引擎]     - 超时设置: %ds", self._timeout)
            logger.info("[搜索引擎]     - API Key 状态: %s", {
                k: "已配置" if v else "未配置" 
                for k, v in self._api_keys.items()
            })
            logger.info("-" * 80)
            
            try:
                # ── [日志] 调用具体搜索引擎 ──
                logger.info("[搜索引擎] 【调用中】正在请求 %s API...", current_engine)
                result = self._search_with_engine(
                    current_engine, query, num_results, page, **kwargs
                )
                
                # ── [日志] 分析结果 ──
                if result.get("ok") and result.get("results"):
                    # 搜索成功
                    elapsed = time.time() - start_time
                    result["fallback_used"] = idx > 0
                    result["fallback_count"] = idx
                    result["fallback_history"] = fallback_history
                    result["elapsed"] = elapsed

                    # ── [日志] 记录成功日志 ──
                    if idx > 0:
                        logger.info("🎉" * 20)
                        logger.info("[搜索引擎] 【降级成功】%s -> %s", engines_to_try[0].upper(), current_engine.upper())
                        logger.info("[搜索引擎]   查询: %s", query[:50])
                        logger.info("[搜索引擎]   结果数量: %d", len(result.get("results", [])))
                        logger.info("[搜索引擎]   总耗时: %.2fs", elapsed)
                        logger.info("[搜索引擎]   降级次数: %d", idx)
                        logger.info("[搜索引擎]   降级历史: %s", fallback_history)
                        logger.info("🎉" * 20)
                    else:
                        logger.info("✅" * 20)
                        logger.info("[搜索引擎] 【搜索成功】引擎: %s", current_engine.upper())
                        logger.info("[搜索引擎]   查询: %s", query[:50])
                        logger.info("[搜索引擎]   结果数量: %d", len(result.get("results", [])))
                        logger.info("[搜索引擎]   总耗时: %.2fs", elapsed)
                        logger.info("✅" * 20)

                    # 更新统计
                    self._stats["searches"] += 1
                    self._stats["total_results"] += len(result.get("results", []))
                    self._stats["engine_usage"][current_engine] += 1
                    
                    # 更新耗时统计
                    timing = self._stats["engine_timing"][current_engine]
                    timing["total"] += elapsed
                    timing["count"] += 1
                    timing["avg"] = timing["total"] / timing["count"]
                    timing["min"] = min(timing["min"], elapsed)
                    timing["max"] = max(timing["max"], elapsed)
                    
                    # 输出耗时对比日志
                    logger.info("[搜索引擎] 【耗时统计】引擎 %s 性能:", current_engine.upper())
                    logger.info("[搜索引擎]   本次耗时: %.2fs", elapsed)
                    logger.info("[搜索引擎]   平均耗时: %.2fs", timing["avg"])
                    logger.info("[搜索引擎]   最小耗时: %.2fs", timing["min"])
                    logger.info("[搜索引擎]   最大耗时: %.2fs", timing["max"])
                    logger.info("[搜索引擎]   累计调用: %d 次", timing["count"])

                    # 写入缓存
                    self._set_cache(cache_key, result)

                    return result

                elif result.get("ok") and not result.get("results"):
                    # 成功但无结果，继续尝试下一个引擎
                    last_error = "无搜索结果"
                    fallback_info = {
                        "engine": current_engine,
                        "status": "no_results",
                        "reason": "返回结果为空",
                        "timestamp": time.time(),
                    }
                    fallback_history.append(fallback_info)
                    
                    logger.warning("⚠️ " * 20)
                    logger.warning("[搜索引擎] 【无结果】引擎 %s 返回空结果", current_engine.upper())
                    logger.warning("[搜索引擎]   查询: %s", query[:50])
                    logger.warning("[搜索引擎]   将尝试下一个引擎...")
                    logger.warning("⚠️ " * 20)

                else:
                    # 搜索失败
                    last_error = result.get("error", "未知错误")
                    fallback_info = {
                        "engine": current_engine,
                        "status": "failed",
                        "reason": last_error,
                        "timestamp": time.time(),
                    }
                    fallback_history.append(fallback_info)
                    
                    logger.warning("❌" * 20)
                    logger.warning("[搜索引擎] 【失败】引擎 %s 搜索失败", current_engine.upper())
                    logger.warning("[搜索引擎]   查询: %s", query[:50])
                    logger.warning("[搜索引擎]   错误: %s", last_error)
                    logger.warning("[搜索引擎]   将尝试下一个引擎...")
                    logger.warning("❌" * 20)

            except Exception as e:
                last_error = str(e)
                fallback_info = {
                    "engine": current_engine,
                    "status": "exception",
                    "reason": last_error,
                    "timestamp": time.time(),
                }
                fallback_history.append(fallback_info)
                
                logger.error("💥" * 20)
                logger.error("[搜索引擎] 【异常】引擎 %s 发生异常", current_engine.upper())
                logger.error("[搜索引擎]   查询: %s", query[:50])
                logger.error("[搜索引擎]   异常类型: %s", type(e).__name__)
                logger.error("[搜索引擎]   异常信息: %s", str(e))
                logger.error("[搜索引擎]   将尝试下一个引擎...")
                logger.error("💥" * 20)

        # 所有引擎都失败了
        elapsed = time.time() - start_time
        error_result = {
            "ok": False,
            "error": f"所有搜索引擎均失败，最后错误: {last_error}",
            "query": query,
            "engine": engines_to_try[0],
            "page": page,
            "fallback_used": len(engines_to_try) > 1,
            "fallback_count": len(fallback_history),
            "fallback_history": fallback_history,
            "elapsed": elapsed,
            "results": [],
        }

        # 记录降级失败日志
        self._stats["searches"] += 1
        self._stats["fallback_count"] += 1
        self._fallback_history.append({
            "query": query,
            "engines_tried": engines_to_try,
            "fallback_history": fallback_history,
            "timestamp": time.time(),
            "elapsed": elapsed,
        })

        logger.error("=" * 80)
        logger.error("[搜索引擎] 【全部失败】所有搜索引擎均失败！")
        logger.error("=" * 80)
        logger.error("[搜索引擎]   查询: %s", query[:50])
        logger.error("[搜索引擎]   尝试的引擎列表: %s", engines_to_try)
        logger.error("[搜索引擎]   降级历史:")
        for history in fallback_history:
            logger.error("     - 引擎: %s | 状态: %s | 原因: %s", 
                        history['engine'], history['status'], history['reason'])
        logger.error("[搜索引擎]   总耗时: %.2fs", elapsed)
        logger.error("[搜索引擎]   最后错误: %s", last_error)
        logger.error("=" * 80)

        return error_result

    def _search_with_engine(self, engine: str, query: str, num_results: int, page: int, **kwargs) -> dict:
        """使用指定引擎执行搜索（从引擎注册表中查找）"""
        engine_info = self._engine_registry.get(engine)
        if not engine_info:
            return {"ok": False, "error": f"不支持的搜索引擎: {engine}"}

        handler = engine_info["handler"]
        return handler(query, num_results=num_results, page=page, **kwargs)

    # ── DuckDuckGo（免 API Key） ──────────────────────────────────

    def _search_duckduckgo(self, query: str, num_results: int = 10, page: int = 1, **kwargs) -> dict:
        """DuckDuckGo HTML 搜索"""
        if not self._http_client:
            return {"ok": False, "error": "HTTP 客户端未配置"}

        result = self._http_client.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query, "s": max(0, (page - 1) * num_results)},
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"},
            timeout=self._timeout,
        )

        if not result.get("ok"):
            return result

        html = result.get("text", "")
        results = self._parse_duckduckgo_html(html)

        return {
            "ok": True,
            "results": results[:num_results],
            "total_estimate": max(len(results), num_results),
            "source_url": result.get("url", ""),
            "engine": "duckduckgo",
        }

    @staticmethod
    def _parse_duckduckgo_html(html: str) -> List[Dict]:
        """解析 DuckDuckGo HTML 搜索结果"""
        from lxml import html as lxml_html

        results = []
        try:
            tree = lxml_html.fromstring(html)
            for item in tree.xpath('//div[contains(@class, "result")]'):
                title_el = item.xpath('.//h2[contains(@class, "result__title")]/a')
                if not title_el:
                    continue

                link = title_el[0]
                url_el = item.xpath('.//a[contains(@class, "result__a")]/@href')

                snippet_el = item.xpath('.//a[contains(@class, "result__snippet")]')
                snippet = snippet_el[0].text_content().strip() if snippet_el else ""

                results.append({
                    "title": link.text_content().strip(),
                    "url": url_el[0] if url_el else "",
                    "snippet": snippet,
                    "source": "duckduckgo",
                })
        except Exception as e:
            logger.warning("解析 DuckDuckGo 结果失败: %s", e)
            results = SearchEngine._parse_result_fallback(html)

        return results

    # ── Tavily（需 API Key） ───────────────────────────────────────

    def _search_tavily(self, query: str, num_results: int = 10, page: int = 1, **kwargs) -> dict:
        """Tavily 搜索 API"""
        api_key = self._api_keys.get("tavily") or self._config.get("tavily_api_key", "")
        logger.info("[Tavily] API Key 状态: %s", "已配置" if api_key else "未配置")
        logger.info("[Tavily] API Key 值: %s...", api_key[:10] if api_key else "无")
        
        if not api_key:
            return {"ok": False, "error": "Tavily API Key 未配置"}

        if not self._http_client:
            return {"ok": False, "error": "HTTP 客户端未配置"}

        offset = (page - 1) * num_results
        
        # 构建请求头
        request_headers = {"Authorization": f"Bearer {api_key}"}
        logger.info("[Tavily] 请求头: %s", request_headers)
        
        result = self._http_client.post(
            "https://api.tavily.com/search",
            json_data={
                "query": query,
                "max_results": num_results,
                "offset": offset,
                "search_depth": kwargs.get("search_depth", "basic"),
                "include_answer": kwargs.get("include_answer", False),
                "include_raw_content": kwargs.get("include_raw_content", False),
            },
            headers=request_headers,
            timeout=self._timeout,
        )

        if not result.get("ok"):
            return result

        data = result.get("text", "")
        try:
            json_data = json.loads(data)
        except json.JSONDecodeError:
            return {"ok": False, "error": "解析 Tavily API 响应失败"}

        results = []
        for item in json_data.get("results", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("content", ""),
                "source": "tavily",
            })

        return {
            "ok": True,
            "results": results,
            "total_estimate": json_data.get("total_results", len(results)),
            "engine": "tavily",
            "answer": json_data.get("answer"),
        }

    # ── Firecrawl（需 API Key） ──────────────────────────────────

    def _search_firecrawl(self, query: str, num_results: int = 10, page: int = 1, **kwargs) -> dict:
        """Firecrawl Search API"""
        api_key = self._api_keys.get("firecrawl") or self._config.get("firecrawl_api_key", "")
        if not api_key:
            return {"ok": False, "error": "Firecrawl API Key 未配置"}

        if not self._http_client:
            return {"ok": False, "error": "HTTP 客户端未配置"}

        result = self._http_client.post(
            "https://api.firecrawl.dev/v1/search",
            json_data={
                "query": query,
                "limit": num_results,
            },
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=self._timeout,
        )

        if not result.get("ok"):
            return result

        data = result.get("text", "")
        try:
            json_data = json.loads(data)
        except json.JSONDecodeError:
            return {"ok": False, "error": "解析 Firecrawl API 响应失败"}

        results = []
        for item in json_data.get("data", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("description", "") or item.get("content", ""),
                "source": "firecrawl",
            })

        return {
            "ok": True,
            "results": results,
            "total_estimate": len(results),
            "engine": "firecrawl",
        }

    # ── 自定义搜索引擎（通用 Handler） ──────────────────────────────

    def _search_custom(self, instance: dict, query: str, num_results: int = 10,
                       page: int = 1, **kwargs) -> dict:
        """通用自定义搜索引擎 handler

        根据 instance 配置动态构建请求并解析响应，支持任意兼容 RESTful API 的搜索引擎。
        instance 字典的字段参见 Task 1 数据模型。

        Args:
            instance: 搜索引擎实例配置字典
            query: 搜索关键词
            num_results: 返回结果数量
            page: 页码（部分自定义引擎支持）
            **kwargs: 额外参数

        Returns:
            dict: {ok, results: [{title, url, snippet, source}], engine, total_estimate}
        """
        if not instance.get('api_endpoint'):
            return {"ok": False, "error": "API 端点 URL 未配置"}

        # 1. 构建 URL
        url = instance['api_endpoint'].replace('{query}', quote_plus(query))
        if instance.get('http_method', 'GET') == 'GET' and instance.get('query_param'):
            # 如果 URL 中没有 {query}，追加查询参数
            if '{query}' not in instance['api_endpoint']:
                sep = '&' if '?' in url else '?'
                url += f"{sep}{instance['query_param']}={quote_plus(query)}"

        # 2. 构建请求头
        headers = {}
        api_key = instance.get('api_key', '')
        auth_template = instance.get('auth_header', '')
        if auth_template and api_key:
            header_str = auth_template.replace('{key}', api_key)
            if ': ' in header_str:
                name, value = header_str.split(': ', 1)
                headers[name.strip()] = value.strip()
            elif ' ' in header_str:
                # "Bearer {key}" 风格
                headers['Authorization'] = header_str.replace('{key}', api_key)
            else:
                headers[header_str] = api_key

        # 3. HTTP 请求
        if not self._http_client:
            return {"ok": False, "error": "HTTP 客户端未配置"}

        timeout = instance.get('timeout', 30)
        if instance.get('http_method', 'GET') == 'POST':
            result = self._http_client.post(url, headers=headers, timeout=timeout)
        else:
            result = self._http_client.get(url, headers=headers, timeout=timeout)

        if not result.get("ok"):
            return result

        # 4. 解析 JSON 响应
        try:
            data = result.get("text", "")
            if isinstance(data, str):
                json_data = json.loads(data)
            else:
                json_data = data
        except (json.JSONDecodeError, TypeError):
            return {"ok": False, "error": "解析 API 响应 JSON 失败"}

        # 5. 沿 results_path 取结果数组
        results_path = instance.get('results_path', '')
        raw_results = _json_get(json_data, results_path) if results_path else json_data
        if raw_results is None:
            raw_results = []
        if isinstance(raw_results, dict):
            raw_results = [raw_results]
        if not isinstance(raw_results, list):
            raw_results = []

        # 6. 提取标准化字段
        title_f = instance.get('title_field', 'title')
        url_f = instance.get('url_field', 'url')
        snippet_f = instance.get('snippet_field', 'snippet')

        results = []
        for item in raw_results[:num_results]:
            if not isinstance(item, dict):
                continue
            results.append({
                "title": item.get(title_f, '') or '',
                "url": item.get(url_f, '') or '',
                "snippet": item.get(snippet_f, '') or '',
                "source": instance.get('name', 'custom'),
            })

        return {
            "ok": True,
            "results": results,
            "total_estimate": len(raw_results),
            "engine": instance.get('name', 'custom'),
        }

    # ── Bing（需 API Key） ────────────────────────────────────────

    def _search_bing(self, query: str, num_results: int = 10, page: int = 1, **kwargs) -> dict:
        """Bing 网页搜索 API"""
        api_key = self._api_keys.get("bing") or self._config.get("bing_api_key", "")
        if not api_key:
            return {"ok": False, "error": "Bing API Key 未配置"}

        if not self._http_client:
            return {"ok": False, "error": "HTTP 客户端未配置"}

        offset = (page - 1) * num_results
        result = self._http_client.get(
            "https://api.bing.microsoft.com/v7.0/search",
            params={"q": query, "count": num_results, "offset": offset, "mkt": "zh-CN"},
            headers={"Ocp-Apim-Subscription-Key": api_key},
            timeout=self._timeout,
        )

        if not result.get("ok"):
            return result

        data = result.get("text", "")
        try:
            json_data = json.loads(data)
        except json.JSONDecodeError:
            return {"ok": False, "error": "解析 Bing API 响应失败"}

        web_pages = json_data.get("webPages", {})
        items = web_pages.get("value", [])

        results = [
            {
                "title": item.get("name", ""),
                "url": item.get("url", ""),
                "snippet": item.get("snippet", ""),
                "source": "bing",
            }
            for item in items
        ]

        return {
            "ok": True,
            "results": results,
            "total_estimate": web_pages.get("totalEstimatedMatches", len(results)),
            "engine": "bing",
        }

    # ── Google（需 API Key + CX） ─────────────────────────────────

    def _search_google(self, query: str, num_results: int = 10, page: int = 1, **kwargs) -> dict:
        """Google 自定义搜索 API"""
        api_key = self._api_keys.get("google") or self._config.get("google_api_key", "")
        cx = self._api_keys.get("google_cx") or self._config.get("google_cx", "")

        if not api_key or not cx:
            return {"ok": False, "error": "Google API Key 或 CX 未配置"}

        if not self._http_client:
            return {"ok": False, "error": "HTTP 客户端未配置"}

        start = (page - 1) * num_results + 1
        result = self._http_client.get(
            "https://www.googleapis.com/customsearch/v1",
            params={"key": api_key, "cx": cx, "q": query, "num": num_results, "start": start},
            timeout=self._timeout,
        )

        if not result.get("ok"):
            return result

        data = result.get("text", "")
        try:
            json_data = json.loads(data)
        except json.JSONDecodeError:
            return {"ok": False, "error": "解析 Google API 响应失败"}

        items = json_data.get("items", [])
        results = [
            {
                "title": item.get("title", ""),
                "url": item.get("link", ""),
                "snippet": item.get("snippet", ""),
                "source": "google",
            }
            for item in items
        ]

        return {
            "ok": True,
            "results": results,
            "total_estimate": json_data.get("searchInformation", {}).get("totalResults", len(results)),
            "engine": "google",
        }

    # ── Brave（需 API Key） ───────────────────────────────────────

    def _search_brave(self, query: str, num_results: int = 10, page: int = 1, **kwargs) -> dict:
        """Brave 搜索 API"""
        api_key = self._api_keys.get("brave") or self._config.get("brave_api_key", "")
        if not api_key:
            return {"ok": False, "error": "Brave API Key 未配置"}

        if not self._http_client:
            return {"ok": False, "error": "HTTP 客户端未配置"}

        offset = (page - 1) * num_results
        result = self._http_client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": num_results, "offset": offset},
            headers={"Accept": "application/json", "Accept-Encoding": "gzip", "X-Subscription-Token": api_key},
            timeout=self._timeout,
        )

        if not result.get("ok"):
            return result

        data = result.get("text", "")
        try:
            json_data = json.loads(data)
        except json.JSONDecodeError:
            return {"ok": False, "error": "解析 Brave API 响应失败"}

        web_results = json_data.get("web", {}).get("results", [])
        results = [
            {
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("description", ""),
                "source": "brave",
            }
            for item in web_results
        ]

        return {
            "ok": True,
            "results": results,
            "total_estimate": json_data.get("web", {}).get("totalEstimatedResults", len(results)),
            "engine": "brave",
        }

    # ── 百度（免 API Key，中文） ───────────────────────────────────

    def _search_baidu(self, query: str, num_results: int = 10, page: int = 1, **kwargs) -> dict:
        """百度 HTML 搜索"""
        if not self._http_client:
            return {"ok": False, "error": "HTTP 客户端未配置"}

        # 百度分页：pn = (page-1) * 10
        pn = max(0, (page - 1) * 10)
        result = self._http_client.get(
            "https://www.baidu.com/s",
            params={"wd": query, "pn": pn},
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
            timeout=self._timeout,
        )

        if not result.get("ok"):
            return result

        html = result.get("text", "")
        results = self._parse_baidu_html(html)

        return {
            "ok": True,
            "results": results[:num_results],
            "total_estimate": max(len(results), num_results),
            "source_url": result.get("url", ""),
            "engine": "baidu",
        }

    @staticmethod
    def _parse_baidu_html(html: str) -> List[Dict]:
        """解析百度 HTML 搜索结果"""
        results = []
        try:
            from lxml import html as lxml_html
            tree = lxml_html.fromstring(html)

            # 百度结果容器：<div class="result" 或 <div class="c-container"
            for item in tree.xpath('//div[contains(@class, "result")] | '
                                   '//div[contains(@class, "c-container")]'):
                # 标题链接
                title_link = item.xpath('.//h3[contains(@class, "t")]/a | '
                                        './/a[contains(@class, "title")]')
                if not title_link:
                    continue

                href = title_link[0].get("href", "")
                title = title_link[0].text_content().strip()

                # 摘要
                snippet_parts = item.xpath('.//div[contains(@class, "c-abstract")] | '
                                           './/span[contains(@class, "content-right")]')
                snippet = snippet_parts[0].text_content().strip() if snippet_parts else ""

                if title and href:
                    results.append({
                        "title": title,
                        "url": href,
                        "snippet": snippet,
                        "source": "baidu",
                    })
        except Exception as e:
            logger.warning("解析百度结果失败: %s", e)
            results = SearchEngine._parse_result_fallback(html)

        return results

    # ── 搜狗（免 API Key，中文） ───────────────────────────────────

    def _search_sogou(self, query: str, num_results: int = 10, page: int = 1, **kwargs) -> dict:
        """搜狗 HTML 搜索"""
        if not self._http_client:
            return {"ok": False, "error": "HTTP 客户端未配置"}

        result = self._http_client.get(
            "https://www.sogou.com/web",
            params={"query": query, "page": page},
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
            timeout=self._timeout,
        )

        if not result.get("ok"):
            return result

        html = result.get("text", "")
        results = self._parse_sogou_html(html)

        return {
            "ok": True,
            "results": results[:num_results],
            "total_estimate": max(len(results), num_results),
            "source_url": result.get("url", ""),
            "engine": "sogou",
        }

    @staticmethod
    def _parse_sogou_html(html: str) -> List[Dict]:
        """解析搜狗 HTML 搜索结果"""
        results = []
        try:
            from lxml import html as lxml_html
            tree = lxml_html.fromstring(html)

            # 搜狗结果项：<div class="vrwrap"> 或 <div class="rb">
            for item in tree.xpath('//div[contains(@class, "vrwrap")] | '
                                   '//div[contains(@class, "rb")]'):
                title_link = item.xpath('.//h3[contains(@class, "vr-title")]/a | '
                                        './/h3[@class="tit"]/a')
                if not title_link:
                    continue

                href = title_link[0].get("href", "")
                title = title_link[0].text_content().strip()

                # 摘要
                snippet_el = item.xpath('.//p[contains(@class, "str-info")] | '
                                        './/div[contains(@class, "star-wiki")] | '
                                        './/div[contains(@class, "str-text")]')
                snippet = snippet_el[0].text_content().strip() if snippet_el else ""

                if title and href:
                    results.append({
                        "title": title,
                        "url": href,
                        "snippet": snippet,
                        "source": "sogou",
                    })
        except Exception as e:
            logger.warning("解析搜狗结果失败: %s", e)
            results = SearchEngine._parse_result_fallback(html)

        return results

    # ── 360 搜索（免 API Key，中文） ──────────────────────────────

    def _search_so360(self, query: str, num_results: int = 10, page: int = 1, **kwargs) -> dict:
        """360 搜索（so.com）HTML 搜索"""
        if not self._http_client:
            return {"ok": False, "error": "HTTP 客户端未配置"}

        # 360 分页：pn = (page-1) * 10
        pn = max(1, (page - 1) * 10 + 1)
        result = self._http_client.get(
            "https://www.so.com/s",
            params={"q": query, "pn": pn},
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/120.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-CN,zh;q=0.9",
            },
            timeout=self._timeout,
        )

        if not result.get("ok"):
            return result

        html = result.get("text", "")
        results = self._parse_so360_html(html)

        return {
            "ok": True,
            "results": results[:num_results],
            "total_estimate": max(len(results), num_results),
            "source_url": result.get("url", ""),
            "engine": "so360",
        }

    @staticmethod
    def _parse_so360_html(html: str) -> List[Dict]:
        """解析 360 搜索 HTML 搜索结果"""
        results = []
        try:
            from lxml import html as lxml_html
            tree = lxml_html.fromstring(html)

            # 360 结果项：<li class="res-list">
            for item in tree.xpath('//li[contains(@class, "res-list")]'):
                title_link = item.xpath('.//h3[contains(@class, "res-title")]/a')
                if not title_link:
                    continue

                href = title_link[0].get("href", "")
                title = title_link[0].text_content().strip()

                # 摘要
                snippet_el = item.xpath('.//p[contains(@class, "res-desc")]')
                snippet = snippet_el[0].text_content().strip() if snippet_el else ""

                if title and href:
                    results.append({
                        "title": title,
                        "url": href,
                        "snippet": snippet,
                        "source": "so360",
                    })
        except Exception as e:
            logger.warning("解析360搜索失败: %s", e)
            results = SearchEngine._parse_result_fallback(html)

        return results

    # ── 批处理 ────────────────────────────────────────────────────

    def multi_search(self, queries: List[str], engine: str = "", **kwargs) -> List[dict]:
        """批量搜索多个关键词"""
        return [self.search(q, engine=engine, **kwargs) for q in queries]

    # ── 缓存 ──────────────────────────────────────────────────────

    def _check_cache(self, key: str) -> Optional[dict]:
        """检查是否有有效的缓存"""
        if key in self._cache:
            entry = self._cache[key]
            if time.time() - entry["time"] < self._cache_ttl:
                self._stats["cached_hits"] += 1
                return entry["data"]
        return None

    def _set_cache(self, key: str, data: dict):
        """设置缓存"""
        self._cache[key] = {"time": time.time(), "data": data}
        if len(self._cache) > 200:
            now = time.time()
            self._cache = {k: v for k, v in self._cache.items() if now - v["time"] < self._cache_ttl}

    def clear_cache(self):
        """清空搜索缓存"""
        self._cache.clear()

    # ── 工具 ──────────────────────────────────────────────────────

    @staticmethod
    def _parse_result_fallback(html: str) -> List[Dict]:
        """简易正则回退解析"""
        results = []
        for match in re.finditer(r'<a[^>]*href="(https?://[^"]+)"[^>]*>(.*?)</a>', html, re.DOTALL):
            title = re.sub(r"<[^>]+>", "", match.group(2)).strip()
            if title and len(title) > 2:
                results.append({
                    "title": title,
                    "url": match.group(1),
                    "snippet": "",
                    "source": "fallback",
                })
        return results[:20]

    def get_available_engines(self) -> List[Dict[str, Any]]:
        """获取所有已注册引擎的可用状态列表（动态生成）"""
        engines = []
        for name, info in self._engine_registry.items():
            if not info["needs_key"]:
                # 无需 API Key 的引擎始终可用
                configured = True
            elif name == "google":
                # Google 需要同时有 API Key 和 CX
                configured = bool(self._api_keys.get("google") and self._api_keys.get("google_cx"))
            else:
                configured = bool(self._api_keys.get(name, ""))
            engines.append({
                "name": name,
                "label": info["label"],
                "needs_key": info["needs_key"],
                "configured": configured,
                "enabled": self._engine_enabled.get(name, True),
            })
        return engines

    def get_stats(self) -> dict:
        """获取搜索统计"""
        return {
            **self._stats,
            "cache_size": len(self._cache),
            "engine_priority": self._engine_priority,
            "engine_enabled": self._engine_enabled,
        }

    def get_fallback_history(self, limit: int = 10) -> List[dict]:
        """获取最近的降级历史记录"""
        return self._fallback_history[-limit:]

    def get_current_status(self) -> dict:
        """获取当前搜索引擎状态（用于前端显示，动态生成）"""
        # 动态生成 api_keys_status
        api_keys_status = {}
        for name in self._engine_registry:
            if name == "google":
                api_keys_status["google"] = bool(self._api_keys.get("google"))
                api_keys_status["google_cx"] = bool(self._api_keys.get("google_cx"))
            elif self._engine_registry[name]["needs_key"]:
                api_keys_status[name] = bool(self._api_keys.get(name, ""))

        return {
            "default_engine": self._default_engine,
            "engine_priority": self._engine_priority,
            "engine_enabled": self._engine_enabled,
            "api_keys_status": api_keys_status,
            "timeout": self._timeout,
            "stats": {
                "total_searches": self._stats["searches"],
                "fallback_count": self._stats["fallback_count"],
                "engine_usage": self._stats["engine_usage"],
                "engine_timing": self._stats["engine_timing"],
                "cache_size": len(self._cache),
            },
            "recent_fallbacks": self.get_fallback_history(10),
        }

    def update_config(self, config: dict):
        """实时更新配置（无需重启）

        支持任意引擎的 API Key 动态更新，格式为 {engine_name}_api_key。
        特殊键：google_cx（直接键名）。
        """
        # 动态识别并更新 API Keys：遍历所有 *_api_key 结尾的配置键
        api_key_updated = []
        for key, value in config.items():
            if key.endswith("_api_key") and value:
                engine_name = key.replace("_api_key", "")
                if not key.startswith("_") and engine_name:
                    self._api_keys[engine_name] = value
                    api_key_updated.append(engine_name)
            elif key == "google_cx" and value:
                self._api_keys["google_cx"] = value
                api_key_updated.append("google_cx")

        if api_key_updated:
            logger.info("[搜索引擎] API Keys 已更新: %s", api_key_updated)

        if "engine_priority" in config:
            self.set_engine_priority(config["engine_priority"])
        if "engine_enabled" in config:
            for engine, enabled in config["engine_enabled"].items():
                self.set_engine_enabled(engine, enabled)
        if "timeout" in config:
            self.set_timeout(config["timeout"])
        if "default_engine" in config:
            self._default_engine = config["default_engine"]

        logger.info("[搜索引擎] 配置已实时更新")
