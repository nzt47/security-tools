"""SqliteVecBackend / VectorStore 后端回退独立排查脚本

背景（L3 回归失败定位辅助，见 docs/pr634_final_merge_summary_20260815.md §2.5）：
Docker 镜像内 `TestSqliteVecBackend`（直接实例化 SqliteVecBackend）全部 PASSED，
但 `TestVectorStoreSqliteVecIntegration` 断言 `expected sqlite_vec, got json` ——
说明 sqlite-vec 扩展本身可用，失败发生在 `VectorStore.__init__` 的后端选择路径
（memory/vector_store/vector_store.py L454-L488：sqlite-vec > chromadb > JSON）。

本脚本独立于 pytest（不加载 conftest 的 _BlockModules 封禁），在真实环境中
对照输出两条路径的可用性，定位回退发生在哪一环：

1. 直接路径（等价 TestSqliteVecBackend）：SqliteVecBackend 实例化 + add/search/count/clear
2. 初始化路径（等价集成测试）：_get_shared_encoder → VectorStore._backend 最终取值

安全设计：
- 主进程只做安全探测（不 import torch/sentence_transformers）
- 可能加载 torch 的 encoder/VectorStore 探测在子进程（--child）中执行，
  300s 超时后终止，隔离 C 扩展崩溃对主进程的影响（复刻 _probe_import 的子进程隔离思路）

用法:
    python scripts/diag_sqlite_vec_fallback.py

输出:
    stdout: 完整 JSON 报告（含结论与归因链）
    stderr: 人类可读的分阶段进度
"""
from __future__ import annotations

import json
import os
import platform
import sqlite3
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

MODEL_NAME = "paraphrase-multilingual-MiniLM-L12-v2"
_CHILD_TIMEOUT = 300  # 子进程（可能加载 torch/模型）超时

REPORT: dict = {"stages": []}


def _log(stage: str, ok: bool, msg: str, **extra) -> None:
    # 防撞名：调用方 **info 中可能含 "ok" 键（如直接后端探测），以位置参数为准
    extra.pop("ok", None)
    entry = {"stage": stage, "ok": ok, "msg": msg}
    entry.update(extra)
    REPORT["stages"].append(entry)
    flag = "[OK]" if ok else "[FAIL]"
    print(f"{flag} {stage}: {msg}", file=sys.stderr)
    for k, v in extra.items():
        print(f"      |- {k}: {v}", file=sys.stderr)


# ════════════════════════════════════════════════════════════
# 阶段 0: 环境信息
# ════════════════════════════════════════════════════════════
def _probe_env() -> dict:
    info = {
        "platform": platform.platform(),
        "python": sys.version.split()[0],
        "sqlite3": sqlite3.sqlite_version,
        "env": {},
    }
    for key in ("CI", "DISABLE_NATIVE_EXT", "HF_HOME", "HF_HUB_OFFLINE",
                "TRANSFORMERS_OFFLINE", "OMP_NUM_THREADS", "MKL_NUM_THREADS"):
        val = os.environ.get(key)
        if val is not None:
            info["env"][key] = val
    try:
        opts = [r[0] for r in sqlite3.connect(":memory:").execute("PRAGMA compile_options")]
        info["has_load_extension"] = "ENABLE_LOAD_EXTENSION" in opts
    except Exception:
        info["has_load_extension"] = False
    _log("env_check", True, "环境信息采集完成", **info)
    return info


# ════════════════════════════════════════════════════════════
# 阶段 1: sqlite-vec 扩展可用性（主进程安全探测）
# ════════════════════════════════════════════════════════════
def _probe_sqlite_vec() -> dict:
    try:
        import sqlite_vec
        info = {"import_ok": True, "version": getattr(sqlite_vec, "__version__", "unknown")}
        lp = getattr(sqlite_vec, "loadable_path", None)
        if callable(lp):
            try:
                info["loadable_path"] = lp()
            except Exception:
                pass
        elif isinstance(lp, str):
            info["loadable_path"] = lp
        # 加载到内存连接验证扩展真正可加载
        conn = sqlite3.connect(":memory:")
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        conn.execute("SELECT vec_version()")
        conn.close()
        info["load_ok"] = True
        _log("sqlite_vec", True, "sqlite-vec 可导入且扩展可加载", **info)
        return info
    except Exception as e:
        _log("sqlite_vec", False, f"sqlite-vec 不可用: {type(e).__name__}: {e}")
        return {"ok": False, "import_ok": False, "error": f"{type(e).__name__}: {e}"}


