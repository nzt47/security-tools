import pytest
from agent.text_tools import humanize_zh


class TestHumanizeZh:
    """中文文本优化工具测试"""

    def test_empty_text(self):
        """测试空文本"""
        result = humanize_zh("")
        assert result["score"] == 100
        assert result["total_issues"] == 0

    def test_empty_text_with_space(self):
        """测试仅含空格的文本"""
        result = humanize_zh("   \n  \t  ")
        assert result["score"] == 100
        assert result["total_issues"] == 0

    def test_pure_ai_text(self):
        """测试典型 AI 写作风格文本"""
        ai_text = """
        作为人工智能领域的先驱，ChatGPT 标志着自然语言处理技术发展的重要里程碑。
        它见证了深度学习算法在理解和生成人类语言方面的巨大进步，
        为未来的人机交互奠定了坚实的基础，是人工智能发展史上不可或缺的一部分。
        """
        result = humanize_zh(ai_text)
        assert result["total_issues"] > 0
        assert result["score"] < 100

    def test_human_text(self):
        """测试人类写作风格文本"""
        human_text = """
        今天天气不错，我出去散步了。路上遇到了老朋友，聊了一会儿。
        他说最近工作很忙，周末都没时间休息。我觉得还是要注意身体，别太累了。
        """
        result = humanize_zh(human_text)
        assert result["score"] > 80

    def test_aggressive_mode(self):
        """测试严格检测模式"""
        text = "这是一个非常好的问题！"
        result_normal = humanize_zh(text, aggressive=False)
        result_aggressive = humanize_zh(text, aggressive=True)
        
        assert "谄媚" in str(result_aggressive["detected_patterns"]) or result_aggressive["total_issues"] >= result_normal["total_issues"]

    def test_pattern_detection(self):
        """测试各种模式检测"""
        test_cases = [
            ("这标志着一个重要的转折点", 1),  # 模式1: 过度强调意义
            ("行业报告显示增长趋势", 5),       # 模式5: 模糊归因
            ("面临若干挑战", 6),                 # 模式6: 挑战与展望
            ("至关重要的是", 7),                 # 模式7: AI词汇
            ("这不仅是技术问题，而且是战略问题", 9),  # 模式9: 否定式排比
            ("希望这对您有帮助", 19),           # 模式19: 协作交流痕迹
            ("好问题！", 21),                    # 模式21: 谄媚语气
            ("值得注意的是", 22),               # 模式22: 填充短语
        ]
        
        for text, pattern_id in test_cases:
            result = humanize_zh(text)
            detected_ids = [p["pattern_id"] for p in result["detected_patterns"]]
            assert pattern_id in detected_ids, f"文本 '{text}' 未能检测到模式 {pattern_id}"

    def test_suggestions_generation(self):
        """测试建议生成"""
        text = "作为行业的领导者，它标志着创新的方向。"
        result = humanize_zh(text)
        
        assert len(result["suggestions"]) > 0
        assert isinstance(result["suggestions"], list)
        assert all(isinstance(s, str) for s in result["suggestions"])

    def test_score_calculation(self):
        """测试评分计算"""
        text1 = "这是一段普通的文本。"
        text2 = "这是一段极其重要的、至关重要的、核心的文本！它标志着一个关键转折点！"
        
        result1 = humanize_zh(text1)
        result2 = humanize_zh(text2)
        
        assert result1["score"] > result2["score"]
        assert 0 <= result1["score"] <= 100
        assert 0 <= result2["score"] <= 100

    def test_emoji_detection(self):
        """测试表情符号检测"""
        text = "Hello! 😊🎉🎈"
        result = humanize_zh(text)
        
        detected_ids = [p["pattern_id"] for p in result["detected_patterns"]]
        assert 17 in detected_ids  # 模式17: 表情符号

    def test_bend_quotes_detection(self):
        """测试弯引号检测"""
        text = "他说：“这是一个测试。”"
        result = humanize_zh(text)
        
        detected_ids = [p["pattern_id"] for p in result["detected_patterns"]]
        assert 18 in detected_ids  # 模式18: 弯引号

    def test_three_sentence_pattern(self):
        """测试三连句检测（严格模式）"""
        text = """第一句话长度差不多。
第二句话长度也差不多。
第三句话长度还是差不多。"""
        result = humanize_zh(text, aggressive=True)
        
        # 严格模式下应该检测到模式99（连续三句长度相似）
        detected_ids = [p["pattern_id"] for p in result["detected_patterns"]]
        assert 99 in detected_ids