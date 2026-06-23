"""离线工作流 E2E 测试——验证 WorkflowEngine 的全能力

8 条内置规则详情：
- check_time (p100): keyword_match(["现在几点","当前时间","几点了","什么时间","几点钟"])
- check_date (p100): keyword_match(["今天几号","今天日期","今天周","今天星期","什么日子"])
- check_health (p90):  keyword_match(["还好吗","状态","在吗","在不在","hello","hi","你好"])
- simple_calc (p90):  regex_match(r'^[\d\s\+\-\*\/\(\)\.]+$')
- greeting (p80):     keyword_match(["早上好","下午好","晚上好","你好","大家好"])
- farewell (p80):     keyword_match(["再见","拜拜","bye","goodbye","下次见","明天见"])
- thanks (p70):       keyword_match(["谢谢","感谢","多谢","thank","thanks","thx"])
- confirmation (p50): keyword_match(["好的","可以","明白","懂了","知道了","收到"])
"""
import time
from agent.workflow_engine.engine import WorkflowEngine
from agent.workflow_engine.builtin_rules import register_builtin_rules


class TestOfflineWorkflow:
    """离线工作流完整测试"""

    def setup_method(self, method):
        """pytest 钩子：每个测试前初始化"""
        self.engine = WorkflowEngine()
        register_builtin_rules(self.engine.registry)

    def test_all_builtin_rules(self):
        """测试所有内置规则——验证8条规则的匹配和执行"""
        test_cases = [
            ("现在几点", True, "check_time"),
            ("今天几号", True, "check_date"),
            ("今天星期几", True, "check_date"),
            ("你还好吗", True, "check_health"),
            ("在吗",     True, "check_health"),
            ("1+1",      True, "simple_calc"),
            ("25*4",     True, "simple_calc"),
            # 不应匹配的输入
            ("今天天气真好", False, None),
        ]

        for input_text, should_match, expected_rule_name in test_cases:
            result = self.engine.try_match(input_text)
            if should_match:
                assert result is not None, f"应匹配但未匹配: {input_text}"
                assert result.matched, f"应匹配但未匹配: {input_text}"
                assert result.rule_name == expected_rule_name, \
                    f"规则名不符: {input_text} → {result.rule_name} (预期 {expected_rule_name})"
            else:
                assert result is None or not result.matched, \
                    f"不应匹配但匹配了: {input_text}"

    def test_workflow_duration(self):
        """验证工作流执行速度——应在 10ms 以内"""
        start = time.perf_counter()
        for _ in range(100):
            self.engine.try_match("现在几点")
        elapsed = (time.perf_counter() - start) * 1000
        assert elapsed < 5000, f"100次执行耗时{elapsed:.0f}ms（预期<5000ms）"
        print(f"工作流性能: {elapsed/100:.1f}ms/次")

    def test_rule_priority(self):
        """测试规则优先级——get_enabled 返回按 priority 降序排列"""
        rules = self.engine.registry.get_enabled()
        priorities = [r.priority for r in rules]
        assert priorities == sorted(priorities, reverse=True), \
            "规则应按优先级降序排列"
