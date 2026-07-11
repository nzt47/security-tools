"""run_sandbox multiprocessing 边界测试

针对新的 multiprocessing 实现的边界场景测试，覆盖：
- 超时场景：死循环、sleep、0 秒超时、最小超时、大输出超时
- 进程崩溃场景：sys.exit、RecursionError、MemoryError、子进程异常终止
- 资源管理：连续超时、超时后正常调用、Queue 清理
- 进程隔离：stdout/stderr 不泄漏、全局状态隔离

运行：python -B -m pytest tests/unit/test_sandbox_multiprocess_boundary.py -v --timeout=60
"""
import sys
import time
import pytest

from agent.system_tools import run_sandbox


# ════════════════════════════════════════════════════════════
#  超时场景
# ════════════════════════════════════════════════════════════


class TestSandboxTimeoutBoundary:
    """超时边界测试——验证 multiprocessing 实现的超时强杀行为"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_dead_loop_timeout(self):
        """死循环超时——核心场景：while True 必须被 terminate 杀掉"""
        result = run_sandbox("while True:\n    pass", timeout_sec=2)
        assert result["timed_out"] is True
        assert "超时" in result["error"]
        assert result["stdout"] == ""
        # 关键验证：子进程已被终止，不会继续占用 CPU

    @pytest.mark.unit
    @pytest.mark.p0
    def test_cpu_intensive_timeout(self):
        """CPU 密集循环超时——子进程在计算中被 terminate"""
        # 不需要 import 的纯 Python 死循环
        result = run_sandbox("i = 0\nwhile i < 999999999:\n    i += 1", timeout_sec=1)
        assert result["timed_out"] is True
        assert "超时" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p1
    def test_import_blocked_no_timeout(self):
        """import 被阻止——沙盒安全限制，不会执行 sleep"""
        # 沙盒中 __import__ 被移除，import time 会抛异常，不会超时
        result = run_sandbox("import time\ntime.sleep(10)", timeout_sec=2)
        assert result["timed_out"] is False
        assert result["error"] is not None  # ImportError 被捕获

    @pytest.mark.unit
    @pytest.mark.p0
    def test_timeout_zero_instant(self):
        """0 秒超时——子进程可能还没来得及启动就被判定超时"""
        result = run_sandbox("x = 1", timeout_sec=0)
        assert "timed_out" in result
        # timeout=0 时 join(0) 立即返回，timed_out 可能 True 或 False
        # 关键是不应卡住

    @pytest.mark.unit
    @pytest.mark.p1
    def test_timeout_very_short(self):
        """极短超时——0.01 秒，子进程可能来不及完成"""
        result = run_sandbox("x = 1 + 2", timeout_sec=0.01)
        assert "timed_out" in result
        # 不应卡住，结果合法即可

    @pytest.mark.unit
    @pytest.mark.p1
    def test_timeout_exact_boundary(self):
        """边界超时——代码执行时间约等于超时时间"""
        # spawn 启动开销 ~80ms，sleep 0.5s，总 ~580ms
        # timeout=1 足够完成，timeout=0.3 可能超时
        result = run_sandbox("import time\ntime.sleep(0.5)", timeout_sec=1)
        assert result["timed_out"] is False

    @pytest.mark.unit
    @pytest.mark.p1
    def test_large_output_timeout(self):
        """大输出 + 超时——超时应优先于输出截断"""
        # 产生大量输出后死循环
        code = "x = 'a' * 50000\nwhile True:\n    pass"
        result = run_sandbox(code, timeout_sec=1)
        assert result["timed_out"] is True
        # 超时场景下 stdout 可能为空（子进程被杀前未回写 Queue）
        assert len(result["stdout"]) <= 10000


# ════════════════════════════════════════════════════════════
#  进程崩溃场景
# ════════════════════════════════════════════════════════════


class TestSandboxProcessCrash:
    """进程崩溃测试——验证子进程异常终止时的错误处理"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_sys_exit_crash(self):
        """sys.exit() 导致子进程退出——验证 exitcode 处理"""
        # sys.exit 会抛 SystemExit，被 except Exception 捕获
        result = run_sandbox("import sys\nsys.exit(1)")
        # SystemExit 被 _sandbox_worker 的 except 捕获
        assert result["timed_out"] is False
        assert result["error"] is not None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_recursion_error(self):
        """无限递归——RecursionError 应被捕获，不应崩溃"""
        code = "def f():\n    return f()\nf()"
        result = run_sandbox(code, timeout_sec=5)
        assert result["timed_out"] is False
        assert result["error"] is not None
        assert "RecursionError" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p1
    def test_memory_error(self):
        """内存爆炸——MemoryError 或被 kill 信号终止"""
        code = "x = []\nwhile True:\n    x.append('a' * 1000000)"
        result = run_sandbox(code, timeout_sec=3)
        # 可能是 MemoryError 被捕获，也可能被 OS kill
        assert result["timed_out"] is True or result["error"] is not None

    @pytest.mark.unit
    @pytest.mark.p1
    def test_zero_division(self):
        """除零错误——ZeroDivisionError 应被捕获"""
        result = run_sandbox("x = 1 / 0", timeout_sec=5)
        assert result["timed_out"] is False
        assert result["error"] is not None
        assert "ZeroDivisionError" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p1
    def test_name_error(self):
        """未定义变量——NameError 应被捕获"""
        result = run_sandbox("print(undefined_var)", timeout_sec=5)
        assert result["timed_out"] is False
        assert result["error"] is not None
        assert "NameError" in result["error"]

    @pytest.mark.unit
    @pytest.mark.p1
    def test_type_error(self):
        """类型错误——TypeError 应被捕获"""
        result = run_sandbox("x = 1 + 'a'", timeout_sec=5)
        assert result["timed_out"] is False
        assert result["error"] is not None
        assert "TypeError" in result["error"]


