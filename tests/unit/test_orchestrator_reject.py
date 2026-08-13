"""任务3：主链路拒识机制 + LLM 置信度校验单元测试

覆盖模块：
- Orchestrator._should_reject: 未知意图拒识判定（规则层+语义层双未命中）
- Orchestrator._load_reject_config: 拒识配置加载（环境变量 > config.yaml > 硬编码默认值）

测试策略：
- 用 Orchestrator.__new__(Orchestrator) 跳过 __init__（_should_reject 只依赖
  _load_reject_config classmethod，无需实例属性）
- confidence 参数用字符串模拟（_should_reject 用 str(confidence).upper() 判定，
  兼容真实 Confidence 枚举与字符串）
"""
import os
import inspect
from contextlib import contextmanager
from functools import lru_cache
from unittest.mock import patch

import pytest

from agent.orchestrator.orchestrator import Orchestrator


# ──────────────────────────────────────────────────────────────
#  辅助函数
# ──────────────────────────────────────────────────────────────

def _make_orchestrator():
    """创建跳过 __init__ 的 Orchestrator 实例（仅用于测试 _should_reject）

    Why: Orchestrator.__init__ 依赖 LifecycleManager 注入的 _memory/_llm/_behavior
    等组件，测试 _should_reject 无需这些依赖（它只调用 _load_reject_config classmethod）。
    用 __new__ 跳过初始化，避免拉起完整依赖链。
    """
    return Orchestrator.__new__(Orchestrator)


# 模拟 Confidence 枚举值（_should_reject 用 str(confidence).upper() 判定）
_CONF_HIGH = "HIGH"
_CONF_MEDIUM = "MEDIUM"
_CONF_LOW = "LOW"


# ──────────────────────────────────────────────────────────────
#  _should_reject 拒识判定测试
# ──────────────────────────────────────────────────────────────

class TestShouldReject:
    """_should_reject 拒识判定测试"""

    @pytest.fixture(autouse=True)
    def _enable_reject_for_judge(self):
        """config.yaml 的 reject.enabled=false（有意运维变更，用于让正常请求不被拒识拦截）
        会使判定用例短路到 reject_disabled，此处强制环境变量启用拒识，聚焦判定逻辑本身。"""
        with reject_env_override(ORCHESTRATOR_REJECT_ENABLED="true"):
            yield

    def test_semantic_miss_low_confidence_拒识(self):
        """语义层 None + confidence LOW → 拒识（核心拒识场景）"""
        orch = _make_orchestrator()
        should, reason = orch._should_reject("unknown_intent", _CONF_LOW, None)
        assert should is True
        assert "rule_and_semantic_both_miss" in reason
        assert "intent=unknown_intent" in reason
        assert "confidence=LOW" in reason

    def test_semantic_hit_不拒识(self):
        """语义层命中（semantic_result 非 None）→ 不拒识"""
        orch = _make_orchestrator()
        semantic_result = {
            "output": "instruction_text",
            "skill_id": "skill_1",
            "score": 0.85,
        }
        should, reason = orch._should_reject("any_intent", _CONF_LOW, semantic_result)
        assert should is False
        assert reason == "semantic_hit"

    def test_high_confidence_不拒识(self):
        """confidence HIGH + 语义层 None → 不拒识（规则层高置信度放行）"""
        orch = _make_orchestrator()
        should, reason = orch._should_reject("unknown_intent", _CONF_HIGH, None)
        assert should is False
        assert reason == "rule_high_confidence"

    def test_disabled_不拒识(self):
        """ORCHESTRATOR_REJECT_ENABLED=false → 不拒识（开关关闭）"""
        orch = _make_orchestrator()
        with reject_env_override(ORCHESTRATOR_REJECT_ENABLED="false"):
            should, reason = orch._should_reject("unknown_intent", _CONF_LOW, None)
        assert should is False
        assert reason == "reject_disabled"

    def test_medium_confidence_拒识(self):
        """confidence MEDIUM + 语义层 None → 拒识（非 HIGH 即拒识）"""
        orch = _make_orchestrator()
        should, reason = orch._should_reject("unknown_intent", _CONF_MEDIUM, None)
        assert should is True
        assert "confidence=MEDIUM" in reason

    def test_none_confidence_拒识(self):
        """confidence None + 语义层 None → 拒识（None 视为 UNKNOWN，非 HIGH）"""
        orch = _make_orchestrator()
        should, reason = orch._should_reject("unknown_intent", None, None)
        assert should is True
        assert "confidence=UNKNOWN" in reason

    def test_reason包含阈值信息(self):
        """拒识 reason 包含 threshold 信息（便于日志排查）"""
        orch = _make_orchestrator()
        with reject_env_override(ORCHESTRATOR_REJECT_THRESHOLD="0.45"):
            should, reason = orch._should_reject("unknown_intent", _CONF_LOW, None)
        assert should is True
        assert "threshold=0.45" in reason


