"""LEDGER2 — 多进程并发重建描述符台账：load() 错误分类回归（实验 + 守卫）

【被测缺陷（改前实测）】
    agent/descriptors/registry.py::DescriptorRegistry.load() 把
    except (json.JSONDecodeError, ValueError, OSError) 三类错误**合并**处理：

        except (json.JSONDecodeError, ValueError, OSError) as e:
            backup = self._path.with_suffix(".corrupted.json")
            self._path.rename(backup)      # ← 把台账整体改名搬走
            ...
            return                          # ← 以"空注册表"继续

    并发重建时 Windows 的瞬态共享冲突（读写撞上 os.replace：Errno 13 / WinError 5、
    Errno 32 / WinError 32）被误判成"存储损坏"，于是：
      ① 台账号称"损坏"被改名搬走（实际内容完好）；
      ② 该进程以空注册表继续 register + save ⇒ 把并发写者的成果整体覆盖掉。

【本文件的两种用法】
  1) 作为 pytest 守卫（默认）：
        python -m pytest tests/unit/test_descriptor_registry_concurrent_load.py -q -p no:randomly --timeout=60
  2) 作为可复现实验 CLI（打印原始计数；改前红 / 改后绿）：
        python tests/unit/test_descriptor_registry_concurrent_load.py --procs 8 --rounds 30
        python tests/unit/test_descriptor_registry_concurrent_load.py --digest

【安全边界】实验只写 tmp_path / --dir 指定的临时目录；不触碰 data/**。
    子进程内 AUDIT_CHAIN_ENABLED=0 / AUDIT_DUAL_WRITE=0，注册动作不写审计链。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import multiprocessing as mp
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT), str(Path(__file__).resolve().parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from descriptors_util import make_descriptor  # noqa: E402  tests/unit 共享构造器

from agent.descriptors.registry import DescriptorRegistry  # noqa: E402

DEFAULT_PROCS = 8
DEFAULT_ROUNDS = 30
DEFAULT_PER_PROC = 4
DEFAULT_CYCLES = 3        # 每轮 load→register→save 的重复次数（制造 load/save 交叠）
DEFAULT_JITTER_MS = 12    # 每个 cycle 前的随机错峰（打破 barrier 同相位，暴露真实竞争）
_BARRIER_TIMEOUT = 20.0


# ════════════════════════════════════════════════════════════
#  实验：8 进程 × 30 轮并发 load → register(自己那份) → save
# ════════════════════════════════════════════════════════════

def _cid(proc_idx: int, k: int) -> str:
    """每个写者固定的一组 capability_id（重建语义：各源各写各的那份）"""
    return "cp.ledger2.p%d.t%d" % (proc_idx, k)


def _expected_cids(procs: int, per_proc: int) -> List[str]:
    return [_cid(i, k) for i in range(procs) for k in range(per_proc)]


def _my_descriptors(idx: int, per_proc: int) -> List[Any]:
    return [
        make_descriptor(
            cid=_cid(idx, k),
            name="p%dt%d" % (idx, k),
            source_type="builtin",
            source_id="ledger2-p%d" % idx,
            description="ledger2 并发重建探针 %d/%d" % (idx, k),
        )
        for k in range(per_proc)
    ]


def _probe_ledger(path: Path, attempts: int = 6, delay: float = 0.05) -> Dict[str, Any]:
    """父进程观察用：容忍瞬态占用，返回 {exists, valid, count, error}"""
    last = ""
    for i in range(attempts):
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            descs = raw.get("descriptors") if isinstance(raw, dict) else None
            return {"exists": True, "valid": True,
                    "count": len(descs or {}), "error": ""}
        except FileNotFoundError:
            return {"exists": False, "valid": False, "count": 0, "error": "missing"}
        except Exception as e:  # noqa: BLE001
            last = "%s: %s" % (type(e).__name__, e)
            time.sleep(delay * (i + 1))
    return {"exists": True, "valid": False, "count": 0, "error": last}


def _worker(job: Dict[str, Any]) -> Dict[str, Any]:
    """单个写者：每轮 load → register(自己那份) → save，轮间用 barrier 对齐。"""
    path_s = job["path"]
    idx = job["idx"]
    rounds = job["rounds"]
    per_proc = job["per_proc"]
    cycles = job.get("cycles", 1)
    jitter_ms = job.get("jitter_ms", 0)
    start = job["start"]
    end = job["end"]
    import random as _random
    rng = _random.Random(1000 + idx)   # 固定种子 → 可复现的错峰序列
    res: Dict[str, Any] = {
        "idx": idx, "rounds_done": 0, "quarantines": 0, "quarantine_evidence": [],
        "load_errors": [], "register_errors": [], "save_errors": [],
        "barrier_broken": False, "fatal": "",
    }
    try:
        mine = _my_descriptors(idx, per_proc)
        for _ in range(rounds):
            try:
                start.wait(_BARRIER_TIMEOUT)
            except threading.BrokenBarrierError:
                res["barrier_broken"] = True
                break
            try:
                for _c in range(cycles):
                    if jitter_ms:
                        time.sleep(rng.random() * jitter_ms / 1000.0)
                    reg = DescriptorRegistry(path_s, autosave=False)
                    try:
                        reg.load()
                    except Exception as e:  # noqa: BLE001  瞬态错误耗尽后的显式失败
                        res["load_errors"].append("%s: %s" % (type(e).__name__, e))
                        reg = None
                    if reg is None:
                        continue
                    for w in reg.load_warnings():
                        if "损坏" in w:
                            res["quarantines"] += 1
                            if len(res["quarantine_evidence"]) < 5:
                                res["quarantine_evidence"].append(w)
                    try:
                        for d in mine:
                            reg.register(d)
                    except Exception as e:  # noqa: BLE001
                        res["register_errors"].append("%s: %s" % (type(e).__name__, e))
                    try:
                        reg.save()
                        res["rounds_done"] += 1
                    except Exception as e:  # noqa: BLE001
                        res["save_errors"].append("%s: %s" % (type(e).__name__, e))
            except Exception as e:  # noqa: BLE001
                res["fatal"] = "%s: %s" % (type(e).__name__, e)
            finally:
                try:
                    end.wait(_BARRIER_TIMEOUT)
                except threading.BrokenBarrierError:
                    res["barrier_broken"] = True
                    break
    except Exception as e:  # noqa: BLE001  子进程绝不静默死掉（否则父进程等 barrier）
        res["fatal"] = "%s: %s" % (type(e).__name__, e)
    try:                       # 统计必须回到父进程（return 值会被 Process 丢弃）
        job["q"].put(res)
    except Exception:  # noqa: BLE001
        pass
    return res


def run_concurrent_rebuild(
    ledger_path: Path,
    *,
    procs: int = DEFAULT_PROCS,
    rounds: int = DEFAULT_ROUNDS,
    per_proc: int = DEFAULT_PER_PROC,
    cycles: int = DEFAULT_CYCLES,
    jitter_ms: float = DEFAULT_JITTER_MS,
    isolate_audit: bool = True,
    join_timeout: float = 60.0,
) -> Dict[str, Any]:
    """并发重建实验；返回原始计数（quarantines / regressions / lost_cids ...）"""
    ledger_path = Path(ledger_path)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    saved_env = {k: os.environ.get(k) for k in (
        "AUDIT_CHAIN_ENABLED", "AUDIT_DUAL_WRITE", "AUDIT_DB_PATH",
        "AUDIT_ROOTS_PATH", "AUDIT_SIGNING_KEY")}
    if isolate_audit:  # 子进程继承环境：注册动作不得写审计链
        os.environ["AUDIT_CHAIN_ENABLED"] = "0"
        os.environ["AUDIT_DUAL_WRITE"] = "0"
        os.environ["AUDIT_DB_PATH"] = str(ledger_path.parent / "_audit_chain.db")
        os.environ["AUDIT_ROOTS_PATH"] = str(ledger_path.parent / "_daily_roots.jsonl")
        os.environ["AUDIT_SIGNING_KEY"] = str(ledger_path.parent / "_audit_key.pem")
    ctx = mp.get_context("spawn")
    start = ctx.Barrier(procs + 1)   # +1 = 观察者（本进程）
    end = ctx.Barrier(procs + 1)
    q: Any = ctx.Queue()
    procs_list = [
        ctx.Process(target=_worker, args=({"path": str(ledger_path), "idx": i,
                                           "rounds": rounds, "per_proc": per_proc,
                                           "cycles": cycles, "jitter_ms": jitter_ms,
                                           "start": start, "end": end, "q": q},))
        for i in range(procs)
    ]
    observed: List[Dict[str, Any]] = []
    try:
        for p in procs_list:
            p.start()
        for r in range(rounds):
            try:
                start.wait(_BARRIER_TIMEOUT)
                end.wait(_BARRIER_TIMEOUT)
            except threading.BrokenBarrierError:
                break
            probe = _probe_ledger(ledger_path)
            observed.append({"round": r + 1, **probe})
        for p in procs_list:
            p.join(join_timeout)
    finally:
        for p in procs_list:
            if p.is_alive():
                p.terminate()
        for p in procs_list:
            p.join(5.0)
        if isolate_audit:
            for k, v in saved_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    workers: List[Dict[str, Any]] = []
    while True:
        try:
            workers.append(q.get(timeout=1.0))
        except Exception:  # noqa: BLE001  queue.Empty
            break
    if len(workers) < procs:
        for p in procs_list:
            try:
                if p.exitcode not in (0, None):
                    workers.append({"idx": -1, "exitcode": p.exitcode,
                                    "fatal": "exitcode=%s" % p.exitcode})
            except Exception:  # noqa: BLE001
                pass

    final = _probe_ledger(ledger_path)
    try:
        raw = json.loads(ledger_path.read_text(encoding="utf-8"))
        final_cids = set((raw.get("descriptors") or {}).keys())
    except Exception:  # noqa: BLE001
        final_cids = set()
    expected = set(_expected_cids(procs, per_proc))
    quarantined_files = sorted(p.name for p in ledger_path.parent.glob("*.corrupted.json"))

    counts = [o["count"] for o in observed]
    regressions = [{"round": observed[i]["round"], "from": counts[i - 1], "to": counts[i]}
                   for i in range(1, len(counts)) if counts[i] < counts[i - 1]]
    return {
        "procs": procs, "rounds": rounds, "per_proc": per_proc,
        "cycles": cycles, "jitter_ms": jitter_ms,
        "expected_total": len(expected),
        "quarantines": sum(w.get("quarantines", 0) for w in workers),
        "quarantine_evidence": [e for w in workers for e in w.get("quarantine_evidence", [])][:6],
        "load_errors": sum(len(w.get("load_errors", [])) for w in workers),
        "save_errors": sum(len(w.get("save_errors", [])) for w in workers),
        "register_errors": sum(len(w.get("register_errors", [])) for w in workers),
        "worker_fatals": [w for w in workers if w.get("fatal")],
        "rounds_done": sum(w.get("rounds_done", 0) for w in workers),
        "workers_reported": len(workers),
        "save_error_samples": [e for w in workers for e in w.get("save_errors", [])][:6],
        "load_error_samples": [e for w in workers for e in w.get("load_errors", [])][:6],
        "observed": observed,
        "regressions": regressions,
        "destroyed_entries": sum(r["from"] - r["to"] for r in regressions),
        "quarantined_files": quarantined_files,
        "final": final,
        "final_count": final["count"],
        "lost_cids": sorted(expected - final_cids),
        "lost_count": len(expected - final_cids),
        "unexpected_cids": sorted(final_cids - expected),
    }


# ════════════════════════════════════════════════════════════
#  单进程载荷 sha256（改前/改后必须逐字节一致）
# ════════════════════════════════════════════════════════════

_FIXED_TS = "2026-01-02T03:04:05+00:00"

#: 改前基线（本卡在未修改 registry.py 时实测填入）
_PAYLOAD_SHA256_BASELINE = "fff74de9257d3484a01233a0dd48547161602ca2abe98e6a0887fcad62a791d1"


def _freeze(reg: DescriptorRegistry) -> None:
    """锁死描述符与审计环里的全部时间戳 → save() 载荷跨运行确定"""
    for d in reg.list():
        d.meta.created_at = _FIXED_TS
        d.meta.updated_at = _FIXED_TS
    for entry in reg.audit_trail():
        entry["ts"] = _FIXED_TS


def frozen_payload_sha256(tmp_path: Path) -> str:
    """构造固定描述符集 → save() → 返回文件 sha256（时间戳锁定，跨运行确定）"""
    path = Path(tmp_path) / "frozen.json"
    reg = DescriptorRegistry(path, autosave=False)
    reg.register(make_descriptor(
        cid="cp.frozen.read", name="read", source_type="builtin",
        source_id="frozen", prov="verified", tenant="default",
        description="冻结样本 A",
        input_schema={"type": "object", "properties": {"path": {"type": "string"}},
                      "required": ["path"]}))
    reg.register(make_descriptor(
        cid="cp.frozen.write", name="write", source_type="mcp",
        source_id="frozen-fs", prov="declared", risk="destructive",
        data_class="internal", approval=True, undo="备份",
        comp="从备份恢复", description="冻结样本 B"))
    reg.register(make_descriptor(
        cid="cp.frozen.list", name="list", source_type="cli",
        source_id="frozen-cli", prov="declared", tenant="tenant-b",
        scope="org", description="冻结样本 C"))
    _freeze(reg)                              # 锁死时间戳 → 序列化确定
    reg.save()
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ════════════════════════════════════════════════════════════
#  守卫 1：并发重建（红 = 有损坏 / 有丢失更新）
# ════════════════════════════════════════════════════════════

@pytest.mark.timeout(180)
def test_concurrent_rebuild_has_no_corruption_and_no_lost_update(tmp_path):
    """8 进程 × 30 轮并发重建：0 次"损坏"改名、0 次丢失更新、0 次回退"""
    summary = run_concurrent_rebuild(tmp_path / "descriptors.json")
    detail = json.dumps({k: v for k, v in summary.items() if k != "observed"},
                        ensure_ascii=False, indent=2)
    assert summary["quarantines"] == 0, "台账被误判损坏并改名搬走:\n" + detail
    assert summary["quarantined_files"] == [], "出现 .corrupted.json:\n" + detail
    assert summary["lost_count"] == 0, ("丢失更新 %d 条:\n" % summary["lost_count"]) + detail
    assert summary["regressions"] == [], "台账条目数回退:\n" + detail
    # 注意：save 侧 os.replace 的 WinError 5 是**另一个**机制（写者之间抢目标句柄）；
    # 它只会让该 cycle 少写一次（记入 save_errors），不会销毁既有条目，故不作为判据，
    # 只要求确有写入发生、且终态条目数完整。
    assert summary["rounds_done"] > 0, detail
    assert summary["final_count"] == summary["expected_total"], detail


# ════════════════════════════════════════════════════════════
#  守卫 2：瞬态 OS 错误 vs 真损坏 的分类（确定性，无并发）
# ════════════════════════════════════════════════════════════

def _seed_ledger(path: Path) -> None:
    reg = DescriptorRegistry(path, autosave=False)
    reg.register(make_descriptor(cid="cp.seed.a", name="a", source_id="s1"))
    reg.register(make_descriptor(cid="cp.seed.b", name="b", source_id="s2"))
    reg.register(make_descriptor(cid="cp.seed.c", name="c", source_id="s3"))
    reg.save()


def _patch_open_permission_error(monkeypatch, path: Path, fails: Optional[int]):
    """让 open(path, 'r') 前 fails 次抛 PermissionError(13)（None = 永远抛）"""
    import builtins
    real_open = builtins.open
    target = os.path.normcase(str(Path(path)))
    state = {"left": fails, "hits": 0}

    def _flaky(file, mode="r", *args, **kwargs):
        try:
            same = os.path.normcase(str(file)) == target
        except Exception:  # noqa: BLE001
            same = False
        if same and "r" in str(mode):
            state["hits"] += 1
            if state["left"] is None or state["left"] > 0:
                if state["left"] is not None:
                    state["left"] -= 1
                raise PermissionError(13, "Permission denied")
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", _flaky)
    return state


def test_transient_permission_error_is_retried_not_quarantined(tmp_path, monkeypatch):
    """瞬态 Errno 13：重试后读到当前磁盘内容，绝不改名、绝不丢条目"""
    path = tmp_path / "d.json"
    _seed_ledger(path)
    state = _patch_open_permission_error(monkeypatch, path, fails=2)
    reg = DescriptorRegistry(path, autosave=False)
    reg.load()
    assert state["hits"] >= 1
    assert state["left"] == 0, "应当发生重试（前 2 次注入的错误被吞掉才算失败）"
    assert reg.count() == 3
    assert not path.with_suffix(".corrupted.json").exists()
    assert not any("损坏" in w for w in reg.load_warnings())
    assert json.loads(path.read_text(encoding="utf-8"))["descriptors"]


def test_persistent_permission_error_never_quarantines(tmp_path, monkeypatch):
    """持续 Errno 13：显式失败（让调用方重试），台账原样留在磁盘上"""
    path = tmp_path / "d.json"
    _seed_ledger(path)
    before = path.read_bytes()
    _patch_open_permission_error(monkeypatch, path, fails=None)
    reg = DescriptorRegistry(path, autosave=False)
    with pytest.raises(OSError):
        reg.load()
    assert path.exists(), "台账被改名搬走了（改前缺陷）"
    assert path.read_bytes() == before
    assert not path.with_suffix(".corrupted.json").exists()
    with pytest.raises(OSError):
        reg.count()  # 也不得凭"空台账"提供假数据（读路径同样显式失败）


def test_real_json_corruption_still_quarantines(tmp_path):
    """真损坏（JSON 解析失败）→ 既有改名备份路径行为保持"""
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    reg = DescriptorRegistry(path, autosave=False)
    reg.load()
    assert reg.count() == 0
    assert reg.load_warnings()
    assert path.with_suffix(".corrupted.json").exists()


def test_non_dict_root_still_quarantines(tmp_path):
    """真损坏（结构非法）→ 既有改名备份路径行为保持"""
    path = tmp_path / "bad2.json"
    path.write_text("[1, 2, 3]", encoding="utf-8")
    reg = DescriptorRegistry(path, autosave=False)
    reg.load()
    assert path.with_suffix(".corrupted.json").exists()
    assert any("损坏" in w for w in reg.load_warnings())


def test_load_missing_file_still_empty(tmp_path):
    """台账不存在 → 空注册表（既有行为保持，且不产生 .corrupted.json）"""
    path = tmp_path / "nope.json"
    reg = DescriptorRegistry(path, autosave=False)
    reg.load()
    assert reg.count() == 0
    assert reg.load_warnings() == []
    assert not path.with_suffix(".corrupted.json").exists()


# ════════════════════════════════════════════════════════════
#  守卫 3：单进程序列化逐字节不变
# ════════════════════════════════════════════════════════════

def test_single_process_payload_digest_unchanged(tmp_path):
    """save() 载荷 sha256 必须等于改前基线（改 load() 不得动序列化）"""
    digest = frozen_payload_sha256(tmp_path)
    assert digest == _PAYLOAD_SHA256_BASELINE, (
        "单进程载荷被改动: %s != %s" % (digest, _PAYLOAD_SHA256_BASELINE))


def test_load_save_roundtrip_is_byte_stable(tmp_path):
    """load → save 逐字节幂等（同一台账重写一遍不得改变一个字节）"""
    frozen_payload_sha256(tmp_path)  # 建库（frozen.json）
    src = tmp_path / "frozen.json"
    before = src.read_bytes()
    reg = DescriptorRegistry(src, autosave=False)
    reg.load()
    reg.save()                       # 就地重写
    assert src.read_bytes() == before, "load→save 往返改了字节"
    assert hashlib.sha256(src.read_bytes()).hexdigest() == \
        hashlib.sha256(before).hexdigest()


# ════════════════════════════════════════════════════════════
#  CLI：可复现实验入口
# ════════════════════════════════════════════════════════════

def _kill_audit_for_cli() -> None:
    """CLI 不在 pytest 内 ⇒ 没有 tests/conftest.py 的审计隔离夹具。

    【事故与纠正·LEDGER2】本卡第一次跑 --digest 时漏了这一步，register() 的
    链式审计留痕直接写进**生产** data/audit/（实测 25 条 cp.frozen.* 落进
    audit_chain.db.seqjournal）。此处双重保险：
      ① 关环境开关（facade 若尚未构造则生效）；
      ② 把已构造的模块级单例 enabled 置 False（不受 import 顺序影响）。
    """
    os.environ["AUDIT_CHAIN_ENABLED"] = "0"
    os.environ["AUDIT_DUAL_WRITE"] = "0"
    os.environ["AUDIT_DB_PATH"] = os.path.join(
        os.environ.get("TEMP", "."), "ledger2_cli_audit_chain.db")
    os.environ["AUDIT_ROOTS_PATH"] = os.path.join(
        os.environ.get("TEMP", "."), "ledger2_cli_daily_roots.jsonl")
    os.environ["AUDIT_SIGNING_KEY"] = os.path.join(
        os.environ.get("TEMP", "."), "ledger2_cli_audit_key.pem")
    try:
        from agent.audit import facade as _facade
        _facade.audit.enabled = False
    except Exception:  # noqa: BLE001 审计不可用不影响本 CLI
        pass


def _main() -> int:
    _kill_audit_for_cli()
    ap = argparse.ArgumentParser(description="LEDGER2 并发重建台账实验")
    ap.add_argument("--procs", type=int, default=DEFAULT_PROCS)
    ap.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS)
    ap.add_argument("--per-proc", type=int, default=DEFAULT_PER_PROC)
    ap.add_argument("--cycles", type=int, default=DEFAULT_CYCLES)
    ap.add_argument("--jitter-ms", type=float, default=DEFAULT_JITTER_MS)
    ap.add_argument("--dir", default="")
    ap.add_argument("--digest", action="store_true")
    args = ap.parse_args()
    tmp = Path(args.dir) if args.dir else Path(
        os.environ.get("TEMP", ".")) / ("ledger2_exp_%d" % os.getpid())
    tmp.mkdir(parents=True, exist_ok=True)
    if args.digest:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            print("payload_sha256 = " + frozen_payload_sha256(Path(td)))
        return 0
    summary = run_concurrent_rebuild(tmp / "descriptors.json", procs=args.procs,
                                     rounds=args.rounds, per_proc=args.per_proc,
                                     cycles=args.cycles, jitter_ms=args.jitter_ms)
    observed = summary.pop("observed")
    print("== 实验目录 ==", tmp)
    print("== 逐轮观察（父进程在轮末读取）==")
    for o in observed:
        print("  round %3d  count=%3d  valid=%s" % (o["round"], o["count"], o["valid"]))
    print("== 汇总 ==")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print("== 判据 ==")
    print("  quarantines(误判损坏次数) = %s" % summary["quarantines"])
    print("  quarantined_files         = %s" % (summary["quarantined_files"],))
    print("  regressions(条目数回退)    = %d 次 / 销毁条目 %d 条 %s" % (
        len(summary["regressions"]), summary["destroyed_entries"],
        summary["regressions"][:3]))
    print("  lost_cids(丢失更新)        = %d %s" % (summary["lost_count"],
                                                   summary["lost_cids"][:8]))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
