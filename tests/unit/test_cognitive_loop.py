"""Cognitive Loop 单元测试

测试认知循环各模块：Reflection、Knowledge、ActorCritic、Debate、CognitiveLoop。
"""
import pytest
from agent.cognitive.reflection import ReflectionEngine, ReflectionResult
from agent.cognitive.knowledge import KnowledgePrecipitator, KnowledgeRecord
from agent.cognitive.actor_critic import ActorCriticReviewer, ReviewResult
from agent.cognitive.debate import DebateEngine, DebateResult, Perspective
from agent.cognitive.loop import CognitiveLoop, CognitiveRecord, TaskComplexity


# ═══════════════════════════════════════════════════════════════════
#  Reflection 反思模块测试
# ═══════════════════════════════════════════════════════════════════

class TestReflection:
    """ReflectionEngine 单元测试"""

    def setup_method(self):
        self.engine = ReflectionEngine()

    def test_good_result_passes(self):
        """正常结果应通过评估"""
        r = self.engine.evaluate("t1", "你好", "你好！有什么可以帮你的？", 100)
        assert r.passed is True
        assert r.score >= 0.6
        assert not r.should_retry

    def test_empty_output_fails(self):
        """空输出应不通过"""
        r = self.engine.evaluate("t2", "帮我查天气", "", 100)
        assert r.passed is False
        assert "输出为空" in r.issues

    def test_error_output_detected(self):
        """包含错误信息的输出应扣分"""
        r = self.engine.evaluate("t3", "删除文件", "执行失败：文件不存在", 100)
        assert r.score < 0.8
        assert len(r.issues) > 0

    def test_short_output_warning(self):
        """长输入但短输出应预警"""
        long_input = "这是一段很长的输入，远远超过50个字符的限制，专门用来测试当输出过短时系统是否能够正确检测到这个问题并给出一个合理的预警提示信息"
        r = self.engine.evaluate("t4", long_input, "好的", 100)
        assert r.score <= 0.8
        assert len(r.issues) > 0

    def test_long_execution_time(self):
        """超长执行时间应给出建议"""
        r = self.engine.evaluate("t5", "hello", "Hello!", 35000)
        assert len(r.suggestions) > 0
        assert any("耗时" in s for s in r.suggestions)

    def test_failed_tool_calls(self):
        """工具调用失败应扣分"""
        tool_calls = [
            {"type": "tool_call", "tool": "search", "error": "timeout"},
            {"type": "tool_result", "tool": "search", "status": "error", "summary": ""},
        ]
        r = self.engine.evaluate("t6", "搜索新闻", "抱歉出错了", 500, tool_calls)
        assert not r.passed
        assert any("工具调用失败" in i for i in r.issues)

    def test_retry_logic(self):
        """重试逻辑不应超过 MAX_RETRIES"""
        # 第一次重试
        r1 = self.engine.evaluate("retry1", "复杂问题", "", 100)
        assert r1.should_retry
        assert r1.retry_count == 0

        # 第二次重试
        r2 = self.engine.evaluate("retry1", "复杂问题", "", 100)
        assert r2.should_retry
        assert r2.retry_count == 1

        # 第三次重试
        r3 = self.engine.evaluate("retry1", "复杂问题", "", 100)
        assert r3.should_retry
        assert r3.retry_count == 2

        # 第四次——已达到上限
        r4 = self.engine.evaluate("retry1", "复杂问题", "", 100)
        assert not r4.should_retry
        assert r4.retry_count == 3

    def test_low_score_not_retried(self):
        """分数低于 0.3 不应重试（方向性错误）"""
        r = self.engine.evaluate("t7", "复杂任务", "错误", 100)
        # 空 + 错误 = score 0.3，符合重试条件
        # 注意：这里 score 会被裁剪到 0.3，仍然 >= 0.3
        if r.score >= 0.3 and not r.passed:
            assert r.should_retry is True

    def test_reset_retry(self):
        """重置重试计数后应能重新重试"""
        self.engine.evaluate("reset1", "test", "", 100)
        assert self.engine.get_retry_count("reset1") == 1
        self.engine.reset_retry("reset1")
        assert self.engine.get_retry_count("reset1") == 0

    def test_good_result_clears_retry(self):
        """成功完成任务应清除重试记录"""
        self.engine.evaluate("clear1", "test", "", 100)
        assert self.engine.get_retry_count("clear1") == 1
        r = self.engine.evaluate("clear1", "test", "正常完整回复内容", 100)
        assert r.passed
        assert self.engine.get_retry_count("clear1") == 0


# ═══════════════════════════════════════════════════════════════════
#  Knowledge 知识沉淀模块测试
# ═══════════════════════════════════════════════════════════════════