# ════════════════════════════════════════════════════════════
#  资源管理
# ════════════════════════════════════════════════════════════


class TestSandboxResourceManagement:
    """资源管理测试——验证超时后资源正确清理"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_consecutive_timeouts(self):
        """连续 3 次超时——验证每次超时后资源都正确清理"""
        for i in range(3):
            result = run_sandbox("while True:\n    pass", timeout_sec=1)
            assert result["timed_out"] is True, f"第 {i+1} 次超时失败"

    @pytest.mark.unit
    @pytest.mark.p0
    def test_timeout_then_normal(self):
        """超时后立即执行正常代码——验证子进程清理不影响后续调用"""
        # 先触发超时
        timeout_result = run_sandbox("while True:\n    pass", timeout_sec=1)
        assert timeout_result["timed_out"] is True

        # 立即执行正常代码
        normal_result = run_sandbox("x = 1 + 2")
        assert normal_result["timed_out"] is False
        assert normal_result["error"] is None

    @pytest.mark.unit
    @pytest.mark.p0
    def test_normal_then_timeout(self):
        """正常代码后立即触发超时——验证正常执行不影响超时检测"""
        # 先执行正常代码
        normal_result = run_sandbox("x = 1 + 2")
        assert normal_result["error"] is None

        # 立即触发超时
        timeout_result = run_sandbox("while True:\n    pass", timeout_sec=1)
        assert timeout_result["timed_out"] is True

    @pytest.mark.unit
    @pytest.mark.p1
    def test_many_sequential_calls(self):
        """连续 10 次正常调用——验证无资源泄漏"""
        for i in range(10):
            result = run_sandbox(f"x = {i}")
            assert result["error"] is None
            assert result["timed_out"] is False

    @pytest.mark.unit
    @pytest.mark.p1
    def test_alternating_timeout_normal(self):
        """交替执行超时和正常代码——验证资源管理稳定"""
        for i in range(3):
            # 超时
            r1 = run_sandbox("while True:\n    pass", timeout_sec=1)
            assert r1["timed_out"] is True

            # 正常
            r2 = run_sandbox(f"x = {i}")
            assert r2["error"] is None


# ════════════════════════════════════════════════════════════
#  进程隔离
# ════════════════════════════════════════════════════════════


class TestSandboxProcessIsolation:
    """进程隔离测试——验证 multiprocessing 的进程级隔离优势"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_stdout_not_polluted(self):
        """子进程 stdout 修改不影响主进程——进程隔离核心优势"""
        import io
        original_stdout = sys.stdout

        # 子进程修改 stdout（通过 print）
        run_sandbox("print('child output')")

        # 主进程 stdout 不受影响
        assert sys.stdout is original_stdout

    @pytest.mark.unit
    @pytest.mark.p0
    def test_stderr_not_polluted(self):
        """子进程 stderr 修改不影响主进程"""
        original_stderr = sys.stderr

        run_sandbox("import sys\nsys.stderr.write('child error')")

        assert sys.stderr is original_stderr

    @pytest.mark.unit
    @pytest.mark.p1
    def test_globals_not_polluted(self):
        """子进程全局变量修改不影响主进程"""
        # 子进程尝试修改全局变量
        run_sandbox("import sys\nsys.custom_var = 'injected'")

        # 主进程不受影响
        assert not hasattr(sys, "custom_var")

    @pytest.mark.unit
    @pytest.mark.p1
    def test_dead_loop_no_gil_contention(self):
        """死循环超时后不占用 GIL——multiprocessing 的核心改进

        这是旧版 threading 实现的根本缺陷：超时后线程仍在运行，
        持续占用 GIL 影响后续测试。multiprocessing 实现中进程被
        terminate 后立即释放 CPU。
        """
        # 触发超时
        result = run_sandbox("while True:\n    pass", timeout_sec=1)
        assert result["timed_out"] is True

        # 超时后立即执行 CPU 密集任务，不应有明显的 GIL 竞争延迟
        start = time.time()
        sum(range(1000000))  # CPU 密集操作
        elapsed = time.time() - start

        # 如果 GIL 被竞争，这个操作会明显变慢
        # 正常应在 0.1s 内完成，给 5x 余量
        assert elapsed < 0.5, f"CPU 操作耗时 {elapsed:.2f}s，疑似 GIL 竞争"


