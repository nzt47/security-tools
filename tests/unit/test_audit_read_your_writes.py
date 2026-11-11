# -*- coding: utf-8 -*-
"""审计链「读自己刚写的」（read-your-writes）回归测试（2026-11-11）。

【为什么新增】审计链是"先落预留日志 → 再入队后台批量入库"，读路径靠
`flush()` 做屏障。旧实现里 `append()` 的顺序是「先 put 再 ++_enqueue_count」：
`flush()` 恰好落在两者之间时读到的是**旧 target** ⇒ 立刻返回成功，
而记录仍在队列里 ⇒ 紧接着的读取少一条。表现就是 CI 上偶发的
`assert 0 == 1`（`test_skill_registry_audit` 两处、`test_memory_comparison` 的 refcount 同族）。

修法：计数与入队在**同一临界区**、且计数先于入队（`chain.py::append` ②）。
本文件用"追加后立刻读"的循环把这条不变量钉死 —— 不依赖 sleep，也不依赖测试轮询。
"""
from __future__ import annotations

import threading
from pathlib import Path

import pytest

from agent.audit.chain import get_audit_chain


@pytest.fixture
def chain(tmp_path: Path):
    instance = get_audit_chain(str(tmp_path / "audit_ryw.db"))
    try:
        yield instance
    finally:
        instance.close()


class TestReadYourWrites:
    def test_连续追加后立刻读取必须全见到(self, chain):
        for i in range(200):
            chain.append(action="test.ryw", actor="tester", subject="s%d" % i)
            rows = chain.entries(action="test.ryw")
            assert len(rows) == i + 1, (
                "第 %d 次追加后立刻读取只看到 %d 条 —— flush 屏障欠等（计数晚于入队）"
                % (i + 1, len(rows))
            )

    def test_并发追加后读取不丢记录(self, chain):
        total = 8
        threads = []

        def worker(index: int) -> None:
            for j in range(10):
                chain.append(action="test.ryw.concurrent", actor="w%d" % index,
                             subject="s%d-%d" % (index, j))

        for index in range(total):
            thread = threading.Thread(target=worker, args=(index,))
            threads.append(thread)
            thread.start()
        for thread in threads:
            thread.join()

        rows = chain.entries(action="test.ryw.concurrent")
        assert len(rows) == total * 10, "并发追加后读取缺记录：%d != %d" % (len(rows), total * 10)

    def test_count_与_entries_口径一致(self, chain):
        for i in range(20):
            chain.append(action="test.ryw.count", actor="tester", subject="c%d" % i)
        assert chain.count() >= 20
        assert len(chain.entries(action="test.ryw.count")) == 20
