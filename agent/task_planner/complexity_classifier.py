"""任务复杂度判定统一接口（任务7 复杂度判定源统一）

背景（Why）:
    TASK-08 报告 §4.1 课程学习降级方案：复用 enhanced_planner 的任务复杂度分级
    （TRIVIAL→SIMPLE→NORMAL→COMPLEX）作课程阶梯，"零新增基建，仅调度与路由调整"。
    审计发现复杂度判定**双源并存**，课程难度自适应若直接依赖 enhanced_planner 会与
    生产 wire 判定不一致，形成"双源口径"：
      - 生产 wire 层 agent/orchestrator/orchestrator.py `_judge_wire_complexity` 启发式
        （score = 复杂指示词数 + 0.5×动作词数；≥1.5→COMPLEX / ≥1.0→NORMAL /
        ≥0.5→SIMPLE / 其余 TRIVIAL），注释明确"后续可替换为
        model_router.analyze_complexity / enhanced_planner 分级"；
      - agent/task_planner/enhanced_planner.py `EnhancedTaskPlanner._evaluate_complexity`
        关键词分级（COMPLEX→MODERATE→SIMPLE→TRIVIAL 高到低匹配），**无任何生产调用方**
        （仅测试引用）。
    本模块统一为**单一判定入口** `ComplexityClassifier`：内部实现可按配置切换
    （wire 启发式 / enhanced_planner 分级），wire 层与规划分支共用同一判定源，
    消除双源；`judged_complexity` 随路由元数据进入 KPI#4 复杂度维度（任务7 Step 2/3）。

【不易】约束（禁止触碰）:
    - 不修改 enhanced_planner.py 内部实现（本模块只做"接线/封装"）：enhanced_planner
      适配器仅调用 `EnhancedTaskPlanner._evaluate_complexity`（纯函数，不触发 DAG
      创建/确认流程），输出 MODERATE 归一到 NORMAL（与 canonical 分档对齐）。
    - wire 分支默认态（planning.wire_enabled=false）行为不变：默认实现 =
      WireHeuristicClassifier，公式/关键词/阈值与既有 `_judge_wire_complexity`
      **逐字节等价**；选型切换走灰度对比（抽样集一致性报告为准，默认 wire）。
    - `meets` 未知 min_complexity 按最高 COMPLEX（3）保守处理：配置非法时收严而非
      放行，守主链路稳定（与 orchestrator 既有 `_wire_complexity_meets` 语义一致）。
    - 任何分类异常由调用方兜底（facade 内部 try/except 吞异常返回 TRIVIAL 并记
      DEBUG 日志），绝不影响主链路。

开关/参数（优先级: 环境变量 > config.yaml > 硬编码默认值）:
    COMPLEXITY_SOURCE                   判定实现（wire | wire_v2 | enhanced_planner，
                                        默认 wire；wire_v2 为复查补充的增强特征
                                        灰度候选，默认零行为变化）
    COMPLEXITY_V2_* / config.yaml:
        learning.complexity.v2.*        wire_v2 权重与阈值（仅 source=wire_v2 生效）

课程阶梯（F4 降级）语义:
    TRIVIAL(0) < SIMPLE(1) < NORMAL(2) < COMPLEX(3)；MODERATE 为 NORMAL 的兼容别名
    （enhanced_planner 用 MODERATE，canonical 分档统一为 NORMAL）。
"""

import logging
import os
import re
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════
#  canonical 分档（课程阶梯统一口径）
# ════════════════════════════════════════════════════════════

COMPLEXITY_LEVELS: Dict[str, int] = {
    "TRIVIAL": 0,
    "SIMPLE": 1,
    "NORMAL": 2,
    "MODERATE": 2,  # enhanced_planner 兼容别名 → NORMAL
    "COMPLEX": 3,
}

# canonical 分档顺序（由低到高；MODERATE 不输出，统一为 NORMAL）
CANONICAL_LEVELS: Tuple[str, ...] = ("TRIVIAL", "SIMPLE", "NORMAL", "COMPLEX")

# 判定实现可选值
SOURCE_WIRE = "wire"
SOURCE_ENHANCED_PLANNER = "enhanced_planner"
SOURCE_DEFAULT = SOURCE_WIRE


