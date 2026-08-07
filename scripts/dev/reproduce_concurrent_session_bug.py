"""复现全局状态覆盖 Bug 的高并发测试脚本（只读，不改代码）

问题背景:
    routes_chat.py 在对话前执行 `Yunshu._session_id = session_id`（全局实例赋值）。
    Flask 多线程处理并发请求时，用户 A 的赋值会被用户 B 覆盖，导致
    Orchestrator._get_user_context() 读到"最后写入者"的会话元数据，
    用户时区/设备/语言上下文互相串扰。

复现原理:
    1. 模拟全局 Yunshu 单实例（DummyHost），_session_id/_session_mgr 为实例属性
    2. N 个用户线程并发执行与 routes_chat 相同的时序:
       写入自己的 session_id → 短随机延迟（模拟 LLM 调用前的其他工作）
       → _get_user_context() 读取 → 校验是否属于自己
    3. 统计"读到他人上下文"的误配次数与比例

用法:
    python scripts/dev/reproduce_concurrent_session_bug.py [--users 8] [--iters 200] [--sleep-max-us 500]
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


class DummyYunshu:
    """模拟 DigitalLife 全局单实例（只承载 _session_id/_session_mgr 两个可变属性）"""
    def __init__(self):
        self._session_id = None
        self._session_mgr = None


def simulate_route_assignment(yunshu, session_mgr, session_id):
    """复刻 routes_chat.py 的全局赋值（Bug 源头）"""
    yunshu._session_id = session_id
    yunshu._session_mgr = session_mgr


def run_reproduce(users: int, iters: int, sleep_max_us: int, mode: str = "global") -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="yunshu_concurrency_repro_"))
    sm = SessionManager(sessions_dir=str(tmp / "sessions"))

    # 每个用户一个会话，元数据互不相同（时区作为身份指纹）
    user_sessions = []
    for i in range(users):
        tz = f"Asia/TZ{i:02d}"
        sess = sm.create_session(
            title=f"用户{i}",
            timezone=tz,
            device_type="mobile" if i % 2 == 0 else "desktop",
            locale="zh-CN" if i % 2 == 0 else "en-US",
        )
        user_sessions.append({"id": sess["id"], "timezone": tz})

    yunshu = DummyYunshu()  # 全局单实例（多线程共享）
    stats = {
        "total": 0,
        "mismatch": 0,
        "own": 0,
        "none": 0,
    }
    stats_lock = threading.Lock()

    def worker(uid: int):
        my_sess = user_sessions[uid]
        for _ in range(iters):
            if mode == "global":
                # ── 复刻修复前 routes_chat 时序 ──
                # 1) 全局赋值（Bug 源头：跨线程互相覆盖）
                simulate_route_assignment(yunshu, sm, my_sess["id"])
                # 2) 短随机延迟：模拟赋值后、LLM 调用前的大量工作（记忆加载/工具路由等）
                if sleep_max_us > 0:
                    time.sleep(random.uniform(0, sleep_max_us) / 1_000_000)
                # 3) 读取用户上下文（_call_llm_v2 内部会调用，回退实例全局）
                ctx = Orchestrator._get_user_context(yunshu)
            else:
                # ── 修复后链路（explicit）：chat(session_id=..., session_mgr=...) ──
                # 每个线程持有自己的会话上下文，不读写共享全局实例状态
                if sleep_max_us > 0:
                    time.sleep(random.uniform(0, sleep_max_us) / 1_000_000)
                ctx = Orchestrator._get_user_context(
                    yunshu,
                    session_id=my_sess["id"],
                    session_mgr=sm,
                )
            with stats_lock:
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

    mismatch_rate = stats["mismatch"] / stats["total"] if stats["total"] else 0
    return {
        **stats,
        "elapsed_s": round(elapsed, 2),
        "mismatch_rate": round(mismatch_rate * 100, 2),
        "users": users,
        "iters": iters,
        "sleep_max_us": sleep_max_us,
    }


def main():
    parser = argparse.ArgumentParser(description="全局状态覆盖 Bug 高并发对照测试")
    parser.add_argument("--users", type=int, default=8, help="并发用户数（线程数）")
    parser.add_argument("--iters", type=int, default=200, help="每用户迭代次数")
    parser.add_argument("--sleep-max-us", type=int, default=500, help="赋值与读取间的最大随机延迟(微秒)")
    parser.add_argument(
        "--mode", choices=["global", "explicit"], default="global",
        help="global=复现修复前 Bug（全局赋值时序）；explicit=验证修复后链路（显式传参）",
    )
    parser.add_argument(
        "--output", default=None,
        help="同时将结果 tee 到指定文件（Python 原生写入，规避 PowerShell 管道编码问题）",
    )
    args = parser.parse_args()

    if args.output:  # tee 模式：终端 + 文件同时输出
        import io

        class _Tee(io.TextIOBase):
            def __init__(self, *streams):
                self.streams = streams

            def write(self, data):
                for s in self.streams:
                    s.write(data)
                return len(data)

            def flush(self):
                for s in self.streams:
                    s.flush()

        sys.stdout = _Tee(sys.__stdout__, open(args.output, "w", encoding="utf-8"))

    print("=" * 64)
    mode_desc = {
        "global": "复现模式（修复前 routes_chat 全局赋值时序）",
        "explicit": "修复验证模式（chat 显式传参链路）",
    }[args.mode]
    print(f"对照测试: {args.users} 用户 × {args.iters} 次迭代，"
          f"延迟 0~{args.sleep_max_us}us")
    print(f"模式: {args.mode} — {mode_desc}")
    print("=" * 64)
    if args.mode == "global":
        print("Bug 机理: routes_chat 对全局 Yunshu 实例做 _session_id 赋值，")
        print("         多线程并发时互相覆盖，读取方拿到'最后写入者'的会话元数据。\n")
    else:
        print("修复机理: chat(session_id=..., session_mgr=...) 显式传参，")
        print("         每线程持有自己的会话上下文，不读写共享全局状态。\n")

    result = run_reproduce(args.users, args.iters, args.sleep_max_us, mode=args.mode)

    print(f"  总读取次数: {result['total']}")
    print(f"  读到自己的上下文: {result['own']}")
    print(f"  读到他人的上下文(误配): {result['mismatch']}")
    print(f"  读到 None: {result['none']}")
    print(f"  误配率: {result['mismatch_rate']}%")
    print(f"  耗时: {result['elapsed_s']}s")
    print("-" * 64)
    if args.mode == "global":
        if result["mismatch"] > 0:
            print(f"✅ Bug 已复现：{result['mismatch']} 次读到他人上下文 "
                  f"({result['mismatch_rate']}%)，全局状态覆盖确认存在")
        else:
            print("⚠️ 本次未复现误配（延迟窗口太小或线程调度未碰撞），"
                  "可增大 --sleep-max-us 或 --iters 重试")
    else:
        if result["mismatch"] == 0:
            print(f"✅ 修复验证通过：{result['total']} 次读取全部命中自己的上下文"
                  f"（误配率 0%）")
        else:
            print(f"❌ 修复验证失败：{result['mismatch']} 次误配，需排查显式传参链路")
    print("=" * 64)


if __name__ == "__main__":
    main()
