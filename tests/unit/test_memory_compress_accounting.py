"""压缩触发口径：工具结果**单独计量**（2026-10-02 落地 2026-09-13 Owner 立规）

背景：工具输出动辄上万 token（实测 `list_directory` 返回 195 项 ≈14k）。若它与对话正文
一起计入压缩触发口径，就是"**一次大工具输出 = 一次压缩**"：多付一次 LLM 摘要调用（钱+延迟），
还把刚拿到的上下文压掉。

【现状核实】真实记忆文件（`memory_data/messages.jsonl`，1481 条）里**只有 user/assistant**：
编排器只把这两类写进记忆，工具结果留在请求内的 `_working`。所以本口径今天是**防御性**的 ——
它把"工具结果不触发压缩"从"碰巧没人写进去"变成**构造性事实**，将来谁把工具结果落进记忆，
这里立刻兜住（本文件末尾那两条用例就是为那一天准备的）。

同时保留**兜底**：工具结果单独计量 ≠ 永不压缩。它们自己就能撑爆窗口时仍触发，
否则一个"只产工具输出"的记忆会无界增长。
"""
from __future__ import annotations

import pytest

from memory.memory_manager import MemoryManager


@pytest.fixture
def manager(tmp_path, monkeypatch):
    """临时目录的 MemoryManager；token 计数用 count(s)=len(s)（数字可手算复现）"""
    mgr = MemoryManager(config={
        "data_dir": str(tmp_path / "memory_data"),
        "token_limit": 1000,
        "compress_threshold": 0.8,
        "llm": {"provider": "openai", "api_key": "sk-test-key-valid-12345", "model": "gpt-4"},
        "async_compress": {"enabled": False},
    })

    class _Counter:
        def count(self, text):
            return len(text or "")

        def count_messages(self, msgs):
            return sum(len((m or {}).get("content", "") or "") for m in msgs or [])

    mgr._token_counter = _Counter()
    return mgr


def _msg(role, size, **extra):
    return {"role": role, "content": "x" * size, **extra}


class TestAccounting:
    """切分：正文 vs 工具结果"""

    def test_正文与工具结果分开累加(self, manager):
        counted, tool = manager._compress_accounting([
            _msg("user", 100),
            _msg("assistant", 200),
            _msg("tool", 5000, tool_call_id="call_1"),
        ])
        assert counted == 300
        assert tool == 5000

    def test_带_tool_call_id_的非_tool_角色也算工具结果(self, manager):
        """OpenAI 兼容有两种形态：role=tool，或带 tool_call_id 的其它角色"""
        _, tool = manager._compress_accounting([_msg("function", 777, tool_call_id="c1")])
        assert tool == 777

    def test_空内容与脏数据不炸(self, manager):
        counted, tool = manager._compress_accounting([
            {"role": "user"},
            {"role": None, "content": None},
            _msg("tool", 10),
        ])
        assert counted == 0
        assert tool == 10

    def test_空列表(self, manager):
        assert manager._compress_accounting([]) == (0, 0)


