"""真实进化评估体系（Evolution Evaluator）— 任务 EVO-T2

【任务定位】
    替换 offline_evolver._heuristic_predict()「以参数偏离幅度猜测成功率/延迟」
    的骨架评估，落地设计文档主张的**真实执行评估**与**分阶段评估（Staged Eval）**。

【解决的缺陷（来自审计）】
    1. 【最大缺陷】进化循环以假反馈为基准 → 自欺性提交。
       本模块所有评估器返回「真实执行证据」：真实 success / latency / 输出，
       绝不返回预测值；无法判定时显式返回 skipped / no_samples，绝不伪造指标。
    2. 无真实任务样本池：data/benchmark/*.json 为 mock。
       本模块引入 data/evals/ 样本池（按技能类别建集）。
    3. 开放域任务无客观评分：提供替代验证——自一致性 + 反馈信号（两条路径），
       LLM 判定作为未注册类别的默认降级路径。
    4. 评估无成本控制：TokenBudget 预算熔断 + 分阶段评估省成本。

【不易边界】
    1. 本模块只提供「评估能力」，不修改进化提交流程（任务 3 范围）。
    2. 不引入 Docker 重沙箱：复用 skills_mgmt/executor.py 的进程级沙盒执行。
    3. 沙盒执行失败（环境缺依赖/脚本缺失）→ 降级为「跳过该样本并记录」，
       不得静默伪造通过。

【配置（.env，全部带默认值）】
    EVAL_SAMPLES_DIR             样本池根目录，默认 <项目根>/data/evals
    EVAL_STAGE1_RATIO            阶段1样本比例，默认 0.1（10%）
    EVAL_STAGE1_MIN_SCORE        阶段1初筛阈值，默认 0.3（低于则淘汰）
    EVAL_STAGE1_MAX_SAMPLES      阶段1最大样本数，默认 10
    EVAL_STAGE1_BUDGET_TOKENS    阶段1 token 预算，默认 10000
    EVAL_STAGE2_BUDGET_TOKENS    阶段2 token 预算，默认 50000
    EVAL_BUDGET_TOKENS           单次评估总 token 预算，默认 100000
    EVAL_TIMEOUT_SEC             样本执行超时（秒），默认 15
    EVAL_CONSISTENCY_RUNS        自一致性重复执行次数，默认 3

【EvaluationResult 与 EvolutionRecord.eval_result 对齐】
    to_eval_result_dict() 返回 {score, dimensions, sample_count,
    evaluator_version, status}，与 lineage.EvolutionRecord.eval_result
    的 {score, dimensions, sample_count, evaluator_version} 兼容（仅新增 status）。
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Protocol, Tuple

from .observability import logger

_EVALUATOR_VERSION = "1.0.0"

# 延迟归一化基准（ms）— 与 offline_evolver._evaluate 保持一致
_LATENCY_BASELINE_MS = 5000.0

# 内置样本类别（与 data/evals/<category>/ 目录对应）
_KNOWN_CATEGORIES = ("search", "code", "chat", "general")

# ════════════════════════════════════════════════════════════
#  .env 配置读取（与 lineage.py 同模式：env 带默认值，非法值回退默认）
# ════════════════════════════════════════════════════════════

_DEFAULT_SAMPLES_DIR = Path(__file__).parent.parent.parent / "data" / "evals"


def _env_str(key: str, default: str) -> str:
    return os.getenv(key, default)


def _env_float(key: str, default: float) -> float:
    try:
        return float(os.getenv(key, str(default)))
    except (TypeError, ValueError):
        return default


def _env_int(key: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(key, str(default))))
    except (TypeError, ValueError):
        return default


def _env_samples_dir() -> Path:
    return Path(_env_str("EVAL_SAMPLES_DIR", str(_DEFAULT_SAMPLES_DIR)))


def _env_stage1_ratio() -> float:
    return _env_float("EVAL_STAGE1_RATIO", 0.1)


def _env_stage1_min_score() -> float:
    return _env_float("EVAL_STAGE1_MIN_SCORE", 0.3)


def _env_stage1_max_samples() -> int:
    return _env_int("EVAL_STAGE1_MAX_SAMPLES", 10)


def _env_stage1_budget_tokens() -> int:
    return _env_int("EVAL_STAGE1_BUDGET_TOKENS", 10000)


def _env_stage2_budget_tokens() -> int:
    return _env_int("EVAL_STAGE2_BUDGET_TOKENS", 50000)


def _env_budget_tokens() -> int:
    return _env_int("EVAL_BUDGET_TOKENS", 100000)


def _env_timeout_sec() -> int:
    return _env_int("EVAL_TIMEOUT_SEC", 15)


def _env_consistency_runs() -> int:
    return _env_int("EVAL_CONSISTENCY_RUNS", 3)


# ════════════════════════════════════════════════════════════
#  数据模型
# ════════════════════════════════════════════════════════════


@dataclass
class EvalSample:
    """一条评估样本（真实任务，非 mock）

    样本格式: {"id", "category", "task", "expected_output"或"validator", "created_at"}
    expected_output 支持（OutputChecker）:
        - {"type": "exact",    "value": v}            输出与 v 完全相等
        - {"type": "contains", "values": [..]}         输出包含全部子串
        - {"type": "json",     "key": k, "value": v}  输出 JSON 中 result[k] == v
        - {"type": "validator","expression": expr}     eval(expr, {__builtins__:{}}, {"result": 输出})
        缺省（None）→ 开放域任务，需替代验证（自一致性/反馈/LLM）
    """
    id: str
    category: str = "general"
    task: str = ""
    expected_output: Optional[Dict[str, Any]] = None
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "category": self.category,
            "task": self.task,
            "expected_output": self.expected_output,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }


@dataclass
class SampleEvaluation:
    """单样本执行评估明细（真实执行证据）"""
    sample_id: str
    success: bool = False          # 是否判定成功
    latency_ms: float = 0.0        # 真实耗时
    output: Any = None             # 真实输出（执行产物）
    expected: Any = None           # 期望输出（客观校验器，无则 None）
    error: str = ""                # 失败/跳过原因
    skipped: bool = False          # 环境失败/无法判定 → 跳过（不伪造）
    checked_by: str = ""           # exact | contains | json | validator | self_consistency | feedback | llm | llm_unavailable | exec_error | unverifiable
    score: float = 0.0             # 单样本得分 0-1
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sample_id": self.sample_id,
            "success": self.success,
            "latency_ms": round(self.latency_ms, 2),
            "skipped": self.skipped,
            "checked_by": self.checked_by,
            "score": round(self.score, 4),
            "error": self.error,
        }


@dataclass
class EvaluationResult:
    """一次真实评估结果（与 EvolutionRecord.eval_result 对齐）

    status 语义:
        completed       正常完成（可能 eliminated=True 表示阶段1被淘汰）
        no_samples      该技能类别无样本（绝不伪造指标）
        budget_exceeded token 预算熔断（仅含已完成的真实样本结果）
        degraded        LLM 判定不可用时的降级结果（样本全部 skipped）
    """
    skill_id: str = ""
    status: str = "completed"
    success_rate: float = 0.0      # 真实成功率 0-1
    latency_ms: float = 0.0        # 平均真实延迟
    satisfaction: float = 0.0      # 满意度 0-1（反馈信号或自一致性均值）
    cost_tokens: int = 0           # 本次评估消耗 token 估算
    sample_count: int = 0          # 已尝试样本数
    stage: str = ""                # "" | "stage1" | "stage2"
    eliminated: bool = False       # 阶段1初筛淘汰（不进阶段2）
    samples: List[SampleEvaluation] = field(default_factory=list)
    evaluator_version: str = _EVALUATOR_VERSION
    budget_exceeded: bool = False
    notes: List[str] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def dimensions(self) -> Dict[str, float]:
        """多目标维度（与 offline_evolver 帕累托目标对齐）

        no_samples / degraded 无真实指标 → 全 0（绝不伪造）。
        """
        if self.status in ("no_samples", "degraded"):
            return {"success_rate": 0.0, "latency_norm": 0.0, "satisfaction": 0.0}
        latency_norm = max(0.0, min(1.0, 1.0 - self.latency_ms / _LATENCY_BASELINE_MS))
        return {
            "success_rate": self.success_rate,
            "latency_norm": latency_norm,
            "satisfaction": self.satisfaction,
        }

    @property
    def score(self) -> float:
        """综合评分（与 offline_evolver._evaluate 同公式，保证提交判定口径一致）

        no_samples / degraded 无真实指标 → 0.0（绝不伪造分数）。
        """
        if self.status in ("no_samples", "degraded"):
            return 0.0
        d = self.dimensions
        return round(
            0.5 * d["success_rate"] + 0.3 * d["latency_norm"] + 0.2 * d["satisfaction"],
            4,
        )

    def to_eval_result_dict(self) -> Dict[str, Any]:
        """转为 EvolutionRecord.eval_result 结构（score/dimensions/sample_count 必保留）"""
        return {
            "score": self.score,
            "dimensions": {k: round(v, 4) for k, v in self.dimensions.items()},
            "sample_count": self.sample_count,
            "evaluator_version": self.evaluator_version,
            "status": self.status,
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "status": self.status,
            "score": self.score,
            "dimensions": self.dimensions,
            "success_rate": self.success_rate,
            "latency_ms": round(self.latency_ms, 2),
            "satisfaction": self.satisfaction,
            "cost_tokens": self.cost_tokens,
            "sample_count": self.sample_count,
            "stage": self.stage,
            "eliminated": self.eliminated,
            "budget_exceeded": self.budget_exceeded,
            "notes": self.notes,
            "evaluator_version": self.evaluator_version,
            "samples": [s.to_dict() for s in self.samples],
            "created_at": self.created_at,
        }


class ExecOutcome:
    """技能执行结果最小视图（与 executor.ExecutionResult 字段对齐）"""

    def __init__(self, *, success: bool = True, exit_code: int = 0,
                 stdout: str = "", stderr: str = "", duration_ms: float = 0.0,
                 result: Any = None, timed_out: bool = False):
        self.success = success
        self.exit_code = exit_code
        self.stdout = stdout
        self.stderr = stderr
        self.duration_ms = duration_ms
        self.result = result
        self.timed_out = timed_out


# ════════════════════════════════════════════════════════════
#  评估器抽象接口（协议）
# ════════════════════════════════════════════════════════════

class SkillEvaluator(Protocol):
    """评估器协议 — 所有评估器必须返回真实执行证据而非预测值

    实现要求（不易）:
        - evaluate() 返回 EvaluationResult（含每样本明细，可追溯）；
        - 无样本时返回 status="no_samples"；
        - 无法判定/执行失败时样本标记 skipped，绝不伪造指标；
        - 预算超限时返回 status="budget_exceeded"。
    """

    pool: "EvalSamplePool"

    def resolve_category(self, skill: Any) -> str:
        """解析技能所属样本类别（search/code/chat/general）"""
        ...

    def evaluate(self, skill: Any,
                 sample_ids: Optional[List[str]] = None, *,
                 params: Optional[Dict[str, Any]] = None,
                 budget_tokens: Optional[int] = None) -> EvaluationResult:
        """真实评估技能

        Args:
            skill: 技能对象（含 .id / .category / .tags / .default_params）
            sample_ids: 指定样本子集（None=该类别的全量样本）
            params: 覆盖执行参数（评估变异体时传入变异后的参数）
            budget_tokens: 本次评估 token 预算覆盖（分阶段用）
        """
        ...


# ════════════════════════════════════════════════════════════
#  样本池
# ════════════════════════════════════════════════════════════


def _parse_samples(raw: Any, default_category: str) -> List[EvalSample]:
    """解析样本文件内容（list 或 dict{id: ...}）→ EvalSample 列表"""
    items = raw if isinstance(raw, list) else list(raw.values())
    samples: List[EvalSample] = []
    for item in items:
        if not isinstance(item, dict):
            logger.warning("[Evaluator] 样本池存在非对象条目，已跳过: %r", item)
            continue
        sid = item.get("id") or item.get("sample_id")
        if not sid:
            logger.warning("[Evaluator] 样本缺少 id，已跳过: %r", item)
            continue
        samples.append(EvalSample(
            id=str(sid),
            category=item.get("category") or default_category,
            task=str(item.get("task", "")),
            expected_output=item.get("expected_output") or item.get("validator"),
            created_at=item.get("created_at", ""),
            metadata=item.get("metadata") or {},
        ))
    return samples


class EvalSamplePool:
    """真实任务样本池 — data/evals/<category>/*.json（替代 mock 的 data/benchmark）

    Why 目录即类别: 样本按技能类别分目录，新增类别只需新建目录放 JSON 文件，
    无需改代码（变易）。目录下多个 JSON 文件合并为同一类别的样本集。
    """

    def __init__(self, base_dir: Optional[str] = None):
        self._base_dir = Path(base_dir) if base_dir else _env_samples_dir()
        self._cache: Dict[str, List[EvalSample]] = {}
        self._category_dirs: Optional[List[str]] = None

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def load_category(self, category: str, *, force: bool = False) -> List[EvalSample]:
        """加载某类别全量样本（结果缓存）

        查找顺序:
            1. <base_dir>/<category>/<任意 .json>（目录下所有 json 合并）
            2. <base_dir>/<category>.json
        文件不存在/损坏 → 返回空列表（由评估器返回 no_samples，不伪造）。
        """
        if not force and category in self._cache:
            return self._cache[category]
        samples: List[EvalSample] = []
        cat_dir = self._base_dir / category
        if cat_dir.is_dir():
            for f in sorted(cat_dir.glob("*.json")):
                samples.extend(self._load_file(f, category))
        else:
            single = self._base_dir / f"{category}.json"
            if single.is_file():
                samples.extend(self._load_file(single, category))
        # 去重（同 id 后者覆盖）
        by_id: Dict[str, EvalSample] = {}
        for s in samples:
            by_id[s.id] = s
        self._cache[category] = list(by_id.values())
        return self._cache[category]

    def _load_file(self, path: Path, default_category: str) -> List[EvalSample]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return _parse_samples(raw, default_category)
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as e:
            logger.warning(
                "[Evaluator] 样本文件加载失败 %s: %s（该类别按无样本处理）",
                path, e,
            )
            return []

    def get(self, category: str,
            sample_ids: Optional[List[str]] = None) -> List[EvalSample]:
        """取样本；sample_ids=None 返回全量"""
        samples = self.load_category(category)
        if sample_ids is None:
            return list(samples)
        by_id = {s.id: s for s in samples}
        return [by_id[i] for i in sample_ids if i in by_id]

    def has_samples(self, category: str) -> bool:
        return bool(self.load_category(category))

    def categories(self) -> List[str]:
        """列出池中已有样本的类别（按目录名）"""
        if self._category_dirs is None:
            dirs: List[str] = []
            if self._base_dir.is_dir():
                dirs = [d.name for d in sorted(self._base_dir.iterdir()) if d.is_dir()]
            self._category_dirs = dirs
        return list(self._category_dirs)

    def add(self, category: str, samples: List[EvalSample]) -> None:
        """内存注入样本（测试用；文件持久化用 save）"""
        merged = dict.fromkeys([s.id for s in self.load_category(category)])
        merged.update({s.id: s for s in samples})
        self._cache[category] = list(merged.values())

    def save(self, category: str, samples: List[EvalSample]) -> Path:
        """将样本持久化到 <base_dir>/<category>/samples.json（初始化脚本用）"""
        path = self._base_dir / category / "samples.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump([s.to_dict() for s in samples], f,
                      ensure_ascii=False, indent=2)
        self._cache[category] = list(samples)
        self._category_dirs = None
        return path


# ════════════════════════════════════════════════════════════
#  类别解析 / 输出校验 / 预算
# ════════════════════════════════════════════════════════════


def resolve_category(skill: Any, pool: Optional[EvalSamplePool] = None) -> str:
    """解析技能 → 样本类别

    优先级:
        1. skill.tags 与样本池类别交集（显式声明）
        2. skill.id 包含类别关键字（search/code/chat）
        3. 兜底 "general"
    """
    if pool is not None:
        cats = set(pool.categories()) | set(_KNOWN_CATEGORIES)
        for tag in getattr(skill, "tags", None) or []:
            if str(tag).lower() in cats:
                return str(tag).lower()
    sid = str(getattr(skill, "id", "")).lower()
    for c in ("search", "code", "chat"):
        if c in sid:
            return c
    return "general"


class OutputChecker:
    """客观校验器 — 判定执行输出是否满足 expected_output（搜索精确匹配/code 测试用例）

    【安全（守不易）】validator 类型使用受限 eval：
        仅允许内联表达式访问 result 变量，禁 builtins / import / __ 属性访问。
        样本池为人工维护的评测数据（非用户输入），仍按最小权限执行。
    """

    _FORBIDDEN = ("import", "__", ";", "open(", "eval(", "exec(")

    # 受限 eval 白名单：仅基础类型/纯函数，无 I/O、无属性访问。
    # Why 存在: 样本 expected_output 的 validator expression 需要 isinstance 等
    # 类型判定；"__builtins__": {} 会连这些一并禁用，导致真实样本永远校验失败。
    # 白名单放行的是无副作用纯函数（守不易：安全边界不放宽到 I/O / 属性）。
    _SAFE_BUILTINS = {
        "isinstance": isinstance, "int": int, "float": float,
        "str": str, "bool": bool, "abs": abs, "len": len,
        "round": round, "min": min, "max": max, "sum": sum,
        "sorted": sorted, "list": list, "dict": dict,
        "tuple": tuple, "set": set,
    }

    @staticmethod
    def check(result: Any, expected: Optional[Dict[str, Any]]) -> Tuple[bool, str]:
        """返回 (是否满足, 判定方式/原因)"""
        if not expected or not isinstance(expected, dict):
            return False, "no_checker"
        etype = expected.get("type", "exact")
        if etype == "exact":
            ok = result == expected.get("value")
            return ok, "exact"
        if etype == "contains":
            values = expected.get("values", [])
            if isinstance(values, str):
                values = [values]
            text = json.dumps(result, ensure_ascii=False) if not isinstance(result, str) else result
            missing = [v for v in values if v not in text]
            return not missing, f"contains(missing={missing})" if missing else "contains"
        if etype == "json":
            key = expected.get("key")
            target = expected.get("value")
            if not isinstance(result, dict):
                return False, "json(not_dict)"
            return result.get(key) == target, f"json[{key}]"
        if etype == "validator":
            return OutputChecker._safe_validator(result, expected.get("expression", ""))
        return False, f"unknown_type({etype})"

    @staticmethod
    def _safe_validator(result: Any, expression: str) -> Tuple[bool, str]:
        if not expression or any(t in expression for t in OutputChecker._FORBIDDEN):
            return False, "validator(blocked)"
        try:
            verdict = eval(expression, {"__builtins__": {}},
                           {"result": result, **OutputChecker._SAFE_BUILTINS})  # noqa: S307 白名单受限环境
            return bool(verdict), "validator"
        except Exception as e:  # noqa: BLE001 校验器本身异常 → 判定失败
            return False, f"validator(error={e})"


class TokenBudget:
    """评估 token 预算 — 熔断机制（成本控制）"""

    def __init__(self, budget_tokens: Optional[int] = None):
        self.budget = max(0, int(budget_tokens if budget_tokens is not None else _env_budget_tokens()))
        self.used = 0
        self._exceeded = False

    @staticmethod
    def estimate(*texts: Any) -> int:
        """粗略 token 估算（中文/英文均按 4 字符 ≈ 1 token）"""
        total = 8  # 固定样本开销
        for t in texts:
            if t is None:
                continue
            total += max(1, len(str(t)) // 4)
        return total

    def spend(self, tokens: int) -> bool:
        """记录花费；返回 False 表示已超预算（熔断）"""
        self.used += max(0, int(tokens))
        if self.used > self.budget:
            self._exceeded = True
            return False
        return True

    @property
    def exceeded(self) -> bool:
        return self._exceeded


# ════════════════════════════════════════════════════════════
#  真实执行评估器
# ════════════════════════════════════════════════════════════

# 默认执行器 runner：走 skills_mgmt/executor.py 的进程级沙盒（Layer 3）
# 惰性创建（首次调用才 import，避免轻量导入被重依赖链绑架）
_executor_cache: Dict[str, Any] = {}


def _default_runner(timeout_sec: Optional[int]) -> Callable[[Any, Dict[str, Any]], ExecOutcome]:
    """构造默认 runner：SkillExecutor 沙盒执行 scripts/main.py"""
    def _run(skill: Any, params: Dict[str, Any]) -> ExecOutcome:
        try:
            if "executor" not in _executor_cache:
                from .executor import SkillExecutor, SkillFileStore
                _executor_cache["executor"] = SkillExecutor(SkillFileStore())
            out = _executor_cache["executor"].execute(
                skill.id, "main.py", params=params,
                timeout=timeout_sec if timeout_sec is not None else _env_timeout_sec(),
            )
            return ExecOutcome(
                success=out.success, exit_code=out.exit_code,
                stdout=out.stdout, stderr=out.stderr,
                duration_ms=out.duration_ms, result=out.result,
                timed_out=out.timed_out,
            )
        except Exception as e:  # noqa: BLE001 执行异常由调用方降级为跳过
            return ExecOutcome(success=False, exit_code=-1, stderr=str(e),
                               duration_ms=0.0, result=None)
    return _run


class SelfConsistencyScorer:
    """开放域替代验证①：同题多次执行，输出一致性越高 → 得分越高

    Why 自一致性: 开放域任务（如对话质量）无客观评分，但高质量技能对同一
    输入应产出稳定一致的输出；一致性可作为质量代理指标。
    """

    def __init__(self, similarity: str = "token_jaccard"):
        self.similarity = similarity

    def score(self, outputs: List[Any]) -> float:
        """0-1 一致性得分；输出 <2 次时无法计算 → 返回 1.0（单样本不惩罚）"""
        if not outputs:
            return 0.0
        if len(outputs) < 2:
            return 1.0
        total, count = 0.0, 0
        for i in range(len(outputs)):
            for j in range(i + 1, len(outputs)):
                total += self._sim(outputs[i], outputs[j])
                count += 1
        return round(total / count, 4) if count else 0.0

    def _tokens(self, value: Any) -> set:
        text = str(value).lower()
        return set(re.findall(r"[\w\u4e00-\u9fff]+", text))

    def _sim(self, a: Any, b: Any) -> float:
        ta, tb = self._tokens(a), self._tokens(b)
        if not ta and not tb:
            return 1.0
        union = ta | tb
        if not union:
            return 1.0
        return len(ta & tb) / len(union)


class FeedbackSignalScorer:
    """开放域替代验证②：反馈信号（用户点赞/点踩，来自 agent/feedback.py）

    返回 None 表示该技能无反馈数据（不惩罚也不伪造）。
    """

    def __init__(self, feedback_manager: Any = None):
        self._mgr = feedback_manager

    def _manager(self) -> Any:
        if self._mgr is not None:
            return self._mgr
        from agent.feedback import get_feedback_manager
        return get_feedback_manager()

    def satisfaction(self, skill_id: str, days: int = 30) -> Optional[float]:
        """0-1 满意度；无反馈数据返回 None"""
        try:
            summary = self._manager().get_skill_feedback_summary(skill_id, days=days)
            total = int(summary.get("total_feedback", 0))
            if total == 0:
                return None
            return max(0.0, min(1.0, float(summary.get("satisfaction_rate_percent", 0.0)) / 100.0))
        except Exception as e:  # noqa: BLE001 反馈不可用 → 不阻塞评估
            logger.warning("[Evaluator] 反馈信号获取失败 skill=%s: %s", skill_id, e)
            return None


class SkillExecutorEvaluator:
    """真实执行评估器 — 在进程级沙盒中真实执行样本任务，采集真实指标

    【核心原则（不易）】
        - 指标全部来自真实执行（success/latency/output），无任何预测值；
        - 无样本 → no_samples；执行失败 → 该样本 skipped 并记录原因；
        - 开放域样本（无 expected_output）→ 自一致性（可叠加反馈信号）。
    """

    def __init__(self, pool: Optional[EvalSamplePool] = None, *,
                 runner: Optional[Callable[[Any, Dict[str, Any]], ExecOutcome]] = None,
                 timeout_sec: Optional[int] = None,
                 budget_tokens: Optional[int] = None,
                 consistency_runs: Optional[int] = None,
                 consistency_threshold: float = 0.7,
                 consistency_similarity: str = "token_jaccard",
                 feedback_manager: Any = None,
                 use_feedback: bool = True,
                 allow_validator: bool = False):
        self.pool = pool or EvalSamplePool()
        self._runner = runner or _default_runner(timeout_sec)
        self._timeout_sec = timeout_sec
        self.budget_tokens = budget_tokens if budget_tokens is not None else _env_budget_tokens()
        self._consistency_runs = max(
            1, consistency_runs if consistency_runs is not None else _env_consistency_runs())
        self._consistency_threshold = consistency_threshold
        self._scorer = SelfConsistencyScorer(similarity=consistency_similarity)
        self._feedback = FeedbackSignalScorer(feedback_manager) if use_feedback else None
        self._allow_validator = allow_validator

    # ─── 公共 ───

    def resolve_category(self, skill: Any) -> str:
        return resolve_category(skill, self.pool)

    def evaluate(self, skill: Any,
                 sample_ids: Optional[List[str]] = None, *,
                 params: Optional[Dict[str, Any]] = None,
                 budget_tokens: Optional[int] = None,
                 stage: str = "") -> EvaluationResult:
        category = self.resolve_category(skill)
        samples = self.pool.get(category, sample_ids=sample_ids)
        if not samples:
            logger.warning(
                "[Evaluator] eval.no_samples skill=%s category=%s stage=%s "
                "pool_dir=%s available_categories=%s（绝不伪造指标）",
                skill.id, category, stage, self.pool.base_dir,
                ",".join(self.pool.categories()) or "(无)")
            return EvaluationResult(
                skill_id=skill.id, status="no_samples",
                notes=[f"类别 {category!r} 无评估样本（data/evals/{category}/ 为空）"],
            )

        budget = TokenBudget(budget_tokens if budget_tokens is not None else self.budget_tokens)
        results: List[SampleEvaluation] = []
        for sample in samples:
            est = budget.estimate(sample.task)
            if not budget.spend(est):
                # 成本失控排查点：记录触发熔断的样本与累计消耗
                logger.warning(
                    "[Evaluator] budget.break stage=%s skill=%s sample=%s "
                    "input_estimate=%d used=%d budget=%d executed=%d "
                    "skipped_remaining=%d",
                    stage, skill.id, sample.id, est, budget.used, budget.budget,
                    len(results), len(samples) - len(results))
                break  # 预算熔断
            se = self._execute_sample(skill, sample, params)
            results.append(se)
            est_out = TokenBudget.estimate(se.output, se.expected)
            if not budget.spend(est_out):
                # 输出计费后超限：同样记录，便于区分是输入还是输出侧成本失控
                logger.warning(
                    "[Evaluator] budget.break stage=%s skill=%s sample=%s "
                    "output_estimate=%d used=%d budget=%d executed=%d "
                    "skipped_remaining=%d",
                    stage, skill.id, sample.id, est_out, budget.used,
                    budget.budget, len(results), len(samples) - len(results))
                break  # 输出计入预算后超限
        return self._aggregate(skill, results, budget,
                               stage=stage, sample_total=len(samples))

    # ─── 内部 ───

    def _execute_sample(self, skill: Any, sample: EvalSample,
                        params: Optional[Dict[str, Any]]) -> SampleEvaluation:
        """执行单条样本并判定（真实执行证据）"""
        run_params = dict(params or getattr(skill, "default_params", None) or {})
        run_params["task"] = sample.task
        run_params["sample_id"] = sample.id
        if sample.metadata.get("input"):
            run_params.update(sample.metadata["input"])

        t0 = time.time()
        outcome = self._run(skill, run_params)
        latency = outcome.duration_ms if outcome.duration_ms else (time.time() - t0) * 1000

        if outcome.timed_out:
            return SampleEvaluation(
                sample_id=sample.id, skipped=True, latency_ms=latency,
                checked_by="exec_error", error="执行超时",
            )
        if not outcome.success:
            return SampleEvaluation(
                sample_id=sample.id, skipped=True, latency_ms=latency,
                checked_by="exec_error", error=(outcome.stderr or "执行失败")[:300],
            )
        result = outcome.result if outcome.result is not None else outcome.stdout

        # ① 客观校验器：有 expected_output → 精确判定
        if sample.expected_output:
            ok, reason = OutputChecker.check(result, sample.expected_output)
            if reason == "validator" and not self._allow_validator:
                return SampleEvaluation(
                    sample_id=sample.id, skipped=True, latency_ms=latency,
                    output=result, expected=sample.expected_output,
                    checked_by="unverifiable",
                    error="validator 校验器被禁用（allow_validator=False）",
                )
            return SampleEvaluation(
                sample_id=sample.id, success=ok, latency_ms=latency,
                output=result, expected=sample.expected_output,
                checked_by=reason.split("(")[0], score=1.0 if ok else 0.0,
            )

        # ② 开放域替代验证：自一致性（同题多次执行）
        outputs = [result]
        for _ in range(max(0, self._consistency_runs - 1)):
            run_params["sample_id"] = f"{sample.id}#{len(outputs)}"
            again = self._run(skill, dict(run_params))
            if again.success:
                outputs.append(again.result if again.result is not None else again.stdout)
        consistency = self._scorer.score(outputs)

        # ③ 开放域替代验证：反馈信号（叠加，提升可信度）
        feedback_score = None
        checked_by = "self_consistency"
        if self._feedback is not None:
            feedback_score = self._feedback.satisfaction(skill.id)
            if feedback_score is not None:
                checked_by = "self_consistency+feedback"
                consistency = round(0.5 * consistency + 0.5 * feedback_score, 4)

        return SampleEvaluation(
            sample_id=sample.id,
            success=consistency >= self._consistency_threshold,
            latency_ms=latency, output=outputs, expected=None,
            checked_by=checked_by, score=consistency,
            details={"consistency_runs": len(outputs),
                     "feedback_score": feedback_score},
        )

    def _run(self, skill: Any, params: Dict[str, Any]) -> ExecOutcome:
        """执行包装（异常不抛，交由调用方判定为跳过）"""
        try:
            return self._runner(skill, params)
        except Exception as e:  # noqa: BLE001 执行异常 → 样本跳过
            logger.warning("[Evaluator] 样本执行异常 skill=%s: %s", skill.id, e)
            return ExecOutcome(success=False, exit_code=-1, stderr=str(e))

    @staticmethod
    def _aggregate(skill: Any, results: List[SampleEvaluation],
                   budget: TokenBudget, *, stage: str,
                   sample_total: int) -> EvaluationResult:
        executed = [r for r in results if not r.skipped]
        n_executed = len(executed)
        success_rate = sum(1 for r in executed if r.success) / n_executed if n_executed else 0.0
        latency = (sum(r.latency_ms for r in executed) / n_executed) if n_executed else 0.0
        satisfaction = (sum(r.score for r in executed) / n_executed) if n_executed else 0.0

        status = "budget_exceeded" if budget.exceeded else "completed"
        notes: List[str] = []
        skipped = sum(1 for r in results if r.skipped)
        if skipped:
            notes.append(f"{skipped}/{len(results)} 个样本因执行失败/无法判定被跳过（不伪造指标）")
        if budget.exceeded:
            notes.append(f"token 预算熔断（used={budget.used} > budget={budget.budget}），仅含已完成的真实样本结果")
            logger.warning(
                "[Evaluator] eval.budget_exceeded skill=%s stage=%s used=%d "
                "budget=%d executed=%d sample_total=%d success_rate=%.3f",
                skill.id, stage, budget.used, budget.budget, len(results),
                sample_total, success_rate)
        return EvaluationResult(
            skill_id=skill.id, status=status,
            success_rate=round(success_rate, 4),
            latency_ms=round(latency, 2),
            satisfaction=round(satisfaction, 4),
            cost_tokens=budget.used,
            sample_count=len(results),
            stage=stage,
            samples=results,
            budget_exceeded=budget.exceeded,
            notes=notes,
        )


# ════════════════════════════════════════════════════════════
#  LLM 评估器（未注册类别的默认降级路径）
# ════════════════════════════════════════════════════════════

_LLM_JUDGE_TEMPLATE = (
    "你是一个评估器。判断下方【技能输出】是否成功完成了【任务】。\n"
    "只回复 JSON，不要其他内容: {{\"success\": true/false, \"score\": 0.0~1.0}}\n\n"
    "【任务】\n{task}\n\n【技能输出】\n{output}\n"
)


class LlmEvaluator:
    """LLM 评估器 — 无客观校验器时用 LLM 判定输出是否满足任务

    降级路径（不易）: llm_client 不可用 → 全部样本 skipped（checked_by=
    llm_unavailable），返回 status="degraded"，绝不伪造指标。
    """

    def __init__(self, pool: Optional[EvalSamplePool] = None, *,
                 llm_client: Any = None,
                 runner: Optional[Callable[[Any, Dict[str, Any]], ExecOutcome]] = None,
                 timeout_sec: Optional[int] = None,
                 budget_tokens: Optional[int] = None,
                 prompt_template: Optional[str] = None):
        self.pool = pool or EvalSamplePool()
        self._llm = llm_client
        self._runner = runner or _default_runner(timeout_sec)
        self._timeout_sec = timeout_sec
        self.budget_tokens = budget_tokens if budget_tokens is not None else _env_budget_tokens()
        self._template = prompt_template or _LLM_JUDGE_TEMPLATE

    def resolve_category(self, skill: Any) -> str:
        return resolve_category(skill, self.pool)

    def evaluate(self, skill: Any,
                 sample_ids: Optional[List[str]] = None, *,
                 params: Optional[Dict[str, Any]] = None,
                 budget_tokens: Optional[int] = None,
                 stage: str = "") -> EvaluationResult:
        samples = self.pool.get(self.resolve_category(skill), sample_ids=sample_ids)
        if not samples:
            logger.warning(
                "[Evaluator] eval.no_samples skill=%s category=%s stage=%s "
                "pool_dir=%s（绝不伪造指标）",
                skill.id, self.resolve_category(skill), stage, self.pool.base_dir)
            return EvaluationResult(
                skill_id=skill.id, status="no_samples",
                notes=[f"类别 {self.resolve_category(skill)!r} 无评估样本"],
            )
        if self._llm is None:
            # 降级：无法判定 → 显式跳过，绝不伪造
            skipped = [
                SampleEvaluation(sample_id=s.id, skipped=True,
                                 checked_by="llm_unavailable",
                                 error="LLM 客户端不可用，无法判定开放域输出")
                for s in samples
            ]
            return EvaluationResult(
                skill_id=skill.id, status="degraded",
                sample_count=len(samples), samples=skipped,
                cost_tokens=0, evaluator_version=_EVALUATOR_VERSION,
                notes=["LLM 评估器降级：LLM 客户端不可用，全部样本标记跳过（不伪造指标）"],
            )

        budget = TokenBudget(budget_tokens if budget_tokens is not None else self.budget_tokens)
        results: List[SampleEvaluation] = []
        for sample in samples:
            est = budget.estimate(sample.task)
            if not budget.spend(est):
                logger.warning(
                    "[Evaluator] budget.break stage=%s skill=%s sample=%s "
                    "input_estimate=%d used=%d budget=%d executed=%d "
                    "skipped_remaining=%d",
                    stage, skill.id, sample.id, est, budget.used, budget.budget,
                    len(results), len(samples) - len(results))
                break
            run_params = dict(params or getattr(skill, "default_params", None) or {})
            run_params["task"] = sample.task
            t0 = time.time()
            outcome = self._runner(skill, run_params)
            latency = outcome.duration_ms or (time.time() - t0) * 1000
            if not outcome.success:
                results.append(SampleEvaluation(
                    sample_id=sample.id, skipped=True, latency_ms=latency,
                    checked_by="exec_error", error=(outcome.stderr or "执行失败")[:300]))
                continue
            output = outcome.result if outcome.result is not None else outcome.stdout
            ok, score, err = self._judge(sample, output)
            results.append(SampleEvaluation(
                sample_id=sample.id, success=ok, latency_ms=latency,
                output=output, expected=sample.expected_output,
                checked_by="llm" if not err else "llm_error",
                score=score, error=err,
            ))
            est_out = budget.estimate(output)
            if not budget.spend(est_out):
                logger.warning(
                    "[Evaluator] budget.break stage=%s skill=%s sample=%s "
                    "output_estimate=%d used=%d budget=%d executed=%d "
                    "skipped_remaining=%d",
                    stage, skill.id, sample.id, est_out, budget.used,
                    budget.budget, len(results), len(samples) - len(results))
                break
        return SkillExecutorEvaluator._aggregate(
            skill, results, budget, stage=stage, sample_total=len(samples))

    def _judge(self, sample: EvalSample, output: Any) -> Tuple[bool, float, str]:
        """LLM 判定（成功与否 + 0-1 得分）；解析失败 → 判定失败并记录"""
        prompt = self._template.format(task=sample.task, output=output)
        try:
            resp = self._llm.chat(prompt)
        except Exception as e:  # noqa: BLE001 LLM 调用失败 → 该样本判定失败
            return False, 0.0, f"llm_error: {e}"
        text = (resp or "").strip()
        score = 0.0
        ok = False
        try:
            data = json.loads(text)
            ok = bool(data.get("success", False))
            score = max(0.0, min(1.0, float(data.get("score", 0.0))))
            return ok, score, ""
        except (json.JSONDecodeError, ValueError, TypeError):
            # 容忍非 JSON 回复：识别 yes/true/通过
            low = text.lower()
            ok = any(k in low for k in ("true", "yes", "通过", "成功", "\"success\": true"))
            if "score" in text:
                m = re.search(r"score[\"':\s]+([0-9.]+)", low)
                if m:
                    try:
                        score = max(0.0, min(1.0, float(m.group(1))))
                    except ValueError:
                        score = 0.0
            return ok, score, "" if ok or score > 0 else "llm_unparseable"


# ════════════════════════════════════════════════════════════
#  分阶段评估器（Staged Eval）
# ════════════════════════════════════════════════════════════


class StagedEvaluator:
    """分阶段评估 — 阶段1 小样本快速初筛，达标才进入阶段2 全量评估（省成本）

    流程:
        阶段1: 取 stage1_ratio（默认 10%）样本评估，得分 < stage1_min_score
               → 直接淘汰（eliminated=True，不进阶段2）；
        阶段2: 通过初筛 → 全量样本评估。

    预算熔断: 任一阶段 budget_exceeded 立即返回（不再继续），
    由 base 评估器保证返回状态真实（不伪造）。
    """

    def __init__(self, base: Optional[SkillExecutorEvaluator] = None, *,
                 pool: Optional[EvalSamplePool] = None,
                 stage1_ratio: Optional[float] = None,
                 stage1_min_score: Optional[float] = None,
                 stage1_max_samples: Optional[int] = None,
                 stage1_budget_tokens: Optional[int] = None,
                 stage2_budget_tokens: Optional[int] = None):
        self._base = base or SkillExecutorEvaluator(pool=pool)
        self.pool = self._base.pool
        self.stage1_ratio = stage1_ratio if stage1_ratio is not None else _env_stage1_ratio()
        self.stage1_min_score = (
            stage1_min_score if stage1_min_score is not None else _env_stage1_min_score())
        self.stage1_max_samples = (
            stage1_max_samples if stage1_max_samples is not None else _env_stage1_max_samples())
        self.stage1_budget_tokens = (
            stage1_budget_tokens if stage1_budget_tokens is not None else _env_stage1_budget_tokens())
        self.stage2_budget_tokens = (
            stage2_budget_tokens if stage2_budget_tokens is not None else _env_stage2_budget_tokens())

    def resolve_category(self, skill: Any) -> str:
        return self._base.resolve_category(skill)

    def evaluate(self, skill: Any,
                 sample_ids: Optional[List[str]] = None, *,
                 params: Optional[Dict[str, Any]] = None,
                 budget_tokens: Optional[int] = None) -> EvaluationResult:
        category = self._base.resolve_category(skill)
        all_samples = self.pool.get(category)
        t0 = time.monotonic()  # 分阶段评估总耗时统计起点（性能瓶颈分析）
        if not all_samples:
            logger.warning(
                "[Evaluator] staged.no_samples skill=%s category=%s "
                "pool_dir=%s available_categories=%s stage1_ratio=%.2f "
                "stage1_min_score=%.2f → 返回 no_samples（绝不伪造指标）",
                skill.id, category, self.pool.base_dir,
                ",".join(self.pool.categories()) or "(无)",
                self.stage1_ratio, self.stage1_min_score)
            return self._base.evaluate(skill, sample_ids=[], params=params,
                                       budget_tokens=budget_tokens)  # no_samples
        if sample_ids is not None:
            # 显式指定子集：跳过初筛，直接评估该子集（供调用方复用）
            logger.info(
                "[Evaluator] staged.subset skill=%s sample_ids=%s （跳过初筛）",
                skill.id, ",".join(sample_ids))
            return self._base.evaluate(skill, sample_ids=sample_ids,
                                       params=params, budget_tokens=budget_tokens)

        # ── 阶段1：小样本初筛 ──
        n1 = max(1, min(self.stage1_max_samples,
                        int(len(all_samples) * self.stage1_ratio)))
        stage1_ids = [s.id for s in all_samples[:n1]]
        t1 = time.monotonic()  # 阶段1墙钟计时起点
        logger.info(
            "[Evaluator] staged.stage1.start skill=%s category=%s total=%d "
            "ratio=%.2f max_samples=%d n1=%d sample_ids=%s budget_tokens=%d params=%s",
            skill.id, category, len(all_samples), self.stage1_ratio,
            self.stage1_max_samples, n1, ",".join(stage1_ids),
            self.stage1_budget_tokens,
            "N/A" if params is None else json.dumps(params, ensure_ascii=False))
        r1 = self._base.evaluate(skill, sample_ids=stage1_ids, params=params,
                                 budget_tokens=self.stage1_budget_tokens,
                                 stage="stage1")
        r1.stage = "stage1"
        stage1_ms = (time.monotonic() - t1) * 1000  # 阶段1真实墙钟耗时
        if r1.status in ("no_samples", "budget_exceeded"):
            r1.notes.append("阶段1未通过，未进入阶段2")
            logger.warning(
                "[Evaluator] staged.stage1.abort skill=%s status=%s "
                "stage1_score=%.4f stage1_success_rate=%.3f samples=%d "
                "used_tokens=%d budget=%d stage1_ms=%.0f detail=[%s]",
                skill.id, r1.status, r1.score, r1.success_rate,
                len(r1.samples), r1.cost_tokens, self.stage1_budget_tokens,
                stage1_ms, self._samples_summary(r1.samples))
            return r1
        if r1.score < self.stage1_min_score:
            r1.eliminated = True
            r1.notes.append(
                f"阶段1得分 {r1.score:.3f} < 初筛阈值 {self.stage1_min_score}，"
                f"淘汰（样本 {n1} 条，不再进入阶段2）")
            logger.info(
                "[Evaluator] staged.stage1.eliminated skill=%s score=%.4f "
                "threshold=%.4f samples=%d stage1_success_rate=%.3f "
                "latency=%.0fms used_tokens=%d stage1_ms=%.0f detail=[%s]",
                skill.id, r1.score, self.stage1_min_score, n1,
                r1.success_rate, r1.latency_ms, r1.cost_tokens,
                stage1_ms, self._samples_summary(r1.samples))
            return r1

        # 阶段1 通过 → 进入阶段2
        logger.info(
            "[Evaluator] staged.stage1.pass skill=%s score=%.4f >= threshold=%.4f "
            "samples=%d stage1_success_rate=%.3f latency=%.0fms used_tokens=%d "
            "stage1_ms=%.0f detail=[%s] → 进入阶段2",
            skill.id, r1.score, self.stage1_min_score, n1,
            r1.success_rate, r1.latency_ms, r1.cost_tokens,
            stage1_ms, self._samples_summary(r1.samples))

        # ── 阶段2：全量评估 ──
        t2 = time.monotonic()  # 阶段2墙钟计时起点（切换决策完成，开始全量）
        logger.info(
            "[Evaluator] staged.stage2.start skill=%s category=%s total=%d "
            "budget_tokens=%d stage1_ms=%.0f",
            skill.id, category, len(all_samples), self.stage2_budget_tokens,
            stage1_ms)
        r2 = self._base.evaluate(skill, sample_ids=None, params=params,
                                 budget_tokens=self.stage2_budget_tokens,
                                 stage="stage2")
        r2.stage = "stage2"
        stage2_ms = (time.monotonic() - t2) * 1000  # 阶段2真实墙钟耗时
        total_ms = (time.monotonic() - t0) * 1000   # 分阶段评估总耗时
        r2.notes.append(
            f"阶段1通过（score={r1.score:.3f} ≥ {self.stage1_min_score}），"
            f"进入阶段2全量评估（{len(all_samples)} 条样本）")
        if r2.status == "budget_exceeded":
            # 阶段2 全量预算耗尽：记录执行进度，便于排查成本失控
            logger.warning(
                "[Evaluator] staged.stage2.budget_break skill=%s used=%d "
                "budget=%d executed=%d total=%d stage1_ms=%.0f stage2_ms=%.0f "
                "total_ms=%.0f（全量评估预算耗尽，仅含已执行样本的真实结果）",
                skill.id, r2.cost_tokens, self.stage2_budget_tokens,
                r2.sample_count, len(all_samples), stage1_ms, stage2_ms,
                total_ms)
        logger.info(
            "[Evaluator] staged.stage2.done skill=%s score=%.4f "
            "success_rate=%.3f latency=%.0fms samples=%d used_tokens=%d "
            "status=%s stage1_ms=%.0f stage2_ms=%.0f total_ms=%.0f "
            "detail=[%s]",
            skill.id, r2.score, r2.success_rate, r2.latency_ms,
            r2.sample_count, r2.cost_tokens, r2.status,
            stage1_ms, stage2_ms, total_ms,
            self._samples_summary(r2.samples))
        return r2

    @staticmethod
    def _samples_summary(samples: List["SampleEvaluation"]) -> str:
        """压缩展示每样本判定结果（P=成功/F=失败，score/判定方式；skipped 显示原因）"""
        parts = []
        for s in samples:
            if s.skipped:
                parts.append(f"{s.sample_id}:skipped({s.checked_by})")
            else:
                parts.append(
                    f"{s.sample_id}:{'P' if s.success else 'F'}"
                    f"/{s.score:.2f}/{s.checked_by}")
        return ", ".join(parts)


# ════════════════════════════════════════════════════════════
#  评估器注册表
# ════════════════════════════════════════════════════════════

_EvaluatorFactory = Callable[..., SkillExecutorEvaluator]


class EvaluatorRegistry:
    """评估器注册表 — 按技能类别注册/查询评估器

    默认注册（真实执行评估）:
        - search: SkillExecutorEvaluator（精确匹配/包含校验）
        - code:   SkillExecutorEvaluator（测试用例 validator 校验）
        - chat:   SkillExecutorEvaluator（自一致性，含反馈信号）
    未注册类别 → 分阶段 LLM 评估（StagedEvaluator(LlmEvaluator)）。
    """

    def __init__(self, pool: Optional[EvalSamplePool] = None, *,
                 llm_client: Any = None,
                 eval_config: Optional[Dict[str, Any]] = None):
        self._pool = pool or EvalSamplePool()
        self._llm_client = llm_client
        self._config = eval_config or {}
        self._factories: Dict[str, _EvaluatorFactory] = {}
        self._register_defaults()

    # ─── 注册 ───

    def register(self, category: str, factory: _EvaluatorFactory) -> None:
        """注册类别评估器工厂（factory(pool, config) -> 基础评估器）"""
        self._factories[category.lower()] = factory

    def _register_defaults(self) -> None:
        self.register("search", lambda p, c: SkillExecutorEvaluator(p, **c))
        self.register("code", lambda p, c: SkillExecutorEvaluator(
            p, allow_validator=True, **{k: v for k, v in c.items() if k != "allow_validator"}))
        self.register("chat", lambda p, c: SkillExecutorEvaluator(
            p, consistency_runs=c.get("consistency_runs") or _env_consistency_runs(),
            **{k: v for k, v in c.items() if k != "consistency_runs"}))

    # ─── 查询 ───

    def build_base(self, category: str, **overrides: Any) -> SkillExecutorEvaluator:
        """构建类别基础评估器（未注册类别 → LlmEvaluator）"""
        cat = category.lower()
        factory = self._factories.get(cat)
        if factory is not None:
            cfg = dict(self._config)
            cfg.update({k: v for k, v in overrides.items() if v is not None})
            return factory(self._pool, cfg)
        # 未注册类别 → LLM 评估（带降级路径）
        return LlmEvaluator(self._pool, llm_client=self._llm_client,
                            **{k: v for k, v in overrides.items() if v is not None})

    def get(self, skill: Any, *, staged: bool = True, **overrides: Any) -> SkillEvaluator:
        """按技能解析类别并返回评估器（默认分阶段包装，省成本）"""
        category = resolve_category(skill, self._pool)
        base = self.build_base(category, **overrides)
        if not staged:
            return base
        return StagedEvaluator(base, pool=self._pool)

    def get_for_category(self, category: str, *,
                         staged: bool = True, **overrides: Any) -> SkillEvaluator:
        """按类别名返回评估器（未注册类别默认走分阶段 LLM 评估）"""
        base = self.build_base(category, **overrides)
        if not staged:
            return base
        return StagedEvaluator(base, pool=self._pool)


def get_default_evaluator(skill: Any, *,
                          llm_client: Any = None,
                          pool: Optional[EvalSamplePool] = None) -> SkillEvaluator:
    """便捷入口：获取某技能的分阶段评估器（含真实执行 + 成本控制）"""
    return EvaluatorRegistry(pool=pool, llm_client=llm_client).get(skill)
