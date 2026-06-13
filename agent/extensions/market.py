"""扩展市场 — 发现、搜索、推荐扩展的市场机制

提供：
  - 内置扩展目录（官方维护）
  - GitHub 社区扩展搜索
  - 扩展推荐
  - 扩展信息查询
"""

import json
import logging
import urllib.request
import urllib.parse
from typing import Optional, Dict, Any, List
from pathlib import Path

from agent.extensions.base import BUILTIN_EXTENSIONS

logger = logging.getLogger(__name__)

# 社区扩展索引 URL（Json 格式）
# 格式: { "skills": [...], "mcp": [...], "channels": [...], "plugins": [...] }
_COMMUNITY_INDEX_URL = "https://raw.githubusercontent.com/nzt47/yunshu-extensions/main/index.json"

# 本地扩展索引缓存
_LOCAL_INDEX_CACHE = Path(__file__).parent.parent / "data" / "extension_market_index.json"


class ExtensionMarket:
    """扩展市场 — 发现和搜索扩展"""

    def __init__(self):
        self._cache: Optional[Dict] = None

    # ── 内置扩展 ──

    def get_builtin_extensions(self) -> Dict[str, List[Dict]]:
        """获取所有内置扩展（官方内置）"""
        return {
            ext_type: [
                {**info, "source": "builtin", "market_type": "builtin"}
                for info in infos
            ]
            for ext_type, infos in BUILTIN_EXTENSIONS.items()
        }

    # ── 社区扩展 ──

    def fetch_community_index(self, timeout: int = 10) -> Optional[Dict]:
        """从远程获取社区扩展索引"""
        try:
            req = urllib.request.Request(
                _COMMUNITY_INDEX_URL,
                headers={"User-Agent": "Yunshu/2.0"},
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                # 缓存到本地
                _LOCAL_INDEX_CACHE.parent.mkdir(parents=True, exist_ok=True)
                with open(_LOCAL_INDEX_CACHE, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                self._cache = data
                logger.info("[扩展市场] 已获取社区扩展索引")
                return data
        except Exception as e:
            logger.warning(f"[扩展市场] 获取社区索引失败: {e}")
            return None

    def get_cached_community_index(self) -> Optional[Dict]:
        """获取缓存的社区扩展索引"""
        if self._cache:
            return self._cache
        try:
            if _LOCAL_INDEX_CACHE.exists():
                with open(_LOCAL_INDEX_CACHE, "r", encoding="utf-8") as f:
                    self._cache = json.load(f)
                return self._cache
        except Exception as e:
            logger.warning(f"[扩展市场] 加载缓存索引失败: {e}")
        return None

    def search_community(self, query: str, ext_type: str = None) -> List[Dict]:
        """搜索社区扩展

        Args:
            query: 搜索关键词
            ext_type: 可选的扩展类型筛选

        Returns:
            匹配的扩展列表
        """
        index = self.get_cached_community_index()
        if not index:
            # 尝试在线获取
            index = self.fetch_community_index()
        if not index:
            return []

        results = []
        query_lower = query.lower()

        for etype, extensions in index.items():
            if ext_type and etype != ext_type:
                continue
            for ext in extensions:
                # 匹配名称、描述、标签
                searchable = " ".join([
                    ext.get("name", ""),
                    ext.get("description", ""),
                    " ".join(ext.get("tags", [])),
                ]).lower()
                if query_lower in searchable:
                    results.append({
                        **ext,
                        "ext_type": etype,
                        "source": "community",
                        "market_type": "community",
                    })

        return results

    # ── GitHub 搜索 ──

    def search_github(
        self, query: str, ext_type: str = None, max_results: int = 10
    ) -> List[Dict]:
        """在 GitHub 上搜索扩展

        搜索策略：
        - skill → repo topic:yunshu-skill
        - mcp → repo topic:yunshu-mcp
        - channel → repo topic:yunshu-channel
        - plugin → repo topic:yunshu-plugin
        """
        topic_map = {
            "skill": "yunshu-skill",
            "claude_skill": "claude-code-skill",
            "mcp": "yunshu-mcp",
            "channel": "yunshu-channel",
            "plugin": "yunshu-plugin",
        }

        search_parts = []
        if ext_type and ext_type in topic_map:
            search_parts.append(f"topic:{topic_map[ext_type]}")
        if query:
            search_parts.append(query)

        search_query = "+".join(search_parts) if search_parts else "topic:yunshu"
        api_url = f"https://api.github.com/search/repositories?q={urllib.parse.quote(search_query)}&sort=updated&per_page={min(max_results, 30)}"

        try:
            req = urllib.request.Request(
                api_url,
                headers={"User-Agent": "Yunshu/2.0", "Accept": "application/vnd.github.v3+json"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            results = []
            for item in data.get("items", [])[:max_results]:
                results.append({
                    "id": item.get("name"),
                    "name": item.get("name"),
                    "full_name": item.get("full_name"),
                    "description": item.get("description", ""),
                    "url": item.get("html_url"),
                    "stars": item.get("stargazers_count", 0),
                    "language": item.get("language", ""),
                    "source": f"github:{item['full_name']}",
                    "source_url": item.get("html_url"),
                    "market_type": "github",
                    "ext_type": ext_type or "unknown",
                })

            return results

        except Exception as e:
            logger.warning(f"[扩展市场] GitHub 搜索失败: {e}")
            return []

    # ── 统一搜索 ──

    def search_all(
        self, query: str = "", ext_type: str = None,
        include_github: bool = True,
    ) -> Dict[str, List[Dict]]:
        """统一搜索所有来源的扩展

        Returns:
            {"builtin": [...], "community": [...], "github": [...]}
        """
        results = {}

        # 内置扩展
        builtin = self.get_builtin_extensions()
        if ext_type:
            results["builtin"] = builtin.get(ext_type, [])
        else:
            results["builtin"] = []
            for items in builtin.values():
                results["builtin"].extend(items)

        # 搜索本地缓存的社区索引
        community = self.search_community(query, ext_type)
        results["community"] = community

        # GitHub 搜索
        if include_github:
            github = self.search_github(query, ext_type)
            results["github"] = github
        else:
            results["github"] = []

        return results

    # ── 推荐 ──

    def get_recommendations(self, ext_type: str = None, limit: int = 5) -> List[Dict]:
        """获取推荐扩展

        推荐策略：
        1. 内置扩展优先
        2. 社区热门扩展
        """
        recommendations = []

        # 内置扩展
        if ext_type:
            builtin = BUILTIN_EXTENSIONS.get(ext_type, [])
            for item in builtin[:limit]:
                recommendations.append({
                    **item,
                    "ext_type": ext_type,
                    "source": "builtin",
                    "recommended": True,
                })
        else:
            for etype, items in BUILTIN_EXTENSIONS.items():
                for item in items[:2]:
                    recommendations.append({
                        **item,
                        "ext_type": etype,
                        "source": "builtin",
                        "recommended": True,
                    })

        # 社区热门（从缓存中取 starred 最高的）
        community = self.get_cached_community_index()
        if community:
            for etype, items in community.items():
                if ext_type and etype != ext_type:
                    continue
                sorted_items = sorted(
                    items,
                    key=lambda x: x.get("stars", 0),
                    reverse=True,
                )
                for item in sorted_items[:2]:
                    if not any(r.get("id") == item.get("id") for r in recommendations):
                        recommendations.append({
                            **item,
                            "ext_type": etype,
                            "source": "community",
                            "recommended": True,
                        })

        return recommendations[:limit]
