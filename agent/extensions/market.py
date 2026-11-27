"""扩展市场 — 发现、搜索、推荐扩展的市场机制

提供：
  - 内置扩展目录（官方维护）
  - 社区扩展索引（JSON，schema v1，见 `agent/extensions/market_index.py`）
  - GitHub 社区扩展搜索
  - 扩展推荐（含官方推荐位 / bundle）

【E4-4 收口】索引是**外部输入**：URL 可配、只允许 https、每次抓取过 `ssrf_guard`、
禁 30x 重定向、有字节上限；解析与字段白名单唯一实现在 `market_index`。
本模块只做**取数与投影**，签名/白名单仍由安装点判定（`plugin_policy` / `plugins_installer`）。
"""
import json
import os
import uuid
import logging
import urllib.request
import urllib.parse
from typing import Optional, Dict, Any, List
from pathlib import Path

from agent.extensions import market_index as MI
from agent.extensions.base import BUILTIN_EXTENSIONS
from agent.logging_utils import log_dict

logger = logging.getLogger(__name__)

def _trace_id():
    """生成 trace_id"""
    return uuid.uuid4().hex[:16]


#: 社区扩展索引 URL（JSON，schema v1）。默认值保持仓库既有地址；置空 = 不拉远程。
_DEFAULT_COMMUNITY_INDEX_URL = (
    "https://raw.githubusercontent.com/nzt47/yunshu-extensions/main/index.json")
ENV_INDEX_URL = "CP_EXTENSION_MARKET_INDEX_URL"
ENV_RECOMMEND_ENABLED = "CP_EXTENSION_MARKET_RECOMMEND_ENABLED"
_TRUE = frozenset({"1", "true", "yes", "on"})
#: 单次抓取的网络超时（秒）；与 GitHub 搜索的 10s 同口径
_FETCH_TIMEOUT_SEC = 10

#: 本地扩展索引缓存（仓库根 data/；运行期状态，已进 .gitignore）
_REPO_ROOT = Path(__file__).resolve().parents[2]
_LOCAL_INDEX_CACHE = _REPO_ROOT / "data" / "extension_market_index.json"


def index_url() -> str:
    """当前生效的索引地址（env 覆盖；空串 = 只用内置 + 本地缓存）"""
    return str(os.environ.get(ENV_INDEX_URL, _DEFAULT_COMMUNITY_INDEX_URL) or "").strip()


def recommend_enabled() -> bool:
    """官方推荐位 / bundle 展示开关（默认关；只影响展示，不影响安装闸门）"""
    return str(os.environ.get(ENV_RECOMMEND_ENABLED, "")).strip().lower() in _TRUE


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """禁止跟随 3xx：索引 URL 被篡改时不能把它当跳板（每次都按同一 URL 判 SSRF）"""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


def _ssrf_block_reason(url: str) -> Optional[str]:
    """出站判定（唯一口径 `guardrails.ssrf_guard`）；守卫不可用 ⇒ fail-closed"""
    try:
        from agent.guardrails import ssrf_guard
    except Exception as exc:  # noqa: BLE001
        return "SSRF 守卫不可用（%s）" % type(exc).__name__
    verdict = ssrf_guard.check_url(url)
    if verdict.allowed:
        return None
    try:
        ssrf_guard.audit_block(verdict, url=url, surface="extensions.market.index")
    except Exception:  # noqa: BLE001 审计是旁路
        pass
    return str(getattr(verdict, "reason", "") or "目标未通过出站判定")


