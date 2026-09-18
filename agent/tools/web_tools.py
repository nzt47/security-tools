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

    @_tools.register("web_get", "发送 HTTP GET 请求获取网页内容。返回页面标题、文本、链接等结构化信息。Fetch a web page by URL, get page content via HTTP GET", schema={
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
    #  数据提取工具（XPath / CSS Selector / 文本清洗）
    #
    #  合并说明（docs/工具集评估与重分类报告.md §6.2 第 0 档 1+2 组）：
    #  web_xpath 与 web_css 的处理体逐行同构，web_clean_data 只是把
    #  DataProcessor.clean_text / _web_processor.process 外露，故三者
    #  合并为 web_extract(kind=...)。参数逐项保留，无能力损失：
    #    web_xpath.expression → selector（兼容别名 expression 亦可）
    #    web_xpath.url/html   → url/html
    #    web_css.selector/url/html → selector/url/html
    #    web_css.attr         → attr（仅 kind='css' 有效）
    #    web_clean_data.text  → html（兼容别名 text 亦可）
    #    web_clean_data.items → items
    #  新增 max_items（截断）与 aggressive（kind='clean' 的加强清洗）。
    # ════════════════════════════════════════════════════════════

    @_tools.register("web_extract", "从网页或 HTML 源码中抽取结构化内容。kind='xpath' 用 XPath 表达式提取；kind='css' 用 CSS 选择器提取（可用 attr 取 href、src 等属性）；kind='clean' 清洗文本（去 HTML 标签、实体解码、压缩空白）。可传 url 抓取，也可直接传 html 或 text。Extract data from HTML by XPath or CSS selector, extract attributes, clean text", schema={
        "type": "object",
        "properties": {
            "kind": {"type": "string", "enum": ["xpath", "css", "clean"], "description": "提取方式（必填）。xpath=XPath 表达式提取；css=CSS 选择器提取；clean=文本清洗"},
            "selector": {"type": "string", "description": "选择器：kind='xpath' 传 XPath 表达式，kind='css' 传 CSS 选择器"},
            "html": {"type": "string", "description": "直接提供 HTML 源码（替代 url）；kind='clean' 时作为待清洗文本"},
            "url": {"type": "string", "description": "网页 URL（未提供 html 时抓取该页面）"},
            "max_items": {"type": "integer", "description": "返回结果条数上限，默认不限"},
            "aggressive": {"type": "boolean", "description": "kind='clean' 时启用加强清洗：额外做单行化与重复行去除，默认 false"},
            "attr": {"type": "string", "description": "kind='css' 时提取的属性名，如 href、src；不传则提取文本"},
            "expression": {"type": "string", "description": "XPath 表达式（selector 的兼容别名）"},
            "text": {"type": "string", "description": "待清洗文本（kind='clean' 下 html 的兼容别名）"},
            "items": {"type": "array", "description": "kind='clean' 时的数据项列表，走去重、评分、清洗管线"},
        },
        "required": ["kind"],
    })
    def _web_extract(**kwargs):
        kind = (kwargs.get("kind") or "").strip().lower()
        html = kwargs.get("html") or kwargs.get("text") or ""
        url = kwargs.get("url", "")
        # selector 为主参数，expression 是 web_xpath 时代的兼容别名
        selector = kwargs.get("selector") or kwargs.get("expression") or ""
        attr = kwargs.get("attr", "")
        max_items = kwargs.get("max_items")
        aggressive = bool(kwargs.get("aggressive", False))
        _items = kwargs.get("items") or []

        if kind not in ("xpath", "css", "clean"):
            return {"ok": False, "error": f"kind 必须为 'xpath'、'css' 或 'clean' 之一，收到: {kwargs.get('kind')!r}"}

        # ── 文本清洗模式（原 web_clean_data） ──
        if kind == "clean":
            if _items:
                processed = dl._web_processor.process(_items)
                return {"ok": True, "results": processed, "count": len(processed),
                        "original_count": len(_items), "processed_count": len(processed)}
            if not html:
                return {"ok": False, "error": "kind='clean' 需要 text、html 或 items 参数"}
            cleaned = DataProcessor.clean_text(html)
            if aggressive:
                cleaned = _aggressive_clean(cleaned)
            return {"ok": True, "results": [cleaned], "count": 1, "text": cleaned}

        # ── 选择器提取模式（原 web_xpath / web_css） ──
        if not selector:
            lang = "XPath 表达式" if kind == "xpath" else "CSS 选择器"
            return {"ok": False, "error": f"kind='{kind}' 需要 selector 参数（{lang}）"}

        if html:
            results = _limit_results(_extract_by_kind(dl, kind, selector, html, attr), max_items)
            return {"ok": True, "results": results, "count": len(results)}

        if not url:
            return {"ok": False, "error": "请提供 url 或 html（text）"}

        # 先获取页面
        fetch_result = dl._web_http.get(url)
        if not fetch_result.get("ok"):
            return fetch_result
        results = _limit_results(
            _extract_by_kind(dl, kind, selector, fetch_result.get("text", ""), attr), max_items)
        return {"ok": True, "url": url, "results": results, "count": len(results)}

    # ════════════════════════════════════════════════════════════
    #  搜索工具
    # ════════════════════════════════════════════════════════════

    @_tools.register("web_search", "搜索互联网信息。默认单引擎搜索，设置 aggregate=true 启用多引擎聚合：并发调用 2-3 个搜索引擎，去重评分排序后返回最优结果（质量更高但稍慢）。preset='news' 进入新闻模式：用多条英文查询检索国际新闻，按来源（BBC、CNN、Reuters、AP 等）优先排序，query 作为新闻主题（留空取综合新闻），返回摘要式结果与发布时间。Search the web, find information online, internet search, latest news headlines", schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词（必填）"},
            "engine": {"type": "string", "description": "指定搜索引擎名称（可选）。不指定按优先级自动选择。注意：aggregate=true 时此参数被忽略"},
            "num_results": {"type": "integer", "description": "期望返回的结果数量，默认 10，最大 50；preset='news' 时上限 15"},
            "page": {"type": "integer", "description": "页码（仅单引擎模式有效），默认 1"},
            "aggregate": {"type": "boolean", "description": "启用多引擎聚合搜索模式。true=并发多引擎去重评分排序（质量更高），false=单引擎快速搜索（默认）。preset='news' 时忽略"},
            "preset": {"type": "string", "enum": ["default", "news"], "description": "检索预设。default=常规网页搜索（默认）；news=新闻模式，用多条英文查询检索国际新闻并按来源（BBC、CNN、Reuters、AP 等）优先排序，query 作为新闻主题（留空取综合新闻）"},
        },
        "required": ["query"],
    })
    def _web_search(**kwargs):
        query = kwargs.get("query", "")
        engine = kwargs.get("engine", "")
        num_results = kwargs.get("num_results", 10)
        page = kwargs.get("page", 1)
        aggregate = kwargs.get("aggregate", False)
        preset = kwargs.get("preset") or "default"
        if preset not in ("default", "news"):
            return {"ok": False, "error": f"preset 必须为 'default' 或 'news'，收到: {preset!r}"}

        # ── 新闻模式（原 fetch_news：多查询 + 来源优先排序） ──
        if preset == "news":
            return _news_search(dl, query, engine, num_results)

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
    #  下载 / 批量请求（文本清洗已并入 web_extract(kind='clean')）
    # ════════════════════════════════════════════════════════════

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

    # ════════════════════════════════════════════════════════════
    #  新闻获取（已并入 web_search(preset="news")，见模块级 _news_search）
    # ════════════════════════════════════════════════════════════