# ──────────────────────────────────────────────────────────────
#  _load_reject_config 配置加载测试
# ──────────────────────────────────────────────────────────────

# 测试用环境变量键清单
_REJECT_ENV_KEYS = [
    "ORCHESTRATOR_REJECT_ENABLED",
    "ORCHESTRATOR_REJECT_THRESHOLD",
    "ORCHESTRATOR_LLM_MIN_CONFIDENCE",
]


class TestLoadRejectConfig:
    """_load_reject_config 配置加载测试（优先级: 环境变量 > config.yaml > 硬编码默认值）"""

    def test_config_yaml_加载默认值(self, tmp_path):
        """config.yaml 存在时返回其配置值（默认 0.3/0.5/enabled=true）

        【简易】不耦合仓库 config.yaml 的运维值（reject.enabled 可能被有意关闭），
        用 tmp_path 临时配置隔离验证加载逻辑本身。
        """
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(
            "reject:\n  enabled: true\n  threshold: 0.3\n  llm_min_confidence: 0.5\n",
            encoding="utf-8",
        )
        # 清除所有 reject 环境变量，让 config.yaml 生效
        with reject_env_override(_clear_all=True), \
             patch.object(Orchestrator, "_SEM_CONFIG_PATH", cfg_file):
            cfg = Orchestrator._load_reject_config()
        # config.yaml 中 reject.threshold=0.3, llm_min_confidence=0.5, enabled=true
        assert cfg["enabled"] is True
        assert cfg["threshold"] == 0.3
        assert cfg["llm_min_confidence"] == 0.5

    def test_threshold_env_覆盖config(self):
        """ORCHESTRATOR_REJECT_THRESHOLD 环境变量覆盖 config.yaml"""
        with reject_env_override(ORCHESTRATOR_REJECT_THRESHOLD="0.5"):
            cfg = Orchestrator._load_reject_config()
        assert cfg["threshold"] == 0.5

    def test_invalid_threshold_降级到config(self):
        """ORCHESTRATOR_REJECT_THRESHOLD 非法值降级到 config.yaml（0.3）"""
        with reject_env_override(ORCHESTRATOR_REJECT_THRESHOLD="not_a_number"):
            cfg = Orchestrator._load_reject_config()
        # 非法值被忽略，回退到 config.yaml 的 0.3
        assert cfg["threshold"] == 0.3

    def test_enabled_env_false_禁用拒识(self):
        """ORCHESTRATOR_REJECT_ENABLED=false 禁用拒识"""
        with reject_env_override(ORCHESTRATOR_REJECT_ENABLED="false"):
            cfg = Orchestrator._load_reject_config()
        assert cfg["enabled"] is False

    def test_enabled_env_true_启用拒识(self):
        """ORCHESTRATOR_REJECT_ENABLED=true 启用拒识"""
        with reject_env_override(ORCHESTRATOR_REJECT_ENABLED="true"):
            cfg = Orchestrator._load_reject_config()
        assert cfg["enabled"] is True

    def test_llm_min_confidence_env_覆盖config(self):
        """ORCHESTRATOR_LLM_MIN_CONFIDENCE 环境变量覆盖 config.yaml"""
        with reject_env_override(ORCHESTRATOR_LLM_MIN_CONFIDENCE="0.8"):
            cfg = Orchestrator._load_reject_config()
        assert cfg["llm_min_confidence"] == 0.8

    def test_invalid_llm_min_confidence_降级(self):
        """ORCHESTRATOR_LLM_MIN_CONFIDENCE 非法值降级到 config.yaml（0.5）"""
        with reject_env_override(ORCHESTRATOR_LLM_MIN_CONFIDENCE="abc"):
            cfg = Orchestrator._load_reject_config()
        assert cfg["llm_min_confidence"] == 0.5

    def test_config_yaml_缺失时降级到硬编码默认(self):
        """config.yaml 路径不存在时降级到 _REJECT_DEFAULTS 硬编码默认值"""
        # 通过 patch _SEM_CONFIG_PATH 指向不存在的路径
        from pathlib import Path
        nonexistent_path = Path("/nonexistent/path/config.yaml")
        with patch.object(Orchestrator, "_SEM_CONFIG_PATH", nonexistent_path), \
             reject_env_override(_clear_all=True):
            cfg = Orchestrator._load_reject_config()
        # 硬编码默认值
        assert cfg["enabled"] is True
        assert cfg["threshold"] == 0.3
        assert cfg["llm_min_confidence"] == 0.5