def normalize_level(level: str) -> str:
    """把任意实现输出归一到 canonical 分档（MODERATE → NORMAL；未知 → 原样大写）"""
    lvl = str(level).strip().upper()
    if lvl == "MODERATE":
        return "NORMAL"
    return lvl


def _load_config_yaml() -> Optional[dict]:
    """读取仓库根 config.yaml（失败返回 None，不抛异常；沿用学习模块同款模式）"""
    try:
        from pathlib import Path
        import yaml as _yaml
        _path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
        if _path.exists():
            return _yaml.safe_load(_path.read_text(encoding="utf-8")) or {}
    except Exception:
        pass
    return None


# ════════════════════════════════════════════════════════════
#  实现一：wire 启发式（生产现状逐字节等价；任务7 对比基线）
# ════════════════════════════════════════════════════════════

_WIRE_COMPLEX_KEYWORDS: Tuple[str, ...] = (
    "架构", "系统", "平台", "重构", "迁移", "分布式",
    "设计一个", "帮我构建", "多步骤", "第一步", "第二步", "完整方案",
)
_WIRE_ACTION_KEYWORDS: Tuple[str, ...] = ("检查", "分析", "创建", "生成", "整理", "监控")


class WireHeuristicClassifier:
    """TASK-01 wire 启发式复杂度分级（生产现状的独立模块化形态）

    【简易】分数 = 复杂指示词数 + 0.5×动作词数（与 PlanningCore._needs_planning 同源）；
    分级：≥1.5 → COMPLEX / ≥1.0 → NORMAL / ≥0.5 → SIMPLE / 其余 TRIVIAL。
    公式/关键词/阈值与 orchestrator 既有 `_judge_wire_complexity` 逐字节等价，
    保证 wire 默认态行为不变（选型对比的基线）。
    """

    name = SOURCE_WIRE
    COMPLEX_KEYWORDS = _WIRE_COMPLEX_KEYWORDS
    ACTION_KEYWORDS = _WIRE_ACTION_KEYWORDS

    def detail(self, message: str) -> Tuple[float, List[str], List[str]]:
        """判定明细：返回 (score, complex_matches, action_matches)，供入口日志定位"""
        complex_matches = [k for k in self.COMPLEX_KEYWORDS if k in message]
        action_matches = [k for k in self.ACTION_KEYWORDS if k in message]
        score = len(complex_matches) + len(action_matches) * 0.5
        return score, complex_matches, action_matches

    def classify(self, message: str) -> str:
        """返回 canonical 分档（TRIVIAL/SIMPLE/NORMAL/COMPLEX）"""
        score, _complex, _action = self.detail(message)
        if score >= 1.5:
            return "COMPLEX"
        if score >= 1.0:
            return "NORMAL"
        if score >= 0.5:
            return "SIMPLE"
        return "TRIVIAL"

    def meets(self, message: str, min_complexity: str) -> bool:
        """message 复杂度 ≥ min_complexity？未知级别按 COMPLEX(3) 保守处理"""
        min_level = COMPLEXITY_LEVELS.get(str(min_complexity).strip().upper(), 3)
        return COMPLEXITY_LEVELS.get(self.classify(message), 0) >= min_level


# ════════════════════════════════════════════════════════════
#  实现一·增强：wire_v2 启发式（复查补充；默认不启用，灰度 A/B 候选）
# ════════════════════════════════════════════════════════════

_WIRE_V2_COMPLEX_KEYWORDS: Tuple[str, ...] = _WIRE_COMPLEX_KEYWORDS + (
    "对比", "方案", "集成", "部署", "优化", "排查", "评估", "规划",
    "跨", "并行", "异步", "缓存", "数据库", "接口", "安全",
    "数据", "流程", "脚本", "自动化", "性能", "兼容",
)
_WIRE_V2_ACTION_KEYWORDS: Tuple[str, ...] = _WIRE_ACTION_KEYWORDS + (
    "实现", "设计", "开发", "修复", "测试", "配置", "编写",
)
_WIRE_V2_STEP_WORDS: Tuple[str, ...] = (
    "首先", "然后", "接着", "最后", "分别", "逐个", "同时", "依次",
    "第一步", "第二步", "第三步", "分步",
)
_WIRE_V2_QUANTIFIERS: Tuple[str, ...] = (
    "多个", "所有", "全部", "每份", "多份", "各类", "各种", "每项", "每个",
)
_NUMERIC_ENTITY_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:%|万|亿|元|条|个|次|份|人|天|小时)?")


