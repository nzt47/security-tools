#!/usr/bin/env python3
"""
Mock 数据构造脚本（一次性测试工具）

功能：
- 构造 mock source-db（holographic.db，含 memory_items + memory_fts）
- 构造 mock source-chroma（含 384 维旧向量，用于验证向量重生成）
- 用于本地端到端验证 migrate_to_tlm.py 迁移逻辑与向量重生成性能基准

数据规模（默认）：
- 主表：500 条（带 metadata、created_at）
- chroma 向量：50 条 384 维（key 与主表前 50 条对齐，验证复用）
- 主表其余 450 条无向量（验证重生成）

用法：
    python scripts/seed_mock_source.py
    python scripts/seed_mock_source.py --n-main 1000 --n-vec 100 --vec-dim 384
    python scripts/seed_mock_source.py --out-dir ./data/mock --clean

约束：
- 只写入指定 out-dir（默认 data/mock/），不污染生产 data/memory/
- chromadb 不可用时跳过 chroma 构造（仅打印警告，迁移将全量重生成）
"""

from __future__ import annotations

import argparse
import json
import random
import shutil
import sqlite3
import sys
import time
from pathlib import Path

# 确保项目根目录在 sys.path（脚本直接运行时 sys.path[0] 是 scripts/）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

DEFAULT_OUT_DIR = "./data/mock"
DEFAULT_N_MAIN = 500
DEFAULT_N_VEC = 50
DEFAULT_VEC_DIM = 384
DEFAULT_COLLECTION = "agent_memory"


def log(msg: str, *, level: str = "INFO") -> None:
    print(f"[{level}] {msg}", file=sys.stderr, flush=True)


def build_source_db(path: Path, n_main: int) -> None:
    """构造 source-db：用 HolographicAdapter 建表，再批量插入主表 + FTS"""
    # 复用 HolographicAdapter 建 schema（确保与生产一致）
    from agent.memory.adapters.holographic_adapter import HolographicAdapter
    HolographicAdapter(db_path=str(path), enable_cache=False)

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    now = time.time()

    # 构造多样化的测试内容（中文 + 英文 + 特殊字符，便于 FTS 校验）
    contents = [
        "用户询问了关于天气的信息，需要查询今日天气预报",
        "系统记录了用户的偏好设置：偏好中文交互",
        "This is an English memory entry for FTS testing",
        "任务提醒：明天下午三点有项目评审会议",
        "用户反馈了一个 bug：界面在移动端显示异常",
        "知识条目：Python 的 GIL 限制了多线程性能",
        "用户画像更新：年龄 30，技术背景，偏好简洁回答",
        "对话历史：用户询问了迁移脚本的实现细节",
        "配置变更：启用了 TLM 三表统一记忆层",
        "错误日志：sqlite-vec 扩展加载失败，降级为纯 FTS5",
    ]

    rows = []
    for i in range(n_main):
        key = f"mem_{i:06d}"
        data = f"[{i:04d}] {contents[i % len(contents)]} (序号 {i})"
        metadata = json.dumps(
            {"index": i, "tag": "mock", "category": "test" if i % 2 == 0 else "dev"},
            ensure_ascii=False,
        )
        created = now - (n_main - i) * 60  # 递增时间戳
        rows.append((key, data, metadata, created, created, 0))

    conn.executemany(
        "INSERT OR REPLACE INTO memory_items "
        "(key, data, metadata, created_at, updated_at, hit_count) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    # 同步 FTS
    conn.executemany(
        "INSERT INTO memory_fts (key, data, metadata) VALUES (?, ?, ?)",
        [(r[0], r[1], r[2]) for r in rows],
    )
    conn.commit()
    conn.close()
    log(f"source-db 构造完成: {path} ({n_main} 条主表记录)")


def build_source_chroma(
    path: Path, n_vec: int, vec_dim: int, collection: str
) -> bool:
    """构造 source-chroma：n_vec 条 vec_dim 维向量

    Returns:
        True=成功，False=chromadb 不可用
    """
    try:
        import chromadb
    except ImportError:
        log("chromadb 未安装，跳过 chroma 构造", level="WARNING")
        return False

    try:
        client = chromadb.PersistentClient(path=str(path))
        try:
            client.delete_collection(name=collection)
        except Exception:
            pass
        coll = client.create_collection(name=collection)

        # key 与 source-db 前 n_vec 条对齐
        ids = [f"mem_{i:06d}" for i in range(n_vec)]
        random.seed(42)
        embeddings = [[random.gauss(0, 1) for _ in range(vec_dim)] for _ in range(n_vec)]
        metadatas = [{"index": i, "source": "mock"} for i in range(n_vec)]
        coll.add(ids=ids, embeddings=embeddings, metadatas=metadatas)
        log(f"source-chroma 构造完成: {path} ({n_vec} 条 {vec_dim} 维向量)")
        return True
    except Exception as e:
        log(f"chroma 构造失败（Windows 已知不兼容）: {type(e).__name__}: {e}", level="WARNING")
        log("仅构造 source-db，迁移时将全量重生成向量", level="WARNING")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description="构造 mock source-db + source-chroma（用于本地测试 migrate_to_tlm.py）",
    )
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR,
                        help=f"输出目录（默认 {DEFAULT_OUT_DIR}）")
    parser.add_argument("--n-main", type=int, default=DEFAULT_N_MAIN,
                        help=f"主表记录数（默认 {DEFAULT_N_MAIN}）")
    parser.add_argument("--n-vec", type=int, default=DEFAULT_N_VEC,
                        help=f"chroma 向量数（默认 {DEFAULT_N_VEC}）")
    parser.add_argument("--vec-dim", type=int, default=DEFAULT_VEC_DIM,
                        help=f"chroma 向量维度（默认 {DEFAULT_VEC_DIM}，与目标 512 不一致以验证重生成）")
    parser.add_argument("--collection", default=DEFAULT_COLLECTION,
                        help=f"chroma collection 名（默认 {DEFAULT_COLLECTION}）")
    parser.add_argument("--clean", action="store_true",
                        help="构造前清空 out-dir")

    args = parser.parse_args()
    out_dir = Path(args.out_dir)

    if args.clean and out_dir.exists():
        shutil.rmtree(out_dir)
        log(f"已清空 {out_dir}")
    out_dir.mkdir(parents=True, exist_ok=True)

    source_db = out_dir / "holographic.db"
    source_chroma = out_dir / "chroma"

    # 删除已存在的旧文件（避免 schema 污染）
    if source_db.exists():
        source_db.unlink()
    if source_chroma.exists():
        shutil.rmtree(source_chroma)

    log("=" * 60)
    log("开始构造 mock 数据")
    log(f"  输出目录: {out_dir}")
    log(f"  主表记录: {args.n_main} 条")
    log(f"  chroma 向量: {args.n_vec} 条 × {args.vec_dim} 维")
    log("=" * 60)

    build_source_db(source_db, args.n_main)
    chroma_ok = build_source_chroma(
        source_chroma, args.n_vec, args.vec_dim, args.collection
    )

    log("")
    log("构造完成，可用以下命令测试迁移：")
    log(f"  python scripts/migrate_to_tlm.py --no-encoder \\")
    log(f"    --source-db {source_db} \\")
    log(f"    --source-chroma {source_chroma} \\")
    log(f"    --target-db {out_dir / 'memory_tlm.db'} \\")
    log(f"    --backup-dir {out_dir / 'backup'}")
    if not chroma_ok:
        log("（chroma 未构造，迁移时将全量重生成向量）", level="WARNING")
    return 0


if __name__ == "__main__":
    sys.exit(main())
