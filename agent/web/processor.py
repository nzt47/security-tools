"""
数据处理与过滤系统 — 清洗、去重、评分、结构化

多层数据管线：原始数据 → 清洗 → 去重 → 质量评分 → 结构化输出。
"""

import re
import json
import logging
from typing import Optional, List, Dict, Any, Set
from urllib.parse import urlparse
from collections import Counter

logger = logging.getLogger(__name__)


class DataProcessor:
    """数据处理流水线 — 清洗、过滤、结构化

    管线阶段：
    1. 清洗：移除噪音、HTML 标签、格式标准化
    2. 去重：URL 归一化 + 内容指纹去重
    3. 质量评分：内容长度、信息密度、来源可信度
    4. 输出：结构化结果
    """

    def __init__(self, config: Optional[dict] = None):
        cfg = config or {}
        self._min_content_length = cfg.get("min_content_length", 50)
        self._max_content_length = cfg.get("max_content_length", 100000)
        self._min_quality_score = cfg.get("min_quality_score", 0.0)
        self._stats = {
            "processed": 0,
            "dedup_removed": 0,
            "quality_filtered": 0,
            "output": 0,
        }

    # ── 主处理管线 ────────────────────────────────────────────────

    def process(self, items: List[dict], **options) -> List[dict]:
        """全流程处理数据项列表

        Args:
            items: 原始数据项列表（每个至少含 url 或 content）
            **options: 管线配置
                dedup: 是否去重（默认 True）
                quality_filter: 是否质量过滤（默认 True）
                clean: 是否清洗（默认 True）

        Returns:
            List[dict]: 处理后的数据项
        """
        if not items:
            return []

        dedup = options.get("dedup", True)
        quality_filter = options.get("quality_filter", True)
        clean_text = options.get("clean", True)

        self._stats["processed"] += len(items)
        seen: Set[str] = set()
        results = []

        for item in items:
            # 清洗
            if clean_text:
                item = self.clean_item(item)

            # 校验
            if not self._validate_item(item):
                continue

            # 去重
            if dedup:
                fingerprint = self._fingerprint(item)
                if fingerprint in seen:
                    self._stats["dedup_removed"] += 1
                    continue
                seen.add(fingerprint)

            # 质量评分
            if quality_filter:
                score = self.score_item(item)
                item["_quality_score"] = score
                if score < self._min_quality_score:
                    self._stats["quality_filtered"] += 1
                    continue

            results.append(item)

        self._stats["output"] = len(results)
        return results

    # ── 数据清洗 ──────────────────────────────────────────────────

    @staticmethod
    def clean_item(item: dict) -> dict:
        """清洗单条数据项"""
        cleaned = dict(item)

        # 文本清洗
        for field in ("title", "snippet", "content", "text"):
            if field in cleaned and isinstance(cleaned[field], str):
                cleaned[field] = DataProcessor.clean_text(cleaned[field])

        # URL 清洗
        if "url" in cleaned and isinstance(cleaned["url"], str):
            cleaned["url"] = DataProcessor.clean_url(cleaned["url"])

        return cleaned

    @staticmethod
    def clean_text(text: str) -> str:
        """清洗文本内容

        操作：
        - 移除 HTML 标签
        - 移除多余空白
        - 移除不可见 Unicode 字符
        - 实体解码
        - 缩进统一
        """
        if not text:
            return ""

        # HTML 标签
        text = re.sub(r"<[^>]+>", " ", text)

        # HTML 实体
        try:
            from html import unescape
            text = unescape(text)
        except Exception:
            pass

        # 不可见字符（保留换行和制表符）
        text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏ - ﻿]", "", text)

        # 合并空白
        text = re.sub(r" {2,}", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)

        return text.strip()

    @staticmethod
    def clean_url(url: str) -> str:
        """清洗和归一化 URL

        操作：
        - 移除跟踪参数（utm_*, fbclid, gclid 等）
        - 移除锚点
        - 解码百分号编码
        """
        from urllib.parse import urlparse, urlunparse, parse_qs, urlencode

        try:
            parsed = urlparse(url)

            # 移除跟踪参数
            track_params = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
                            "fbclid", "gclid", "gclsrc", "dclid", "msclkid",
                            "ref", "ref_src", "ref_url", "source", "si"}

            query_params = parse_qs(parsed.query, keep_blank_values=True)
            cleaned_params = {k: v for k, v in query_params.items() if k.lower() not in track_params}

            new_query = urlencode(cleaned_params, doseq=True) if cleaned_params else ""
            return urlunparse(parsed._replace(query=new_query, fragment=""))
        except Exception:
            return url.split("#")[0]

    @staticmethod
    def extract_domain(url: str) -> str:
        """从 URL 中提取域名"""
        try:
            return urlparse(url).netloc.lower()
        except Exception:
            return ""

    # ── 校验 ──────────────────────────────────────────────────────

    def _validate_item(self, item: dict) -> bool:
        """验证数据项是否有效"""
        content = item.get("content") or item.get("text") or item.get("snippet") or ""
        if len(content.strip()) < self._min_content_length:
            return False
        if len(content) > self._max_content_length:
            return False
        return True

    # ── 去重 ──────────────────────────────────────────────────────

    @staticmethod
    def _fingerprint(item: dict) -> str:
        """计算内容指纹用于去重

        基于 URL 域名 + 路径归一化 + 内容前 100 字
        """
        parts = []

        # URL 指纹
        url = item.get("url", "")
        if url:
            try:
                parsed = urlparse(url)
                # 域名归一化（去 www.）
                netloc = parsed.netloc.lower()
                if netloc.startswith("www."):
                    netloc = netloc[4:]
                parts.append(f"domain:{netloc}")

                # 路径归一化
                path = parsed.path.rstrip("/")
                if path:
                    parts.append(f"path:{path}")
            except Exception:
                parts.append(f"url:{url}")

        # 内容指纹（取标题或内容前 100 字）
        title = (item.get("title") or "")[:100].strip()
        if title:
            parts.append(f"title:{title.lower()}")

        # snippet/content 前 200 字
        snippet = (item.get("snippet") or item.get("content") or "")
        snippet = re.sub(r"\s+", " ", snippet)[:200].strip().lower()
        if snippet:
            parts.append(f"snippet:{snippet}")

        return "|".join(parts)

    # ── 质量评分 ──────────────────────────────────────────────────

    @staticmethod
    def score_item(item: dict) -> float:
        """质量评分（0.0 ~ 1.0）

        评分维度：
        - 内容长度是否适中（20 分）
        - 标题质量（20 分）
        - 来源可信度（30 分）
        - 内容丰富度（30 分）
        """
        score = 0.0

        # 1. 内容长度（0-20）
        content = item.get("content") or item.get("text") or item.get("snippet") or ""
        content_len = len(content.strip())
        if 200 <= content_len <= 5000:
            score += 20
        elif 50 <= content_len < 200:
            score += 10
        elif 5000 < content_len <= 20000:
            score += 15
        else:
            score += 5

        # 2. 标题质量（0-20）
        title = item.get("title", "")
        if title and len(title) >= 5:
            score += 10
            if not re.match(r"^\d+$", title):
                score += 5
            if not title.endswith(("...", "…")):
                score += 5

        # 3. 来源可信度（0-30）
        url = item.get("url", "")
        if url:
            trusted_domains = {
                "wikipedia.org", "github.com", "stackoverflow.com",
                "stackexchange.com", "developer.mozilla.org",
                "docs.python.org", "npmjs.com", "pypi.org",
                "arxiv.org", "scholar.google.com", "reuters.com",
                "bbc.com", "bbc.co.uk",
            }
            domain = DataProcessor.extract_domain(url)
            if domain in trusted_domains:
                score += 30
            elif domain.endswith((".edu", ".gov", ".org")):
                score += 25
            elif domain.endswith((".com", ".io", ".dev")):
                score += 15
            else:
                score += 10

        # 4. 内容丰富度（0-30）
        if content:
            # 包含标点符号
            punct_count = sum(1 for c in content if c in "。.!！？?，,、；;：:")
            if punct_count >= 5:
                score += 10
            # 包含多个段落
            if "\n\n" in content:
                score += 10
            # 包含数字
            if re.search(r"\d+", content):
                score += 5
            # 包含中英文混合
            if re.search(r"[一-鿿]", content) and re.search(r"[a-zA-Z]", content):
                score += 5

        return min(max(score / 100.0, 0.0), 1.0)

    # ── 批量处理工具 ──────────────────────────────────────────────

    def merge_results(self, *result_lists: List[dict], dedup: bool = True, limit: int = 50) -> List[dict]:
        """合并多个搜索结果，去重 + 排序

        Args:
            *result_lists: 多个结果列表
            dedup: 是否去重
            limit: 返回上限

        Returns:
            List[dict]: 合并后的结果（按质量评分降序）
        """
        all_items = []
        for rl in result_lists:
            all_items.extend(rl)

        processed = self.process(all_items, dedup=dedup, quality_filter=True)
        # 按质量评分降序
        processed.sort(key=lambda x: x.get("_quality_score", 0), reverse=True)

        return processed[:limit]

    @staticmethod
    def summarize_results(results: List[dict], max_summary_length: int = 2000) -> str:
        """将搜索结果汇总为可读摘要"""
        if not results:
            return "（无结果）"

        lines = []
        for i, r in enumerate(results, 1):
            title = r.get("title", "").strip()
            url = r.get("url", "").strip()
            snippet = (r.get("snippet") or r.get("content") or "").strip()
            score = r.get("_quality_score", 0)

            if not title and not snippet:
                continue

            line = f"{i}. {title or '(无标题)'}"
            if snippet:
                snip = snippet[:200].replace("\n", " ")
                line += f"\n   {snip}"
            if url:
                line += f"\n   🔗 {url}"
            if score > 0:
                line += f"  (评分: {score:.2f})"

            lines.append(line)

        summary = "\n\n".join(lines)
        return summary[:max_summary_length]

    def get_stats(self) -> dict:
        """获取处理统计"""
        return dict(self._stats)

    def reset(self):
        """重置统计"""
        self._stats = {k: 0 for k in self._stats}
