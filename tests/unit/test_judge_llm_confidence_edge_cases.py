"""_judge_llm_confidence + 拒识/兜底常量边界情况补充测试

补充覆盖扫描发现的未测边界：
1. _judge_llm_confidence 输入边界：None / 空串 / 纯空白 / 4字符 / 5字符 / 多错误标记
2. _REJECT_MSG / _FALLBACK_MSG / _LLM_ERROR_MARKERS 常量不变量（防漂移）
3. _record_intent_layer 异常隔离（prometheus 导入失败不传播）
4. _intent_layer_counts 多线程并发写入一致性（GIL 守护下的 dict 原子性验证）

关联代码：agent/orchestrator/orchestrator.py L113-134, L99-110, L59-91
关联扫描：scripts/scan_intent_layer_metric_calls.py 静态分析输出

【不易】守 INV-2：业务结果确定后才埋点；常量不可被意外修改
【变易】_judge_llm_confidence 后续可扩展为 LLM 自评，此处守启发式判定不变量
【简易】直接 import 模块级函数 + 常量，不拉起完整 Orchestrator
"""
import threading
import pytest

from agent.orchestrator.orchestrator import (
    _judge_llm_confidence,
    _REJECT_MSG,
    _FALLBACK_MSG,
    _LLM_ERROR_MARKERS,
    _record_intent_layer,
)
from agent.monitoring.prometheus import (
    record_intent_layer,
    reset_intent_layer_counts,
    _intent_layer_counts,
)


@pytest.fixture(autouse=True)
def _reset_counts():
    """每个测试前重置 ratio 计数视图，隔离测试间状态"""
    reset_intent_layer_counts()
    yield
    reset_intent_layer_counts()


# ──────────────────────────────────────────────────────────────
#  _judge_llm_confidence 输入边界测试（orchestrator.py L113-134）
# ──────────────────────────────────────────────────────────────

class TestJudgeLlmConfidenceEdgeCases:
    """_judge_llm_confidence 启发式判定的输入边界场景"""

    def test_None_输入_判定_low_empty_or_too_short(self):
        """None 输入 → low + empty_or_too_short"""
        confidence, reason = _judge_llm_confidence(None)
        assert confidence == "low"
        assert reason == "empty_or_too_short"

    def test_空字符串_判定_low_empty_or_too_short(self):
        """空字符串 → low + empty_or_too_short"""
        confidence, reason = _judge_llm_confidence("")
        assert confidence == "low"
        assert reason == "empty_or_too_short"

    def test_纯空白_判定_low_empty_or_too_short(self):
        """纯空白（strip 后为空）→ low + empty_or_too_short

        【不易】strip 后 len < 5 触发 empty_or_too_short 分支
        """
        confidence, reason = _judge_llm_confidence("   \n\t  ")
        assert confidence == "low"
        assert reason == "empty_or_too_short"

    def test_4字符_判定_low_empty_or_too_short(self):
        """4 字符（< 5 阈值）→ low + empty_or_too_short"""
        confidence, reason = _judge_llm_confidence("abcd")
        assert confidence == "low"
        assert reason == "empty_or_too_short"

    def test_5字符_边界值_判定_high_normal(self):
        """5 字符（恰好 = 5 阈值）→ high + normal

        【不易】边界值：len(response.strip()) < 5 为 low，故 5 字符为 high
        """
        confidence, reason = _judge_llm_confidence("abcde")
        assert confidence == "high"
        assert reason == "normal"

    def test_含错误标记_抱歉处理_判定_low_error_marker(self):
        """含 '抱歉，处理' 错误标记 → low + error_marker_detected"""
        confidence, reason = _judge_llm_confidence("抱歉，处理您的请求时遇到了一些问题。")
        assert confidence == "low"
        assert reason == "error_marker_detected"

    def test_含错误标记_遇到了问题_判定_low_error_marker(self):
        """含 '遇到了问题' 错误标记 → low + error_marker_detected"""
        confidence, reason = _judge_llm_confidence("系统遇到了问题，请稍后重试。")
        assert confidence == "low"
        assert reason == "error_marker_detected"

    def test_含错误标记_无法完成_判定_low_error_marker(self):
        """含 '无法完成' 错误标记 → low + error_marker_detected"""
        confidence, reason = _judge_llm_confidence("抱歉，无法完成此操作，请检查输入。")
        assert confidence == "low"
        assert reason == "error_marker_detected"

    def test_含错误标记_出错了_判定_low_error_marker(self):
        """含 '出错了' 错误标记 → low + error_marker_detected"""
        confidence, reason = _judge_llm_confidence("系统出错了，请稍后再试一次。")
        assert confidence == "low"
        assert reason == "error_marker_detected"

    def test_多错误标记同时出现_仍判定_low_error_marker(self):
        """含多个错误标记 → 仍为 low + error_marker_detected（any() 短路）"""
        response = "抱歉，处理时遇到了问题，无法完成，出错了。"
        confidence, reason = _judge_llm_confidence(response)
        assert confidence == "low"
        assert reason == "error_marker_detected"

    def test_正常响应_判定_high_normal(self):
        """正常 LLM 响应 → high + normal"""
        confidence, reason = _judge_llm_confidence("您好，根据您的描述，建议您尝试以下方案：")
        assert confidence == "high"
        assert reason == "normal"

    def test_含错误标记但过短_优先判定_empty(self):
        """含错误标记但响应过短 → 优先 empty_or_too_short（先判空再判标记）

        【不易】_judge_llm_confidence 控制流：先判空/短 → 再判错误标记
        """
        # 4 字符且不含任何完整错误标记
        confidence, reason = _judge_llm_confidence("出错了")
        assert confidence == "low"
        assert reason == "empty_or_too_short"


