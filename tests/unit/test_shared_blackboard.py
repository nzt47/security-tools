"""SharedBlackboard 共享黑板单元测试

覆盖维度:
- 基础读写: write/read, 缺失/类型不匹配返回 None
- schema 校验: type/required/properties/嵌套/列表 type/bool 排除
- 快照: 深拷贝独立、含 failures/warnings
- 失败追踪: record_failure/has_failure/get_failures
- 操作审计: _operations (供 executor 锁外批量打印排查)
- 性能: read/write < 0.1ms (内存字典 + 校验)
- 模板集成: $bb.<step>.<key> 类型化引用 + $step 兼容层

不变量: 黑板纯内存无 I/O, 满足 "持锁操作严禁 I/O" 硬约束
"""
import time

import pytest

from agent.workflow_learning import SharedBlackboard, WorkflowSchemaError
from agent.workflow_learning.executor import _resolve_template


# ═══════════════════════════════════════════════════════════════════
#  1. 基础读写
# ═══════════════════════════════════════════════════════════════════

class TestBlackboardBasic:
    """黑板基础读写"""

    def test_write_and_read_basic(self):
        """无 schema 写入后应能读出原值"""
        bb = SharedBlackboard()
        bb.write("A", "result", {"score": 0.9})
        assert bb.read("A", "result") == {"score": 0.9}

    def test_write_no_schema_skips_validation(self):
        """schema=None 不校验, 任意类型均可写入"""
        bb = SharedBlackboard()
        bb.write("A", "k", "anything")
        bb.write("A", "k2", 123)
        bb.write("B", "k", [1, 2, 3])
        assert bb.read("A", "k") == "anything"
        assert bb.read("A", "k2") == 123
        assert bb.read("B", "k") == [1, 2, 3]

    def test_read_missing_returns_none(self):
        """读取不存在的 step/key 应返回 None (不抛异常)"""
        bb = SharedBlackboard()
        assert bb.read("X", "y") is None
        bb.write("A", "k", 1)
        assert bb.read("A", "missing") is None
        assert bb.read("B", "k") is None

    def test_read_type_mismatch_returns_none(self):
        """expected_type 不匹配应返回 None (不抛异常)"""
        bb = SharedBlackboard()
        bb.write("A", "k", "string_val")
        assert bb.read("A", "k", int) is None
        assert bb.read("A", "k", str) == "string_val"

    def test_read_type_match_with_subclass(self):
        """expected_type 应支持父类匹配 (isinstance 语义)"""
        bb = SharedBlackboard()
        bb.write("A", "k", {"a": 1})
        assert bb.read("A", "k", dict) == {"a": 1}

    def test_overwrite_same_key(self):
        """同 step+key 重复写入应覆盖"""
        bb = SharedBlackboard()
        bb.write("A", "k", 1)
        bb.write("A", "k", 2)
        assert bb.read("A", "k") == 2

    def test_list_step_keys(self):
        """列出某步骤已写入的所有键"""
        bb = SharedBlackboard()
        bb.write("A", "k1", 1)
        bb.write("A", "k2", 2)
        bb.write("B", "k3", 3)
        assert set(bb.list_step_keys("A")) == {"k1", "k2"}
        assert bb.list_step_keys("B") == ["k3"]
        assert bb.list_step_keys("C") == []


# ═══════════════════════════════════════════════════════════════════
#  2. schema 校验
# ═══════════════════════════════════════════════════════════════════

