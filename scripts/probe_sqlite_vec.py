"""sqlite-vec 可用性探针

测试 sqlite-vec 在当前平台（默认 Windows）是否可用，验证以下能力：
1. pip 安装 sqlite-vec
2. 加载 sqlite 扩展
3. 创建 vec0 虚拟表
4. 插入 512 维向量
5. KNN 查询

用法:
    python scripts/probe_sqlite_vec.py

输出:
    明确的可用 / 不可用结论 + 降级建议

约束:
    - 不污染主 venv：使用 --user 安装或独立 venv
    - 失败时不抛出异常，仅输出明确结论
"""

from __future__ import annotations

import json
import os
import platform
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPORT: list[dict] = []


def _log(stage: str, ok: bool, msg: str, *, duration_ms: float = 0.0, **extra):
    entry = {
        "stage": stage,
        "ok": ok,
        "msg": msg,
        "duration_ms": round(duration_ms, 2),
        **extra,
    }
    REPORT.append(entry)
    flag = "[OK]" if ok else "[FAIL]"
    print(f"{flag} {stage}: {msg} ({duration_ms:.1f}ms)", file=sys.stderr)
    if extra:
        for k, v in extra.items():
            print(f"      └─ {k}: {v}", file=sys.stderr)


def _try_install() -> bool:
    """尝试通过 pip --user 安装 sqlite-vec"""
    t0 = time.perf_counter()
    try:
        # 优先尝试已安装
        import sqlite_vec  # type: ignore
        _log("import_direct", True, "sqlite-vec 已安装可直接导入",
             duration_ms=(time.perf_counter() - t0) * 1000,
             version=getattr(sqlite_vec, "__version__", "unknown"))
        return True
    except ImportError:
        pass

    # 尝试 pip install --user
    try:
        cmd = [sys.executable, "-m", "pip", "install", "--user", "--quiet",
               "sqlite-vec"]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=120
        )
        duration = (time.perf_counter() - t0) * 1000
        if result.returncode != 0:
            _log("pip_install", False,
                 "pip install --user sqlite-vec 失败",
                 duration_ms=duration,
                 returncode=result.returncode,
                 stderr=result.stderr[:300] if result.stderr else "")
            return False
        _log("pip_install", True, "pip install --user sqlite-vec 成功",
             duration_ms=duration)
        return True
    except subprocess.TimeoutExpired:
        _log("pip_install", False, "pip install 超时 (120s)",
             duration_ms=(time.perf_counter() - t0) * 1000)
        return False
    except Exception as e:
        _log("pip_install", False, f"pip install 异常: {type(e).__name__}: {e}",
             duration_ms=(time.perf_counter() - t0) * 1000)
        return False


def _loadable_path() -> str | None:
    """获取 sqlite-vec 的 loadable path"""
    try:
        import sqlite_vec  # type: ignore
        path = getattr(sqlite_vec, "loadable_path", None)
        if callable(path):
            return path()
        if isinstance(path, str):
            return path
        # 旧版 API: 通过 adapter
        adapter = getattr(sqlite_vec, "load", None)
        if callable(adapter):
            # sqlite_vec.load() 在新版返回一个 adapter 可作为 conn.load_extension 的参数
            # 但此处我们想要的是路径字符串
            return None
        return None
    except Exception:
        return None


def _try_load(conn: sqlite3.Connection) -> bool:
    """尝试加载 sqlite-vec 扩展到指定连接"""
    # 方式 1: sqlite_vec.loadable_path() + conn.load_extension
    path = _loadable_path()
    if path:
        try:
            conn.enable_load_extension(True)
            conn.load_extension(path)
            conn.enable_load_extension(False)
            return True
        except Exception as e:
            _log("load_extension", False,
                 f"通过 loadable_path 加载失败: {type(e).__name__}: {e}",
                 loadable_path=path)
            # 继续尝试方式 2
        else:
            _log("load_extension", True,
                 f"通过 loadable_path 加载成功",
                 loadable_path=path)
            return True

    # 方式 2: sqlite_vec.load(conn) (新版 API)
    try:
        import sqlite_vec  # type: ignore
        load_fn = getattr(sqlite_vec, "load", None)
        if callable(load_fn):
            load_fn(conn)
            _log("load_extension", True,
                 "通过 sqlite_vec.load(conn) 加载成功")
            return True
    except Exception as e:
        _log("load_extension", False,
             f"sqlite_vec.load(conn) 失败: {type(e).__name__}: {e}")

    return False