# ════════════════════════════════════════════════════════════
# 阶段 2: 直接实例化路径（等价 TestSqliteVecBackend）
# ════════════════════════════════════════════════════════════
def _probe_direct_backend() -> dict:
    try:
        from memory.vector_store.sqlite_vec_backend import SqliteVecBackend
    except ImportError as e:
        _log("direct_backend", False, f"SqliteVecBackend 模块导入失败: {e}")
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}
    with tempfile.TemporaryDirectory(prefix="diag_sqlitevec_") as td:
        db = os.path.join(td, "diag.db")
        try:
            backend = SqliteVecBackend(db_path=db, collection_name="diag", dim=4)
            add_ok = backend.add("a", "hello", [0.1, 0.2, 0.3, 0.4], {"k": "v"}, "2026-08-15")
            hits = backend.search([0.1, 0.2, 0.3, 0.4], top_k=1)
            count = backend.count()
            stats = backend.get_stats()
            cleared = backend.clear()
            info = {
                "add_ok": add_ok,
                "search_hits": len(hits),
                "count": count,
                "stats_ok": bool(stats),
                "clear_ok": cleared,
            }
            _log("direct_backend", True, "SqliteVecBackend 直接实例化全链路通过", **info)
            return info
        except Exception as e:
            _log("direct_backend", False, f"直接后端全链路失败: {type(e).__name__}: {e}")
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}


# ════════════════════════════════════════════════════════════
# 阶段 3: sentence-transformers / 编码器可用性（主进程安全探测）
# _check_chroma_available 与 _resolve_encoder_availability 内部均使用
# 子进程探测（_probe_import，30s 超时），不污染主进程 import 锁。
# ════════════════════════════════════════════════════════════
def _probe_st_encoder() -> dict:
    from memory.vector_store import vector_store as vs
    t0 = time.perf_counter()
    vs._check_chroma_available()
    has_st = bool(vs.HAS_SENTENCE_TRANSFORMERS)
    has_chroma = bool(vs.HAS_CHROMA)
    model_cached = vs._is_model_fully_cached(MODEL_NAME)
    encoder_ok = vs._resolve_encoder_availability(MODEL_NAME)
    # 复刻 vector_store.py L472：st_ok = HAS_ST or encoder_ok
    st_ok = has_st or encoder_ok
    info = {
        "has_sentence_transformers": has_st,
        "has_chroma": has_chroma,
        "model_fully_cached": model_cached,
        "encoder_ok": encoder_ok,
        "st_ok": st_ok,
        "probe_seconds": round(time.perf_counter() - t0, 1),
    }
    _log("st_encoder", st_ok, "编码器可用性探测（HAS_ST / encoder_ok / st_ok）", **info)
    return info


# ════════════════════════════════════════════════════════════
# 阶段 4: 初始化路径（子进程隔离，可能加载 torch + 模型）
# ════════════════════════════════════════════════════════════
def _child_main(model: str) -> int:
    import logging
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    res: dict = {"model": model}
    try:
        from memory.vector_store.vector_store import VectorStore, _get_shared_encoder
        t0 = time.time()
        enc = _get_shared_encoder(model)
        res["shared_encoder_none"] = enc is None
        res["shared_encoder_seconds"] = round(time.time() - t0, 1)
        with tempfile.TemporaryDirectory(prefix="diag_vs_") as td:
            t1 = time.time()
            vs = VectorStore(collection_name="diag_probe", persist_dir=td, model_name=model)
            res["backend"] = vs._backend
            res["sqlite_vec_backend_ready"] = vs._sqlite_vec_backend is not None
            res["init_seconds"] = round(time.time() - t1, 1)
    except Exception as e:
        res["error"] = f"{type(e).__name__}: {e}"
    print(json.dumps(res, ensure_ascii=False))
    return 0


