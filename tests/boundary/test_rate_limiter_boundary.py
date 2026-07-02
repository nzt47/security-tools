"""限流器（RateLimiter）边界测试

覆盖场景：boundary / overflow / extreme
对应 Day 4 计划任务：BT-002

测试目标模块：agent/rate_limiter.py
实际 API：
  - RateLimiter: check() / get_category() / wait_time() / reset()
  - 令牌桶算法：capacity + refill_rate
  - 默认分类: default(10,1.0) / network(5,0.5) / shell(2,0.2) / file(15,1.0)
"""

import threading
import time

import pytest

from agent.rate_limiter import RateLimiter


@pytest.fixture
def fast_limiter():
    """快速补充的限流器（便于测试令牌补充）"""
    return RateLimiter(
        limits={
            "default": (3, 10.0),
            "network": (2, 5.0),
            "shell": (1, 1.0),
            "file": (5, 10.0),
        }
    )


@pytest.fixture
def default_limiter():
    """默认配置的限流器"""
    return RateLimiter()


class TestTokenBoundary:
    """令牌消耗与补充边界条件测试"""

    def test_first_call_consumes_token_and_allowed(self, fast_limiter):
        """首次调用消耗一个令牌并返回 True"""
        assert fast_limiter.check("some_tool") is True

    def test_exact_capacity_calls_all_allowed(self, fast_limiter):
        """刚好达到容量上限的调用全部允许"""
        assert fast_limiter.check("tool_1") is True
        assert fast_limiter.check("tool_2") is True
        assert fast_limiter.check("tool_3") is True

    def test_one_more_than_capacity_blocked(self, fast_limiter):
        """超过容量上限的一次被限流"""
        fast_limiter.check("tool_1")
        fast_limiter.check("tool_2")
        fast_limiter.check("tool_3")
        assert fast_limiter.check("tool_4") is False

    def test_token_refill_after_wait_allows_call(self, fast_limiter):
        """等待补充后令牌恢复，允许调用"""
        for _ in range(3):
            fast_limiter.check("tool")
        assert fast_limiter.check("tool") is False
        time.sleep(0.15)
        assert fast_limiter.check("tool") is True

    def test_token_refill_not_exceed_capacity(self, fast_limiter):
        """令牌补充不超过容量上限"""
        fast_limiter.check("tool")
        time.sleep(0.5)
        assert fast_limiter.check("tool") is True
        assert fast_limiter.check("tool") is True
        assert fast_limiter.check("tool") is True
        assert fast_limiter.check("tool") is False

    def test_partial_refill_still_blocks(self, fast_limiter):
        """令牌不足 1 个时仍被限流"""
        fast_limiter.check("shell_execute")
        assert fast_limiter.check("shell_execute") is False
        time.sleep(0.05)
        assert fast_limiter.check("shell_execute") is False

    def test_wait_time_zero_when_tokens_available(self, fast_limiter):
        """有令牌时 wait_time 返回 0"""
        assert fast_limiter.wait_time("tool") == 0.0

    def test_wait_time_positive_when_exhausted(self, fast_limiter):
        """令牌耗尽时 wait_time 返回正数"""
        for _ in range(3):
            fast_limiter.check("tool")
        wait = fast_limiter.wait_time("tool")
        assert wait > 0.0