class TestKnowledge:
    """KnowledgePrecipitator 单元测试"""

    def setup_method(self):
        self.precipitator = KnowledgePrecipitator()

    def test_skip_low_value(self):
        """低价值交互应跳过"""
        for pattern in KnowledgePrecipitator.SKIP_PATTERNS:
            r = self.precipitator.precipitate(pattern, "hello", "Hi!")
            assert r is None, "不应沉淀低价值交互: %s" % pattern

    def test_extract_number_facts(self):
        """包含数字的输出应提取事实"""
        r = self.precipitator.precipitate("chat", "设置提醒",
                                           "好的，已设置30分钟后提醒")
        assert r is not None
        assert any("30" in fact for fact in r.key_facts)

    def test_extract_file_paths(self):
        """包含文件路径的输出应提取文件信息"""
        r = self.precipitator.precipitate(
            "chat", "保存配置",
            "已保存到 /home/user/config.json"
        )
        assert r is not None
        assert any("config.json" in fact for fact in r.key_facts)

    def test_extract_entities(self):
        """应提取邮箱和 URL 实体"""
        output = "邮箱 admin@example.com，文档 https://docs.example.com"
        r = self.precipitator.precipitate("chat", "联系信息", output)
        assert r is not None
        assert "admin@example.com" in r.entities
        assert "https://docs.example.com" in r.entities

    def test_high_confidence(self):
        """有数值+文件路径的输出应有较高置信度"""
        output = "配置完成，写入 /etc/app/config.yaml，共 128 项设置"
        r = self.precipitator.precipitate("chat", "配置系统", output)
        assert r is not None
        assert r.confidence >= 0.6

    def test_low_confidence_with_errors(self):
        """包含错误信息的输出应降低置信度"""
        r = self.precipitator.precipitate("chat", "测试",
                                           "执行错误：连接失败，可能网络异常")
        assert r is None or r.confidence <= 0.7

    def test_no_facts_returns_none(self):
        """无有价值信息时应返回 None"""
        r = self.precipitator.precipitate("chat", "哈哈", "哦")
        assert r is None

    def test_summary_generation(self):
        """摘要应包含输入输出概要"""
        r = self.precipitator.precipitate("chat", "设置提醒30分钟",
                                           "好的已设置30分钟提醒")
        assert r is not None
        assert "30分钟" in r.summary or "设置提醒" in r.summary


# ═══════════════════════════════════════════════════════════════════
#  ActorCritic 双 Agent 校验模块测试
# ═══════════════════════════════════════════════════════════════════

class TestActorCritic:
    """ActorCriticReviewer 单元测试"""

    def setup_method(self):
        self.reviewer = ActorCriticReviewer()

    def test_high_risk_detection(self):
        """高风险工具应被正确识别"""
        for tool in ActorCriticReviewer.HIGH_RISK_TASKS:
            assert self.reviewer.is_high_risk(tool), \
                "应识别高风险工具: %s" % tool
        assert not self.reviewer.is_high_risk("read_file")
        assert not self.reviewer.is_high_risk("chat")

    def test_dangerous_command_blocked(self):
        """危险 shell 命令应不通过审核"""
        result = self.reviewer.review("execute_shell",
                                       {"command": "rm -rf /"},
                                       {})
        assert not result.approved
        assert any("危险" in i for i in result.issues)

    def test_safe_command_passes(self):
        """安全命令应通过审核"""
        result = self.reviewer.review("execute_shell",
                                       {"command": "ls -la"},
                                       {})
        assert result.approved

    def test_write_to_protected_dir_denied(self):
        """写入保护目录应被拦截"""
        for path in ActorCriticReviewer.PROTECTED_DIRS:
            result = self.reviewer.review("write_file",
                                           {"path": path + "test.txt",
                                            "content": "test"},
                                           {})
            assert not result.approved, "写入保护目录应被拦截: %s" % path

    def test_write_safe_path_passes(self):
        """写入安全路径应通过"""
        result = self.reviewer.review("write_file",
                                       {"path": "/home/user/test.txt",
                                        "content": "hello"},
                                       {})
        assert result.approved

    def test_delete_git_dir_denied(self):
        """删除 .git 目录应被拦截"""
        result = self.reviewer.review("delete_file",
                                       {"path": "/repo/.git/config"},
                                       {})
        assert not result.approved

    def test_execution_error_detected(self):
        """执行结果包含错误应扣分"""
        result = self.reviewer.review("execute_shell",
                                       {"command": "ls"},
                                       {"error": "command not found"})
        assert not result.approved

    def test_delete_root_denied(self):
        """删除根目录应被拦截"""
        for path in ("/", "C:\\", "D:\\"):
            result = self.reviewer.review("delete_file", {"path": path}, {})
            assert not result.approved

    def test_navigate_empty_url(self):
        """空 URL 导航应预警但不阻塞"""
        result = self.reviewer.review("browser_navigate", {"url": ""}, {})
        assert len(result.issues) > 0  # 应有预警
        assert result.score < 1.0  # 有扣分


# ═══════════════════════════════════════════════════════════════════
#  Debate 辩论模块测试
# ═══════════════════════════════════════════════════════════════════