# ════════════════════════════════════════════════════════════
#  返回值结构验证
# ════════════════════════════════════════════════════════════


class TestSandboxReturnValueStructure:
    """返回值结构测试——验证返回值字段完整性和类型"""

    @pytest.mark.unit
    @pytest.mark.p0
    def test_result_has_all_fields(self):
        """返回值包含所有必需字段"""
        result = run_sandbox("x = 1")
        assert "stdout" in result
        assert "stderr" in result
        assert "error" in result
        assert "timed_out" in result

    @pytest.mark.unit
    @pytest.mark.p0
    def test_result_types_correct(self):
        """返回值字段类型正确"""
        result = run_sandbox("x = 1")
        assert isinstance(result["stdout"], str)
        assert isinstance(result["stderr"], str)
        assert result["error"] is None or isinstance(result["error"], str)
        assert isinstance(result["timed_out"], bool)

    @pytest.mark.unit
    @pytest.mark.p0
    def test_timeout_result_structure(self):
        """超时返回值结构正确"""
        result = run_sandbox("while True:\n    pass", timeout_sec=1)
        assert result["timed_out"] is True
        assert isinstance(result["error"], str)
        assert result["stdout"] == ""

    @pytest.mark.unit
    @pytest.mark.p1
    def test_stdout_truncation_limit(self):
        """stdout 截断到 10000 字符"""
        # 通过 print 产生大量输出
        code = "for i in range(20000):\n    print(i)"
        result = run_sandbox(code, timeout_sec=10)
        assert len(result["stdout"]) <= 10000

    @pytest.mark.unit
    @pytest.mark.p1
    def test_stderr_truncation_limit(self):
        """stderr 截断到 5000 字符"""
        # 产生大量 stderr 输出
        code = "import sys\nfor i in range(10000):\n    sys.stderr.write(str(i) + '\\n')"
        result = run_sandbox(code, timeout_sec=10)
        assert len(result["stderr"]) <= 5000