class TestCategoryBoundary:
    """工具分类边界条件测试"""

    def test_network_category_keywords(self, default_limiter):
        """网络工具关键词正确分类"""
        network_tools = [
            "http_get", "fetch_url", "web_search", "browse_page",
            "download_file", "post_data", "scrape_html", "crawl_site",
        ]
        for tool in network_tools:
            assert default_limiter.get_category(tool) == "network", f"Failed: {tool}"

    def test_shell_category_keywords(self, default_limiter):
        """Shell 工具关键词正确分类"""
        shell_tools = [
            "shell_execute", "execute_cmd", "run_program", "start_process",
            "stop_process",
        ]
        for tool in shell_tools:
            assert default_limiter.get_category(tool) == "shell", f"Failed: {tool}"

    def test_file_category_keywords(self, default_limiter):
        """文件工具关键词正确分类

        注意：search_file 中的 "search" 先匹配 network_keywords，会被分类为 network，
        因此此处不测试 search_file（在 test_network_over_file_priority 中单独验证）。
        """
        file_tools = [
            "read_file", "write_file", "list_dir",
            "compress_zip", "decompress_tar", "diff_files", "get_file_info",
        ]
        for tool in file_tools:
            assert default_limiter.get_category(tool) == "file", f"Failed: {tool}"

    def test_search_file_classified_as_network_due_to_priority(self, default_limiter):
        """search_file 因 "search" 关键词优先匹配 network 分类"""
        # search_file 同时包含 file 关键词 "search_file" 和 network 关键词 "search"
        # 按 get_category 优先级：network > shell > file > default
        assert default_limiter.get_category("search_file") == "network"

    def test_default_category_for_unknown(self, default_limiter):
        """未知工具分类为 default

        注意：translate 在 network_keywords 中（翻译工具通常为网络服务），
        因此此处不测试 translate（在 test_network_category_keywords 中验证）。
        """
        unknown_tools = ["calculate", "unknown_tool", "misc_func", "math_op"]
        for tool in unknown_tools:
            assert default_limiter.get_category(tool) == "default", f"Failed: {tool}"

    def test_category_case_insensitive(self, default_limiter):
        """分类匹配不区分大小写"""
        assert default_limiter.get_category("HTTP_GET") == "network"
        assert default_limiter.get_category("Shell_Execute") == "shell"
        assert default_limiter.get_category("READ_FILE") == "file"

    def test_category_priority_network_over_file(self, default_limiter):
        """网络关键词优先于文件关键词"""
        assert default_limiter.get_category("web_search_file") == "network"

    def test_different_categories_independent_buckets(self, fast_limiter):
        """不同分类的令牌桶相互独立"""
        for _ in range(3):
            fast_limiter.check("default_tool")
        assert fast_limiter.check("default_tool") is False
        assert fast_limiter.check("http_get") is True


class TestTokenOverflow:
    """令牌溢出与过量消耗测试"""

    def test_rapid_calls_exceed_capacity_blocked(self, fast_limiter):
        """快速连续调用超过容量后被限流"""
        results = [fast_limiter.check("tool") for _ in range(10)]
        assert results[:3] == [True, True, True]
        assert all(r is False for r in results[3:])

    def test_shell_capacity_1_blocks_immediately(self, fast_limiter):
        """shell 容量为 1 时第二次调用立即被限流"""
        assert fast_limiter.check("shell_execute") is True
        assert fast_limiter.check("shell_execute") is False

    def test_overflow_tokens_dropped(self, fast_limiter):
        """超过容量的令牌被丢弃（不会累积）"""
        fast_limiter.check("tool")
        time.sleep(1.0)
        count = 0
        for _ in range(5):
            if fast_limiter.check("tool"):
                count += 1
        assert count == 3

    def test_wait_time_calculation_after_exhaustion(self, fast_limiter):
        """令牌耗尽后 wait_time 计算正确"""
        fast_limiter.check("shell_execute")
        wait = fast_limiter.wait_time("shell_execute")
        assert wait == 1.0

    def test_wait_time_decreases_after_partial_wait(self, fast_limiter):
        """部分等待后调用 check 触发部分补充，wait_time 相应减少

        wait_time() 基于 bucket 静态状态计算，sleep 本身不会更新 bucket。
        需调用 check() 触发 refill 计算，bucket 的 tokens 才会反映时间流逝。
        fast_limiter shell 配置：capacity=1, refill_rate=1.0/s
        - 首次 check：tokens=0, last_refill=t0
        - sleep(0.3) 后调用 check：elapsed=0.3, new_tokens=0.3 < 1 → 返回 False，tokens=0.3
        - wait_time：needed=0.7, wait=0.7/1.0=0.7
        """
        fast_limiter.check("shell_execute")  # tokens=0
        assert fast_limiter.wait_time("shell_execute") == 1.0
        time.sleep(0.3)
        # wait_time 基于静态 bucket，不会自动更新
        assert fast_limiter.wait_time("shell_execute") == 1.0
        # 调用 check 触发 refill，tokens 更新为 0.3（仍 < 1，返回 False）
        assert fast_limiter.check("shell_execute") is False
        # 现在 wait_time 基于更新后的 tokens=0.3 计算
        wait = fast_limiter.wait_time("shell_execute")
        assert 0.5 <= wait <= 0.8  # needed=0.7, wait=0.7