def _v2_cfg(key: str, default: float) -> float:
    """wire_v2 参数读取：环境变量 COMPLEXITY_V2_<KEY 大写> > config.yaml
    learning.complexity.v2.<key> > 默认；非法值回退默认。"""
    env = os.environ.get("COMPLEXITY_V2_" + key.upper())
    if env is not None and str(env).strip():
        try:
            return float(env.strip())
        except (TypeError, ValueError):
            logger.warning("[ComplexityClassifier] COMPLEXITY_V2_%s 非法值 %r，回退默认 %s",
                           key.upper(), env, default)
    try:
        cfg = _load_config_yaml()
        val = (((cfg or {}).get("learning") or {}).get("complexity") or {}
               .get("v2") or {}).get(key)
        if val is not None:
            return float(val)
    except (TypeError, ValueError):
        pass
    return default


class WireV2Classifier:
    """wire_v2 增强特征复杂度分级（复查补充；任务7 判定质量提升的灰度候选）

    背景：任务7 对比报告显示 wire 与 enhanced_planner 一致率仅 29.50%、对人工
    标注符合率均低（20%/32%），判定质量是课程阶梯的数据瓶颈。wire_v2 在
    wire 启发式（复杂指示词 + 动作词）基础上补充五类低风险特征：
        1. 扩充复杂/动作关键词表（覆盖更多任务域表达）；
        2. 文本长度（长指令倾向多步骤，超阈值加分）；
        3. 数字/金额/百分比/日期实体（数据类任务复杂度信号）；
        4. 步骤连接词（"首先/然后/最后/分别…" = 显式多步骤）；
        5. 量词（"多个/所有/各类…" = 批量/全域处理）。

    【不易】默认 source=wire 时本实现不参与任何判定（零行为变化）；仅
    COMPLEXITY_SOURCE=wire_v2（env 或 config.yaml learning.complexity.source）
    灰度切换。全部权重/阈值可配置（COMPLEXITY_V2_* / config v2 段），
    非法值回退默认；分级公式/阈值与 wire 不同（特征更多，默认阈值更高），
    切换前后行为差异由灰度 A/B（scripts/complexity_v2_compare.py）度量。
    """

    name = "wire_v2"

    COMPLEX_KEYWORDS = _WIRE_V2_COMPLEX_KEYWORDS
    ACTION_KEYWORDS = _WIRE_V2_ACTION_KEYWORDS
    STEP_WORDS = _WIRE_V2_STEP_WORDS
    QUANTIFIERS = _WIRE_V2_QUANTIFIERS

    def __init__(self) -> None:
        self._complex_w = _v2_cfg("complex_keyword_weight", 1.0)
        self._action_w = _v2_cfg("action_keyword_weight", 0.5)
        self._length_threshold = _v2_cfg("length_threshold", 60.0)
        self._length_w = _v2_cfg("length_weight", 0.5)
        self._numeric_w = _v2_cfg("numeric_entity_weight", 0.25)
        self._step_w = _v2_cfg("step_word_weight", 0.5)
        self._quant_w = _v2_cfg("quantifier_weight", 0.5)
        self._complex_th = _v2_cfg("complex_threshold", 2.0)
        self._normal_th = _v2_cfg("normal_threshold", 1.5)
        self._simple_th = _v2_cfg("simple_threshold", 0.5)

    def detail(self, message: str) -> Tuple[float, List[str], List[str]]:
        """判定明细：返回 (score, complex_matches, action_matches)，兼容 wire 形态"""
        text = str(message)
        complex_matches = [k for k in self.COMPLEX_KEYWORDS if k in text]
        action_matches = [k for k in self.ACTION_KEYWORDS if k in text]
        step_matches = [k for k in self.STEP_WORDS if k in text]
        quant_matches = [k for k in self.QUANTIFIERS if k in text]
        score = (len(complex_matches) * self._complex_w
                 + len(action_matches) * self._action_w)
        if len(text) > self._length_threshold:
            score += self._length_w
        score += min(len(_NUMERIC_ENTITY_RE.findall(text)), 3) * self._numeric_w
        score += min(len(step_matches), 3) * self._step_w
        score += min(len(quant_matches), 3) * self._quant_w
        return score, complex_matches, action_matches

    def classify(self, message: str) -> str:
        score, _complex, _action = self.detail(message)
        if score >= self._complex_th:
            return "COMPLEX"
        if score >= self._normal_th:
            return "NORMAL"
        if score >= self._simple_th:
            return "SIMPLE"
        return "TRIVIAL"

    def meets(self, message: str, min_complexity: str) -> bool:
        min_level = COMPLEXITY_LEVELS.get(str(min_complexity).strip().upper(), 3)
        return COMPLEXITY_LEVELS.get(self.classify(message), 0) >= min_level


