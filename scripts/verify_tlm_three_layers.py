"""TLM 三层组装 Mock 数据验证脚本 [TLM-L3]

用途：
- 构造 L0/L1/L2 不同热度特征的 Mock 数据
- 验证 ContextAssembler.assemble 三层组装效果
- 配合 DEBUG 日志排查组装逻辑与性能问题

Mock 数据热度特征：
- L0 热数据：importance 高 + access_count 高 + 最近访问 → 应被 get_hot_records 选中
- L1 温数据：access_count 中等 + data 包含 query 关键词 → FTS5 检索命中
- L2 冷数据：access_count=0 + embedding 相似 → 向量检索命中后从 Markdown 归档懒加载

运行方式：
    python scripts/verify_tlm_three_layers.py

退出码：0 全部通过；1 有检查未通过；2 环境依赖缺失
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import struct
import sys
import tempfile
import time
from pathlib import Path

# 加入项目根（便于直接 python scripts/xxx.py 运行）
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 确保 stdout 行缓冲（重定向到文件时也能实时看到日志）
try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

from agent.memory.adapters.holographic_adapter import HolographicAdapter
from agent.memory.context_assembler import ContextAssembler
from agent.memory.hotness_scorer import HotnessScorer
from agent.memory.markdown_syncer import MarkdownSyncer


# ── Mock embedding 函数 ──

_VEC_DIM = 512  # 与 HolographicAdapter._VEC_DIM 对齐


def mock_embedding(text: str, dim: int = _VEC_DIM) -> list[float]:
    """基于 SHA256 hash 生成确定性向量（便于复现）

    Why: 无需真实模型即可让向量检索可命中；相同 text → 相同向量，
    不同 text → 不同向量（hash 雪崩）。归一化后便于 cosine 相似度。
    """
    if not text:
        return [0.0] * dim
    h = hashlib.sha256(text.encode("utf-8")).digest()
    buf = (h * ((dim * 4 // len(h)) + 1))[: dim * 4]
    vec = list(struct.unpack(f"<{dim}f", buf))
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


# ── Mock 数据集 ──

# Why 单 token 无空格：FTS5 不会触发 phrase query，前缀匹配 + phrase 都能命中 warm 记录
QUERY = "异步编程"  # 验证查询（L1 FTS5 + L2 向量检索共用）

# L0 热数据：importance 高，模拟多次访问后应进 L0 Top-N
L0_RECORDS = [
    {
        "key": "hot_001",
        "data": "Python asyncio 事件循环核心原理：通过 epoll/select 监听 IO 事件，"
                "协程挂起到就绪后回调 resume。",
        "category": "tutorial",
        "importance": 3.0,
    },
    {
        "key": "hot_002",
        "data": "async/await 语法糖本质是协程状态机，Python 3.5+ 引入，"
                "底层依赖 asyncio.Future 与 Task 驱动。",
        "category": "tutorial",
        "importance": 2.5,
    },
]

# L1 温数据：access_count 较低但 data 含 query 关键词，FTS5 应命中
L1_RECORDS = [
    {
        "key": "warm_001",
        "data": "Python 异步编程中常见的回调地狱问题，用 async/await 改写后可读性提升。",
        "category": "qa",
        "importance": 1.0,
    },
    {
        "key": "warm_002",
        "data": "asyncio.gather 与 asyncio.wait 的使用场景对比：gather 保序、wait 灵活。",
        "category": "qa",
        "importance": 1.0,
    },
]

# L2 冷数据：access_count=0，靠向量检索命中 + Markdown 归档懒加载
# 注意 data 故意不含完整 query 关键词（FTS5 不命中），但语义相近（向量检索应命中）
L2_RECORDS = [
    {
        "key": "cold_001",
        "data": "归档笔记：三年前整理的 tornado 框架协程实现，"
                "包含老版本 @gen.coroutine 装饰器用法与 yield Future 模式。",
        "category": "archive",
        "importance": 1.0,
    },
    {
        "key": "cold_002",
        "data": "归档文档：Python 3.4 asyncio 早期 API 设计，"
                "@asyncio.coroutine 已废弃，推荐使用 async def。",
        "category": "archive",
        "importance": 1.0,
    },
]


async def setup_data(adapter: HolographicAdapter,
                     scorer: HotnessScorer,
                     syncer: MarkdownSyncer) -> None:
    """写入 Mock 数据并模拟热度分化

    热度分化策略：
    - L0：record_access 8 次 + timestamp=now（最近访问 + 高频）
    - L1：record_access 2 次 + timestamp=now-4h（中等频率 + 较久前）
    - L2：不调 record_access（access_count=0，永不进 L0）
    """
    now = time.time()

    # 1. 写入所有记录（含 embedding，让向量检索可命中）
    all_records = L0_RECORDS + L1_RECORDS + L2_RECORDS
    for rec in all_records:
        emb = mock_embedding(rec["data"])
        meta = {
            "category": rec["category"],
            "importance": rec["importance"],
        }
        ok = await adapter.save_with_embedding(
            key=rec["key"],
            data=rec["data"],
            metadata=meta,
            embedding=emb,
        )
        if not ok:
            raise RuntimeError(f"save_with_embedding 失败: {rec['key']}")

    # 2. 给 L2 冷数据补一个与 QUERY 相似的 embedding（确保向量检索命中）
    # Why: mock_embedding(QUERY) 与 mock_embedding(cold_xxx) 不同，向量检索不会命中；
    #      这里用 QUERY embedding 覆盖 cold 记录的向量，模拟"语义相似命中冷数据"
    if getattr(adapter, "_vec_available", False):
        query_emb = mock_embedding(QUERY)
        for rec in L2_RECORDS:
            try:
                with adapter._get_conn() as conn:
                    conn.execute(
                        f"DELETE FROM {adapter._VEC_TABLE} WHERE id = ?",
                        (rec["key"],)
                    )
                    import sqlite_vec  # noqa: F401
                    conn.execute(
                        f"INSERT INTO {adapter._VEC_TABLE} (id, embedding) VALUES (?, ?)",
                        (rec["key"], sqlite_vec.serialize_float32(query_emb))
                    )
                    conn.commit()
            except Exception as e:
                print(f"  [warn] 覆盖 cold 向量失败 {rec['key']}: {e}")

    # 3. 模拟热度分化（仅更新内存缓存，get_hot_records 会用缓存覆盖主表值）
    for _ in range(8):
        for rec in L0_RECORDS:
            scorer.record_access(rec["key"], timestamp=now)
    for _ in range(2):
        for rec in L1_RECORDS:
            scorer.record_access(rec["key"], timestamp=now - 3600 * 4)  # 4 小时前

    # 4. 触发 syncer flush，生成 Markdown 归档（L2 懒加载依赖 .md 文件存在）
    syncer._flush()


async def main() -> int:
    # 配置日志：开启 DEBUG 看 ContextAssembler 组装过程
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )
    for noisy in ("urllib3", "asyncio", "sqlite_vec"):
        logging.getLogger(noisy).setLevel(logging.INFO)

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        db = os.path.join(tmp, "mock_tlm.db")
        md_dir = os.path.join(tmp, "mock_md")

        adapter = HolographicAdapter(db_path=db, enable_cache=False)
        scorer = HotnessScorer(adapter)
        syncer = MarkdownSyncer(
            adapter, output_dir=md_dir,
            debounce_seconds=3600,
            batch_threshold=100000,
        )
        adapter.set_scorer(scorer)
        adapter.set_syncer(syncer)
        adapter._embedding_func = lambda text: mock_embedding(text)

        vec_available = getattr(adapter, "_vec_available", False)

        print("=" * 72)
        print("【环境信息】")
        print("=" * 72)
        print(f"db_path: {db}")
        print(f"md_dir:  {md_dir}")
        print(f"sqlite-vec 可用: {vec_available}")
        print(f"HotnessScorer 公式: H = importance × access_count / (hours_since + 1)^0.8")
        if not vec_available:
            print("  [warn] sqlite-vec 不可用，L2 向量检索将跳过（降级验证 L0/L1）")

        print()
        print("=" * 72)
        print("【步骤 1】写入 Mock 数据并模拟热度分化")
        print("=" * 72)
        await setup_data(adapter, scorer, syncer)

        md_files = list(Path(md_dir).rglob("*.md"))
        print(f"Markdown 归档文件数: {len(md_files)}")
        for f in md_files:
            print(f"  - {f.relative_to(md_dir)}")

        print()
        print("热度分布（get_hot_records Top-5）:")
        hot = scorer.get_hot_records(top_n=5)
        for i, rec in enumerate(hot, 1):
            print(f"  [{i}] key={rec.get('key'):12s} "
                  f"hotness={rec.get('hotness', 0):.3f} "
                  f"access_count={rec.get('access_count', 0)} "
                  f"importance={rec.get('importance', 0)}")

        print()
        print("=" * 72)
        print(f"【步骤 2】调 ContextAssembler.assemble(query={QUERY!r})")
        print("=" * 72)
        # l0_top_n=2：只取热度 Top-2，确保 L0 只含 hot_001/hot_002
        assembler = ContextAssembler(adapter, scorer, syncer, l0_top_n=2)
        result = await assembler.assemble(QUERY, max_tokens=2000)

        print()
        print(f"L0 热数据摘要 (tokens={result['meta']['l0_tokens']}/{assembler.l0_token_limit}):")
        print("-" * 72)
        print(result["L0"] or "(空)")
        print()
        print(f"L1 温数据检索结果 ({result['meta']['l1_count']} 条):")
        print("-" * 72)
        for i, r in enumerate(result["L1"], 1):
            meta = getattr(r, "metadata", None) or {}
            key = meta.get("key", "?") if isinstance(meta, dict) else "?"
            content = getattr(r, "content", "") or ""
            data = content[:80] if isinstance(content, str) else str(content)[:80]
            print(f"  [{i}] key={key} data={data}...")
        print()
        print(f"L2 冷数据 fragments ({result['meta']['l2_count']} 条):")
        print("-" * 72)
        for i, f in enumerate(result["L2"], 1):
            print(f"  [{i}] key={f['key']} source={f['source']}")
            print(f"      fragment: {f['fragment'][:120]}...")
        print()
        print(f"组装耗时: {result['meta']['elapsed_ms']:.2f}ms")

        print()
        print("=" * 72)
        print("【步骤 3】三层组装效果断言")
        print("=" * 72)
        checks = [
            ("L0 非空（应命中 hot_001/hot_002）",
             bool(result["L0"])
             and "hot_001" in result["L0"]
             and "hot_002" in result["L0"]),
            ("L0 不含 warm/cold（温/冷数据不应进热数据层）",
             "warm_" not in result["L0"] and "cold_" not in result["L0"]),
            ("L1 非空（FTS5 应命中 warm 记录）",
             len(result["L1"]) > 0),
            ("L1 含 warm 记录",
             any("warm" in ((getattr(r, "metadata", None) or {}).get("key", ""))
                 for r in result["L1"])),
            ("L0 token 未超硬上限 300",
             result["meta"]["l0_tokens"] <= assembler.l0_token_limit),
        ]
        if vec_available:
            checks.extend([
                ("L2 非空（向量检索应命中 cold 记录）",
                 len(result["L2"]) > 0),
                ("L2 含 cold 记录",
                 any("cold" in f["key"] for f in result["L2"])),
                ("L2 fragment 全部来自 markdown_archive",
                 all(f["source"] == "markdown_archive" for f in result["L2"])),
                ("L2 fragment 非空字符串",
                 all(f["fragment"] for f in result["L2"])),
            ])
        else:
            checks.append(
                ("L2 跳过（sqlite-vec 不可用时降级，符合预期）",
                 len(result["L2"]) == 0)
            )

        all_pass = True
        for desc, ok in checks:
            mark = "✅" if ok else "❌"
            print(f"  {mark} {desc}")
            if not ok:
                all_pass = False

        print()
        if all_pass:
            print("结论: ✅ 三层组装效果符合预期")
            return 0
        else:
            print("结论: ❌ 部分检查未通过，请看上方 DEBUG 日志排查")
            return 1


if __name__ == "__main__":
    try:
        rc = asyncio.run(main())
    except KeyboardInterrupt:
        rc = 130
    sys.exit(rc)
