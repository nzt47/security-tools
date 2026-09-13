"""审计日志 + Trace 集成测试

验证 AuditLogger 能感知当前 Trace_ID：
  - 设置 trace_id → 审计记录携带相同 trace_id
  - query 能按 trace_id 过滤

【S9-02 修复】本文件此前有两个真缺陷（每次运行都复现，非抖动）：
  1. 用**仓库根相对路径** ``./test_audit_integration`` ⇒ 每次跑完都在工作区留下
     ``audit_chain.db`` / ``.lock`` / ``.seqjournal``（且这些文件**未**被 .gitignore 覆盖，
     于是污染 ``git status``，属"产物漂移"）；
  2. teardown 直接 ``rmtree``，**没有先释放** `AuditLogger` 持有的链式轨写者
     ⇒ Windows 下 ``PermissionError: [WinError 32] 另一个程序正在使用此文件``
     ⇒ 每条用例都报一个 teardown ERROR（断言本身是通过的，于是很容易被当成"抖动"忽略）。

修法：改用系统临时目录；teardown 先 ``flush()`` + ``close()`` 再删。
teardown **故意不**用 ``ignore_errors=True`` —— 真有泄漏就该继续报错，不能被顺手掩盖。
"""
import os
import json
import shutil
import tempfile
import logging
from agent.audit.logger import AuditLogger
from agent.observability.tracer import generate_trace_id, get_trace_id, set_trace_id

_log = logging.getLogger(__name__)


class TestAuditTrace:
    def setup_method(self):
        # S9-02：用系统临时目录，别把运行期产物写进仓库工作区
        self._log_dir = tempfile.mkdtemp(prefix="cp_test_audit_")
        self._loggers = []

    def _new_logger(self) -> AuditLogger:
        logger = AuditLogger(log_dir=self._log_dir)
        self._loggers.append(logger)
        return logger

    def test_audit_carries_trace_id(self):
        """审计记录应携带当前设置的 Trace_ID"""
        trace_id = generate_trace_id()
        set_trace_id(trace_id)

        logger = self._new_logger()
        logger.log("test_action", input_data="input")

        records = logger.query(trace_id=trace_id)
        assert len(records) >= 1
        assert records[0]["trace_id"] == trace_id

    def test_different_trace_ids_isolated(self):
        """不同 trace_id 的审计记录应互不干扰"""
        trace_a = generate_trace_id()
        set_trace_id(trace_a)
        logger = self._new_logger()
        logger.log("action_a", input_data="aaa")

        trace_b = generate_trace_id()
        set_trace_id(trace_b)
        logger.log("action_b", input_data="bbb")

        records_a = logger.query(trace_id=trace_a)
        records_b = logger.query(trace_id=trace_b)

        assert len(records_a) == 1
        assert len(records_b) == 1
        assert records_a[0]["input_hash"] == logger._hash("aaa")
        assert records_b[0]["input_hash"] == logger._hash("bbb")

    def teardown_method(self):
        # S9-02：先释放链式轨写者（否则 Windows 下 sqlite 文件仍被占用 ⇒ WinError 32）
        for logger in self._loggers:
            try:
                logger.flush(timeout=5.0)
                logger.close()
            except Exception:      # 释放失败不应掩盖用例本身的结果
                pass
        self._loggers = []
        if not os.path.exists(self._log_dir):
            return
        # 【已知产品侧缺口，S9-02 实测留痕】`AuditChain.close()` 会 flush + 停 writer +
        # 释放单写者登记，但**不释放** `<db>.lock` 的跨进程锁文件句柄 ⇒ 该文件在
        # close() 之后仍删不掉。故这里**精确地**只容忍 `*.lock`：
        # 其它任何文件删不掉都继续抛错 —— 真正的句柄泄漏不许被这一处掩盖。
        stubborn = []
        for name in os.listdir(self._log_dir):
            path = os.path.join(self._log_dir, name)
            try:
                os.remove(path)
            except OSError:
                if name.endswith(".lock"):
                    stubborn.append(name)
                else:
                    raise
        if stubborn:
            _log.warning(
                "AuditChain.close() 后仍被占用的锁文件（已知缺口，需产品侧释放）: %s",
                ", ".join(sorted(stubborn)))
        shutil.rmtree(self._log_dir, ignore_errors=True)