# ════════════════════════════════════════════════════════════
#  实现二：enhanced_planner 分级（适配封装，不改造内部实现）
# ════════════════════════════════════════════════════════════


class EnhancedPlannerClassifier:
    """enhanced_planner 复杂度分级适配器（任务7 Step 1 对比候选）

    【不易】只做"接线/封装"：内部调用 `EnhancedTaskPlanner._evaluate_complexity`
    （纯函数复杂度评估，不触发 create_plan 的 DAG 创建/确认流程）；
    输出 TaskComplexity（TRIVIAL/SIMPLE/MODERATE/COMPLEX）归一到 canonical
    （MODERATE → NORMAL）。**不修改 enhanced_planner.py 任何实现**。
    """

    name = SOURCE_ENHANCED_PLANNER

    def __init__(self) -> None:
        # 延迟导入：仅在选型到 enhanced_planner 时加载，避免默认路径拉入 DAG 依赖
        from agent.task_planner.enhanced_planner import EnhancedTaskPlanner
        self._planner = EnhancedTaskPlanner()

    def detail(self, message: str) -> Tuple[float, List[str], List[str]]:
        """判定明细：(score=分档序数, complex_matches=[], action_matches=[])

        enhanced_planner 为关键词分级，无分数语义：score 取 canonical 序数，
        命中关键词表按该实现的 COMPLEXITY_KEYWORDS 反查（供日志定位）。
        """
        lvl = self.classify(message)
        # 反查命中关键词（复用实现自身关键词表，不引入新口径）
        matches: List[str] = []
        try:
            from agent.task_planner.enhanced_planner import TaskComplexity
            for cx in (TaskComplexity.COMPLEX, TaskComplexity.MODERATE,
                       TaskComplexity.SIMPLE, TaskComplexity.TRIVIAL):
                for kw in self._planner.COMPLEXITY_KEYWORDS.get(cx, []):
                    if kw in message:
                        matches.append(kw)
        except Exception:
            matches = []
        return float(COMPLEXITY_LEVELS.get(lvl, 0)), matches, []

    def classify(self, message: str) -> str:
        try:
            result = self._planner._evaluate_complexity(str(message))
            return normalize_level(result.value)
        except Exception as e:
            logger.debug("[ComplexityClassifier] enhanced_planner 分级失败，回退 TRIVIAL: %s", e)
            return "TRIVIAL"

    def meets(self, message: str, min_complexity: str) -> bool:
        min_level = COMPLEXITY_LEVELS.get(str(min_complexity).strip().upper(), 3)
        return COMPLEXITY_LEVELS.get(self.classify(message), 0) >= min_level


# ════════════════════════════════════════════════════════════
#  统一入口（facade）
# ════════════════════════════════════════════════════════════

_IMPLEMENTATIONS: Dict[str, Any] = {
    SOURCE_WIRE: WireHeuristicClassifier,
    "wire_v2": WireV2Classifier,
    SOURCE_ENHANCED_PLANNER: EnhancedPlannerClassifier,
}


def build_classifier(source: Optional[str] = None) -> "ComplexityClassifier":
    """按 source 构建统一判定器；非法 source 回退 wire（默认，零行为变化）"""
    src = str(source or SOURCE_DEFAULT).strip().lower()
    return ComplexityClassifier(src)