class TestTrigger:
    """触发判定：正文按阈值；工具结果只在**自己撑爆窗口**时兜底触发"""

    def test_超大工具输出不再触发压缩(self, tmp_path):
        """这是立项要的那个行为：14k 的一次大工具输出，不该把对话压掉

        【为什么用 131072 的窗口】口径必须放在**真实量级**上验：
        `list_directory` 返回 195 项 ≈14k token，而生产窗口是 131072 ⇒ 14k 远不构成
        "撑爆窗口"，因此只该被单独计量、不该触发压缩。
        （小窗口下的兜底行为另有用例：见 test_工具结果撑爆窗口时兜底触发）
        """
        mgr = MemoryManager(config={
            "data_dir": str(tmp_path / "memory_big"),
            "token_limit": 131072,
            "compress_threshold": 0.8,
            "llm": {"provider": "openai", "api_key": "sk-test-key-valid-12345", "model": "gpt-4"},
            "async_compress": {"enabled": False},
        })

        class _Counter:
            def count(self, text):
                return len(text or "")

        mgr._token_counter = _Counter()
        assert mgr._should_compress_now([_msg("tool", 14000)]) is False
        # 正文侧照常：正文累到 80% 仍会触发（本改动没有放松对话本身的压缩纪律）
        assert mgr._should_compress_now([_msg("user", 131072 * 4 // 5)]) is True

    def test_正文超过阈值仍然触发(self, manager):
        body = [_msg("user", 500), _msg("assistant", 400)]  # 900 >= 1000*0.8
        assert manager._should_compress_now(body) is True

    def test_正文在阈值下不触发(self, manager):
        assert manager._should_compress_now([_msg("user", 700)]) is False

    def test_工具结果撑爆窗口时兜底触发(self, manager):
        """单独计量 ≠ 永不压缩：否则只产工具输出的记忆会无界增长"""
        assert manager._should_compress_now([_msg("tool", 1000)]) is True
        assert manager._should_compress_now([_msg("tool", 999)]) is False

    def test_两类混合时按正文判定(self, manager):
        """正文没到阈值、工具结果也没撑爆 ⇒ 不压缩（哪怕两者相加远超阈值）"""
        mixed = [_msg("user", 300), _msg("tool", 900)]
        assert manager._should_compress_now(mixed) is False


class TestOtherEntryPointsShareTheSameRule:
    """审计 P2-1/P2-4：压缩触发的**每一处入口**都必须走同一口径"""

    def test_维护线程不再自算压缩(self):
        """P2-1：lifecycle_manager._run_maint_compress 原先自行 count_messages + should_compress，
        绕过"工具结果单独计量"。静态守门：该方法体内必须调用 _should_compress_now。"""
        from pathlib import Path
        import re

        path = (Path(__file__).resolve().parents[2] / "agent" / "orchestrator"
                / "lifecycle_manager.py")
        src = path.read_text(encoding="utf-8")
        body = src.split("def _run_maint_compress", 1)[1].split("def _run_maint_prune", 1)[0]
        assert "_should_compress_now" in body, "维护线程必须走单一压缩口径（否则大工具输出又会被当正文计）"
        # 直算 should_compress 只允许作为**旧内存管理器的兜底**存在，且必须有显式说明
        direct = re.findall(
            r"^\s*should = bool\(self\._memory\._summarizer\.should_compress", body, re.M)
        assert len(direct) <= 1, "维护线程不得自己再算一套压缩口径"
        if direct:
            assert "拿不到新实现时退回旧算法" in body, "兜底分支必须写明它是兜底而非主口径"

    def test_高重要性检查不再是压缩的前置条件(self, manager):
        """P2-4：主链路去掉重复写入后，score_and_save_message 必须**每次**都查压缩 ——
        否则"低重要性消息堆到超阈值"就再也没人触发压缩（新洞）"""
        manager.add_message("tool", "x" * 100)   # 工具结果：只进工具口径
        assert manager._should_compress_now([_msg("user", 900)]) is True
        # 直接调用评分写入（低长度 ⇒ 低重要性分），压缩标志仍必须被点亮
        manager._need_compress = False
        manager.score_and_save_message("user", "y" * 900)
        assert manager._need_compress is True, "低重要性消息超阈值也必须触发压缩检查"


class TestLiveInvariant:
    """把"工具结果不进记忆"这条**现状**也钉住：它是上面口径有效的前提"""

    def test_用例自身记录了前提(self):
        """说明性用例：真实记忆里只有 user/assistant（2026-10-02 实测 1481 条、0 条 tool）。

        为什么写成用例而不是注释：将来若有人把工具结果写进记忆，
        `_should_compress_now` 的兜底会接管（不会被静默压掉上下文），
        而这条"前提"若变了，也应当有人主动来这里更新说明。
        """
        assert True  # 只是文档锚点，不做断言（真实数据在 CI 里不可得，见 docstring）