def _write_cache(index: Dict[str, Any]) -> None:
    """原子写缓存（写失败只警告：下次重新抓）"""
    try:
        _LOCAL_INDEX_CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _LOCAL_INDEX_CACHE.with_suffix(".json.tmp")
        tmp.write_text(MI.dumps_cached(index), encoding="utf-8")
        tmp.replace(_LOCAL_INDEX_CACHE)
    except Exception as exc:  # noqa: BLE001
        logger.warning(log_dict({'module_name': 'market', 'action': 'cache',
                                 'msg': '[扩展市场] 写索引缓存失败（%s）' % type(exc).__name__}))


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

    def fetch_community_index(self, timeout: int = _FETCH_TIMEOUT_SEC) -> Optional[Dict]:
        """从远程获取社区扩展索引（**归一后的 v1 契约**；失败返回 None）

        安全边界：只允许 https；每次抓取都过 `ssrf_guard.check_url`；禁跟随 3xx；
        最多读 `market_index.MAX_INDEX_BYTES + 1` 字节（超限即拒，不截断解析）。
        """
        url = index_url()
        if not url:
            logger.info(log_dict({'module_name': 'market', 'action': 'index',
                                  'msg': '[扩展市场] 未配置索引地址（只用内置 + 本地缓存）'}))
            return None
        if not url.lower().startswith("https://"):
            logger.warning(log_dict({'module_name': 'market', 'action': 'index',
                                     'msg': '[扩展市场] 索引地址必须是 https，已拒绝'}))
            return None
        blocked = _ssrf_block_reason(url)
        if blocked is not None:
            logger.warning(log_dict({'module_name': 'market', 'action': 'index',
                                     'msg': '[扩展市场] 索引地址未通过出站判定：%s' % blocked}))
            return None
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Yunshu/2.0"})
            opener = urllib.request.build_opener(_NoRedirect())
            with opener.open(req, timeout=timeout) as resp:
                payload = resp.read(MI.MAX_INDEX_BYTES + 1)
            if len(payload) > MI.MAX_INDEX_BYTES:
                logger.warning(log_dict({'module_name': 'market', 'action': 'index',
                                         'msg': '[扩展市场] 索引超过字节上限，已拒绝'}))
                return None
            raw = json.loads(payload.decode("utf-8"))
            index = MI.normalize_index(raw)
        except (MI.IndexSchemaError, ValueError, OSError) as exc:
            logger.warning(log_dict({'module_name': 'market', 'action': 'index',
                                     'msg': '[扩展市场] 获取社区索引失败（%s）' % type(exc).__name__}))
            return None
        except Exception as exc:  # noqa: BLE001 兜底：任何失败都只回 None
            logger.warning(log_dict({'module_name': 'market', 'action': 'index',
                                     'msg': '[扩展市场] 解析社区索引失败（%s）' % type(exc).__name__}))
            return None
        _write_cache(index)
        self._cache = index
        logger.info(log_dict({'module_name': 'market', 'action': 'index',
                              'msg': '[扩展市场] 索引已更新：%d 条 / %d 个 bundle / 丢弃 %d'
                                     % (index["count"], len(index["bundles"]), index["dropped"])}))
        return index

    def get_cached_community_index(self) -> Optional[Dict]:
        """获取缓存的社区扩展索引（归一后的 v1 契约；旧缓存也能读）"""
        if self._cache:
            return self._cache
        try:
            if _LOCAL_INDEX_CACHE.exists():
                raw = json.loads(_LOCAL_INDEX_CACHE.read_text(encoding="utf-8"))
                self._cache = MI.normalize_index(raw)
                return self._cache
        except Exception as exc:  # noqa: BLE001 缓存坏了等于没有
            logger.warning(log_dict({'module_name': 'market', 'action': 'cache',
                                     'msg': '[扩展市场] 加载缓存索引失败（%s）' % type(exc).__name__}))
        return None

    def search_community(self, query: str, ext_type: str = None) -> List[Dict]:
        """搜索社区扩展（按类型过滤 + 名称/描述/标签子串匹配）"""
        index = self.get_cached_community_index()
        if not index:
            index = self.fetch_community_index()
        if not index:
            return []

        results = []
        query_lower = (query or "").lower()
        for ext in index.get("items") or []:
            if ext_type and ext.get("type") != ext_type:
                continue
            searchable = " ".join([
                ext.get("name", ""),
                ext.get("description", ""),
                " ".join(ext.get("tags", [])),
            ]).lower()
            if query_lower in searchable:
                results.append({
                    **ext,
                    "ext_type": ext.get("type", ""),
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
            with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_SEC) as resp:
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

        except Exception as exc:  # noqa: BLE001 网络失败不该拖垮搜索
            logger.warning(log_dict({'module_name': 'market', 'action': 'github',
                                     'msg': '[扩展市场] GitHub 搜索失败（%s）' % type(exc).__name__}))
            return []

    # ── 统一搜索 ──

    def search_all(
        self, query: str = "", ext_type: str = None,
        include_github: bool = True,
    ) -> Dict[str, Any]:
        """统一搜索所有来源的扩展

        Returns:
            {"builtin": [...], "community": [...], "github": [...],
             "recommend_enabled": bool, "featured": [...], "bundles": [...]}
        """
        results: Dict[str, Any] = {}

        # 内置扩展
        builtin = self.get_builtin_extensions()
        if ext_type:
            results["builtin"] = builtin.get(ext_type, [])
        else:
            results["builtin"] = []
            for items in builtin.values():
                results["builtin"].extend(items)

        # 搜索本地缓存的社区索引
        results["community"] = self.search_community(query, ext_type)

        # GitHub 搜索
        if include_github:
            results["github"] = self.search_github(query, ext_type)
        else:
            results["github"] = []

        # 官方推荐位 / bundle（默认关；只影响展示）
        enabled = recommend_enabled()
        results["recommend_enabled"] = enabled
        results["featured"] = []
        results["bundles"] = []
        if enabled:
            index = self.get_cached_community_index()
            if index:
                results["featured"] = [
                    {**e, "ext_type": e.get("type", ""), "market_type": "community"}
                    for e in MI.featured_items(index, ext_type)
                ]
                if not ext_type or ext_type == MI.BUNDLE_TYPE:
                    results["bundles"] = list(index.get("bundles") or [])

        return results

    # ── 推荐 ──

    def get_recommendations(self, ext_type: str = None, limit: int = 5,
                            *, force: bool = False) -> List[Dict]:
        """获取推荐扩展

        推荐策略（默认关：`CP_EXTENSION_MARKET_RECOMMEND_ENABLED`）：
        1. 社区索引里 `featured: true` 的条目（官方推荐位）
        2. 内置扩展
        3. 社区热门（stars 降序）
        """
        if not force and not recommend_enabled():
            return []
        recommendations: List[Dict] = []

        index = self.get_cached_community_index()
        if index:
            for item in MI.featured_items(index, ext_type):
                recommendations.append({
                    **item,
                    "ext_type": item.get("type", ""),
                    "source": "community",
                    "recommended": True,
                    "featured": True,
                })

        # 内置扩展
        if ext_type:
            for item in BUILTIN_EXTENSIONS.get(ext_type, [])[:limit]:
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

        # 社区热门（stars 降序；跳过已收录的 id）
        if index:
            for etype in MI.TYPE_ORDER:
                if ext_type and etype != ext_type:
                    continue
                rows = sorted(
                    [e for e in index.get("items") or [] if e.get("type") == etype],
                    key=lambda x: x.get("stars", 0),
                    reverse=True,
                )
                for item in rows[:2]:
                    if not any(r.get("id") == item.get("id") for r in recommendations):
                        recommendations.append({
                            **item,
                            "ext_type": etype,
                            "source": "community",
                            "recommended": True,
                        })

        return recommendations[:limit]