def _extract_by_kind(dl, kind: str, selector: str, html: str, attr: str):
    """按 kind 分派到 Scraper 的对应提取方法"""
    if kind == "xpath":
        return dl._web_scraper.xpath(selector, html=html)
    return dl._web_scraper.css(selector, html=html, attr=attr or None)


def _limit_results(results, max_items):
    """截断结果列表（max_items 非法或为空时不截断）"""
    if max_items is None:
        return results
    try:
        limit = int(max_items)
    except (TypeError, ValueError):
        return results
    if limit <= 0:
        return results
    return results[:limit]


def _aggressive_clean(text: str) -> str:
    """加强清洗：单行化（连续空白压成一个空格）并去掉重复行

    用于把整页文本压成便于塞进上下文的一行/数行，
    是 web_extract(aggressive=True) 相对 DataProcessor.clean_text 的增量。
    """
    import re
    if not text:
        return ""
    text = re.sub(r"[\u200b-\u200f\u2028\u2029\ufeff]", "", text)
    seen = set()
    kept = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line in seen:
            continue
        seen.add(line)
        kept.append(line)
    return re.sub(r"\s+", " ", " ".join(kept)).strip()


# 新闻来源优先级（索引越小越靠前）——原 fetch_news 的排序逻辑
_NEWS_PREFERRED = ["bbc.com", "cnn.com", "reuters.com", "apnews.com",
                   "theguardian.com", "nytimes.com", "wsj.com", "economist.com"]

