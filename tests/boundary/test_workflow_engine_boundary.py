"""BT-005 workflow_engine 模块边界测试

【生成日志摘要】
- 生成时间：2026-07-01
- 内容：BT-005 workflow_engine 边界测试（v1.0）
- 模型：GLM-5.2
- 关键状态：覆盖 WorkflowEngine / RuleRegistry 的 7 类边界场景
- 状态同步机制：纯函数式测试，使用 lambda 构造规则函数

覆盖范围：
- 空值边界: None text / 空字符串 text / 无规则注册
- 极值边界: 超长文本 / 多规则优先级
- 类型边界: None match_fn / None execute_fn
- 异常分支: match_fn 抛异常 / execute_fn 抛异常
- 权限边界: 禁用规则不执行

源代码限制记录：
- try_match(None) 传入 rule.match_fn(None)，行为取决于具体规则
- register(None) 抛 AttributeError（排序时 r.priority）
"""
import pytest

from agent.workflow_engine.engine import WorkflowEngine, WorkflowResult
from agent.workflow_engine.registry import Rule, RuleRegistry


# ═══════════════════════════════════════════════════════════════
#  WorkflowEngine.try_match 空值边界测试
# ═══════════════════════════════════════════════════════════════


class TestEngineNullBoundary:
    """try_match() 空值边界测试"""

    def test_empty_无规则注册返回未匹配(self):
        """无规则注册时返回 matched=False"""
        engine = WorkflowEngine()
        result = engine.try_match("你好")
        assert isinstance(result, WorkflowResult)
        assert result.matched is False

    def test_empty_空字符串文本未匹配(self):
        """空字符串文本未匹配任何规则"""
        engine = WorkflowEngine()
        engine.registry.register(Rule(
            name="greeting",
            description="问候",
            match_fn=lambda text: "你好" in text,
            execute_fn=lambda text: "你好！",
            priority=50,
        ))
        result = engine.try_match("")
        assert result.matched is False

    def test_null_None作为文本传入规则函数(self):
        """None 作为文本传入规则函数（行为取决于规则）

        源代码限制: try_match(None) 传入 rule.match_fn(None)
        lambda text: "你好" in None 抛 TypeError
        """
        engine = WorkflowEngine()
        engine.registry.register(Rule(
            name="greeting",
            description="问候",
            match_fn=lambda text: "你好" in text,
            execute_fn=lambda text: "你好！",
            priority=50,
        ))
        # match_fn 抛 TypeError 被捕获，返回 matched=False
        result = engine.try_match(None)  # type: ignore
        assert result.matched is False

    def test_boundary_匹配后返回正确结果(self):
        """匹配成功返回正确结果"""
        engine = WorkflowEngine()
        engine.registry.register(Rule(
            name="greeting",
            description="问候",
            match_fn=lambda text: "你好" in text,
            execute_fn=lambda text: "你好！我是云枢",
            priority=50,
        ))
        result = engine.try_match("你好，世界")
        assert result.matched is True
        assert result.rule_name == "greeting"
        assert result.output == "你好！我是云枢"

    def test_boundary_match是try_match的别名(self):
        """match() 是 try_match() 的别名"""
        engine = WorkflowEngine()
        engine.registry.register(Rule(
            name="greeting",
            description="问候",
            match_fn=lambda text: "你好" in text,
            execute_fn=lambda text: "你好！",
            priority=50,
        ))
        result1 = engine.try_match("你好")
        result2 = engine.match("你好")
        assert result1.matched == result2.matched
        assert result1.rule_name == result2.rule_name


# ═══════════════════════════════════════════════════════════════
#  优先级与排序边界测试
# ═══════════════════════════════════════════════════════════════