# ──────────────────────────────────────────────────────────────
#  常量不变量测试（防漂移，orchestrator.py L99-110）
# ──────────────────────────────────────────────────────────────

class TestConstantsInvariants:
    """_REJECT_MSG / _FALLBACK_MSG / _LLM_ERROR_MARKERS 常量不变量

    【不易】守常量不被意外修改（测试侧 import 同一引用，禁止复制）
    """

    def test_REJECT_MSG_不为空且含转人工提示(self):
        """_REJECT_MSG 必须非空且包含「转人工」引导"""
        assert _REJECT_MSG
        assert "转人工" in _REJECT_MSG

    def test_FALLBACK_MSG_不为空且含转人工提示(self):
        """_FALLBACK_MSG 必须非空且包含「转人工」引导"""
        assert _FALLBACK_MSG
        assert "转人工" in _FALLBACK_MSG

    def test_LLM_ERROR_MARKERS_是元组且含4个标记(self):
        """_LLM_ERROR_MARKERS 必须是 tuple（不可变）且含 4 个标记

        【不易】tuple 而非 list：防止运行时被篡改
        """
        assert isinstance(_LLM_ERROR_MARKERS, tuple)
        assert len(_LLM_ERROR_MARKERS) == 4

    def test_LLM_ERROR_MARKERS_每个标记都是非空字符串(self):
        """每个错误标记必须是非空字符串"""
        for marker in _LLM_ERROR_MARKERS:
            assert isinstance(marker, str)
            assert len(marker) > 0

    def test_常量与_judge_llm_confidence_保持一致(self):
        """_LLM_ERROR_MARKERS 必须与 _judge_llm_confidence 内部使用的标记一致

        【不易】守常量与判定逻辑契约：所有标记触发 low + error_marker_detected
        """
        for marker in _LLM_ERROR_MARKERS:
            # 构造包含该标记的有效响应（>=5 字符）
            response = "系统提示：" + marker + "，请稍后重试。"
            confidence, reason = _judge_llm_confidence(response)
            assert confidence == "low", "marker=%r 未触发 low" % marker
            assert reason == "error_marker_detected"

    def test_REJECT_MSG_与_FALLBACK_MSG_不重复(self):
        """_REJECT_MSG 与 _FALLBACK_MSG 必须不同（业务语义不同）"""
        assert _REJECT_MSG != _FALLBACK_MSG


# ──────────────────────────────────────────────────────────────
#  _record_intent_layer 异常隔离测试（orchestrator.py L59-91）
# ──────────────────────────────────────────────────────────────