# 文章发布时间的候选字段（不同引擎命名不一）
_NEWS_DATE_FIELDS = ("published_date", "published_at", "published",
                     "pub_date", "date", "publishedTime", "timestamp", "time", "age")

# 通用新闻查询（topic 留空时使用），与原 fetch_news 逐字一致
_NEWS_DEFAULT_QUERIES = ["latest world news today",
                         "international breaking news",
                         "top global headlines"]

#: 新闻模式多条查询的**总时间预算**（秒）——防止把 3 × 单查询超时叠成 30s 的等待
_NEWS_TOTAL_BUDGET_SEC = 15.0


def _news_published(item: dict) -> str:
    """取文章发布时间；取不到返回空串

    注意：没有发布时间时**不伪造**（原实现给每条结果盖了运行时刻 now，
    那是"看起来有时间"的假信息）。调用方会把空值显示为 unknown。
    """
    for key in _NEWS_DATE_FIELDS:
        value = item.get(key)
        if value:
            return str(value).strip()
    return ""


def _news_search(dl, topic: str, engine: str, max_results) -> dict:
    """新闻模式（原 fetch_news 的逻辑，合并进 web_search(preset="news")）

    【2026-09-18 行为变更：多条查询**结果合并**】
    原实现逐条尝试 3 条查询，但**第一条成功就 break**（"首个成功查询即采用"），
    于是另外两条查询形同虚设：只要第一条返回哪怕 1 条结果，最终结果集就只有它那一条
    （`seen_urls` 去重也因此毫无意义）。
    现将 3 条查询的结果**合并成一个结果集**（按 url 去重 → 来源优先级排序 → 截断），
    这样"某条查询召回偏少/偏旧"不再直接决定最终质量。保留的边界：
      - 查询**按顺序**执行，累计到 `limit` 条即提前停止（够一页就不再打多余的请求）；
      - 总耗时超过 `_NEWS_TOTAL_BUDGET_SEC` 即停止后续查询（避免 3×超时叠加）；
      - 单条查询失败照旧被吞掉、不影响整体；
      - 返回里如实披露 `queries_used`，让"这次到底用了几条查询"可查（不猜）。
    排序、截断、发布时间口径（缺失标 unknown、不伪造）与既有实现一致。
    """
    import time as _time

    start = _time.monotonic()

    try:
        limit = min(int(max_results or 10), 15)  # 新闻模式上限与原 max_results 上限一致
    except (TypeError, ValueError):
        limit = 10

    queries = _NEWS_DEFAULT_QUERIES
    if topic:
        queries = [f"latest {topic} news", f"{topic} breaking news", f"{topic} today"]

    all_results = []
    seen_urls = set()
    queries_used = []      # 本次**实际发起**的查询（含无结果/失败的，如实披露）
    queries_failed = []    # 其中抛异常的（引擎不可用等），供排查"为什么结果少"
    for q in queries:
        if len(all_results) >= limit:
            break                                   # 已够一页，不再打多余请求
        if _time.monotonic() - start > _NEWS_TOTAL_BUDGET_SEC:
            logger.info("[web_search] 新闻查询达到总预算 %.0fs，剩余查询跳过: %s",
                        _NEWS_TOTAL_BUDGET_SEC, q)
            break
        queries_used.append(q)
        try:
            searcher = dl._get_web_search()
            if searcher is None:
                continue
            # engine 留空即按优先级自动选择（与原实现相同）；显式传入时原样透传
            res = searcher.search(q, engine=engine, num_results=limit, timeout=10)
            if res and isinstance(res, dict) and res.get("ok") and res.get("results"):
                for item in res["results"]:
                    url = (item.get("url") or "").strip()
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        all_results.append({
                            "title": (item.get("title") or "").strip(),
                            "url": url,
                            "snippet": (item.get("snippet") or "").strip(),
                            "source": _guess_source(url),
                            "published": _news_published(item),
                        })
            # 不再 break：继续下一条查询并合并（本次改动的核心）
        except Exception as e:  # noqa: BLE001 单个查询失败不影响整体
            queries_failed.append(q)
            logger.debug("[web_search] 新闻查询失败（跳过并继续）q=%r: %s: %s",
                         q, type(e).__name__, e)

    def _score(item):
        url = item["url"].lower()
        for i, domain in enumerate(_NEWS_PREFERRED):
            if domain in url:
                return i
        return len(_NEWS_PREFERRED)

    all_results.sort(key=_score)
    all_results = all_results[:limit]

    # 使用搜索结果摘要（跳过正文获取避免超时）
    for item in all_results:
        item["content"] = item.get("snippet", "")

    # 无结果时返回友好提示（这里的时刻是"检索时刻"，不是文章发布时间）
    if not all_results:
        now = _time.strftime("%Y-%m-%d %H:%M UTC")
        text = (
            "已获取到以下信息：\n"
            "  - 当前暂无搜索结果，搜索引擎暂时不可用。\n"
            f"  - 检索时间: {now}\n"
            "  - 建议: 稍后重试或直接输入具体关键词"
        )
        return {"ok": True, "result": text, "count": 0, "preset": "news",
                "queries_used": queries_used, "queries_failed": queries_failed}

    # 格式化输出（时间取文章发布时间，缺失则 unknown —— 不伪造）
    lines = ["已获取到以下信息：", f"  - 找到 {len(all_results)} 条结果:"]
    for i, item in enumerate(all_results, 1):
        title = item.get("title", "无标题")
        source = item.get("source", "未知来源")
        url = item.get("url", "")
        snippet = item.get("content") or item.get("snippet", "")
        published = item.get("published") or "unknown"
        lines.append("")
        lines.append(f"...{i}. **{title}**")
        lines.append(f"   - 来源: {source}")
        lines.append(f"   - 时间: {published}")
        lines.append(f"   - 摘要: {snippet[:300]}")
        lines.append(f"   - 链接: {url}")

    return {"ok": True, "result": "\n".join(lines), "count": len(all_results),
            "preset": "news", "queries_used": queries_used,
            "queries_failed": queries_failed}