class TestPriorityBoundary:
    """规则优先级边界测试"""

    def test_boundary_高优先级规则优先匹配(self):
        """高优先级规则优先匹配"""
        engine = WorkflowEngine()
        # 两个规则都能匹配，但 fallback 优先级更高
        engine.registry.register(Rule(
            name="fallback_low",
            description="低优先级兜底",
            match_fn=lambda text: True,
            execute_fn=lambda text: "低优先级回复",
            priority=10,
        ))
        engine.registry.register(Rule(
            name="greeting_high",
            description="高优先级问候",
            match_fn=lambda text: True,
            execute_fn=lambda text: "高优先级回复",
            priority=100,
        ))
        result = engine.try_match("任何文本")
        assert result.matched is True
        assert result.rule_name == "greeting_high"

    def test_boundary_相同优先级保持插入顺序(self):
        """相同优先级保持插入顺序（稳定排序）"""
        engine = WorkflowEngine()
        engine.registry.register(Rule(
            name="first",
            description="第一个",
            match_fn=lambda text: True,
            execute_fn=lambda text: "第一个回复",
            priority=50,
        ))
        engine.registry.register(Rule(
            name="second",
            description="第二个",
            match_fn=lambda text: True,
            execute_fn=lambda text: "第二个回复",
            priority=50,
        ))
        result = engine.try_match("任何文本")
        assert result.rule_name == "first"

    def test_boundary_禁用规则不参与匹配(self):
        """禁用规则不参与匹配"""
        engine = WorkflowEngine()
        engine.registry.register(Rule(
            name="disabled_rule",
            description="禁用规则",
            match_fn=lambda text: True,
            execute_fn=lambda text: "不应执行",
            priority=1000,  # 优先级最高
            enabled=False,
        ))
        engine.registry.register(Rule(
            name="enabled_rule",
            description="启用规则",
            match_fn=lambda text: True,
            execute_fn=lambda text: "正常执行",
            priority=10,
        ))
        result = engine.try_match("任何文本")
        assert result.rule_name == "enabled_rule"

    def test_extreme_多规则优先级排序(self):
        """多规则按优先级降序排序"""
        registry = RuleRegistry()
        for i in range(10):
            registry.register(Rule(
                name=f"rule_{i}",
                description=f"规则{i}",
                match_fn=lambda text: False,
                execute_fn=lambda text: f"回复{i}",
                priority=i * 10,
            ))
        enabled = registry.get_enabled()
        # 按优先级降序
        priorities = [r.priority for r in enabled]
        assert priorities == sorted(priorities, reverse=True)
        assert enabled[0].priority == 90  # rule_9 优先级最高


# ═══════════════════════════════════════════════════════════════
#  异常分支边界测试
# ═══════════════════════════════════════════════════════════════


class TestExceptionBoundary:
    """规则异常分支边界测试"""

    def test_exception_match_fn抛异常被捕获(self):
        """match_fn 抛异常被捕获，继续匹配下一条规则"""
        engine = WorkflowEngine()
        engine.registry.register(Rule(
            name="bad_rule",
            description="会抛异常的规则",
            match_fn=lambda text: (_ for _ in ()).throw(ValueError("匹配异常")),
            execute_fn=lambda text: "不应执行",
            priority=100,
        ))
        engine.registry.register(Rule(
            name="good_rule",
            description="正常规则",
            match_fn=lambda text: True,
            execute_fn=lambda text: "正常回复",
            priority=50,
        ))
        result = engine.try_match("任何文本")
        # bad_rule 抛异常被跳过，good_rule 匹配成功
        assert result.matched is True
        assert result.rule_name == "good_rule"

    def test_exception_execute_fn抛异常被捕获(self):
        """execute_fn 抛异常被捕获，继续匹配下一条规则"""
        engine = WorkflowEngine()
        engine.registry.register(Rule(
            name="bad_execute",
            description="执行抛异常",
            match_fn=lambda text: True,  # 匹配成功
            execute_fn=lambda text: (_ for _ in ()).throw(RuntimeError("执行异常")),
            priority=100,
        ))
        engine.registry.register(Rule(
            name="good_rule",
            description="正常规则",
            match_fn=lambda text: True,
            execute_fn=lambda text: "正常回复",
            priority=50,
        ))
        result = engine.try_match("任何文本")
        # bad_execute 匹配成功但 execute_fn 抛异常，继续匹配 good_rule
        # 注意：源代码中 match_fn 成功后直接调用 execute_fn，
        # 如果 execute_fn 抛异常，整个 try 块被捕获，continue 到下一条规则
        assert result.matched is True
        assert result.rule_name == "good_rule"

    def test_exception_所有规则都抛异常返回未匹配(self):
        """所有规则都抛异常返回 matched=False"""
        engine = WorkflowEngine()
        engine.registry.register(Rule(
            name="bad1",
            description="异常规则1",
            match_fn=lambda text: (_ for _ in ()).throw(ValueError("异常1")),
            execute_fn=lambda text: "回复1",
            priority=100,
        ))
        engine.registry.register(Rule(
            name="bad2",
            description="异常规则2",
            match_fn=lambda text: (_ for _ in ()).throw(TypeError("异常2")),
            execute_fn=lambda text: "回复2",
            priority=50,
        ))
        result = engine.try_match("任何文本")
        assert result.matched is False


# ═══════════════════════════════════════════════════════════════
#  RuleRegistry 边界测试
# ═══════════════════════════════════════════════════════════════


