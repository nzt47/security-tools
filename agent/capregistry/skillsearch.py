"""技能语义召回（`POST /capabilities/skills/search`）—— **复用**已有检索栈

## 为什么不新建向量库（TASK-05 §3 第 1 步第 3 项明写"必须复用"）

仓库已经有成熟且经过调优的三路检索栈：

| 资产 | 路径 |
|---|---|
| 向量库 | `data/skill_vectors/chroma.sqlite3`（`chromadb`） |
| 词法路 | `rank-bm25`（`agent/skills_mgmt/bm25_searcher.py`） |
| 融合 | RRF（`agent/skills_mgmt/loader.py::_rrf_fuse`） |
| 精排 | `agent/skills_mgmt/reranker.py` |

新建第二套向量库会同时引入"两份索引不一致""两份权重口径"两类问题 ——
与 `TASK-00` §0.5 的 **D1（单一真相源）** 直接冲突。

## 本模块只做三件事

1. **惰性装配** `SkillLoader`（它自己按需拉起 chromadb / rank_bm25 / reranker，
   失败时逐路降级 —— 这些降级行为是**既有的**，本模块不重写）；
2. 把 `MatchResult` **归一化**成与 `/capabilities/tools` 同族的信封
   （`status/code/data/error/meta`），让 CI 能凭 `status` 判定；
3. 在 `meta` 里**如实披露**哪些路真的生效了（`retrieval` 字段）——
   `TASK-00` §0.2b 的纪律：引用任何数字必须标注构成，
   而"三路融合"与"单路 TF-IDF 兜底"的召回质量**不可混为一谈**。

## 诚实标注

技能实体（`data/skills.json` 等）**不在版本控制内**（`TASK-00` §0.3）。
故本端点在新克隆的仓库上可能召回为空 —— 那不是 bug，是**实体不可复现**的
既有约束。空结果时 `meta.retrieval.entity_available=false` 会明确说出来。
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

__all__ = ["search_skills", "reset_loader_cache"]

#: `SkillLoader` 进程内单例（它内部有 TF-IDF 倒排索引缓存，重复构造等于每次重建）
_CACHE: Dict[str, Any] = {"loader": None}


def _get_loader() -> Any:
    """惰性构造 `SkillLoader`（构造失败 ⇒ 抛，由调用方转成 `unavailable`）"""
    if _CACHE["loader"] is None:
        from agent.skills_mgmt.loader import SkillLoader  # noqa: PLC0415 惰性：较重
        _CACHE["loader"] = SkillLoader()
    return _CACHE["loader"]


def reset_loader_cache() -> None:
    """清空单例（测试用）"""
    _CACHE["loader"] = None


def _entities_available() -> bool:
    """技能实体是否可读（`data/skills.json` 等**不在版本控制内**）

    只用于**如实披露**，不参与召回判定（召回该空就空，不因缺实体而报错）。
    """
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    for rel in ("data/skills.json", "data/skills_mgmt.json",
                "data/skills_repo", "agent/data/skills.json"):
        if os.path.exists(os.path.join(root, rel)):
            return True
    return False


def search_skills(query: str, *, top_k: int = 5,
                  use_vector: bool = True, use_bm25: bool = True,
                  use_reranker: bool = False) -> Dict[str, Any]:
    """语义召回 Top-K（返回与 `/capabilities/tools` 同族的信封）

    Args:
        query: 检索意图文本
        top_k: 返回条数（路由层已收敛到 1..50）
        use_vector: 启用向量路（chroma + BGE-m3，未装依赖时既有代码会降级）
        use_bm25: 启用 BM25 路（`rank_bm25` 未装时既有代码会静默降级）
        use_reranker: 启用 Cross-Encoder 精排（**默认关闭**：它要额外拉起模型，
            冷启动代价高，且与"关掉 LLM 也能用"这条判据无关 ——
            本端点的默认形态必须**不依赖任何生成式模型**）
    """
    started = time.perf_counter()
    try:
        loader = _get_loader()
    except Exception as exc:  # noqa: BLE001  检索栈不可用 ⇒ 明确 unavailable（不伪装成空结果）
        logger.error("[capabilities] 技能检索栈不可用: %s", exc, exc_info=True)
        return {
            "status": "error", "code": "unavailable", "data": None,
            "error": {"code": "unhealthy",
                      "message": "技能检索栈不可用（SkillLoader 构造失败）",
                      "retryable": True},
            "meta": {"entity_available": _entities_available()},
        }
    try:
        result = loader.match(str(query), top_k=int(top_k),
                              use_vector=bool(use_vector),
                              use_bm25=bool(use_bm25),
                              use_reranker=bool(use_reranker))
    except Exception as exc:  # noqa: BLE001
        logger.error("[capabilities] 技能检索失败: %s", exc, exc_info=True)
        return {
            "status": "error", "code": "internal_error", "data": None,
            "error": {"code": "internal_error",
                      "message": "检索内部错误，已脱敏；请查看服务端日志定位",
                      "retryable": False},
            "meta": {"entity_available": _entities_available()},
        }
    payload = result.to_dict() if hasattr(result, "to_dict") else {}
    matches = payload.get("matches") or []
    # 只保留叶子字段（**不搬运** SkillMatch 对象本体；D8/§0.2 的"不要序列化活数据"）
    items = [{
        "skill_id": str(m.get("skill_id") or ""),
        "name": str(m.get("name") or ""),
        "description": str(m.get("description") or "")[:300],
        "score": m.get("score"),
        "rank": i + 1,
    } for i, m in enumerate(matches)]
    return {
        "status": "ok",
        "code": "ok",
        "data": {"items": items, "total": len(items), "query": str(query),
                 "top_k": int(top_k)},
        "error": None,
        "meta": {
            # 如实披露"哪几路真的生效"（TASK-00 §0.2b：引用数字必须标注构成）
            "retrieval": {
                "requested": {"vector": bool(use_vector), "bm25": bool(use_bm25),
                              "reranker": bool(use_reranker)},
                "stack": "agent/skills_mgmt/loader.py::SkillLoader.match "
                         "(TF-IDF 倒排 + 向量 + BM25 → RRF 融合)",
                "reused": True,
                "note": ("逐路降级是**既有**行为：依赖未装时该路静默走兜底，"
                         "故 requested 与 effective 可能不同；本字段只报 requested"),
            },
            "entity_available": _entities_available(),
            "entity_note": ("技能实体（data/skills.json 等）**不在版本控制内** ⇒ "
                            "新克隆仓库上可能召回为空，属既有约束而非缺陷"),
            "total_scanned": payload.get("total_scanned"),
            "fallback_used": payload.get("fallback_used"),
            "timing": {"duration_ms": round((time.perf_counter() - started) * 1000.0, 3)},
        },
    }