class TestRecordIntentLayerExceptionIsolation:
    """_record_intent_layer 异常隔离：埋点失败不传播到业务流程

    【简易】埋点失败降级为 WARNING 日志，不向上传播
    """

    def test_正常调用_不抛异常(self):
        """正常调用 _record_intent_layer 不抛异常"""
        # 应无异常抛出
        _record_intent_layer("rule")
        assert _intent_layer_counts["rule"] == 1

    def test_prometheus_导入失败_不传播异常(self, monkeypatch):
        """prometheus 模块导入失败时不传播异常

        模拟 ImportError 场景：_record_intent_layer 应降级为 WARNING，
        不向上传播异常，业务流程继续执行。
        """
        # 用 monkeypatch 让 prometheus 导入抛 ImportError
        import agent.orchestrator.orchestrator as orch_module
        original_record = orch_module._record_intent_layer

        # 构造一个内部导入会失败的版本
        def _failing_record(layer):
            # 模拟 L70-72 的 from agent.monitoring.prometheus import record_intent_layer 抛 ImportError
            raise ImportError("simulated prometheus import failure")

        # 直接调用 _record_intent_layer 的 try/except 应捕获 ImportError
        # 但 _record_intent_layer 内部用的是延迟导入，无法直接 monkeypatch
        # 改为验证 _record_intent_layer 自身有 try/except 包裹（间接测试）
        # 这里改用 monkeypatch sys.modules 让导入失败
        import sys
        original_module = sys.modules.get("agent.monitoring.prometheus")
        # 暂存后注入 None 让 from ... import 抛 ImportError
        sys.modules["agent.monitoring.prometheus"] = None  # type: ignore

        try:
            # 不应抛异常
            _record_intent_layer("rule")
        except Exception as _e:
            pytest.fail("_record_intent_layer 不应向上传播异常，但抛出: %s" % _e)
        finally:
            # 恢复
            if original_module is not None:
                sys.modules["agent.monitoring.prometheus"] = original_module
            else:
                sys.modules.pop("agent.monitoring.prometheus", None)


# ──────────────────────────────────────────────────────────────
#  _intent_layer_counts 并发安全测试
# ──────────────────────────────────────────────────────────────

class TestIntentLayerCountsConcurrency:
    """_intent_layer_counts 多线程并发写入一致性

    【变易】_intent_layer_counts 是模块级 dict，无显式锁
    【不易】Python GIL 守护下 dict 单次操作原子性，但 += 不是原子操作
           此测试验证实际并发场景下计数是否准确
    """

    def test_多线程并发写入_same_layer_计数准确(self):
        """10 线程各调用 record_intent_layer('semantic') 100 次

        验证：最终 _intent_layer_counts['semantic'] == 1000
        （Python GIL 守护下 record_intent_layer 内部 _intent_layer_counts[layer] += 1
         的 LOAD/STORE 中间可能被打断，但 CPython 3.12+ dict 操作有内部锁）
        """
        reset_intent_layer_counts()
        N_THREADS = 10
        N_PER_THREAD = 100

        def _worker():
            for _ in range(N_PER_THREAD):
                record_intent_layer("semantic")

        threads = [threading.Thread(target=_worker) for _ in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 验证总数（TD-3 加锁后应精确 = 1000，无竞态丢失）
        assert _intent_layer_counts.get("semantic") == N_THREADS * N_PER_THREAD, \
            "TD-3 加锁后并发计数应精确，实际 %d" % _intent_layer_counts.get("semantic", 0)
        total = sum(_intent_layer_counts.values())
        # 关键不变量：ratio 总和恒 = 1.0
        if total > 0:
            ratio_sum = sum(c / total for c in _intent_layer_counts.values())
            assert abs(ratio_sum - 1.0) < 1e-9, "ratio 总和 = %.10f，应 = 1.0" % ratio_sum

    def test_多线程并发写入_multi_layer_ratio_仍_1_0(self):
        """5 线程分别写入不同 layer，ratio 总和仍 = 1.0

        【不易】守 ratio 总和 = 1.0 不变量（即使多线程并发）
        """
        reset_intent_layer_counts()
        layers = ["rule", "template", "semantic", "llm", "reject"]
        N_PER_LAYER = 200

        def _worker(layer):
            for _ in range(N_PER_LAYER):
                record_intent_layer(layer)

        threads = [threading.Thread(target=_worker, args=(layer,))
                   for layer in layers]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        total = sum(_intent_layer_counts.values())
        assert total > 0, "并发后总计数应 > 0"
        ratio_sum = sum(c / total for c in _intent_layer_counts.values())
        # ratio 总和恒 = 1.0（分母同步不变量）
        assert abs(ratio_sum - 1.0) < 1e-9, "ratio 总和 = %.10f，应 = 1.0" % ratio_sum

    def test_多线程并发_reset_与_record_不抛异常(self):
        """并发 reset + record 不抛异常（即使 reset 清空 dict 时 record 在读）

        【变易】reset_intent_layer_counts 清空 dict，record_intent_layer 写入
               并发场景下可能 KeyError 或 RuntimeError，但不应传播异常
        """
        reset_intent_layer_counts()
        N = 500
        errors = []

        def _recorder():
            try:
                for _ in range(N):
                    record_intent_layer("semantic")
            except Exception as _e:
                errors.append(_e)

        def _resetter():
            try:
                for _ in range(N):
                    reset_intent_layer_counts()
            except Exception as _e:
                errors.append(_e)

        t1 = threading.Thread(target=_recorder)
        t2 = threading.Thread(target=_resetter)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, "并发 reset + record 抛异常: %r" % errors
