"""验证解耦修复：并发场景下显式传参 → 误配率必须为 0

对照: scripts/dev/reproduce_concurrent_session_bug.py（修复前 87.5% 误配）
原理: 修复后 _get_user_context(session_id, session_mgr) 走显式参数，
      每个线程持有自己的会话上下文，不再读写共享全局实例状态。

用法:
    python scripts/dev/verify_concurrent_session_fix.py [--users 8] [--iters 300]
"""
import argparse
import random
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))  # 项目根

from agent.session_manager import SessionManager
from agent.orchestrator.orchestrator import Orchestrator


def run_verify(users: int, iters: int, sleep_max_us: int) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="yunshu_fix_verify_"))
    sm = SessionManager(sessions_dir=str(tmp / "sessions"))

    user_sessions = []
    for i in range(users):
        tz = f"Asia/TZ{i:02d}"
        sess = sm.create_session(
            title=f"用户{i}", timezone=tz,
            device_type="mobile" if i % 2 == 0 else "desktop",
            locale="zh-CN" if i % 2 == 0 else "en-US",
        )
        user_sessions.append({"id": sess["id"], "timezone": tz})

    stats = {"total": 0, "mismatch": 0, "own": 0, "none": 0}
    lock = threading.Lock()
    dummy = object.__new__(Orchestrator)  # 无状态占位实例（模拟 chat 的 self）

    def worker(uid: int):
        my = user_sessions[uid]
        for _ in range(iters):
            if sleep_max_us > 0:
                time.sleep(random.uniform(0, sleep_max_us) / 1_000_000)
            # 修复后链路：chat(session_id=..., session_mgr=...) → _get_user_context(显式)
            ctx = Orchestrator._get_user_context(
                dummy,
                session_id=my["id"], session_mgr=sm,
            )
            with lock:
                stats["total"] += 1
                if ctx is None:
                    stats["none"] += 1
                elif f"Asia/TZ{uid:02d}" in ctx:
                    stats["own"] += 1
                else:
                    stats["mismatch"] += 1

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(users)]
    t0 = time.perf_counter()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.perf_counter() - t0

    return {
        **stats,
        "elapsed_s": round(elapsed, 2),
        "mismatch_rate": round(stats["mismatch"] / stats["total"] * 100, 4) if stats["total"] else 0,
    }


def verify_backward_compat():
    """向后兼容：无参调用仍回退实例全局属性（CLI 等未接入 SessionManager 的调用方）"""
    tmp = Path(tempfile.mkdtemp(prefix="yunshu_fix_compat_"))
    sm = SessionManager(sessions_dir=str(tmp / "sessions"))
    sess = sm.create_session(title="兼容", timezone="Asia/Tokyo", locale="ja-JP")

    class Host:
        pass

    host = Host()
    host._session_mgr = sm
    host._session_id = sess["id"]
    ctx = Orchestrator._get_user_context(host)
    assert ctx and "Asia/Tokyo" in ctx and "ja-JP" in ctx, f"向后兼容失败: {ctx!r}"

    host2 = Host()  # 无 _session_mgr
    assert Orchestrator._get_user_context(host2) is None
    return True


def main():
    parser = argparse.ArgumentParser(description="验证解耦修复后的并发安全")
    parser.add_argument("--users", type=int, default=8)
    parser.add_argument("--iters", type=int, default=300)
    parser.add_argument("--sleep-max-us", type=int, default=500)
    args = parser.parse_args()

    print("=" * 64)
    print(f"修复验证: {args.users} 用户 × {args.iters} 次迭代（显式传参链路）")
    print("=" * 64)

    r = run_verify(args.users, args.iters, args.sleep_max_us)
    print(f"  总读取次数: {r['total']}")
    print(f"  读到自己的上下文: {r['own']}")
    print(f"  误配(读到他人): {r['mismatch']}")
    print(f"  误配率: {r['mismatch_rate']}%")
    print(f"  耗时: {r['elapsed_s']}s")

    ok = r["mismatch"] == 0
    print("-" * 64)
    print(f"  [{'PASS' if ok else 'FAIL'}] 并发误配率 = {r['mismatch_rate']}%"
          f"（修复前复现为 87.5%）")

    compat = verify_backward_compat()
    print(f"  [{'PASS' if compat else 'FAIL'}] 向后兼容：无参调用仍回退实例全局属性")

    print("=" * 64)
    if ok and compat:
        print("结果: 并发竞态已根治，向后兼容 ✓")
        sys.exit(0)
    sys.exit(1)


if __name__ == "__main__":
    main()