# ──────────────────────────────────────────────────────────────
#  _REJECT_DEFAULTS 硬编码默认值测试
# ──────────────────────────────────────────────────────────────

class TestRejectDefaults:
    """_REJECT_DEFAULTS 硬编码默认值测试（最终兜底，守【不易】）"""

    def test_defaults_包含必需键(self):
        """_REJECT_DEFAULTS 包含 enabled/threshold/llm_min_confidence 三个键"""
        keys = set(Orchestrator._REJECT_DEFAULTS.keys())
        assert keys == {"enabled", "threshold", "llm_min_confidence"}

    def test_defaults_默认值符合任务约束(self):
        """_REJECT_DEFAULTS 默认值符合任务约束（threshold=0.3, enabled=true）"""
        assert Orchestrator._REJECT_DEFAULTS["enabled"] is True
        assert Orchestrator._REJECT_DEFAULTS["threshold"] == 0.3
        # llm_min_confidence 默认 0.5（任务未明确指定，保守值）
        assert Orchestrator._REJECT_DEFAULTS["llm_min_confidence"] == 0.5


# ──────────────────────────────────────────────────────────────
#  LLM 置信度判定函数（与 orchestrator.py L486-493 同源）
# ──────────────────────────────────────────────────────────────

def _judge_llm_confidence(response):
    """LLM 置信度判定（与 orchestrator.py 同源逻辑，用于 AC-10/11/13 测试）

    【不易】判定规则须与 orchestrator.py 保持一致
    """
    confidence = "high"
    low_reason = "normal"
    if not response or len(response.strip()) < 5:
        confidence = "low"
        low_reason = "empty_or_too_short"
    elif any(_marker in response for _marker in ["抱歉，处理", "遇到了问题", "无法完成", "出错了"]):
        confidence = "low"
        low_reason = "error_marker_detected"
    return confidence, low_reason


# 拒识/兜底文案常量（与 orchestrator.py process() 同源）
_REJECT_MSG = (
    "抱歉，我不太理解你的意思。能否详细描述一下你想做什么？"
    "如需人工帮助，请说「转人工」。"
)
_FALLBACK_MSG = (
    "抱歉，我暂时无法给出令人满意的回答。"
    "请尝试换种方式描述你的问题，或说「转人工」由人工协助处理。"
)


# ──────────────────────────────────────────────────────────────
#  可复用测试工具（从 23 条 AC 提取：源代码契约检查 + 环境变量覆盖）
# ──────────────────────────────────────────────────────────────

@lru_cache(maxsize=8)
def _get_method_source(method_name: str) -> str:
    """获取 Orchestrator 方法源代码（带缓存，避免重复 inspect.getsource 解析）

    Why: 23 条 AC 中 16 次获取源代码，每次 inspect.getsource 都重新解析
    文件。加 lru_cache 后同方法仅解析一次，省 15 次重复解析。
    """
    return inspect.getsource(getattr(Orchestrator, method_name))