class TestSchemaValidation:
    """json-schema 子集校验"""

    def test_write_valid_object_schema(self):
        """合法 object + required + properties 应通过"""
        bb = SharedBlackboard()
        schema = {
            "type": "object",
            "required": ["score"],
            "properties": {"score": {"type": "number"}},
        }
        bb.write("A", "k", {"score": 0.9}, schema=schema)
        assert bb.read("A", "k") == {"score": 0.9}

    def test_write_invalid_type_raises(self):
        """type 不匹配应抛 WorkflowSchemaError"""
        bb = SharedBlackboard()
        with pytest.raises(WorkflowSchemaError) as exc:
            bb.write("A", "k", "not_int", schema={"type": "integer"})
        assert "A" in str(exc.value)
        assert "k" in str(exc.value)

    def test_write_missing_required_raises(self):
        """缺必填字段应抛异常"""
        bb = SharedBlackboard()
        schema = {"type": "object", "required": ["a", "b"]}
        with pytest.raises(WorkflowSchemaError):
            bb.write("A", "k", {"a": 1}, schema=schema)

    def test_write_nested_properties_validation(self):
        """嵌套 properties 应递归校验"""
        bb = SharedBlackboard()
        schema = {
            "type": "object",
            "properties": {
                "inner": {
                    "type": "object",
                    "required": ["x"],
                    "properties": {"x": {"type": "string"}},
                },
            },
        }
        bb.write("A", "k", {"inner": {"x": "ok"}}, schema=schema)
        with pytest.raises(WorkflowSchemaError):
            bb.write("B", "k", {"inner": {"x": 123}}, schema=schema)
        with pytest.raises(WorkflowSchemaError):
            bb.write("C", "k", {"inner": {}}, schema=schema)

    def test_write_type_as_list(self):
        """type 为列表时应满足任一即可"""
        bb = SharedBlackboard()
        schema = {"type": ["string", "null"]}
        bb.write("A", "k", "ok", schema=schema)
        bb.write("B", "k", None, schema=schema)
        assert bb.read("A", "k") == "ok"
        assert bb.read("B", "k") is None

    def test_bool_not_integer(self):
        """bool 不应被接受为 integer (json-schema 语义)"""
        bb = SharedBlackboard()
        with pytest.raises(WorkflowSchemaError):
            bb.write("A", "k", True, schema={"type": "integer"})

    def test_bool_not_number(self):
        """bool 不应被接受为 number"""
        bb = SharedBlackboard()
        with pytest.raises(WorkflowSchemaError):
            bb.write("A", "k", False, schema={"type": "number"})

    def test_integer_accepted_as_number(self):
        """integer 应被 number 接受 (int 是 number 子类型)"""
        bb = SharedBlackboard()
        bb.write("A", "k", 42, schema={"type": "number"})
        assert bb.read("A", "k") == 42

    def test_array_type(self):
        """array 类型校验"""
        bb = SharedBlackboard()
        bb.write("A", "k", [1, 2, 3], schema={"type": "array"})
        with pytest.raises(WorkflowSchemaError):
            bb.write("B", "k", "not_array", schema={"type": "array"})

    def test_any_type_accepts_all(self):
        """any 类型应接受所有值"""
        bb = SharedBlackboard()
        for v in ["x", 1, 1.5, True, None, [1], {"a": 1}]:
            bb.write("A", "k", v, schema={"type": "any"})

    def test_unknown_type_raises(self):
        """未知 type 名应抛异常 (而非静默通过)"""
        bb = SharedBlackboard()
        with pytest.raises(WorkflowSchemaError):
            bb.write("A", "k", 1, schema={"type": "bogus"})

    def test_schema_error_carries_context(self):
        """WorkflowSchemaError 应携带 step_id/key/reason 上下文"""
        bb = SharedBlackboard()
        with pytest.raises(WorkflowSchemaError) as exc:
            bb.write("stepX", "keyY", "bad", schema={"type": "integer"})
        err = exc.value
        assert err.details["step_id"] == "stepX"
        assert err.details["key"] == "keyY"
        assert "reason" in err.details


# ═══════════════════════════════════════════════════════════════════
#  3. 快照
# ═══════════════════════════════════════════════════════════════════