class TestExtremeValues:
    """极端值与异常输入测试"""

    def test_empty_tool_name_default_category(self, default_limiter):
        """空工具名分类为 default"""
        assert default_limiter.get_category("") == "default"

    def test_very_long_tool_name(self, default_limiter):
        """超长工具名正常处理"""
        long_name = "a" * 10000
        assert default_limiter.get_category(long_name) == "default"
        assert default_limiter.check(long_name) is True

    def test_special_characters_in_tool_name(self, default_limiter):
        """特殊字符工具名正常处理"""
        special_names = ["tool!@#$%", "tool\n\t", "tool with spaces", "工具名"]
        for name in special_names:
            category = default_limiter.get_category(name)
            assert category in ("default", "network", "shell", "file")

    def test_custom_limits_with_zero_refill(self):
        """零补充速率的限流器（令牌耗尽后永远不可用）"""
        limiter = RateLimiter(limits={"default": (1, 0.0)})
        assert limiter.check("tool") is True
        assert limiter.check("tool") is False
        time.sleep(0.1)
        assert limiter.check("tool") is False

    def test_custom_limits_with_huge_capacity(self):
        """超大容量的限流器"""
        limiter = RateLimiter(limits={"default": (10000, 1.0)})
        for _ in range(10000):
            assert limiter.check("tool") is True
        assert limiter.check("tool") is False

    def test_custom_limits_with_huge_refill_rate(self):
        """较大补充速率的限流器

        注：refill_rate 不能过大（如 1000/s），否则第 1 次和第 2 次 check 之间
        已补充令牌导致第 2 次也返回 True。使用 10/s（每 100ms 补充 1 个）使时序可控。
        """
        limiter = RateLimiter(limits={"default": (1, 10.0)})
        assert limiter.check("tool") is True   # 消耗唯一令牌
        assert limiter.check("tool") is False  # 令牌耗尽，100ms 内未补充
        time.sleep(0.15)                       # 等 150ms，补充 1.5 个令牌
        assert limiter.check("tool") is True   # 有令牌可用

    def test_unknown_category_falls_back_to_default(self):
        """未知分类回退到 default 配置"""
        limiter = RateLimiter(limits={"default": (5, 1.0), "network": (3, 0.5)})
        for _ in range(5):
            assert limiter.check("unknown_tool") is True
        assert limiter.check("unknown_tool") is False


class TestConcurrencySafety:
    """并发访问线程安全测试"""

    def test_concurrent_check_thread_safe(self, default_limiter):
        """并发调用 check 时令牌计数准确"""
        num_threads = 10
        allowed_count = []
        lock = threading.Lock()

        def worker():
            results = [default_limiter.check("tool") for _ in range(1)]
            with lock:
                allowed_count.extend(results)

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(allowed_count) == 10
        assert sum(allowed_count) == 10
        assert default_limiter.check("tool") is False

    def test_concurrent_different_categories_independent(self, fast_limiter):
        """并发访问不同分类时互不干扰"""
        results = {"network": [], "shell": [], "file": [], "default": []}
        lock = threading.Lock()

        def worker_network():
            r = [fast_limiter.check("http_get") for _ in range(2)]
            with lock:
                results["network"].extend(r)

        def worker_shell():
            r = [fast_limiter.check("shell_execute") for _ in range(1)]
            with lock:
                results["shell"].extend(r)

        def worker_file():
            r = [fast_limiter.check("read_file") for _ in range(5)]
            with lock:
                results["file"].extend(r)

        def worker_default():
            r = [fast_limiter.check("tool") for _ in range(3)]
            with lock:
                results["default"].extend(r)

        threads = [
            threading.Thread(target=worker_network),
            threading.Thread(target=worker_shell),
            threading.Thread(target=worker_file),
            threading.Thread(target=worker_default),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert all(results["network"])
        assert all(results["shell"])
        assert all(results["file"])
        assert all(results["default"])


class TestResetFunction:
    """重置功能边界测试"""

    def test_reset_clears_all_buckets(self, fast_limiter):
        """reset 清空所有令牌桶"""
        fast_limiter.check("http_get")
        fast_limiter.check("shell_execute")
        fast_limiter.check("read_file")
        fast_limiter.check("tool")
        fast_limiter.reset()
        assert fast_limiter.check("http_get") is True
        assert fast_limiter.check("shell_execute") is True

    def test_reset_multiple_times_safe(self, fast_limiter):
        """多次 reset 安全无副作用"""
        fast_limiter.check("tool")
        fast_limiter.reset()
        fast_limiter.reset()
        fast_limiter.reset()
        assert fast_limiter.check("tool") is True

    def test_wait_time_zero_after_reset(self, fast_limiter):
        """reset 后 wait_time 返回 0"""
        for _ in range(3):
            fast_limiter.check("tool")
        assert fast_limiter.wait_time("tool") > 0
        fast_limiter.reset()
        assert fast_limiter.wait_time("tool") == 0.0