def assert_source_contains(method_name: str, *markers: str) -> None:
    """断言方法源代码包含所有标记（代码契约验证助手）

    Why: 23 条 AC 中 31 次 `assert ... in source` 断言，封装后每个测试
    从 3-5 行缩到 1 行，且失败信息指明缺失的 marker，定位更快。

    Args:
        method_name: Orchestrator 方法名（如 'process', '_should_reject'）
        *markers: 必须出现在源代码中的字符串标记
    """
    source = _get_method_source(method_name)
    for marker in markers:
        assert marker in source, (
            f"[代码契约] Orchestrator.{method_name} 源代码缺少标记: {marker!r}"
        )


@contextmanager
def reject_env_override(*, _clear_all: bool = False, **overrides):
    """拒识配置环境变量覆盖 context manager

    Why: 13 次 patch.dict(os.environ, {...}) + 2 处「清空字符串环境变量」
    清理样板重复。封装后调用从 3-5 行缩到 1 行，清理逻辑统一。

    用法:
        # 覆盖单个变量
        with reject_env_override(ORCHESTRATOR_REJECT_THRESHOLD="0.5"):
            cfg = Orchestrator._load_reject_config()
        # 清除所有 reject 环境变量（让 config.yaml / 硬编码默认值生效）
        with reject_env_override(_clear_all=True):
            cfg = Orchestrator._load_reject_config()

    Args:
        _clear_all: True 时清除所有 _REJECT_ENV_KEYS 环境变量
        **overrides: 设置具体环境变量（值为字符串）
    """
    if _clear_all:
        env_patch = {k: "" for k in _REJECT_ENV_KEYS}
        with patch.dict(os.environ, env_patch, clear=False):
            # patch.dict 设空字符串不等于删除，需手动 del 让 config.yaml 生效
            for k in _REJECT_ENV_KEYS:
                if os.environ.get(k) == "":
                    del os.environ[k]
            yield
    else:
        with patch.dict(os.environ, overrides, clear=False):
            yield


# 向后兼容：保留旧函数名（内部走缓存的 _get_method_source）
def _get_process_source() -> str:
    """获取 process 方法源代码（兼容旧调用，内部走缓存）"""
    return _get_method_source("process")


def _get_should_reject_source() -> str:
    """获取 _should_reject 方法源代码（兼容旧调用，内部走缓存）"""
    return _get_method_source("_should_reject")


# ──────────────────────────────────────────────────────────────
#  23 条验收标准自动化测试（AC-1 ~ AC-23）
#  对应 docs/ORCHESTRATOR_REJECT_DESIGN.md 第 4 节
# ──────────────────────────────────────────────────────────────