def _serialize_vec(v: list[float]) -> bytes:
    """将 float list 序列化为 sqlite-vec 期望的 little-endian float32 blob"""
    return struct.pack(f"<{len(v)}f", *v)


def _test_vec0_table(conn: sqlite3.Connection, dim: int = 512,
                     n_vectors: int = 100) -> dict:
    """测试 vec0 虚拟表: 创建 / 插入 / KNN 查询"""
    table_name = "test_vec"
    result: dict = {
        "dim": dim,
        "n_vectors": n_vectors,
        "create_ok": False,
        "insert_ok": False,
        "knn_ok": False,
        "knn_recall": 0.0,
        "insert_ms": 0.0,
        "knn_ms": 0.0,
    }

    # 创建
    try:
        conn.execute(f"DROP TABLE IF EXISTS {table_name}")
        conn.execute(
            f"CREATE VIRTUAL TABLE {table_name} USING vec0("
            f"  id INTEGER PRIMARY KEY, "
            f"  embedding FLOAT[{dim}]"
            f")"
        )
        conn.commit()
        result["create_ok"] = True
    except Exception as e:
        _log("vec0_create", False,
             f"创建 vec0 虚拟表失败: {type(e).__name__}: {e}")
        return result
    _log("vec0_create", True, f"vec0 虚拟表创建成功 (dim={dim})")

    # 插入
    t0 = time.perf_counter()
    try:
        rows = []
        for i in range(n_vectors):
            # 生成一个有结构的向量: 前 10 维编码 id，其他随机但稳定
            vec = [0.0] * dim
            for j in range(min(10, dim)):
                vec[j] = float((i >> j) & 1)
            # 剩余维度用 hash-like 稳定值
            for j in range(10, dim):
                vec[j] = ((i * 31 + j * 17) % 1000) / 1000.0
            rows.append((i, _serialize_vec(vec)))

        conn.executemany(
            f"INSERT INTO {table_name} (id, embedding) VALUES (?, ?)",
            rows,
        )
        conn.commit()
        result["insert_ok"] = True
        result["insert_ms"] = (time.perf_counter() - t0) * 1000
    except Exception as e:
        _log("vec0_insert", False,
             f"插入向量失败: {type(e).__name__}: {e}",
             duration_ms=(time.perf_counter() - t0) * 1000)
        return result
    _log("vec0_insert", True,
         f"插入 {n_vectors} 条 {dim} 维向量成功",
         duration_ms=result["insert_ms"])

    # KNN 查询: 查询与 id=5 完全相同的向量
    t0 = time.perf_counter()
    try:
        # 重新构造 id=5 的向量
        query_vec = [0.0] * dim
        for j in range(min(10, dim)):
            query_vec[j] = float((5 >> j) & 1)
        for j in range(10, dim):
            query_vec[j] = ((5 * 31 + j * 17) % 1000) / 1000.0
        query_blob = _serialize_vec(query_vec)

        cur = conn.execute(
            f"SELECT id, distance FROM {table_name} "
            f"WHERE embedding MATCH ? "
            f"ORDER BY distance "
            f"LIMIT 5",
            (query_blob,),
        )
        rows = cur.fetchall()
        result["knn_ms"] = (time.perf_counter() - t0) * 1000
        result["knn_ok"] = True

        # Recall@1: 第一条应该就是 id=5
        if rows and rows[0][0] == 5:
            result["knn_recall"] = 1.0
        else:
            result["knn_recall"] = 0.0
    except Exception as e:
        _log("vec0_knn", False,
             f"KNN 查询失败: {type(e).__name__}: {e}",
             duration_ms=(time.perf_counter() - t0) * 1000)
        return result
    _log("vec0_knn", True,
         f"KNN 查询成功, recall@1={result['knn_recall']}",
         duration_ms=result["knn_ms"],
         topk_result_ids=[r[0] for r in rows[:5]])

    return result