class TestSnapshot:
    """黑板快照 (调试 / trace)"""

    def test_snapshot_returns_deepcopy(self):
        """快照应为深拷贝, 修改快照不影响黑板"""
        bb = SharedBlackboard()
        bb.write("A", "k", {"nested": [1, 2]})
        snap = bb.snapshot()
        snap["data"]["A"]["k"]["nested"].append(3)
        snap["data"]["A"]["k"]["new"] = "x"
        assert bb.read("A", "k") == {"nested": [1, 2]}

    def test_snapshot_contains_data(self):
        """快照应包含所有写入数据"""
        bb = SharedBlackboard()
        bb.write("A", "k1", 1)
        bb.write("B", "k2", "x")
        snap = bb.snapshot()
        assert snap["data"]["A"]["k1"] == 1
        assert snap["data"]["B"]["k2"] == "x"

    def test_snapshot_contains_failures(self):
        """快照应包含失败记录"""
        bb = SharedBlackboard()
        bb.record_failure("A", "timeout")
        snap = bb.snapshot()
        assert len(snap["failures"]) == 1
        assert snap["failures"][0]["step_id"] == "A"

    def test_snapshot_contains_warnings(self):
        """快照应包含 read 缺失/类型不匹配的 warning"""
        bb = SharedBlackboard()
        bb.write("A", "k", "str")
        bb.read("A", "missing")
        bb.read("A", "k", int)
        snap = bb.snapshot()
        assert len(snap["warnings"]) == 2

    def test_snapshot_keys_structure(self):
        """快照应含 data/failures/warnings/write_ts/operations 五键"""
        bb = SharedBlackboard()
        snap = bb.snapshot()
        assert set(snap.keys()) == {"data", "failures", "warnings",
                                    "write_ts", "operations"}


# ═══════════════════════════════════════════════════════════════════
#  3.5 操作审计 (_operations — 供 executor 锁外批量打印排查)
# ═══════════════════════════════════════════════════════════════════

class TestOperationsAudit:
    """黑板操作审计 — 每次 write/read/record_failure 追加内存记录"""

    def test_write_records_operation(self):
        """write 应记录 op, 含 schema 标志"""
        bb = SharedBlackboard()
        bb.write("A", "k", 1)
        bb.write("A", "k", 2, schema={"type": "integer"})
        writes = [o for o in bb.snapshot()["operations"] if o["op"] == "write"]
        assert len(writes) == 2
        assert writes[0]["step"] == "A"
        assert writes[0]["key"] == "k"
        assert writes[0]["schema"] is False
        assert writes[1]["schema"] is True

    def test_read_records_hit_and_miss(self):
        """read 应记录 hit/miss/mismatch"""
        bb = SharedBlackboard()
        bb.write("A", "k", "v")
        bb.read("A", "k")
        bb.read("A", "missing")
        bb.read("A", "k", int)
        reads = [o for o in bb.snapshot()["operations"] if o["op"] == "read"]
        assert len(reads) == 3
        assert reads[0]["hit"] is True
        assert reads[1]["hit"] is False
        assert reads[2]["hit"] is False
        assert reads[2].get("mismatch") is True

    def test_record_failure_logs_operation(self):
        """record_failure 应记录 op=fail"""
        bb = SharedBlackboard()
        bb.record_failure("A", "timeout", error="boom")
        fails = [o for o in bb.snapshot()["operations"] if o["op"] == "fail"]
        assert len(fails) == 1
        assert fails[0]["step"] == "A"
        assert fails[0]["reason"] == "timeout"

    def test_operations_in_snapshot_is_copy(self):
        """snapshot 返回的 operations 应是副本, 修改不影响黑板"""
        bb = SharedBlackboard()
        bb.write("A", "k", 1)
        snap = bb.snapshot()
        snap["operations"].append({"op": "fake"})
        assert len(bb.snapshot()["operations"]) == 1


# ═══════════════════════════════════════════════════════════════════
#  4. 失败追踪
# ═══════════════════════════════════════════════════════════════════

class TestFailureTracking:
    """步骤失败记录 (供后续步骤决策跳过/降级)"""

    def test_record_failure(self):
        """记录失败应追加到列表"""
        bb = SharedBlackboard()
        bb.record_failure("A", "condition_not_met")
        bb.record_failure("B", "execution_error", error="boom")
        failures = bb.get_failures()
        assert len(failures) == 2
        assert failures[0]["step_id"] == "A"
        assert failures[1]["error"] == "boom"

    def test_has_failure(self):
        """查询某步骤是否已失败"""
        bb = SharedBlackboard()
        bb.record_failure("A", "x")
        assert bb.has_failure("A") is True
        assert bb.has_failure("B") is False

    def test_get_failures_returns_copy(self):
        """get_failures 返回副本, 修改不影响内部"""
        bb = SharedBlackboard()
        bb.record_failure("A", "x")
        failures = bb.get_failures()
        failures.clear()
        assert len(bb.get_failures()) == 1


# ═══════════════════════════════════════════════════════════════════
#  5. 性能 (< 0.1ms 单次读写)
# ═══════════════════════════════════════════════════════════════════