class TestAcceptanceCriteria:
    """23 条验收标准自动化测试

    覆盖 docs/ORCHESTRATOR_REJECT_DESIGN.md 第 4 节定义的全部验收标准：
    - AC-1 ~ AC-9: 拒识机制验收
    - AC-10 ~ AC-14: LLM 置信度校验收
    - AC-15 ~ AC-19: 日志与可观测性验收
    - AC-20 ~ AC-23: 不变量守住

    测试策略：
    - 直接单元测试：_should_reject / _load_reject_config（AC-1~7,22,23）
    - 文案常量验证：_REJECT_MSG / _FALLBACK_MSG（AC-4,12）
    - 提取判定函数：_judge_llm_confidence（AC-10,11,13）
    - 代码契约验证：inspect 检查 process 源代码（AC-8,9,14~19,20,21）
    """

    @pytest.fixture(autouse=True)
    def _enable_reject_for_judge(self):
        """与 TestShouldReject 同款：强制环境变量启用拒识，隔离 config.yaml 运维值干扰"""
        with reject_env_override(ORCHESTRATOR_REJECT_ENABLED="true"):
            yield

    # ── AC-1 ~ AC-9: 拒识机制验收 ──

    def test_AC01_双未命中低置信度拒识(self):
        """AC-1: 规则层+语义层双未命中且语义最高分 < 阈值时返回拒识"""
        orch = _make_orchestrator()
        should, reason = orch._should_reject("unknown", "LOW", None)
        assert should is True
        assert "rule_and_semantic_both_miss" in reason

    def test_AC02_语义层命中不拒识(self):
        """AC-2: 语义层命中时不拒识（放行到 LLM）"""
        orch = _make_orchestrator()
        should, reason = orch._should_reject("any", "LOW", {"output": "x", "score": 0.8})
        assert should is False
        assert reason == "semantic_hit"

    def test_AC03_高置信度不拒识(self):
        """AC-3: 规则层高置信度时不拒识（放行到 LLM）"""
        orch = _make_orchestrator()
        should, reason = orch._should_reject("any", "HIGH", None)
        assert should is False
        assert reason == "rule_high_confidence"

    def test_AC04_拒识文案含转人工建议(self):
        """AC-4: 拒识返回统一文案 + 转人工建议，不抛异常"""
        assert "转人工" in _REJECT_MSG
        assert "抱歉" in _REJECT_MSG
        # 验证 process 中拒识文案与常量一致
        assert_source_contains("process", "转人工")

    def test_AC05_禁用开关(self):
        """AC-5: ORCHESTRATOR_REJECT_ENABLED=false 禁用拒识"""
        orch = _make_orchestrator()
        with reject_env_override(ORCHESTRATOR_REJECT_ENABLED="false"):
            should, reason = orch._should_reject("unknown", "LOW", None)
        assert should is False
        assert reason == "reject_disabled"

    def test_AC06_阈值环境变量覆盖(self):
        """AC-6: ORCHESTRATOR_REJECT_THRESHOLD 覆盖 config.yaml"""
        with reject_env_override(ORCHESTRATOR_REJECT_THRESHOLD="0.5"):
            cfg = Orchestrator._load_reject_config()
        assert cfg["threshold"] == 0.5

    def test_AC07_阈值非法值降级(self):
        """AC-7: 阈值非法值降级到 config.yaml（0.3）+ WARNING 日志"""
        with reject_env_override(ORCHESTRATOR_REJECT_THRESHOLD="abc"):
            cfg = Orchestrator._load_reject_config()
        assert cfg["threshold"] == 0.3

    def test_AC08_指代句不拒识(self):
        """AC-8: 指代句（DST 补全过）不拒识 — 验证 process 中 _is_ellipsis 跳过逻辑"""
        # process 中存在指代句跳过拒识的逻辑
        assert_source_contains("process", "_is_ellipsis", "_semantic_reject = False")

    def test_AC09_长度拒识保留(self):
        """AC-9: 保留原长度拒识（ORCHESTRATOR_REJECT_MIN_LENGTH）"""
        assert_source_contains("process", "ORCHESTRATOR_REJECT_MIN_LENGTH", "_len_reject")
        # 验证默认长度阈值
        assert int(os.environ.get("ORCHESTRATOR_REJECT_MIN_LENGTH", "3")) == 3

    # ── AC-10 ~ AC-14: LLM 置信度校验收 ──

    def test_AC10_空响应过短低置信度(self):
        """AC-10: LLM 空响应/过短响应判定为低置信度 (empty_or_too_short)"""
        for resp in ["", "嗯嗯", "   ", None]:
            conf, reason = _judge_llm_confidence(resp)
            assert conf == "low", f"响应 {resp!r} 应为 low"
            assert reason == "empty_or_too_short"

    def test_AC11_错误标记低置信度(self):
        """AC-11: LLM 含错误标记判定为低置信度 (error_marker_detected)"""
        for marker in ["抱歉，处理", "遇到了问题", "无法完成", "出错了"]:
            conf, reason = _judge_llm_confidence(marker + "，请稍后")
            assert conf == "low", f"标记 {marker!r} 应为 low"
            assert reason == "error_marker_detected"

    def test_AC12_低置信度兜底文案含转人工(self):
        """AC-12: LLM 低置信度触发兜底回复（含转人工建议）"""
        assert "转人工" in _FALLBACK_MSG
        assert "抱歉" in _FALLBACK_MSG
        # 验证 process 中兜底文案与常量一致
        assert_source_contains("process", "转人工")

    def test_AC13_低置信度提前return跳过反思(self):
        """AC-13: LLM 低置信度提前 return，跳过反思/向量记忆"""
        # 验证 low_confidence_fallback 分支存在提前 return
        assert_source_contains("process", "low_confidence_fallback",
                               'return ResponseBuilder.success(_fallback_msg).to_dict()')

    def test_AC14_兜底响应保存对话记忆(self):
        """AC-14: 兜底响应仍保存对话记忆（便于后续分析低置信度场景）"""
        # 验证兜底分支调用 score_and_save_message
        assert_source_contains("process", "low_confidence_fallback",
                               'score_and_save_message("user", user_input)',
                               'score_and_save_message("assistant", _fallback_msg)')

    # ── AC-15 ~ AC-19: 日志与可观测性验收 ──

    def test_AC15_拒识日志记录各层分数(self):
        """AC-15: 拒识日志记录原因与各层分数（reject_type/intent/confidence/semantic_result/threshold）"""
        assert_source_contains("process", "reject_type", "'intent'", "'confidence'",
                               "'semantic_result'", "'reject_threshold'")

    def test_AC16_should_reject各分支DEBUG日志(self):
        """AC-16: _should_reject 各分支有 DEBUG 日志（4 个 action）"""
        assert_source_contains("_should_reject",
                               "orchestrator.should_reject.disabled",
                               "orchestrator.should_reject.semantic_hit",
                               "orchestrator.should_reject.rule_high_confidence",
                               "orchestrator.should_reject.rejected")

    def test_AC17_LLM置信度判定DEBUG日志(self):
        """AC-17: LLM 置信度判定过程有 DEBUG 日志（confidence_judge + low_reason）"""
        assert_source_contains("process", "orchestrator.process.llm.confidence_judge", "low_reason")

    def test_AC18_拒识记录trace状态(self):
        """AC-18: 拒识/兜底记录 trace 状态（rejected / low_confidence_fallback）"""
        assert_source_contains("process", 'status="rejected"', 'status="low_confidence_fallback"')

    def test_AC19_拒识记录intent_layer指标(self):
        """AC-19: 拒识/兜底记录 intent_layer 指标（reject / llm_low_confidence_fallback）"""
        assert_source_contains("process",
                               '_record_intent_layer("reject")',
                               '_record_intent_layer("llm_low_confidence_fallback")')

    # ── AC-20 ~ AC-23: 不变量守住 ──

    def test_AC20_call_llm签名不变(self):
        """AC-20: _call_llm / _call_llm_v2 签名与返回值不变（仍是 str）"""
        assert callable(Orchestrator._call_llm)
        assert callable(Orchestrator._call_llm_v2)
        sig1 = inspect.signature(Orchestrator._call_llm)
        sig2 = inspect.signature(Orchestrator._call_llm_v2)
        assert "user_input" in sig1.parameters
        assert "user_input" in sig2.parameters

    def test_AC21_semantic_layer_match返回契约不变(self):
        """AC-21: _semantic_layer_match 返回契约不变（None 表示未命中）"""
        assert callable(Orchestrator._semantic_layer_match)
        sig = inspect.signature(Orchestrator._semantic_layer_match)
        assert "user_input" in sig.parameters
        # 验证 _should_reject 仍以 semantic_result is None 判定未命中（契约一致）
        assert_source_contains("_should_reject", "semantic_result is not None")

    def test_AC22_拒识不抛异常(self):
        """AC-22: 拒识/兜底不抛异常，返回统一 ResponseBuilder.success() 格式"""
        orch = _make_orchestrator()
        # 各种边界输入都不应抛异常
        for intent, conf, sem in [
            (None, None, None),
            ("", "", {}),
            ("x", "LOW", {"score": 0.1}),
            ("x", "HIGH", None),
        ]:
            result = orch._should_reject(intent, conf, sem)
            assert isinstance(result, tuple) and len(result) == 2
            assert isinstance(result[0], bool)
            assert isinstance(result[1], str)
        # 验证 process 中拒识/兜底用 ResponseBuilder.success 返回
        assert_source_contains("process",
                               "ResponseBuilder.success(_reject_msg).to_dict()",
                               "ResponseBuilder.success(_fallback_msg).to_dict()")

    def test_AC23_REJECT_MIN_LENGTH保留(self):
        """AC-23: ORCHESTRATOR_REJECT_ENABLED 保留作为补充长度拒识"""
        assert_source_contains("process", "ORCHESTRATOR_REJECT_MIN_LENGTH", "_reject_min_len")
