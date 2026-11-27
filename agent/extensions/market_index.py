"""扩展市场索引 v1 —— **唯一契约**（解析 + 校验 + 归一）。

【为什么单开一份】此前市场索引只有一句文档注释（`{skills, mcp, channels, plugins}`），
而代码按**单数**类型名比较（`market.py:110/258`）⇒ 复数键整类被过滤，"有索引用不上"
从 2026-10 一直躺到现在。索引是**外部输入**（远程 JSON），必须像任何外部输入一样：
先定契约、再逐项校验、非法项拒收且可计数。

【契约（schema_version = 1）】
  {
    "schema_version": 1,
    "plugins":  [{"id": "...", "name": "...", "description": "...", "source": "github:o/r",
                  "tags": [...], "stars": 0, "featured": true}],
    "skills": [...], "mcp": [...], "channels": [...],
    "bundles": [{"id": "...", "name": "...", "description": "...",
                 "featured": true, "items": ["plugin-id", ...]}]
  }
  · 类型键单数/复数都接受（`skills`/`skill`、`plugins`/`plugin`…），也接受
    `{"items": [{... "type": "plugin"}]}` 的扁平写法 —— 兼容旧索引，但**归一后只有一种形状**。
  · 条目字段**白名单**（见 `ENTRY_FIELDS`）：索引里的 `command`/`args`/`deps` 一律丢弃
    （否则远程索引可直达成安装器的进程参数 = 投毒面）。
  · `source` 只允许 `github:`/`url:`/`local:`/`path:`/`builtin:` 前缀；不合法即丢弃该条。
  · bundle **只允许 plugin**（mcp/skill/channel 的安装会走 pip/npm 或起进程，绕过既有验签边界）。
  · 上限：索引 1 MiB / 条目 512 / 每个 bundle 50 项 / id 128 字符。

【不做什么】不做远程签名托管、不签包、不补依赖；签名与白名单仍由
`plugin_policy` / `plugins_installer` 在**安装点**判定（本模块只决定"索引里有什么"）。
"""
from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Tuple

SCHEMA_VERSION = 1

#: 索引 JSON 的字节上限（远程抓取与本地缓存共用）
MAX_INDEX_BYTES = 1024 * 1024
#: 归一后最多保留的条目数（防一个超大索引把 UI/推荐拖死）
MAX_ENTRIES = 512
#: 单个 bundle 最多包含的条目数
MAX_BUNDLE_ITEMS = 50
#: id 的最大长度（同时用于 bundle 引用）
MAX_ID_CHARS = 128
#: 文本字段上限（description/tags）
MAX_DESC_CHARS = 2048
MAX_TAGS = 16
MAX_TAG_CHARS = 32
#: 只允许来自这些前缀的 source（安装器只认它们；其余一律丢弃）
SOURCE_PREFIXES: Tuple[str, ...] = ("github:", "url:", "local:", "path:", "builtin:")
#: 条目字段白名单（其余键丢弃：索引不得携带 command/args/deps 等安装参数）
ENTRY_FIELDS = frozenset({
    "id", "name", "description", "tags", "stars", "source", "featured",
    "homepage", "author", "version", "type", "ext_type",
})
#: 类型键别名 → 规范类型
TYPE_ALIASES = {
    "skill": "skill", "skills": "skill",
    "mcp": "mcp", "mcps": "mcp", "servers": "mcp",
    "channel": "channel", "channels": "channel",
    "plugin": "plugin", "plugins": "plugin",
}
TYPE_ORDER: Tuple[str, ...] = ("skill", "mcp", "channel", "plugin")
#: bundle 只允许 plugin（其它类型的安装边界不同，见模块文档）
BUNDLE_TYPE = "plugin"


class IndexSchemaError(ValueError):
    """索引顶层形状不合法（缺 schema_version 版本不符 / 非 dict/list）"""


def _as_int(value: Any, default: int = 0) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number >= 0 else default


