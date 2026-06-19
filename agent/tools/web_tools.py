"""工具注册模块 — 互联网工具（HTTP请求、搜索、爬取、批量）"""
import logging
from agent import tools as _tools

logger = logging.getLogger(__name__)


def register_all(dl):
    """注册所有互联网工具

    Args:
        dl: DigitalLife 实例（用于访问 self 属性）
    """

    # ════════════════════════════════════════════════════════════
    #  网络模块初始化
    # ════════════════════════════════════════════════════════════

    from agent.web import HttpClient, Scraper, SearchEngine, DataProcessor, CrawlerController

    # 读取网络配置
    network_config = {}
    try:
        from agent.network_config import NetworkConfigManager
        config_manager = NetworkConfigManager()
        network_config = config_manager.get_raw_config()
        logger.info("[网络] 已加载网络配置")
    except Exception as e:
        logger.warning("[网络] 加载网络配置失败，使用默认配置: %s", e)

    # 获取网络配置参数
    net_cfg = network_config.get("network", {})
    search_cfg = network_config.get("search", {})
    scrape_cfg = network_config.get("web_scraping", {})

    dl._web_http = HttpClient({
        "timeout": net_cfg.get("timeout", 30),
        "max_retries": net_cfg.get("max_retries", 3),
        "backoff_factor": net_cfg.get("backoff_factor", 0.5),
        "proxy": net_cfg.get("proxy_url") if net_cfg.get("proxy_enabled") else None,
    })
    dl._web_scraper = Scraper(dl._web_http)

    # 初始化搜索引擎，使用配置中的完整设置
    search_api_keys = network_config.get("search_api_keys", {})
    search_engine_config = {
        "default_engine": search_cfg.get("default_engine", "sogou"),
        "cache_ttl": search_cfg.get("cache_ttl", 300),
        "timeout": search_cfg.get("timeout", 30),
        "engine_priority": search_cfg.get("engine_priority", ["tavily", "firecrawl", "sogou", "baidu", "so360", "duckduckgo"]),
        "engine_enabled": search_cfg.get("engine_enabled", {
            "tavily": True,
            "firecrawl": True,
            "sogou": True,
            "baidu": True,
            "so360": True,
            "duckduckgo": True,
            "bing": True,
            "google": True,
            "brave": True,
        }),
        # API Keys
        "tavily_api_key": search_api_keys.get("tavily", ""),
        "firecrawl_api_key": search_api_keys.get("firecrawl", ""),
        "bing_api_key": search_api_keys.get("bing", ""),
        "google_api_key": search_api_keys.get("google", ""),
        "google_cx": search_api_keys.get("google_cx", ""),
        "brave_api_key": search_api_keys.get("brave", ""),
    }
    dl._search_engine_config = search_engine_config  # 保存配置供延迟初始化
    dl._web_search = None  # 延迟初始化，首次搜索时才创建
    logger.info("[ok] 搜索引擎配置已保存（延迟初始化）: 默认引擎=%s, 优先级=%s",
               search_cfg.get("default_engine", "duckduckgo"),
               search_cfg.get("engine_priority", ["duckduckgo", "tavily"]))

    dl._web_processor = DataProcessor()
    dl._web_aggregator = None  # 聚合搜索器，按需懒加载
    dl._web_crawler = CrawlerController({
        "default_delay": scrape_cfg.get("delay_between_requests", 1.0),
        "respect_robots_txt": scrape_cfg.get("respect_robots_txt", True),
    })

    logger.info("[ok] 网络模块已激活（搜索引擎: %s）", search_cfg.get("default_engine", "duckduckgo"))

    # ════════════════════════════════════════════════════════════
    #  HTTP 请求工具
    # ════════════════════════════════════════════════════════════

    @_tools.register("web_get", "发送 HTTP GET 请求获取网页内容。返回页面标题、文本、链接等结构化信息", schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "请求的 URL"},
            "timeout": {"type": "integer", "description": "超时秒数，默认 30"},
            "headers": {"type": "object", "description": "自定义请求头"},
        },
        "required": ["url"],
    })
    def _web_get(**kwargs):
        url = kwargs.get("url", "")
        timeout = kwargs.get("timeout", 30)
        headers = kwargs.get("headers", {})
        if not url:
            return {"ok": False, "error": "请提供 URL"}
        result = dl._web_http.get(url, timeout=timeout, headers=headers or None)
        if result.get("ok") and result.get("text"):
            # 同时返回解析后的结构化信息
            parsed = dl._web_scraper.parse(result["text"], url=result.get("url", url))
            result["parsed"] = {k: parsed.get(k) for k in ("title", "text", "links", "images", "meta", "headings") if k != "html"}
        return result

    @_tools.register("web_post", "发送 HTTP POST 请求，支持表单数据和 JSON 数据", schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "请求的 URL"},
            "data": {"type": "object", "description": "表单数据"},
            "json_data": {"type": "object", "description": "JSON 数据"},
            "timeout": {"type": "integer", "description": "超时秒数，默认 30"},
        },
        "required": ["url"],
    })
    def _web_post(**kwargs):
        url = kwargs.get("url", "")
        data = kwargs.get("data", {})
        json_data = kwargs.get("json_data", {})
        timeout = kwargs.get("timeout", 30)
        if not url:
            return {"ok": False, "error": "请提供 URL"}
        if json_data:
            return dl._web_http.post(url, json_data=json_data, timeout=timeout)
        return dl._web_http.post(url, data=data, timeout=timeout)

    # ════════════════════════════════════════════════════════════
    #  数据提取工具（XPath / CSS Selector）
    # ════════════════════════════════════════════════════════════

    @_tools.register("web_xpath", "使用 XPath 表达式从网页中提取信息", schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "网页 URL"},
            "expression": {"type": "string", "description": "XPath 表达式"},
            "html": {"type": "string", "description": "直接提供 HTML 源码（替代 url）"},
        },
        "required": ["expression"],
    })
    def _web_xpath(**kwargs):
        url = kwargs.get("url", "")
        expression = kwargs.get("expression", "")
        html = kwargs.get("html", "")
        if not expression:
            return {"ok": False, "error": "请提供 XPath 表达式"}
        if html:
            results = dl._web_scraper.xpath(expression, html=html)
            return {"ok": True, "results": results, "count": len(results)}
        if not url:
            return {"ok": False, "error": "请提供 URL 或 HTML 源码"}
        # 先获取页面
        fetch_result = dl._web_http.get(url)
        if not fetch_result.get("ok"):
            return fetch_result
        results = dl._web_scraper.xpath(expression, html=fetch_result.get("text", ""))
        return {"ok": True, "url": url, "results": results, "count": len(results)}

    @_tools.register("web_css", "使用 CSS 选择器从网页中提取信息", schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "网页 URL"},
            "selector": {"type": "string", "description": "CSS 选择器"},
            "attr": {"type": "string", "description": "提取的属性名，如 href、src"},
            "html": {"type": "string", "description": "直接提供 HTML 源码（替代 url）"},
        },
        "required": ["selector"],
    })
    def _web_css(**kwargs):
        url = kwargs.get("url", "")
        selector = kwargs.get("selector", "")
        attr = kwargs.get("attr", "")
        html = kwargs.get("html", "")
        if not selector:
            return {"ok": False, "error": "请提供 CSS 选择器"}
        if html:
            results = dl._web_scraper.css(selector, html=html, attr=attr or None)
            return {"ok": True, "results": results, "count": len(results)}
        if not url:
            return {"ok": False, "error": "请提供 URL 或 HTML 源码"}
        fetch_result = dl._web_http.get(url)
        if not fetch_result.get("ok"):
            return fetch_result
        results = dl._web_scraper.css(selector, html=fetch_result.get("text", ""), attr=attr or None)
        return {"ok": True, "url": url, "results": results, "count": len(results)}

    # ════════════════════════════════════════════════════════════
    #  搜索工具
    # ════════════════════════════════════════════════════════════

    @_tools.register("web_search", "搜索互联网信息。默认单引擎搜索，设置 aggregate=true 启用多引擎聚合：并发调用 2-3 个搜索引擎，去重评分排序后返回最优结果（质量更高但稍慢）", schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词（必填）"},
            "engine": {"type": "string", "description": "指定搜索引擎名称（可选）。不指定按优先级自动选择。注意：aggregate=true 时此参数被忽略"},
            "num_results": {"type": "integer", "description": "期望返回的结果数量，默认 10，最大 50"},
            "page": {"type": "integer", "description": "页码（仅单引擎模式有效），默认 1"},
            "aggregate": {"type": "boolean", "description": "启用多引擎聚合搜索模式。true=并发多引擎去重评分排序（质量更高），false=单引擎快速搜索（默认）"},
        },
        "required": ["query"],
    })
    def _web_search(**kwargs):
        query = kwargs.get("query", "")
        engine = kwargs.get("engine", "")
        num_results = kwargs.get("num_results", 10)
        page = kwargs.get("page", 1)
        aggregate = kwargs.get("aggregate", False)
        if not query:
            return {"ok": False, "error": "请提供搜索关键词"}

        # 定期检查引擎健康状态（每 5 分钟重试失败引擎）
        dl._check_engine_health()

        # ── 聚合搜索模式 ──
        if aggregate:
            if dl._web_aggregator is None:
                from agent.search_aggregator import SearchAggregator
                dl._web_aggregator = SearchAggregator(dl._get_web_search())
            result = dl._web_aggregator.aggregate_search(
                query, num_results=num_results, timeout=15.0
            )
            # 截断过长内容以控制 token 消耗
            if result.get("ok") and result.get("results"):
                pre_count = len(result["results"])
                for item in result["results"]:
                    snippet_max = 300 if num_results and num_results >= 5 else 150
                    if len(item.get("snippet", "")) > snippet_max:
                        item["snippet"] = item["snippet"][:snippet_max] + "…"
                    if len(item.get("title", "")) > 80:
                        item["title"] = item["title"][:80] + "…"
                # 按 token 估算控制返回量
                max_results_by_token = min(len(result["results"]), 8)
                result["results"] = result["results"][:max_results_by_token]
                result["_was_truncated"] = pre_count > len(result["results"])
            return result

        # ── 单引擎搜索模式（原有逻辑） ──
        # 根据 num_results 参数动态调整请求量，确保够用但不浪费
        fetch_count = min((num_results or 10) + 2, 12)
        result = dl._get_web_search().search(query, engine=engine, num_results=fetch_count, page=page)
        # 引擎健康追踪：如果搜索失败且指定了引擎，标记为不健康
        if not result.get("ok") and engine:
            dl._mark_engine_unhealthy(engine)
        if result.get("ok") and result.get("results"):
            # 使用数据处理器过滤和评分
            processed = dl._web_processor.process(result["results"])
            # 截断过长内容以控制 token 消耗
            for item in processed:
                snippet_max = 300 if num_results and num_results >= 5 else 150
                if len(item.get("snippet", "")) > snippet_max:
                    item["snippet"] = item["snippet"][:snippet_max] + "…"
                if len(item.get("title", "")) > 80:
                    item["title"] = item["title"][:80] + "…"
            # 按 token 估算控制返回量：每条平均约 200 token，上下文最多保留 4000 token
            max_results_by_token = min(len(processed), 8)
            result["results"] = processed[:max_results_by_token]
            result["total_found"] = len(processed)
            result["summary"] = DataProcessor.summarize_results(processed)
        # 确保返回给模型的内容不会过大
        if isinstance(result, dict) and "results" in result:
            total_found = result.get("total_found", len(result.get("results", [])))
            result["_was_truncated"] = total_found > len(result.get("results", []))
        return result

    # ════════════════════════════════════════════════════════════
    #  数据清洗 / 下载 / 批量请求
    # ════════════════════════════════════════════════════════════

    @_tools.register("web_clean_data", "清洗和结构化网页文本数据，去重、评分、去除跟踪参数", schema={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "待清洗的文本"},
            "items": {"type": "array", "description": "待处理的数据项列表"},
        },
    })
    def _web_clean_data(**kwargs):
        text = kwargs.get("text", "")
        _items = kwargs.get("items", [])
        if text:
            return {"ok": True, "cleaned": DataProcessor.clean_text(text)}
        if _items:
            processed = dl._web_processor.process(_items)
            return {"ok": True, "original_count": len(_items), "processed_count": len(processed), "results": processed}
        return {"ok": False, "error": "请提供 text 或 items 参数"}

    @_tools.register("web_download", "从 URL 下载文件到本地", schema={
        "type": "object",
        "properties": {
            "url": {"type": "string", "description": "文件的 URL"},
            "filepath": {"type": "string", "description": "本地保存路径"},
        },
        "required": ["url", "filepath"],
    })
    def _web_download(**kwargs):
        url = kwargs.get("url", "")
        filepath = kwargs.get("filepath", "")
        if not url:
            return {"ok": False, "error": "请提供 URL"}
        if not filepath:
            return {"ok": False, "error": "请提供本地保存路径 (filepath)"}
        return dl._web_http.download(url, filepath)

    @_tools.register("web_batch", "批量请求多个 URL", schema={
        "type": "object",
        "properties": {
            "urls": {"type": "array", "items": {"type": "string"}, "description": "URL 列表"},
            "max_concurrency": {"type": "integer", "description": "最大并发数，默认 5"},
        },
        "required": ["urls"],
    })
    def _web_batch(**kwargs):
        urls = kwargs.get("urls", [])
        max_concurrency = kwargs.get("max_concurrency", 5)
        if not urls:
            return {"ok": False, "error": "请提供 URL 列表 (urls)"}
        results = dl._web_http.batch_request(urls, max_concurrency=max_concurrency)
        return {"ok": True, "total": len(results), "results": results}