def _check_sqlite_compile_options() -> dict:
    """检查 sqlite3 编译选项"""
    info = {
        "sqlite_version": sqlite3.sqlite_version,
        "python_version": sys.version.split()[0],
        "platform": platform.platform(),
        "has_load_extension": False,
        "compile_options": [],
    }
    try:
        cur = sqlite3.connect(":memory:").execute("PRAGMA compile_options")
        opts = [row[0] for row in cur.fetchall()]
        info["compile_options"] = opts
        info["has_load_extension"] = "ENABLE_LOAD_EXTENSION" in opts or \
            "ENABLE_LOAD_EXTENSION=1" in opts
    except Exception:
        pass
    return info


def main() -> int:
    print("=" * 60, file=sys.stderr)
    print("sqlite-vec 可用性探针", file=sys.stderr)
    print("=" * 60, file=sys.stderr)

    # 阶段 0: 环境信息
    env_info = _check_sqlite_compile_options()
    _log("env_check", True,
         f"SQLite {env_info['sqlite_version']} / "
         f"Python {env_info['python_version']} / "
         f"{env_info['platform']}",
         has_load_extension=env_info["has_load_extension"])

    if not env_info["has_load_extension"]:
        _log("env_check", False,
             "当前 sqlite3 编译未启用 ENABLE_LOAD_EXTENSION，"
             "无法加载扩展")

    # 阶段 1: 安装
    if not _try_install():
        _emit_final_report(available=False,
                           reason="sqlite-vec 安装失败",
                           env_info=env_info)
        return 1

    # 阶段 2: 加载扩展
    # 使用临时 db 测试
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "probe.db"
        try:
            conn = sqlite3.connect(str(db_path))
            conn.enable_load_extension(True)
        except AttributeError as e:
            _log("conn_init", False,
                 f"sqlite3 连接不支持 enable_load_extension: {e}")
            _emit_final_report(available=False,
                               reason="sqlite3 不支持扩展加载",
                               env_info=env_info)
            return 1
        except Exception as e:
            _log("conn_init", False,
                 f"sqlite3 连接初始化失败: {type(e).__name__}: {e}")
            _emit_final_report(available=False,
                               reason="sqlite3 连接初始化失败",
                               env_info=env_info)
            return 1

        if not _try_load(conn):
            _emit_final_report(available=False,
                               reason="sqlite-vec 扩展加载失败",
                               env_info=env_info)
            return 1

        # 阶段 3: vec0 虚拟表 + 512 维向量 + KNN
        vec_result = _test_vec0_table(conn, dim=512, n_vectors=100)

        # 验证 vec0 函数 (可选)
        try:
            ver = conn.execute("SELECT vec_version()").fetchone()
            if ver:
                _log("vec_version", True, f"vec_version={ver[0]}")
        except Exception:
            pass

        conn.close()

    # 最终结论
    available = (
        vec_result["create_ok"]
        and vec_result["insert_ok"]
        and vec_result["knn_ok"]
        and vec_result["knn_recall"] == 1.0
    )

    _emit_final_report(
        available=available,
        reason=("512 维向量 vec0 虚拟表 + KNN 查询全通过"
                if available
                else "vec0 虚拟表或 KNN 查询未通过"),
        env_info=env_info,
        vec_result=vec_result,
    )
    return 0 if available else 1


def _emit_final_report(*, available: bool, reason: str,
                       env_info: dict, vec_result: dict | None = None):
    """输出最终报告（JSON 输出到 stdout，便于程序化读取）"""
    fallback_advice = (
        "降级方案: "
        "1) 优先使用现有 HolographicAdapter (SQLite + FTS5) 作为兜底全文检索; "
        "2) 短期可使用 ChromaDB + sentence_transformers (已支持, 但有重量级依赖); "
        "3) 长期可使用 sqlite3 原生 + 自实现的 brute-force KNN (小数据集 <1k 条可用); "
        "4) 若仍需轻量向量检索, 可考虑 hnswlib (纯 C++) 或 LanceDB。"
    )

    report = {
        "available": available,
        "conclusion": ("可用: sqlite-vec 在当前 Windows 环境可正常使用, "
                       "建议作为 TLM 的 L2 向量层首选。"
                       if available else
                       f"不可用: {reason}。{fallback_advice}"),
        "reason": reason,
        "env_info": env_info,
        "vec_result": vec_result or {},
        "stages": REPORT,
        "fallback_advice": fallback_advice if not available else "",
    }

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("=" * 60, file=sys.stderr)
    print(f"结论: {'✅ 可用' if available else '❌ 不可用'}", file=sys.stderr)
    print(report["conclusion"], file=sys.stderr)
    print("=" * 60, file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
