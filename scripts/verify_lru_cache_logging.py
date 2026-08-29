#!/usr/bin/env python3
"""验证 tool_router_hybrid.EmbeddingIndex 的 query embedding LRU 缓存日志。

Daily Regression(e2e-recovery-tests job)以
`python scripts/verify_lru_cache_logging.py` 运行,环境:
    HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 AGENT_HYBRID_EMBEDDING=0

【设计】不启动真实子进程 worker、不加载模型:直接注入 _ensure_worker=True 与
确定性 _encode_via_worker,驱动 search() 走 cache.miss → cache.hit 两条日志路径,
断言:
  1. 首次查询 → embedding.cache.miss 日志 + misses=1
  2. 重复查询 → embedding.cache.hit 日志 + hits=1
  3. get_cache_stats() 统计正确(hit_rate=0.5, cache_size=1)
退出码: 0=通过, 1=失败(供 CI 门禁)。
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from agent.tool_router_hybrid import EmbeddingIndex  # noqa: E402

_LOGGER_NAME = "agent.tool_router_hybrid"


def _capture_actions() -> list[str]:
    """捕获 tool_router_hybrid logger 的 cache 相关 action 日志"""
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    seen: list[str] = []

    class _Handler(logging.Handler):
        def emit(self, record):
            msg = getattr(record, "msg", None)
            if isinstance(msg, dict) and msg.get("action", "").startswith("embedding.cache."):
                seen.append(msg["action"])

    handler = _Handler()
    logger.addHandler(handler)
    return seen, lambda: logger.removeHandler(handler)


def _main() -> int:
    idx = EmbeddingIndex()
    # 注入确定性测试数据(绕过子进程/模型)
    idx._doc_ids = ["d1", "d2"]
    idx._embeddings = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    idx._ensure_worker = lambda: True  # type: ignore[method-assign]

    def _fake_encode(texts):
        if isinstance(texts, str):
            texts = [texts]
        return [[float(len(t)) / 100.0, 0.5] for t in texts]

    idx._encode_via_worker = _fake_encode  # type: ignore[method-assign]

    seen, detach = _capture_actions()
    try:
        r1 = idx.search("hello world", top_k=1)  # 首次 → miss
        r2 = idx.search("hello world", top_k=1)  # 重复 → hit
    finally:
        detach()

    stats = idx.get_cache_stats()
    ok = True
    problems: list[str] = []

    if stats.get("misses") != 1:
        ok = False
        problems.append(f"misses={stats.get('misses')} 期望 1")
    if stats.get("hits") != 1:
        ok = False
        problems.append(f"hits={stats.get('hits')} 期望 1")
    if "embedding.cache.miss" not in seen:
        ok = False
        problems.append("未捕获 embedding.cache.miss 日志")
    if "embedding.cache.hit" not in seen:
        ok = False
        problems.append("未捕获 embedding.cache.hit 日志")
    if stats.get("cache_size") != 1:
        ok = False
        problems.append(f"cache_size={stats.get('cache_size')} 期望 1")
    if r1 != r2:
        ok = False
        problems.append("同查询两次结果不一致")

    print("=== LRU 缓存日志验证 ===")
    print(f"stats: {stats}")
    print(f"captured actions: {seen}")
    if ok:
        print("✅ LRU query 缓存 hit/miss 日志与统计验证通过")
        return 0
    print("❌ LRU 缓存验证失败:")
    for p in problems:
        print(f"  - {p}")
    return 1


if __name__ == "__main__":
    sys.exit(_main())