def _as_str(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()[:limit]


def _as_tags(value: Any) -> List[str]:
    if not isinstance(value, (list, tuple)):
        return []
    tags: List[str] = []
    for item in value:
        tag = _as_str(item, MAX_TAG_CHARS)
        if tag and tag not in tags:
            tags.append(tag)
        if len(tags) >= MAX_TAGS:
            break
    return tags


def _safe_source(value: Any) -> str:
    """source 只接受已知前缀（空串 = 未声明，保持兼容：安装时按 id 走）"""
    text = _as_str(value, 256)
    if not text:
        return ""
    return text if text.startswith(SOURCE_PREFIXES) else ""


def normalize_entry(raw: Any, *, ext_type: str = "") -> Optional[Dict[str, Any]]:
    """把一条索引条目归一成白名单字段；非法返回 None（调用方计数丢弃）"""
    if not isinstance(raw, dict):
        return None
    entry_id = _as_str(raw.get("id"), MAX_ID_CHARS)
    if not entry_id:
        return None
    declared_type = _as_str(raw.get("type") or raw.get("ext_type"), 16).lower()
    entry_type = TYPE_ALIASES.get(declared_type, "") or ext_type
    if entry_type not in TYPE_ORDER:
        return None
    source = _safe_source(raw.get("source"))
    if raw.get("source") and not source:
        return None
    entry: Dict[str, Any] = {
        "type": entry_type,
        "id": entry_id,
        "name": _as_str(raw.get("name"), MAX_ID_CHARS) or entry_id,
        "description": _as_str(raw.get("description"), MAX_DESC_CHARS),
        "tags": _as_tags(raw.get("tags")),
        "stars": _as_int(raw.get("stars")),
        "featured": raw.get("featured") is True,
    }
    for key in ("homepage", "author", "version"):
        text = _as_str(raw.get(key), 256)
        if text:
            entry[key] = text
    if source:
        entry["source"] = source
    # 白名单之外别再往外带（防御性：raw 里多余字段根本不进 entry）
    return entry


def _iter_typed(raw: Dict[str, Any]) -> Iterable[Tuple[str, Any]]:
    """按类型键（单/复数）产出 (规范类型, 条目列表)"""
    for key, value in raw.items():
        if key in ("bundles", "schema_version", "items"):
            continue
        ext_type = TYPE_ALIASES.get(str(key).strip().lower())
        if ext_type and isinstance(value, list):
            yield ext_type, value


def _normalize_bundles(raw: Any, item_ids: set) -> Tuple[List[Dict[str, Any]], int]:
    if not isinstance(raw, list):
        return [], 0
    bundles: List[Dict[str, Any]] = []
    seen: set = set()
    dropped = 0
    for entry in raw:
        if not isinstance(entry, dict):
            dropped += 1
            continue
        bundle_id = _as_str(entry.get("id"), MAX_ID_CHARS)
        if not bundle_id or bundle_id in seen:
            dropped += 1
            continue
        items: List[str] = []
        for ref in entry.get("items") or []:
            ref_id = _as_str(ref, MAX_ID_CHARS)
            if ref_id and ref_id in item_ids and ref_id not in items:
                items.append(ref_id)
            if len(items) >= MAX_BUNDLE_ITEMS:
                break
        if not items:
            dropped += 1
            continue
        seen.add(bundle_id)
        bundles.append({
            "id": bundle_id,
            "type": BUNDLE_TYPE,
            "name": _as_str(entry.get("name"), MAX_ID_CHARS) or bundle_id,
            "description": _as_str(entry.get("description"), MAX_DESC_CHARS),
            "featured": entry.get("featured") is True,
            "items": items,
        })
        if len(bundles) >= MAX_ENTRIES:
            break
    return bundles, dropped


def normalize_index(raw: Any) -> Dict[str, Any]:
    """把任意形状的索引归一成 v1 契约

    Returns:
        `{"schema_version":1, "items":[entry...], "bundles":[bundle...],
           "dropped": <丢弃条目数>, "count": len(items)}`

    Raises:
        IndexSchemaError: 顶层不是 dict/list，或 schema_version 不是 1
    """
    if isinstance(raw, list):
        pairs: List[Tuple[str, Any]] = [("", raw)]
    elif isinstance(raw, dict):
        version = raw.get("schema_version")
        if version is not None and _as_int(version, -1) != SCHEMA_VERSION:
            raise IndexSchemaError("unsupported schema_version")
        if isinstance(raw.get("items"), list):
            pairs = [("", raw["items"])]
        else:
            pairs = [(t, value) for t, value in _iter_typed(raw)]
    else:
        raise IndexSchemaError("index must be an object or array")

    items: List[Dict[str, Any]] = []
    seen: set = set()
    dropped = 0
    for ext_type, rows in pairs:
        for row in rows:
            entry = normalize_entry(row, ext_type=ext_type)
            if entry is None:
                dropped += 1
                continue
            key = (entry["type"], entry["id"])
            if key in seen:
                dropped += 1
                continue
            seen.add(key)
            items.append(entry)
            if len(items) >= MAX_ENTRIES:
                break
        if len(items) >= MAX_ENTRIES:
            break

    plugin_ids = {e["id"] for e in items if e["type"] == BUNDLE_TYPE}
    bundles, bundle_dropped = _normalize_bundles(
        raw.get("bundles") if isinstance(raw, dict) else None, plugin_ids)
    return {
        "schema_version": SCHEMA_VERSION,
        "items": items,
        "bundles": bundles,
        "dropped": dropped + bundle_dropped,
        "count": len(items),
    }


def empty_index() -> Dict[str, Any]:
    """空索引（与 normalize_index 同形；抓取失败/未配置时用它，避免 None 分支散落）"""
    return {"schema_version": SCHEMA_VERSION, "items": [], "bundles": [],
            "dropped": 0, "count": 0}


def featured_items(index: Dict[str, Any], ext_type: str = "") -> List[Dict[str, Any]]:
    """官方推荐位：`featured: true` 的条目（按类型过滤，保序）"""
    items = index.get("items") or []
    return [e for e in items if e.get("featured") and (not ext_type or e.get("type") == ext_type)]


def featured_bundles(index: Dict[str, Any], ext_type: str = "") -> List[Dict[str, Any]]:
    """推荐 bundle：`featured: true` 且（未筛选或筛选 plugin）"""
    if ext_type and ext_type != BUNDLE_TYPE:
        return []
    return [b for b in (index.get("bundles") or []) if b.get("featured")]


def dumps_cached(index: Dict[str, Any]) -> str:
    """缓存落盘用的稳定序列化（ensure_ascii=False，便于人读/审计）"""
    return json.dumps(index, ensure_ascii=False, indent=2, sort_keys=True)


__all__ = [
    "SCHEMA_VERSION", "MAX_INDEX_BYTES", "MAX_ENTRIES", "MAX_BUNDLE_ITEMS",
    "SOURCE_PREFIXES", "ENTRY_FIELDS", "TYPE_ALIASES", "TYPE_ORDER", "BUNDLE_TYPE",
    "IndexSchemaError", "normalize_entry", "normalize_index", "empty_index",
    "featured_items", "featured_bundles", "dumps_cached",
]