def _probe_integration_path() -> dict:
    t0 = time.perf_counter()
    try:
        proc = subprocess.run(
            [sys.executable, os.path.abspath(__file__), "--child", MODEL_NAME],
            timeout=_CHILD_TIMEOUT,
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        _log("integration_path", False,
             f"子进程超时(>{_CHILD_TIMEOUT}s)，torch/ST 加载或模型下载卡死")
        return {"ok": False, "error": "timeout"}
    res: dict = {"ok": proc.returncode == 0, "duration_s": round(time.perf_counter() - t0, 1)}
    stdout = proc.stdout.strip()
    if stdout:
        try:
            res.update(json.loads(stdout.splitlines()[-1]))
        except Exception as e:
            res["parse_error"] = f"{type(e).__name__}: {e}"
    if proc.stderr:
        lines = [ln for ln in proc.stderr.splitlines() if ln.strip()]
        # 截断保留关键日志尾部（编码器加载失败 / 降级 / 后端启用）
        res["child_log_tail"] = lines[-15:]
    backend = res.get("backend", "unknown")
    _log("integration_path", backend == "sqlite_vec",
         f"VectorStore 初始化后端: {backend}",
         **{k: v for k, v in res.items() if k != "ok"})
    return res


# ════════════════════════════════════════════════════════════
# 汇总结论
# ════════════════════════════════════════════════════════════
def _conclude(env: dict, vec: dict, direct: dict, st: dict, integ: dict) -> str:
    direct_ok = bool(direct.get("add_ok"))
    backend = integ.get("backend")
    if not vec.get("import_ok"):
        return ("sqlite-vec 扩展不可用（import/load 失败），SqliteVecBackend 直接路径 "
                f"亦失败：{vec.get('error', 'unknown')}。L3 现象不符（镜像内直接测试通过）。")
    if not direct_ok:
        return (f"sqlite-vec 扩展可加载但 SqliteVecBackend 直接实例化失败："
                f"{direct.get('error')}。")
    if backend == "sqlite_vec":
        return "本环境集成路径正常（_backend=sqlite_vec），与 L3 失败现象不符。"
    if backend != "json":
        return f"本环境后端为未知值 {backend!r}，请人工复核。"

    # backend == json 且直接路径可用 → 复现 L3 现象，继续归因
    reasons = []
    if not st.get("st_ok"):
        reasons.append(
            f"st_ok=False（HAS_ST={st.get('has_sentence_transformers')}, "
            f"encoder_ok={st.get('encoder_ok')}），VectorStore.__init__ 在进入 "
            "_init_sqlite_vec 前已走 JSON fallback（vector_store.py L472/L482）"
        )
    if integ.get("shared_encoder_none") is True:
        reasons.append(
            "_get_shared_encoder 返回 None（模型加载失败或模块被 mock），"
            "_init_sqlite_vec 内部降级（vector_store.py L515-L517）"
        )
    if not reasons:
        reasons.append("st_ok=True 且 shared_encoder 非 None，但 _init_sqlite_vec 失败——"
                       "检查子进程日志 tail 中的异常详情")
    ci = env.get("env", {}).get("CI")
    dne = env.get("env", {}).get("DISABLE_NATIVE_EXT")
    hint = ""
    if ci or dne:
        hint = (f"（注意：CI={ci} / DISABLE_NATIVE_EXT={dne} 时，pytest conftest 的 "
                "autouse fixture 会 _BlockModules 封禁 sqlite_vec/sentence_transformers，"
                "集成测试内必然回退 json；本脚本为独立运行，不受该封禁影响）")
    return "复现 L3 现象：扩展可用但集成路径回退 json。归因链：" + "；".join(reasons) + hint


def main() -> int:
    print("=" * 60, file=sys.stderr)
    print("SqliteVecBackend / VectorStore 后端回退排查", file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    env = _probe_env()
    vec = _probe_sqlite_vec()
    direct = _probe_direct_backend()
    st = _probe_st_encoder()
    integ = _probe_integration_path()
    conclusion = _conclude(env, vec, direct, st, integ)

    report = {
        "model": MODEL_NAME,
        "env": env,
        "sqlite_vec": vec,
        "direct_backend": direct,
        "st_encoder": st,
        "integration_path": integ,
        "conclusion": conclusion,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("=" * 60, file=sys.stderr)
    print("结论:", conclusion, file=sys.stderr)
    print("=" * 60, file=sys.stderr)
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        sys.exit(_child_main(sys.argv[2] if len(sys.argv) > 2 else MODEL_NAME))
    sys.exit(main())