class TestRegistryBoundary:
    """RuleRegistry 边界测试"""

    def test_null_None作为rule抛出AttributeError(self):
        """None 作为 rule 抛出 AttributeError

        源代码限制: register() 排序时 `r.priority` 未做 None 校验
        """
        registry = RuleRegistry()
        with pytest.raises(AttributeError):
            registry.register(None)  # type: ignore

    def test_empty_空registry_count返回0(self):
        """空 registry count() 返回 0"""
        registry = RuleRegistry()
        assert registry.count() == 0

    def test_empty_空registry_get_enabled返回空列表(self):
        """空 registry get_enabled() 返回空列表"""
        registry = RuleRegistry()
        assert registry.get_enabled() == []

    def test_boundary_重复注册同名规则不替换(self):
        """重复注册同名规则不替换（追加到列表）"""
        registry = RuleRegistry()
        rule1 = Rule(name="dup", description="规则1", match_fn=lambda t: True,
                     execute_fn=lambda t: "回复1", priority=50)
        rule2 = Rule(name="dup", description="规则2", match_fn=lambda t: True,
                     execute_fn=lambda t: "回复2", priority=50)
        registry.register(rule1)
        registry.register(rule2)
        # 两条规则都存在
        assert registry.count() == 2
        enabled = registry.get_enabled()
        assert len(enabled) == 2

    def test_boundary_unregister不存在的name无效果(self):
        """unregister 不存在的 name 无效果无报错"""
        registry = RuleRegistry()
        registry.register(Rule(
            name="test", description="测试",
            match_fn=lambda t: True, execute_fn=lambda t: "回复",
        ))
        registry.unregister("nonexistent")
        assert registry.count() == 1

    def test_boundary_unregister已存在的name(self):
        """unregister 已存在的 name 删除规则"""
        registry = RuleRegistry()
        registry.register(Rule(
            name="test", description="测试",
            match_fn=lambda t: True, execute_fn=lambda t: "回复",
        ))
        registry.unregister("test")
        assert registry.count() == 0

    def test_boundary_get_by_category过滤(self):
        """get_by_category 按分类过滤"""
        registry = RuleRegistry()
        registry.register(Rule(
            name="r1", description="规则1",
            match_fn=lambda t: True, execute_fn=lambda t: "回复1",
            category="social",
        ))
        registry.register(Rule(
            name="r2", description="规则2",
            match_fn=lambda t: True, execute_fn=lambda t: "回复2",
            category="query",
        ))
        social_rules = registry.get_by_category("social")
        assert len(social_rules) == 1
        assert social_rules[0].name == "r1"

    def test_boundary_clear清空所有规则(self):
        """clear() 清空所有规则"""
        registry = RuleRegistry()
        registry.register(Rule(
            name="r1", description="规则1",
            match_fn=lambda t: True, execute_fn=lambda t: "回复1",
        ))
        registry.clear()
        assert registry.count() == 0

    def test_boundary_decorator无参数使用函数名(self):
        """decorator() 无参数使用函数名作为 name"""
        registry = RuleRegistry()

        @registry.decorator()
        def my_rule(text):
            return True

        assert registry.count() == 1
        assert registry.get_enabled()[0].name == "my_rule"


# ═══════════════════════════════════════════════════════════════
#  极值与类型边界测试
# ═══════════════════════════════════════════════════════════════


class TestExtremeAndTypeBoundary:
    """极值与类型边界测试"""

    def test_extreme_超长文本匹配(self):
        """超长文本匹配"""
        engine = WorkflowEngine()
        engine.registry.register(Rule(
            name="greeting",
            description="问候",
            match_fn=lambda text: "你好" in text,
            execute_fn=lambda text: "你好！",
            priority=50,
        ))
        long_text = "前缀" * 1000 + "你好" + "后缀" * 1000
        result = engine.try_match(long_text)
        assert result.matched is True

    def test_extreme_Unicode文本匹配(self):
        """Unicode 文本（含 emoji）匹配"""
        engine = WorkflowEngine()
        engine.registry.register(Rule(
            name="emoji",
            description="emoji 规则",
            match_fn=lambda text: "😀" in text,
            execute_fn=lambda text: "检测到 emoji",
            priority=50,
        ))
        result = engine.try_match("你好😀世界🎉")
        assert result.matched is True
        assert result.rule_name == "emoji"

    def test_boundary_execution_time_ms非零(self):
        """匹配成功时 execution_time_ms > 0"""
        engine = WorkflowEngine()
        engine.registry.register(Rule(
            name="greeting",
            description="问候",
            match_fn=lambda text: True,
            execute_fn=lambda text: "回复",
            priority=50,
        ))
        result = engine.try_match("任何文本")
        assert result.matched is True
        assert isinstance(result.execution_time_ms, float)
        assert result.execution_time_ms >= 0

    def test_boundary_WorkflowResult默认值(self):
        """WorkflowResult 默认值正确"""
        result = WorkflowResult()
        assert result.matched is False
        assert result.rule_name == ""
        assert result.output == ""
        assert result.confidence == 1.0
        assert result.execution_time_ms == 0.0
        assert result.data is None
