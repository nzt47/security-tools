"""AuditLogger 审计日志全面单元测试

测试目标：覆盖 agent/audit/logger.py 的所有分支
覆盖维度：
1. 正常路径：log 记录、query 查询
2. 边界条件：空输入、空输出、metadata 为 None
3. 哈希计算：_hash 方法
4. 全局单例：audit_logger
"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from agent.audit.logger import AuditLogger, audit_logger


@pytest.fixture
def audit(tmp_path):
    """独立审计日志器（使用临时目录）"""
    return AuditLogger(log_dir=str(tmp_path))


@pytest.fixture(autouse=True)
def _redirect_module_singleton(tmp_path, monkeypatch):
    """把**模块级单例** `audit_logger` 重绑到本用例的 `tmp_path`（绝不写生产审计链）。

    【TASK-A / 遗留 L2 的根因（2026-09-21 实测）】
      `agent/audit/logger.py:209` 的 `audit_logger = AuditLogger()` 在 **import 期**就以默认
      `log_dir="./data/audit/"` 定型，并用 `chain_db_path=<log_dir>/audit_chain.db`
      **显式传参**构造自己的 `AuditFacade`；而 `AuditFacade.__init__:198` 的路径优先级是

          db_path（显式实参） > AUDIT_DB_PATH（环境变量） > DEFAULT_DB_PATH

      ⇒ **显式实参压过环境变量** ⇒ 该单例既不读 `AUDIT_DB_PATH`，也完全不经过
        `facade.audit` ⇒ `tests/conftest.py` 的会话级隔离（设 `AUDIT_DB_PATH` +
        重绑 `facade.audit`）对**这个对象**恒为失效。

      实测代价：本文件 `TestGlobalInstance::test_audit_logger_can_log` 每跑一次就
        ① 往生产链 `data/audit/audit_chain.db` +1 条（count 20088→20089、
           max_seq 20103→20104；**主库 size 不变** ⇒ 该库是 WAL/journal 模式，
           只比 size/mtime 会漏判，必须比 `count(*)` 与 `max(seq)`）；
        ② 往生产旧轨 `data/audit/audit_2026MMDD.jsonl` 追加一行 `global_test_action`。

    【为什么用 monkeypatch 改绑单例、而不是 importlib.reload】
      改绑既有对象的绑定字段可在用例结束**精确复原**；reload 会重建模块级单例，
      使其它模块已持有的旧引用与新单例分裂（本仓已多处踩到该陷阱）。
      同时清空 `_facade`/`_track` 这两个懒加载缓存，迫使本次写入在 `tmp_path` 上重建，
      避免复用按生产路径建好的旧台账。
    """
    from agent.audit.logger import audit_logger as _singleton

    monkeypatch.setattr(_singleton, "_log_dir", tmp_path, raising=False)
    # 先取原名（仍带日期分片后缀），再换目录 —— 保持"按日分片"契约不变
    monkeypatch.setattr(_singleton, "_current_file",
                        tmp_path / _singleton._current_file.name, raising=False)
    monkeypatch.setattr(_singleton, "_chain_db_path",
                        str(tmp_path / "audit_chain.db"), raising=False)
    monkeypatch.setattr(_singleton, "_roots_path",
                        str(tmp_path / "daily_roots.jsonl"), raising=False)
    monkeypatch.setattr(_singleton, "_facade", None, raising=False)
    monkeypatch.setattr(_singleton, "_track", None, raising=False)
    yield
    # 关闭本次在 tmp_path 上建起的链（释放单写者登记）；monkeypatch 随后自动复原字段
    facade = getattr(_singleton, "_facade", None)
    if facade is not None:
        facade.close()
    _singleton._facade = None
    _singleton._track = None


# ── 1. 初始化 ──────────────────────────────────────────


class TestInit:
    def test_creates_log_dir(self, tmp_path):
        log_dir = tmp_path / "nested" / "audit"
        al = AuditLogger(log_dir=str(log_dir))
        assert log_dir.exists()

    def test_current_file_pattern(self, audit):
        """当前文件应按日期命名"""
        assert "audit_" in audit._current_file.name
        assert audit._current_file.name.endswith(".jsonl")

    def test_default_log_dir(self, monkeypatch, tmp_path):
        # 【TASK-A】默认目录断言用 chdir 落在 tmp_path：`AuditLogger()` 的构造会
        # `mkdir(parents=True, exist_ok=True)`，不 chdir 就会去碰仓库的生产
        # `data/audit/`（虽是 no-op，但没必要让用例接触生产目录面）。
        monkeypatch.chdir(tmp_path)
        al = AuditLogger()
        assert "audit" in str(al._log_dir).lower() or "data" in str(al._log_dir)


# ── 2. log 记录 ──────────────────────────────────────────


class TestLog:
    def test_log_basic(self, audit):
        audit.log("test_action")
        # 文件应存在并有内容
        assert audit._current_file.exists()
        content = audit._current_file.read_text(encoding="utf-8").strip()
        record = json.loads(content)
        assert record["action"] == "test_action"
        assert record["status"] == "success"

    def test_log_with_input_output(self, audit):
        audit.log("action", input_data="input", output_data="output")
        content = audit._current_file.read_text(encoding="utf-8").strip()
        record = json.loads(content)
        assert record["input_hash"]  # 非空
        assert record["output_hash"]

    def test_log_empty_input_output(self, audit):
        """空输入输出应记录空字符串哈希字段"""
        audit.log("action", input_data="", output_data="")
        content = audit._current_file.read_text(encoding="utf-8").strip()
        record = json.loads(content)
        assert record["input_hash"] == ""
        assert record["output_hash"] == ""

    def test_log_with_metadata(self, audit):
        audit.log("action", metadata={"key": "value"})
        content = audit._current_file.read_text(encoding="utf-8").strip()
        record = json.loads(content)
        assert record["metadata"] == {"key": "value"}

    def test_log_metadata_none_defaults_to_empty(self, audit):
        audit.log("action")
        content = audit._current_file.read_text(encoding="utf-8").strip()
        record = json.loads(content)
        assert record["metadata"] == {}

    def test_log_with_status(self, audit):
        audit.log("action", status="failure")
        content = audit._current_file.read_text(encoding="utf-8").strip()
        record = json.loads(content)
        assert record["status"] == "failure"

    def test_log_includes_timestamp(self, audit):
        audit.log("action")
        content = audit._current_file.read_text(encoding="utf-8").strip()
        record = json.loads(content)
        assert "timestamp" in record
        assert "T" in record["timestamp"]  # ISO 格式

    def test_log_includes_trace_id(self, audit):
        audit.log("action")
        content = audit._current_file.read_text(encoding="utf-8").strip()
        record = json.loads(content)
        assert "trace_id" in record

    def test_log_includes_stack_depth(self, audit):
        audit.log("action")
        content = audit._current_file.read_text(encoding="utf-8").strip()
        record = json.loads(content)
        assert record["stack_depth"] > 0

    def test_log_appends_multiple(self, audit):
        """多次 log 应追加到同一文件"""
        audit.log("action1")
        audit.log("action2")
        audit.log("action3")
        lines = audit._current_file.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 3


# ── 3. _hash 哈希计算 ──────────────────────────────────


class TestHash:
    def test_hash_returns_string(self, audit):
        h = audit._hash("test")
        assert isinstance(h, str)

    def test_hash_length_16(self, audit):
        """哈希应为 16 字符（sha256 前 16 位）"""
        h = audit._hash("test")
        assert len(h) == 16

    def test_hash_deterministic(self, audit):
        """相同输入应产生相同哈希"""
        h1 = audit._hash("test")
        h2 = audit._hash("test")
        assert h1 == h2

    def test_hash_different_inputs_different(self, audit):
        h1 = audit._hash("input1")
        h2 = audit._hash("input2")
        assert h1 != h2

    def test_hash_hex_chars(self, audit):
        h = audit._hash("test")
        assert all(c in "0123456789abcdef" for c in h)


# ── 4. query 查询 ──────────────────────────────────────────


class TestQuery:
    def test_query_empty_returns_empty(self, audit):
        results = audit.query()
        assert results == []

    def test_query_returns_all(self, audit):
        audit.log("action1")
        audit.log("action2")
        results = audit.query()
        assert len(results) == 2

    def test_query_by_action(self, audit):
        audit.log("action_a")
        audit.log("action_b")
        audit.log("action_a")
        results = audit.query(action="action_a")
        assert len(results) == 2
        assert all(r["action"] == "action_a" for r in results)

    def test_query_by_trace_id(self, audit):
        with patch("agent.audit.logger.get_trace_id", return_value="trace_123"):
            audit.log("action1")
        with patch("agent.audit.logger.get_trace_id", return_value="trace_456"):
            audit.log("action2")
        results = audit.query(trace_id="trace_123")
        assert len(results) == 1
        assert results[0]["trace_id"] == "trace_123"

    def test_query_limit(self, audit):
        for i in range(10):
            audit.log(f"action_{i}")
        results = audit.query(limit=5)
        assert len(results) == 5

    def test_query_filter_combined(self, audit):
        with patch("agent.audit.logger.get_trace_id", return_value="trace_x"):
            audit.log("target_action")
            audit.log("other_action")
        results = audit.query(trace_id="trace_x", action="target_action")
        assert len(results) == 1


# ── 5. 全局单例 ──────────────────────────────────────────


class TestGlobalInstance:
    def test_audit_logger_is_instance(self):
        assert isinstance(audit_logger, AuditLogger)

    def test_audit_logger_can_log(self):
        audit_logger.log("global_test_action")
        # 不抛异常即通过


# ── 6. 集成场景 ──────────────────────────────────────────


class TestIntegration:
    def test_log_then_query_roundtrip(self, audit):
        audit.log("login", input_data="user=admin", status="success")
        audit.log("logout", input_data="user=admin", status="success")
        results = audit.query(action="login")
        assert len(results) == 1
        assert results[0]["input_hash"]

    def test_multiple_log_files(self, tmp_path):
        """跨日志文件查询"""
        al1 = AuditLogger(log_dir=str(tmp_path))
        # 模拟不同日期的文件
        al1._current_file = tmp_path / "audit_20260101.jsonl"
        al1.log("old_action")
        al2 = AuditLogger(log_dir=str(tmp_path))
        al2._current_file = tmp_path / "audit_20260102.jsonl"
        al2.log("new_action")
        # 查询应跨文件
        results = AuditLogger(log_dir=str(tmp_path)).query()
        assert len(results) >= 2


# ── 7. 生产隔离回归锁定（TASK-A / 遗留 L2） ──────────────────
#
# 这一节锁定的是**修复本身**，而不仅是 `log()` 的功能：本文件里的写入
# **只允许**落到用例自己的 `tmp_path`，且**必须**真实落链（否则"隔离"会退化成
# 静默不写，那是另一种假绿 —— 见 TASK-00 D12「夹具形状不得代替生产形状」）。


def _production_audit_dir() -> Path:
    return Path(__file__).resolve().parents[2] / "data" / "audit"


class TestProductionIsolation:
    def test_singleton_is_redirected_to_tmp_path(self, tmp_path):
        """autouse 夹具必须把模块级单例的四个路径绑定全部改到 tmp_path"""
        for attr in ("_log_dir", "_chain_db_path", "_roots_path"):
            got = Path(str(getattr(audit_logger, attr))).resolve()
            assert got == tmp_path.resolve() or tmp_path.resolve() in got.parents, (
                f"模块级单例的 {attr} 未指向 tmp_path：{got}"
            )
        assert Path(str(audit_logger._current_file)).parent.resolve() == tmp_path.resolve()

    def test_log_writes_into_tmp_path_and_really_hits_chain(self, tmp_path):
        """写入必须落在 tmp_path，且链式轨**真的被写到**（不是静默 no-op 的假隔离）

        只断言"没写生产"是不够的：若隔离把链写也一起弄坏（例如改绑后 facade 建不起来、
        best-effort 静默吞掉），那会是另一种假绿（TASK-00 D12）。故这里正向断言链里有
        本次动作的条目。
        """
        marketing = "t10_isolation_probe_action"
        audit_logger.log(marketing)
        audit_logger.flush()

        legacy = Path(str(audit_logger._current_file))
        assert legacy.exists(), "旧轨 JSONL 未落到 tmp_path"
        assert marketing in legacy.read_text(encoding="utf-8"), "旧轨未包含本次动作"
        assert (tmp_path / "audit_chain.db").exists(), "链式台账未在 tmp_path 建起"

        chain = audit_logger.chain
        assert chain is not None, "链式轨不可用（隔离把链写一起弄坏了）"
        actions = [e.action for e in audit_logger.query_chain(limit=20)]
        assert actions == [marketing], f"tmp 链内容不符：{actions}"

    def test_production_audit_dir_untouched_by_this_file(self, tmp_path):
        """本文件写入的动作**不得**出现在仓库生产审计面上（旧轨 + 链）"""
        marketing = "t10_isolation_probe_action_negative"
        audit_logger.log(marketing)
        audit_logger.flush()

        prod = _production_audit_dir()
        offenders = [
            f.name for f in prod.glob("audit_*.jsonl")
            if marketing in f.read_text(encoding="utf-8", errors="replace")
        ]
        assert not offenders, f"生产旧轨被测试写入：{offenders}"

        db = prod / "audit_chain.db"
        if not db.exists():  # 生产链不存在（干净环境）→ 无可污染
            pytest.skip("生产审计链不存在，跳过链侧负例")
        import sqlite3

        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            hit = con.execute(
                "select count(*) from audit_chain where action = ?", (marketing,)
            ).fetchone()[0]
        finally:
            con.close()
        assert hit == 0, f"生产链被测试写入 {hit} 条 action={marketing}"
