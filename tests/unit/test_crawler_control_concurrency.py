"""web/crawler_control 并发安全测试。

修复前：模块级单例 CrawlerController 被多路请求并发调用——_stats 的 += 为
读-改-写序列（并发丢计数）；_ua_index/_proxy_index 轮换非原子（丢轮换）；
remove_proxy 的 list.remove 与 get_proxy 的索引并发抛 IndexError；_proxy_stats
遍历与 add/remove 并发抛 RuntimeError；wait_if_needed 持锁 sleep（锁内阻塞把
并发请求串行化秒级）。修复后：统一 RLock 保护统计/UA/代理/延迟/robots 缓存，
锁内仅内存变更；sleep 与 robots.txt 抓取等网络 I/O 全在锁外（持锁纪律）；
report_result 递归调用 rotate_*，故选 RLock。
"""

import threading
import time

from agent.web.crawler_control import CrawlerController


class TestCrawlerControlConcurrency:
    """CrawlerController 并发读写（RLock 原子化）。"""

    def test_concurrent_acquire_count_precise(self):
        """50 线程 × 20 次并发 acquire：requests_made 计数无丢失"""
        cc = CrawlerController({"default_delay": 0,  # 关闭限速，专注计数
                                "proxies": ["p1", "p2", "p3"]})
        n_threads, per = 50, 20
        total = n_threads * per
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(tid):
            try:
                barrier.wait()
                for i in range(per):
                    cfg = cc.acquire(f"http://site-{tid}-{i}.com/")
                    assert "User-Agent" in cfg["headers"]
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert cc.get_stats()["requests_made"] == total     # 读-改-写计数无丢失

    def test_concurrent_report_result_stats_precise(self):
        """50 线程 × 20 次并发报告 403 失败：retries/blocked/轮换计数全部精确"""
        cc = CrawlerController({"default_delay": 0,
                                "proxies": ["p1", "p2", "p3"],
                                "user_agents": ["ua-1", "ua-2", "ua-3", "ua-4"]})
        n_threads, per = 50, 20
        total = n_threads * per
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(tid):
            try:
                barrier.wait()
                for i in range(per):
                    # 403 → retries+1、blocked_count+1、rotate_ua、rotate_proxy；
                    # error 为空 → proxy_stats success 分支（不触发 fail>=3 额外轮换）
                    cc.report_result(f"http://site-{tid}-{i}.com/", success=False,
                                     status_code=403, error="")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        stats = cc.get_stats()
        assert stats["retries"] == total                     # 失败计数无丢失
        assert stats["blocked_count"] == total               # 屏蔽计数无丢失
        assert stats["ua_switches"] == total                 # UA 轮换无丢失
        assert stats["proxy_switches"] == total              # 代理轮换无丢失
        # 每次 403 都标记当前代理 success → 3 个代理合计 total
        assert sum(v["success"] for v in stats["proxy_stats"].values()) == total
        assert sum(v["fail"] for v in stats["proxy_stats"].values()) == 0

    def test_concurrent_add_remove_proxy_no_crash(self):
        """并发 add/remove_proxy + get_proxy/get_stats：无 IndexError/RuntimeError"""
        cc = CrawlerController({"default_delay": 0, "proxies": ["p0"]})
        n_threads, per = 40, 25
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(tid):
            try:
                barrier.wait()
                for i in range(per):
                    proxy = f"proxy-{tid}-{i}"
                    cc.add_proxy(proxy)
                    cc.get_proxy()
                    cc.remove_proxy(proxy)
                    cc.get_stats()
                    cc.get_proxy()  # 列表可能被其它线程清空，但不应越界
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"并发增删代理不应抛 IndexError/RuntimeError: {errors}"
        # 每个线程都移除自己添加的代理 → 仅剩初始 p0；_proxy_stats 仅在
        # add_proxy/report_result 时建条目（原语义），动态条目随 remove 清空
        assert cc._proxies == ["p0"]
        assert cc._proxy_stats == {}

    def test_concurrent_ua_rotation_no_crash(self):
        """并发 get_user_agent/_rotate_ua/set_user_agents（变长列表）：无 IndexError"""
        cc = CrawlerController({"default_delay": 0,
                                "user_agents": ["ua-%d" % i for i in range(20)]})
        n_threads, per = 30, 100
        barrier = threading.Barrier(n_threads)
        errors = []

        def worker(tid):
            try:
                barrier.wait()
                for i in range(per):
                    ua = cc.get_user_agent()
                    assert isinstance(ua, str) and ua
                    if tid % 3 == 0:
                        cc._rotate_ua()
                    elif tid % 3 == 1 and i % 20 == 0:
                        # 缩短列表 → 索引漂移并发越界的场景
                        cc.set_user_agents(["short-%d" % j for j in range(3)])
                    else:
                        cc.add_user_agent(f"dyn-{tid}-{i}")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"UA 轮换不应抛 IndexError: {errors}"

    def test_concurrent_rate_limit_respected(self):
        """10 线程并发 acquire 同域（delay=0.1）：锁外 sleep 限速仍生效"""
        cc = CrawlerController({"default_delay": 0.1, "proxies": []})
        n_threads = 10
        barrier = threading.Barrier(n_threads)
        errors = []
        start = time.time()

        def worker():
            try:
                barrier.wait()
                cc.acquire("http://example.com/page")
            except Exception as e:  # pragma: no cover
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        elapsed = time.time() - start

        assert not errors
        # 10 个请求串行排队，各自等待 ≥0.8×0.1s（抖动下限）→ 总耗时 ≥ 0.72s
        assert elapsed >= 0.6, f"限速未生效: elapsed={elapsed:.2f}s"
        assert elapsed < 2.5, f"限速过度放大: elapsed={elapsed:.2f}s"
        assert cc.get_stats()["requests_made"] == n_threads