def _guess_source(url: str) -> str:
    """从URL猜测新闻来源"""
    url_lower = url.lower()
    sources = {
        "bbc.com": "BBC", "bbc.co.uk": "BBC",
        "cnn.com": "CNN",
        "reuters.com": "Reuters",
        "apnews.com": "AP News",
        "theguardian.com": "The Guardian",
        "nytimes.com": "New York Times",
        "wsj.com": "Wall Street Journal",
        "economist.com": "The Economist",
        "bloomberg.com": "Bloomberg",
        "aljazeera.com": "Al Jazeera",
        "npr.org": "NPR",
        "foxnews.com": "Fox News",
        "usatoday.com": "USA Today",
        "time.com": "Time",
        "washingtonpost.com": "Washington Post",
        "reuters.com": "Reuters",
        "yahoo.com": "Yahoo News",
        "google.com": "Google News",
        "sohu.com": "搜狐新闻",
        "sina.com": "新浪新闻",
        "163.com": "网易新闻",
        "thepaper.cn": "澎湃新闻",
        "xinhuanet.com": "新华网",
        "people.com.cn": "人民网",
    }
    for domain, name in sources.items():
        if domain in url_lower:
            return name
    return "新闻媒体"


def _extract_relevant(text: str, max_len: int = 300) -> str:
    """从网页文本中提取最相关的内容段落"""
    import re
    # 移除HTML标签
    clean = re.sub(r'<[^>]+>', '', text)
    # 移除多余空白
    clean = re.sub(r'\s+', ' ', clean).strip()
    if len(clean) <= max_len:
        return clean
    # 尝试在句号处截断
    truncated = clean[:max_len]
    last_period = truncated.rfind('。')
    if last_period > max_len * 0.5:
        return truncated[:last_period + 1]
    last_dot = truncated.rfind('.')
    if last_dot > max_len * 0.5:
        return truncated[:last_dot + 1]
    return truncated + "..."