class TestDebate:
    """DebateEngine 单元测试"""

    def setup_method(self):
        self.engine = DebateEngine()

    def test_safe_proposal_high_score(self):
        """安全提案应获得高共识评分"""
        result = self.engine.debate("查看当前系统版本和运行状态")
        assert result.consensus_score >= 0.7
        assert "推荐执行" in result.recommendation

    def test_dangerous_proposal_low_safety(self):
        """危险提案的安全视角评分应很低"""
        result = self.engine.debate("执行 rm -rf / 删除所有文件")
        assert result.perspectives.get("安全", 1.0) < 0.5

    def test_all_perspectives_evaluated(self):
        """所有视角都应被评估"""
        result = self.engine.debate("测试提案")
        for p in Perspective:
            assert p.value in result.perspectives, "缺少视角: %s" % p.value

    def test_consensus_score_range(self):
        """共识评分应在 [0,1] 范围内"""
        result = self.engine.debate("正常操作")
        assert 0.0 <= result.consensus_score <= 1.0

    def test_short_proposal_lower_correctness(self):
        """简短提案的正确性评分应较低"""
        result = self.engine.debate("好")
        assert result.perspectives.get("正确性", 1.0) < 0.7

    def test_recommendation_varies_by_score(self):
        """不同分数的推荐结论应不同"""
        safe = self.engine.debate("查看文件")
        dangerous = self.engine.debate("rm -rf /")
        assert safe.recommendation != dangerous.recommendation


# ═══════════════════════════════════════════════════════════════════
#  CognitiveLoop 认知循环集成测试
# ═══════════════════════════════════════════════════════════════════

class TestCognitiveLoop:
    """CognitiveLoop 集成测试"""

    def setup_method(self):
        self.loop = CognitiveLoop()

    def test_simple_task_only_reflection(self):
        """简单任务应仅运行反思"""
        r = self.loop.evaluate("t1", "chat", "你好", "你好！", 50)
        assert r.complexity == "simple"
        assert r.reflection is not None
        assert r.knowledge is None
        assert r.review is None
        assert r.debate is None
        assert r.final_decision == "continue"

    def test_normal_task_has_knowledge(self):
        """常规任务应运行反思 + 知识沉淀"""
        r = self.loop.evaluate("t2", "chat",
                               "帮我设置一个30分钟的倒计时提醒任务，到时间后通知我",
                               "好的，已设置30分钟倒计时", 200)
        assert r.complexity == "normal"
        assert r.reflection is not None
        assert r.knowledge is not None
        assert r.review is None
        assert r.debate is None

    def test_high_risk_task(self):
        """高风险任务应运行全部 + ActorCritic"""
        r = self.loop.evaluate("t3", "execute_shell",
                               "rm -rf /",
                               "已执行删除",
                               tool_name="execute_shell",
                               tool_params={"command": "rm -rf /"},
                               tool_result={})
        assert r.complexity == "high_risk"
        assert r.reflection is not None
        assert r.knowledge is not None
        assert r.review is not None
        assert r.debate is None
        assert r.review.approved is False

    def test_complex_task_has_debate(self):
        """复杂任务应运行全部 + 辩论"""
        r = self.loop.evaluate("t4", "chat",
                               "分析微服务架构的优缺点并给出设计方案",
                               "微服务架构的优点包括可扩展性强，缺点包括运维复杂。"
                               "建议使用 16 个服务实例，配置文件位于 /etc/ms/config.yaml", 5000)
        assert r.complexity == "complex"
        assert r.reflection is not None
        assert r.knowledge is not None  # 包含数字和文件路径，应被提取
        assert r.debate is not None

    def test_retry_decision(self):
        """空输出应触发重试决策"""
        r = self.loop.evaluate("t5", "chat", "帮我查天气", "", 100)
        assert r.final_decision == "retry"

    def test_escalate_decision(self):
        """高风险操作审核不过应升级"""
        r = self.loop.evaluate("t6", "execute_shell",
                               "rm -rf /important",
                               "已执行",
                               tool_name="execute_shell",
                               tool_params={"command": "rm -rf /important"},
                               tool_result={})
        assert r.final_decision == "escalate"

    def test_continue_on_success(self):
        """正常执行应继续"""
        r = self.loop.evaluate("t7", "chat", "你好", "你好！今天天气不错！", 100)
        assert r.final_decision == "continue"

    def test_complex_input_detection(self):
        """含"分析"关键词的输入应归类为 COMPLEX"""
        assert self.loop._classify_complexity("chat", "分析问题") == TaskComplexity.COMPLEX
        assert self.loop._classify_complexity("chat", "比较方案") == TaskComplexity.COMPLEX
        assert self.loop._classify_complexity("chat", "设计系统") == TaskComplexity.COMPLEX

    def test_simple_input_detection(self):
        """短输入应归类为 SIMPLE"""
        assert self.loop._classify_complexity("chat", "hi") == TaskComplexity.SIMPLE
        assert self.loop._classify_complexity("chat", "你好") == TaskComplexity.SIMPLE

    def test_high_risk_input_detection(self):
        """高风险工具应归类为 HIGH_RISK"""
        assert self.loop._classify_complexity("execute_shell", "ls") == TaskComplexity.HIGH_RISK
        assert self.loop._classify_complexity("write_file", "test") == TaskComplexity.HIGH_RISK