class ComplexityClassifier:
    """任务复杂度判定统一接口（单一入口）

    用法（生产，统一经 get_complexity_classifier 获取）:
        from agent.task_planner.complexity_classifier import get_complexity_classifier
        judged = get_complexity_classifier().classify(user_input)
        meets  = get_complexity_classifier().meets(user_input, "COMPLEX")

    source: wire（默认，生产现状等价）| enhanced_planner（灰度对比候选）。
    """

    def __init__(self, source: str = SOURCE_DEFAULT):
        src = source.strip().lower() if source else SOURCE_DEFAULT
        impl_cls = _IMPLEMENTATIONS.get(src)
        if impl_cls is None:
            logger.warning("[ComplexityClassifier] 未知判定源 %r，回退 wire: %s",
                           source, "（默认实现零行为变化）")
            impl_cls = WireHeuristicClassifier
            src = SOURCE_DEFAULT
        self.source = src
        self._impl = impl_cls()

    def classify(self, message: str) -> str:
        """任务复杂度分级（canonical: TRIVIAL/SIMPLE/NORMAL/COMPLEX）"""
        try:
            return normalize_level(self._impl.classify(str(message)))
        except Exception as e:  # noqa: BLE001 分类异常绝不阻断主链路
            logger.debug("[ComplexityClassifier] classify 异常，回退 TRIVIAL: %s", e)
            return "TRIVIAL"

    def detail(self, message: str) -> Tuple[float, List[str], List[str]]:
        """判定明细 (score, complex_matches, action_matches)——兼容 orchestrator
        `_wire_complexity_detail` 返回形态（wire 排查日志消费）"""
        try:
            return self._impl.detail(str(message))
        except Exception as e:  # noqa: BLE001
            logger.debug("[ComplexityClassifier] detail 异常，返回空明细: %s", e)
            return 0.0, [], []

    def meets(self, message: str, min_complexity: str) -> bool:
        """message 复杂度 ≥ min_complexity（未知级别保守按 COMPLEX 收严）"""
        try:
            return bool(self._impl.meets(str(message), str(min_complexity)))
        except Exception as e:  # noqa: BLE001
            logger.debug("[ComplexityClassifier] meets 异常，保守放行=False: %s", e)
            return False


# ════════════════════════════════════════════════════════════
#  配置解析与全局单例（环境变量 > config.yaml > 硬编码默认值）
# ════════════════════════════════════════════════════════════


def resolve_source() -> str:
    """解析判定源配置：COMPLEXITY_SOURCE 环境变量 > config.yaml
    learning.complexity.source > 默认 wire"""
    env = os.environ.get("COMPLEXITY_SOURCE")
    if env is not None and str(env).strip():
        return str(env).strip().lower()
    cfg = _load_config_yaml()
    try:
        src = ((cfg or {}).get("learning", {}) or {}).get("complexity", {}).get("source")
        if src is not None and str(src).strip():
            return str(src).strip().lower()
    except Exception:
        pass
    return SOURCE_DEFAULT


_classifier_lock = threading.Lock()
_global_classifier: Optional[ComplexityClassifier] = None


def get_complexity_classifier() -> ComplexityClassifier:
    """获取全局复杂度判定器（线程安全单例；source 变化时经 reset 重建）"""
    global _global_classifier
    if _global_classifier is None:
        with _classifier_lock:
            if _global_classifier is None:
                _global_classifier = build_classifier(resolve_source())
    return _global_classifier


def reset_complexity_classifier() -> None:
    """重置全局判定器（测试 / 配置切换用）"""
    global _global_classifier
    with _classifier_lock:
        _global_classifier = None


__all__ = [
    "COMPLEXITY_LEVELS",
    "CANONICAL_LEVELS",
    "SOURCE_WIRE",
    "SOURCE_ENHANCED_PLANNER",
    "normalize_level",
    "WireHeuristicClassifier",
    "WireV2Classifier",
    "EnhancedPlannerClassifier",
    "ComplexityClassifier",
    "build_classifier",
    "resolve_source",
    "get_complexity_classifier",
    "reset_complexity_classifier",
]
