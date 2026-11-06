# -*- coding: utf-8 -*-
"""C-7 第二步：技能 × 元生功能的**向量候选**（默认关，provider 可选 local / api）。

为什么需要：字面口径（containment，阈值 0.25）在全语料的上限只有 0.133 ⇒ 清单恒空。
「写了一条 read_file 技能」这种重复**语义上**看得出来、字面看不出来，得靠 embedding。

**三种 embedding 是排它单选**（同一个时刻只有一个生效，由 settings 的 enum 校验器保证）：
  · st_bge_m3（默认）：复用仓库现成的 BGE-m3（sentence-transformers，1024 维，中文最好）。
    模型加载实测 ~87.7s（与 `CP_SKILL_VECTOR_PREWARM` 预热的是同一个模型家族），
    故**总开关默认关**、按需开启；开启后结果按文本摘要缓存，重复打开面板不再重算。
  · native_minilm：复用 chromadb 自带的 onnxruntime embedding（all-MiniLM-L6-v2，384 维，
    不拉 torch、内存最省，中文弱于 BGE-m3）。
  · api：OpenAI 兼容的 `POST {base}/embeddings`（无本地依赖、无内存压力，按量计费/需联网）
    ⇒ 默认 `CP_SKILL_OVERLAP_EMBED_API_BASE` 为空 = 不可用，由 Owner 在「设置中心」选到 api
    并填端点后生效。密钥只从 env 读，**不打印、不进日志**。

【为什么做成单选而不是三个布尔】三种后端产出的向量维度不同、语义空间也不同，
同时开两个只会让候选列自相矛盾（甚至把 384 维和 1024 维的分数混在一起排序）。
故：`CP_SKILL_OVERLAP_VECTOR` 只管**开不开**；`CP_SKILL_OVERLAP_VECTOR_PROVIDER` 决定**用哪个**，
取值只接受这三种，其余（含空串）回退默认 st_bge_m3；旧值 `local` 作为别名等价于 st_bge_m3。

【不易】阈值保持 0.25 不下调：0.1 会把「请 / 文件 / 使用」这类泛词撞出来的对放进来
（2026-11-01 字面版实测 3 条全是假阳性）。向量候选与字面候选**并列呈现**，不合并计数。
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: 向量候选的余弦阈值（与字面口径同一个数，别各定一套）
VECTOR_THRESHOLD = 0.25
#: 每条技能最多报几个向量候选
TOP_K = 3
#: 三种 embedding 是**排它单选**（顺序即设置中心下拉的顺序）
PROVIDERS = ("st_bge_m3", "native_minilm", "api")
#: 旧值/别名 → 规范值（`local` 是 C-7 第一版写过的名字）
_ALIASES = {"local": "st_bge_m3", "bge": "st_bge_m3", "bge-m3": "st_bge_m3",
            "minilm": "native_minilm", "onnx": "native_minilm",
            "openai": "api"}
#: 本地 embedding 默认模型（与向量腿同一个家族）
DEFAULT_LOCAL_MODEL = "BAAI/bge-m3"
#: chromadb 自带 onnx 模型名（只用于展示，实际由 chromadb 决定）
NATIVE_MINILM_MODEL = "all-MiniLM-L6-v2 (onnx)"
#: API embedding 默认模型（OpenAI 兼容命名的通用默认；Owner 可按供应商改）
DEFAULT_API_MODEL = "text-embedding-3-small"

_LOCK = threading.Lock()
_CACHE: Dict[str, List[float]] = {}
_CACHE_MAX = 4000
_LOCAL_MODEL: List[Any] = []  # [SentenceTransformer 实例] —— 懒加载
_MINILM_EF: List[Any] = []  # [chromadb 默认 embedding function] —— 懒加载


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def enabled() -> bool:
    """总开关（默认关）。"""
    return _env_bool("CP_SKILL_OVERLAP_VECTOR", False)


def provider() -> str:
    """embedding 来源：**三选一**（st_bge_m3 默认 / native_minilm / api）。

    其余值（含空串、拼错、旧别名）一律规范化：别名先映射，非三值之一 ⇒ 回退 st_bge_m3。
    这样「三种 embedding 排它」不靠调用方自觉，而是每次读都只可能拿到一个。
    """
    raw = str(os.environ.get("CP_SKILL_OVERLAP_VECTOR_PROVIDER", "") or "").strip().lower()
    raw = _ALIASES.get(raw, raw)
    return raw if raw in PROVIDERS else "st_bge_m3"


def model_name() -> str:
    """模型名：显式配置优先，否则按 provider 取默认。"""
    raw = str(os.environ.get("CP_SKILL_OVERLAP_EMBED_MODEL", "") or "").strip()
    if raw:
        return raw
    prov = provider()
    if prov == "api":
        return DEFAULT_API_MODEL
    if prov == "native_minilm":
        return NATIVE_MINILM_MODEL
    return DEFAULT_LOCAL_MODEL


def _key(provider_name: str, model: str, text: str) -> str:
    return "%s|%s|%s" % (provider_name, model,
                         hashlib.sha256(text.encode("utf-8")).hexdigest())


def _cache_get(k: str) -> Optional[List[float]]:
    with _LOCK:
        return _CACHE.get(k)


def _cache_put(k: str, vec: List[float]) -> None:
    with _LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.clear()  # 简易上限：满了整表清空（面板按需调用，不做精细 LRU）
        _CACHE[k] = vec


def reset_cache() -> None:
    """清空向量缓存（测试隔离；换模型/换端点后也可手动刷新）。"""
    with _LOCK:
        _CACHE.clear()


def _normalize(vec: Sequence[float]) -> List[float]:
    norm = sum(float(x) * float(x) for x in vec) ** 0.5
    if norm <= 0:
        return [0.0 for _ in vec]
    return [float(x) / norm for x in vec]


def _embed_local(texts: List[str]) -> List[List[float]]:
    """默认后端：本地 BGE-m3（懒加载；加载失败抛异常，由上层收口成「不可用」）。"""
    if not _LOCAL_MODEL:
        from sentence_transformers import SentenceTransformer  # 重型依赖：只有开启时才导入

        _LOCAL_MODEL.append(SentenceTransformer(model_name(), device="cpu"))
    model = _LOCAL_MODEL[0]
    vecs = model.encode(texts, normalize_embeddings=True, batch_size=8,
                        show_progress_bar=False)
    return [[float(x) for x in row] for row in vecs]


def _embed_minilm(texts: List[str]) -> List[List[float]]:
    """备选本地后端：chromadb 自带 onnxruntime all-MiniLM-L6-v2（不拉 torch）。"""
    if not _MINILM_EF:
        from chromadb.utils import embedding_functions  # 重型依赖：只有选中时才导入

        _MINILM_EF.append(embedding_functions.DefaultEmbeddingFunction())
    ef = _MINILM_EF[0]
    vecs = ef(list(texts))
    return [[float(x) for x in row] for row in vecs]


def _embed_api(texts: List[str]) -> List[List[float]]:
    """OpenAI 兼容 /embeddings。端点为空 ⇒ 明确报错（不是静默降级）。"""
    base = str(os.environ.get("CP_SKILL_OVERLAP_EMBED_API_BASE", "") or "").strip()
    key = str(os.environ.get("CP_SKILL_OVERLAP_EMBED_API_KEY", "") or "").strip()
    if not base:
        raise RuntimeError("CP_SKILL_OVERLAP_EMBED_API_BASE 未配置")
    url = base.rstrip("/") + "/embeddings"
    out: List[List[float]] = []
    for start in range(0, len(texts), 32):
        chunk = texts[start:start + 32]
        body = json.dumps({"model": model_name(), "input": chunk}).encode("utf-8")
        req = urllib.request.Request(url, data=body, method="POST")
        req.add_header("Content-Type", "application/json")
        if key:
            req.add_header("Authorization", "Bearer " + key)
        with urllib.request.urlopen(req, timeout=60) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        rows = payload.get("data") or []
        if len(rows) != len(chunk):
            raise RuntimeError("embedding 返回条数不符")
        out.extend([_normalize(r.get("embedding") or []) for r in rows])
    return out


def embed_texts(texts: Sequence[str]) -> List[List[float]]:
    """批量取向量（带缓存）。provider=api 时按 base/model 分键。"""
    prov, model = provider(), model_name()
    out: List[Optional[List[float]]] = [None] * len(texts)
    todo: List[Tuple[int, str]] = []
    for i, t in enumerate(texts):
        k = _key(prov, model, t)
        hit = _cache_get(k)
        if hit is not None:
            out[i] = hit
        else:
            todo.append((i, t))
    if todo:
        pending = [t for _, t in todo]
        # 三选一：每次只走**一个**后端，绝不并行/混用（维度不同，混了分数不可比）
        if prov == "api":
            fresh = _embed_api(pending)
        elif prov == "native_minilm":
            fresh = _embed_minilm(pending)
        else:
            fresh = _embed_local(pending)
        for (i, t), vec in zip(todo, fresh):
            vec_n = _normalize(vec)  # 三个后端都归一化，余弦=点积（口径统一）
            _cache_put(_key(prov, model, t), vec_n)
            out[i] = vec_n
    return [v if v is not None else [] for v in out]


def _cos(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b:
        return 0.0
    n = min(len(a), len(b))
    return float(sum(float(a[i]) * float(b[i]) for i in range(n)))


def vector_candidates(skills: List[Dict[str, object]],
                      tools: List[Dict[str, object]],
                      *, threshold: float = VECTOR_THRESHOLD,
                      top_k: int = TOP_K) -> Dict[str, Any]:
    """算「技能 × 元生功能」的向量候选。**永不抛异常**（失败如实写进 stats）。"""
    t0 = time.time()
    stats: Dict[str, Any] = {
        "enabled": enabled(), "provider": provider(), "model": model_name(),
        "threshold": threshold, "top_k": top_k,
        "skills_embedded": 0, "tools_embedded": 0, "pairs": 0,
        "candidates": 0, "elapsed_ms": 0.0, "error": "",
    }
    if not stats["enabled"]:
        stats["error"] = "disabled:CP_SKILL_OVERLAP_VECTOR=0"
        return {"items": [], "stats": stats}
    try:
        from agent.skills_mgmt.native_functions import _body_of  # 同一份正文口径

        skill_rows = [(str(s.get("skill_id") or ""), _body_of(s)) for s in skills]
        skill_rows = [(sid, body) for sid, body in skill_rows if body.strip()]
        tool_rows = [(str(t.get("id") or ""), str(t.get("summary") or "")) for t in tools]
        tool_rows = [(tid, txt) for tid, txt in tool_rows if txt.strip()]
        if not skill_rows or not tool_rows:
            stats["error"] = "no_comparable_text"
            return {"items": [], "stats": stats}
        svecs = embed_texts([body for _, body in skill_rows])
        tvecs = embed_texts([txt for _, txt in tool_rows])
        stats["skills_embedded"] = len(svecs)
        stats["tools_embedded"] = len(tvecs)
        items: List[Dict[str, Any]] = []
        for (sid, _body), sv in zip(skill_rows, svecs):
            scored = []
            for (tid, _txt), tv in zip(tool_rows, tvecs):
                stats["pairs"] += 1
                score = _cos(sv, tv)
                if score >= threshold:
                    scored.append({"native_id": tid, "kind": "tool",
                                   "score": round(score, 3), "reason": "vector"})
            scored.sort(key=lambda x: x["score"], reverse=True)
            if scored:
                items.append({"skill_id": sid, "matches": scored[:top_k]})
        items.sort(key=lambda h: h["matches"][0]["score"], reverse=True)
        stats["candidates"] = len(items)
        return {"items": items, "stats": stats}
    except Exception as exc:  # noqa: BLE001  面板不能因为向量不可用就 500
        # 【C-6】只回类名；原文进日志（key/端点永不进日志）
        stats["error"] = type(exc).__name__
        import logging
        logging.getLogger(__name__).warning(
            "[overlap_vector] 向量候选不可用（provider=%s model=%s）: %s",
            stats["provider"], stats["model"], type(exc).__name__, exc_info=True)
        return {"items": [], "stats": stats}
    finally:
        stats["elapsed_ms"] = round((time.time() - t0) * 1000, 1)