class TestPerformance:
    """黑板读写性能 (内存字典 + 校验)"""

    def test_write_perf_under_0_1ms(self):
        """单次 write (含 schema 校验) 应 < 0.1ms"""
        bb = SharedBlackboard()
        schema = {"type": "object",
                  "required": ["score"],
                  "properties": {"score": {"type": "number"}}}
        for i in range(100):
            bb.write(f"pre{i}", "k", {"score": i}, schema=schema)
        t0 = time.perf_counter()
        for i in range(1000):
            bb.write(f"s{i}", "k", {"score": i}, schema=schema)
        avg_ms = (time.perf_counter() - t0) / 1000 * 1000
        assert avg_ms < 0.1, f"write 平均 {avg_ms:.4f}ms 超过 0.1ms"

    def test_read_perf_under_0_1ms(self):
        """单次 read (含类型校验) 应 < 0.1ms"""
        bb = SharedBlackboard()
        for i in range(1000):
            bb.write(f"s{i}", "k", {"score": i})
        t0 = time.perf_counter()
        for i in range(1000):
            bb.read(f"s{i}", "k", dict)
        avg_ms = (time.perf_counter() - t0) / 1000 * 1000
        assert avg_ms < 0.1, f"read 平均 {avg_ms:.4f}ms 超过 0.1ms"

    def test_write_no_schema_perf_under_0_1ms(self):
        """无 schema write 应更快 (< 0.01ms 量级)"""
        bb = SharedBlackboard()
        t0 = time.perf_counter()
        for i in range(1000):
            bb.write(f"s{i}", "k", i)
        avg_ms = (time.perf_counter() - t0) / 1000 * 1000
        assert avg_ms < 0.1


# ═══════════════════════════════════════════════════════════════════
#  6. 模板集成 ($bb. 黑板引用)
# ═══════════════════════════════════════════════════════════════════

class TestBlackboardTemplate:
    """$bb.<step>.<key> 模板引用 — 类型化数据传递"""

    def _ctx(self):
        return {"input": "task", "prev_output": "", "param": {}, "step": {}}

    def test_bb_full_ref_returns_original_value(self):
        """整串 $bb.A.k 应返回原值 (保留 dict 类型)"""
        bb = SharedBlackboard()
        bb.write("A", "k", {"score": 0.9})
        v = _resolve_template("$bb.A.k", self._ctx(), bb)
        assert v == {"score": 0.9}
        assert isinstance(v, dict)

    def test_bb_full_ref_returns_number_type(self):
        """整串 $bb.A.k 数字应保留 int 类型"""
        bb = SharedBlackboard()
        bb.write("A", "k", 42)
        v = _resolve_template("$bb.A.k", self._ctx(), bb)
        assert v == 42
        assert isinstance(v, int)

    def test_bb_missing_returns_none_for_full_ref(self):
        """整串 $bb.X.missing 缺失应返回 None (非原 token)"""
        bb = SharedBlackboard()
        v = _resolve_template("$bb.X.missing", self._ctx(), bb)
        assert v is None

    def test_bb_embedded_ref_is_str(self):
        """嵌入引用 (非整串) 应 str 化替换"""
        bb = SharedBlackboard()
        bb.write("A", "k", "hello")
        v = _resolve_template("前缀-$bb.A.k-后缀", self._ctx(), bb)
        assert v == "前缀-hello-后缀"

    def test_bb_ref_when_blackboard_none_falls_back_ctx(self):
        """blackboard=None 时 $bb. 引用走 ctx 字典查找 (兼容层)"""
        ctx = {"bb": {"A": {"k": "from_ctx"}}}
        v = _resolve_template("$bb.A.k", ctx, None)
        assert v == "from_ctx"

    def test_step_template_compat_layer(self):
        """$step.A.output 兼容层 (ctx) 不受黑板影响"""
        ctx = self._ctx()
        ctx["step"]["A"] = {"output": {"v": 1}}
        v = _resolve_template("$step.A.output", ctx, SharedBlackboard())
        assert v == {"v": 1}

    def test_input_template_with_blackboard(self):
        """$input 引用在 blackboard 存在时仍从 ctx 读"""
        bb = SharedBlackboard()
        v = _resolve_template("$input", self._ctx(), bb)
        assert v == "task"
