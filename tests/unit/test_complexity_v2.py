"""复杂度判定 wire_v2（复查补充 · P0-1 判定质量提升灰度候选）— 单元测试

覆盖：
  - WireV2Classifier 分级行为（关键词/长度/数字实体/步骤词/量词五类特征）
  - 默认 source=wire：零行为变化（build_classifier 默认 wire 逐字节等价既有实现）
  - build_classifier("wire_v2") 可切换；未知 source 回退 wire
  - 参数非法值回退默认（COMPLEXITY_V2_* 环境变量）
  - meets 语义（≥ min_complexity）
  - 全部权重/阈值可配置，默认不引入任何主链路变化

运行：python -m pytest tests/unit/test_complexity_v2.py -q
"""
import pytest

from agent.task_planner.complexity_classifier import (
    WireHeuristicClassifier,
    WireV2Classifier,
    build_classifier,
    get_complexity_classifier,
    reset_complexity_classifier,
)


def _v2(**overrides):
    """构造 v2 判定器，可用 env 覆盖参数（monkeypatch 注入）"""
    import os
    for k, v in overrides.items():
        os.environ[f"COMPLEXITY_V2_{k.upper()}"] = str(v)
    return WireV2Classifier()


class TestWireV2Classify:
    def test_trivial_short_question(self):
        assert WireV2Classifier().classify("今天天气怎么样") == "TRIVIAL"

    def test_simple_single_action(self):
        # "帮我" + "写" 动作词，短文本 → SIMPLE 起
        lvl = WireV2Classifier().classify("帮我写一个爬虫脚本")
        assert lvl in ("SIMPLE", "NORMAL")

    def test_complex_multi_feature(self):
        # 长文本 + 步骤词 + 量词 + 数字实体 → COMPLEX
        msg = ("首先分析这三个数据文件，然后分别生成统计报表，"
               "最后把所有结果合并成一份完整方案并对比性能，预算 10000 元")
        assert WireV2Classifier().classify(msg) == "COMPLEX"

    def test_step_words_promote(self):
        base = WireV2Classifier()
        simple = base.classify("帮我整理一下这些数据")
        stepped = base.classify("首先帮我整理这些数据，然后生成报告，最后发送")
        assert stepped != "TRIVIAL"

    def test_numeric_entity_contributes(self):
        base = WireV2Classifier()
        # 无数字实体版本 → TRIVIAL；带数字/百分比 → 至少 SIMPLE 且更高档
        plain = base.classify("统计销售额同比增长")
        numeric = base.classify("统计 2025 年销售额同比增长 15%")
        assert plain == "TRIVIAL"
        assert numeric in ("SIMPLE", "NORMAL", "COMPLEX")
        assert numeric != "TRIVIAL"

    def test_detail_shape_compatible(self):
        score, cm, am = WireV2Classifier().detail("帮我分析并设计一个系统架构方案")
        assert isinstance(score, float) and score > 0
        assert isinstance(cm, list) and isinstance(am, list)


class TestWireV2Config:
    def test_invalid_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("COMPLEXITY_V2_COMPLEX_KEYWORD_WEIGHT", "abc")
        c = WireV2Classifier()
        assert c._complex_w == 1.0  # 回退默认

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("COMPLEXITY_V2_COMPLEX_THRESHOLD", "99")
        c = WireV2Classifier()
        assert c._complex_th == 99.0
        # complex 门槛极高 → 不再判 COMPLEX（其余分档仍生效）
        assert c.classify("首先分析这三个文件，然后分别生成报表，最后合并方案") != "COMPLEX"

    def test_meets_semantics(self):
        c = WireV2Classifier()
        assert c.meets("帮我写一个爬虫脚本", "SIMPLE") is True
        assert c.meets("你好", "COMPLEX") is False


class TestSourceSwitching:
    def test_default_source_is_wire(self, monkeypatch):
        # 默认（无 env/config 覆盖）→ wire 实现，与既有启发式逐字节等价
        monkeypatch.delenv("COMPLEXITY_SOURCE", raising=False)
        reset_complexity_classifier()
        try:
            c = get_complexity_classifier()
            assert c.source == "wire"
            # 与 WireHeuristicClassifier 行为一致
            wire = WireHeuristicClassifier()
            for msg in ("今天天气怎么样", "帮我设计一个系统架构方案",
                        "分析并对比三个方案的性能", "你好"):
                assert c.classify(msg) == wire.classify(msg)
        finally:
            reset_complexity_classifier()

    def test_build_wire_v2(self):
        c = build_classifier("wire_v2")
        assert c.source == "wire_v2"
        assert isinstance(c._impl, WireV2Classifier)

    def test_unknown_source_falls_back_wire(self):
        c = build_classifier("no_such_source")
        assert c.source == "wire"
        assert isinstance(c._impl, WireHeuristicClassifier)
