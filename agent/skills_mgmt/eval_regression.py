"""评估集回归门禁（任务1 Step 4）— 进化产物"不退化"判定

【背景】
    TASK-08 护栏 G5 要求"进化产物先过离线评估集回归（标准集不退化）→ 再进入在线观察期"。
    本模块把"评估集回归"实现为可执行门禁：
        evaluate_regression(skill, sampleset_version, budget) -> RegressionResult
    status ∈ {PASS, FAIL, NO_SAMPLES, budget_exceeded}（绝不伪造指标）。

【基线语义（谱系）】
    - 首次评估：记录该 Skill 在 <sampleset_version> 上的 baseline 分数（持久化到
      data/evals/baselines.json，按 skill_id + version 区分）；
    - 后续评估：delta = score - baseline_score；delta < -degrade_threshold（默认 0.05）
      → FAIL（"不退化"门槛）。
    - 基线只升不降：新分数高于基线时更新基线（记录当前最优），低于基线不更新
      （保持原基线作为退化判定基准，防止"温水煮青蛙"式逐步下滑）。

【复用（不易边界）】
    - 不修改 evaluator.py：门禁在其之上新增编排层，复用 EvaluatorRegistry /
      StagedEvaluator / TokenBudget / EvalSamplePool；
    - 无样本/版本未登记 → NO_SAMPLES；预算熔断 → budget_exceeded；LLM 不可用 → NO_SAMPLES；
    - 门禁默认只读告警（接线端控制，见 offline_evolver EVOLUTION_REGRESSION_GATE）。

【配置（.env，带默认值；环境变量 > config.yaml > 硬编码）】
    EVAL_REGRESSION_DEGRADE_THRESHOLD   退化阈值，默认 0.05（delta < -0.05 → FAIL）
    EVAL_REGRESSION_BASELINE_FILE       基线存储，默认 <项目根>/data/evals/baselines.json
    EVAL_REGRESSION_DEFAULT_SET         默认样本集版本，默认 v1
    EVAL_REGRESSION_BUDGET_TOKENS       门禁评估预算，默认 100000
    EVAL_SAMPLES_DIR                    样本池根目录，默认 <项目根>/data/evals

【CLI】
    python -m agent.skills_mgmt.eval_regression --skill <id> --set v1 --budget 500k
    退出码: 0=PASS, 1=FAIL, 2=NO_SAMPLES/budget_exceeded, 3=技能不存在, 4=其他错误
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .observability import logger

# ════════════════════════════════════════════════════════════
#  配置（env > config.yaml > 默认值）
# ════════════════════════════════════════════════════════════

_DEFAULT_SAMPLES_DIR = Path(__file__).parent.parent.parent / "data" / "evals"
_DEFAULT_BASELINE_FILE = _DEFAULT_SAMPLES_DIR / "baselines.json"


def _config_yaml() -> Optional[Dict[str, Any]]:
    cfg_path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
    if not cfg_path.exists():
        return None
    try:
        import yaml as _yaml  # 延迟导入，避免硬依赖
        with open(cfg_path, "r", encoding="utf-8") as f:
            return _yaml.safe_load(f) or {}
    except Exception:  # noqa: BLE001 配置解析失败回退默认
        return None


def _cfg_value(key: str, default: Any) -> Any:
    cfg = _config_yaml()
    if cfg is not None:
        val = ((cfg.get("skills_mgmt", {}) or {}).get("eval_samples", {}) or {}).get(key)
        if val is not None:
            return val
    return default


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


def _degrade_threshold() -> float:
    """退化阈值（EVAL_REGRESSION_DEGRADE_THRESHOLD，默认 0.05）"""
    env = os.environ.get("EVAL_REGRESSION_DEGRADE_THRESHOLD")
    if env is not None and env.strip():
        try:
            return float(env.strip())
        except ValueError:
            pass
    try:
        return float(_cfg_value("degrade_threshold", 0.05))
    except (TypeError, ValueError):
        return 0.05


def _baseline_file() -> Path:
    env = os.environ.get("EVAL_REGRESSION_BASELINE_FILE")
    if env and env.strip():
        return Path(env)
    return Path(str(_cfg_value("baselines_file", str(_DEFAULT_BASELINE_FILE))))


def _samples_dir() -> Path:
    env = os.environ.get("EVAL_SAMPLES_DIR")
    if env and env.strip():
        return Path(env)
    return Path(str(_cfg_value("dir", str(_DEFAULT_SAMPLES_DIR))))


def _default_set() -> str:
    env = os.environ.get("EVAL_REGRESSION_DEFAULT_SET")
    if env and env.strip():
        return env.strip()
    return str(_cfg_value("default_sampleset", "v1"))


def _default_budget() -> int:
    env = os.environ.get("EVAL_REGRESSION_BUDGET_TOKENS")
    if env is not None and env.strip():
        try:
            return max(0, int(env.strip()))
        except ValueError:
            pass
    try:
        return max(0, int(_cfg_value("budget_tokens", 100000)))
    except (TypeError, ValueError):
        return 100000


# ════════════════════════════════════════════════════════════
#  数据结构
# ════════════════════════════════════════════════════════════

# 回归结果状态（绝不伪造指标：样本不足/预算熔断显式返回）
PASS = "PASS"
FAIL = "FAIL"
NO_SAMPLES = "NO_SAMPLES"
BUDGET_EXCEEDED = "budget_exceeded"


@dataclass
class RegressionResult:
    """一次评估集回归结果"""
    skill_id: str = ""
    sampleset_version: str = ""
    status: str = NO_SAMPLES        # PASS / FAIL / NO_SAMPLES / budget_exceeded
    score: float = 0.0              # 本次评估综合分（无真实指标 = 0.0，不伪造）
    baseline_score: Optional[float] = None   # 历史基线分（None=首次评估）
    delta_vs_baseline: Optional[float] = None  # score - baseline（None=首次）
    used_tokens: int = 0            # 本次评估 token 消耗
    sample_count: int = 0           # 本次评估样本数
    degrade_threshold: float = 0.05
    notes: List[str] = field(default_factory=list)
    eval_result: Optional[Dict[str, Any]] = None  # 底层评估快照（谱系落库用）
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    @property
    def passed(self) -> bool:
        return self.status == PASS

    def to_dict(self) -> Dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "sampleset_version": self.sampleset_version,
            "status": self.status,
            "score": round(self.score, 4),
            "baseline_score": (round(self.baseline_score, 4)
                               if self.baseline_score is not None else None),
            "delta_vs_baseline": (round(self.delta_vs_baseline, 4)
                                  if self.delta_vs_baseline is not None else None),
            "used_tokens": self.used_tokens,
            "sample_count": self.sample_count,
            "degrade_threshold": self.degrade_threshold,
            "notes": self.notes,
            "created_at": self.created_at,
        }


# ════════════════════════════════════════════════════════════
#  样本集版本解析（manifest.json）
# ════════════════════════════════════════════════════════════


class SamplesetRegistry:
    """样本集版本登记表 — data/evals/manifest.json

    门禁按 (version, category) 解析样本 id 清单；版本/类别未登记 → None（NO_SAMPLES）。
    """

    def __init__(self, manifest_path: Optional[Path] = None):
        self._path = Path(manifest_path) if manifest_path else _samples_dir() / "manifest.json"
        self._cache: Optional[Dict[str, Any]] = None

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> Dict[str, Any]:
        if self._cache is not None:
            return self._cache
        if not self._path.exists():
            self._cache = {}
            return self._cache
        try:
            with open(self._path, "r", encoding="utf-8") as f:
                self._cache = json.load(f) or {}
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("[EvalRegression] manifest 读取失败 %s: %s（按无版本处理）",
                           self._path, e)
            self._cache = {}
        return self._cache

    def sample_ids(self, version: str, category: str) -> Optional[List[str]]:
        """返回 (version, category) 的样本 id 清单；未登记返回 None"""
        data = self._load()
        spec = (data.get("versions") or {}).get(version)
        if not isinstance(spec, dict):
            return None
        cats = spec.get("categories") or {}
        ids = cats.get(category)
        if not isinstance(ids, list) or not ids:
            return None
        return [str(i) for i in ids]


# ════════════════════════════════════════════════════════════
#  基线存储（data/evals/baselines.json，线程安全 + 原子写）
# ════════════════════════════════════════════════════════════

_BASELINE_SCHEMA_VERSION = 1


class BaselineStore:
    """回归基线存储 — {skill_id: {version: {score, sample_count, evaluator_version, created_at}}}

    Why 独立于 EvolutionArchive: 基线是"标准集分数"的持续状态（可覆盖更新），
    与进化事件记录（append-only 谱系）语义不同；存放在 data/evals/ 下便于审计与清理。
    """

    def __init__(self, path: Optional[Path] = None):
        self._path = Path(path) if path else _baseline_file()
        self._lock = threading.RLock()
        self._data: Optional[Dict[str, Any]] = None

    @property
    def path(self) -> Path:
        return self._path

    def _load(self) -> Dict[str, Any]:
        with self._lock:
            if self._data is not None:
                return self._data
            if not self._path.exists():
                self._data = {"schema_version": _BASELINE_SCHEMA_VERSION, "baselines": {}}
                return self._data
            try:
                with open(self._path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                self._data = raw if isinstance(raw, dict) else {}
                self._data.setdefault("schema_version", _BASELINE_SCHEMA_VERSION)
                self._data.setdefault("baselines", {})
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("[EvalRegression] 基线存储损坏，重置 %s: %s", self._path, e)
                self._data = {"schema_version": _BASELINE_SCHEMA_VERSION, "baselines": {}}
            return self._data

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", delete=False,
            dir=str(self._path.parent), suffix=".tmp",
        ) as tmp:
            json.dump(self._data, tmp, ensure_ascii=False, indent=2)
            tmp_path = tmp.name
        for attempt in range(3):
            try:
                os.replace(tmp_path, self._path)
                return
            except OSError:
                if attempt == 2:
                    raise
                time.sleep(0.05)

    def get(self, skill_id: str, version: str) -> Optional[Dict[str, Any]]:
        data = self._load()
        return ((data.get("baselines") or {}).get(skill_id) or {}).get(version)

    def record(self, skill_id: str, version: str, *, score: float,
               sample_count: int, evaluator_version: str) -> Dict[str, Any]:
        """记录/更新基线（线程安全 + 原子写）；返回基线条目"""
        with self._lock:
            data = self._load()
            by_skill = data.setdefault("baselines", {}).setdefault(skill_id, {})
            entry = {
                "score": round(score, 4),
                "sample_count": sample_count,
                "evaluator_version": evaluator_version,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
            by_skill[version] = entry
            self._persist()
            return dict(entry)


# ════════════════════════════════════════════════════════════
#  回归门禁
# ════════════════════════════════════════════════════════════


class RegressionGate:
    """评估集回归门禁 — 复用 StagedEvaluator 分阶段评估与 TokenBudget 熔断

    evaluate(skill, params=None) 流程:
        1. 解析技能类别 → manifest 样本集 (version, category) → 无 → NO_SAMPLES
        2. 评估（显式样本子集 + 预算）→ budget_exceeded 熔断
        3. 与基线比较 → PASS / FAIL；首次评估自动记录基线
    """

    def __init__(self, *, samples_dir: Optional[str] = None,
                 manifest_path: Optional[Path] = None,
                 baseline_store: Optional[BaselineStore] = None,
                 degrade_threshold: Optional[float] = None,
                 default_version: Optional[str] = None,
                 default_budget: Optional[int] = None,
                 evaluator_factory: Optional[Any] = None):
        self._samples_dir = Path(samples_dir) if samples_dir else _samples_dir()
        self._registry = SamplesetRegistry(manifest_path)
        self._baselines = baseline_store or BaselineStore()
        self._degrade_threshold = (
            degrade_threshold if degrade_threshold is not None else _degrade_threshold())
        self._default_version = default_version or _default_set()
        self._default_budget = default_budget if default_budget is not None else _default_budget()
        self._evaluator_factory = evaluator_factory

    # ─── 查询 ───

    def has_baseline(self, skill_id: str, version: Optional[str] = None) -> bool:
        return self._baselines.get(skill_id, version or self._default_version) is not None

    def baseline_score(self, skill_id: str, version: Optional[str] = None) -> Optional[float]:
        entry = self._baselines.get(skill_id, version or self._default_version)
        if entry:
            try:
                return float(entry.get("score"))
            except (TypeError, ValueError):
                return None
        return None

    # ─── 评估 ───

    def evaluate(self, skill: Any, *, params: Optional[Dict[str, Any]] = None,
                 sampleset_version: Optional[str] = None,
                 budget_tokens: Optional[int] = None,
                 record_baseline: bool = True,
                 evaluator: Optional[Any] = None) -> RegressionResult:
        version = sampleset_version or self._default_version
        budget = budget_tokens if budget_tokens is not None else self._default_budget
        skill_id = str(getattr(skill, "id", ""))
        result = RegressionResult(
            skill_id=skill_id, sampleset_version=version,
            degrade_threshold=self._degrade_threshold,
        )

        # 步骤1: 类别 + 样本集解析
        # evaluator 显式注入时（evolver 复用真实评估器）优先使用；否则按类别构建
        if evaluator is None:
            evaluator = self._build_evaluator(skill)
        category = evaluator.resolve_category(skill)
        ids = self._registry.sample_ids(version, category)
        if ids is None:
            result.notes.append(
                f"样本集版本 {version!r} 类别 {category!r} 未登记（manifest 缺失或未覆盖）→ NO_SAMPLES，绝不伪造指标")
            logger.warning(
                "[EvalRegression] eval.no_samples skill=%s version=%s category=%s "
                "manifest=%s（绝不伪造指标）",
                skill_id, version, category, self._registry.path)
            return result

        # 步骤2: 真实评估（显式子集 + TokenBudget 熔断）
        try:
            ev = evaluator.evaluate(skill, sample_ids=ids, params=params,
                                    budget_tokens=budget)
        except Exception as e:  # noqa: BLE001 评估异常 → 显式 NO_SAMPLES，不伪造
            logger.warning("[EvalRegression] 评估异常 skill=%s: %s（按 NO_SAMPLES 处理）",
                           skill_id, e)
            result.notes.append(f"评估异常: {e}")
            return result

        result.used_tokens = ev.cost_tokens or 0
        result.sample_count = ev.sample_count or 0
        result.eval_result = ev.to_eval_result_dict()

        if ev.status == "budget_exceeded":
            result.status = BUDGET_EXCEEDED
            result.notes.append(
                f"token 预算熔断（used={ev.cost_tokens} > budget={budget}），"
                f"仅含已完成样本的真实结果，不伪造分数")
            logger.warning(
                "[EvalRegression] eval.budget_exceeded skill=%s version=%s used=%d "
                "budget=%d executed=%d",
                skill_id, version, ev.cost_tokens, budget, ev.sample_count)
            return result
        if ev.status in ("no_samples", "degraded"):
            result.notes.append(
                f"评估返回 {ev.status}（无样本/降级不可判定）→ NO_SAMPLES，绝不伪造指标")
            logger.warning(
                "[EvalRegression] eval.no_samples skill=%s version=%s status=%s "
                "（绝不伪造指标）", skill_id, version, ev.status)
            return result

        # 步骤3: 与基线比较
        result.score = ev.score
        baseline = self._baselines.get(skill_id, version)
        if baseline is None:
            # 首次评估 → 记录基线（谱系）
            if record_baseline:
                self._baselines.record(
                    skill_id, version, score=ev.score,
                    sample_count=ev.sample_count,
                    evaluator_version=ev.evaluator_version)
            result.status = PASS
            result.baseline_score = None
            result.delta_vs_baseline = None
            result.notes.append(
                f"首次评估：已记录基线 score={ev.score:.4f}（samples={ev.sample_count}）")
            logger.info(
                "[EvalRegression] baseline.recorded skill=%s version=%s score=%.4f "
                "samples=%d", skill_id, version, ev.score, ev.sample_count)
            return result

        result.baseline_score = float(baseline.get("score", 0.0))
        delta = ev.score - result.baseline_score
        result.delta_vs_baseline = round(delta, 4)
        if delta < -self._degrade_threshold:
            result.status = FAIL
            result.notes.append(
                f"回归退化：score={ev.score:.4f} < baseline={result.baseline_score:.4f} "
                f"delta={delta:+.4f}（阈值 -{self._degrade_threshold}）")
            logger.warning(
                "[EvalRegression] eval.fail skill=%s version=%s score=%.4f "
                "baseline=%.4f delta=%+.4f threshold=%.4f",
                skill_id, version, ev.score, result.baseline_score,
                delta, self._degrade_threshold)
        else:
            result.status = PASS
            result.notes.append(
                f"回归通过：score={ev.score:.4f} baseline={result.baseline_score:.4f} "
                f"delta={delta:+.4f}")
            # 基线只升不降：新分更高时更新基线
            if record_baseline and ev.score > result.baseline_score:
                self._baselines.record(
                    skill_id, version, score=ev.score,
                    sample_count=ev.sample_count,
                    evaluator_version=ev.evaluator_version)
                logger.info(
                    "[EvalRegression] baseline.updated skill=%s version=%s "
                    "score=%.4f（高于原基线 %.4f）",
                    skill_id, version, ev.score, result.baseline_score)
        return result

    # ─── 内部 ───

    def _build_evaluator(self, skill: Any):
        if self._evaluator_factory is not None:
            ev = self._evaluator_factory(skill)
            if ev is not None:
                return ev
        from .evaluator import EvalSamplePool, EvaluatorRegistry
        pool = EvalSamplePool(base_dir=str(self._samples_dir))
        registry = EvaluatorRegistry(pool=pool)
        return registry.get_for_category(registry_category(skill, pool))


def registry_category(skill: Any, pool: Any) -> str:
    """按技能解析样本类别（供门禁构建类别评估器）"""
    from .evaluator import resolve_category
    return resolve_category(skill, pool)


def evaluate_regression(skill: Any, sampleset_version: Optional[str] = None,
                        budget_tokens: Optional[int] = None, *,
                        params: Optional[Dict[str, Any]] = None,
                        gate: Optional[RegressionGate] = None,
                        record_baseline: bool = True) -> RegressionResult:
    """便捷入口：评估某技能在评估集上的回归结果"""
    g = gate or RegressionGate()
    return g.evaluate(skill, params=params, sampleset_version=sampleset_version,
                      budget_tokens=budget_tokens, record_baseline=record_baseline)


def query_regression_status(skill_id: str, sampleset_version: Optional[str] = None,
                            *, gate: Optional[RegressionGate] = None) -> Optional[Dict[str, Any]]:
    """TASK-04 发布审核链只读查询：返回该技能最近回归状态（无数据返回 None）

    只读：不评估、不写盘；供 enforce_before_publish 前置查询与审计日志。
    """
    g = gate or RegressionGate()
    version = sampleset_version or g._default_version
    entry = g._baselines.get(skill_id, version)
    if entry is None:
        return None
    return {
        "skill_id": skill_id,
        "sampleset_version": version,
        "baseline_score": round(float(entry.get("score", 0.0)), 4),
        "sample_count": entry.get("sample_count"),
        "evaluator_version": entry.get("evaluator_version"),
        "created_at": entry.get("created_at"),
    }


# ════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════


def _parse_budget(text: str) -> int:
    """解析预算："500k" → 500000；"1m" → 1000000；纯数字 → 原值"""
    t = (text or "").strip().lower()
    if not t:
        return _default_budget()
    mult = 1
    if t.endswith("k"):
        mult, t = 1000, t[:-1]
    elif t.endswith("m"):
        mult, t = 1_000_000, t[:-1]
    try:
        return max(0, int(float(t) * mult))
    except ValueError:
        return _default_budget()


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        description="评估集回归门禁 CLI（任务1）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--skill", required=True, help="技能 ID")
    parser.add_argument("--set", default=None, help="样本集版本（默认 v1）")
    parser.add_argument("--budget", default=None, help="评估 token 预算（如 500k / 100000）")
    parser.add_argument("--params", default=None, help="评估参数覆盖（JSON 字符串）")
    parser.add_argument("--store", default=None, help="技能存储路径（默认 data/skills_mgmt.json）")
    parser.add_argument("--samples-dir", default=None, help="样本池根目录（默认 data/evals）")
    args = parser.parse_args(argv)

    try:
        from .store import SkillStore
        store = SkillStore(path=args.store) if args.store else SkillStore()
        skill = store.get(args.skill)
    except Exception as e:  # noqa: BLE001
        print(json.dumps({"skill": args.skill, "error": f"技能加载失败: {e}"},
                         ensure_ascii=False))
        return 4
    if skill is None:
        print(json.dumps({"skill": args.skill, "error": "技能不存在"},
                         ensure_ascii=False))
        return 3

    params = None
    if args.params:
        try:
            params = json.loads(args.params)
        except json.JSONDecodeError as e:
            print(json.dumps({"error": f"params JSON 解析失败: {e}"}, ensure_ascii=False))
            return 4

    gate_kwargs = {}
    if args.samples_dir:
        gate_kwargs["samples_dir"] = args.samples_dir
    gate = RegressionGate(**gate_kwargs)
    result = gate.evaluate(skill, params=params,
                           sampleset_version=args.set,
                           budget_tokens=_parse_budget(args.budget))
    print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    if result.status == PASS:
        return 0
    if result.status == FAIL:
        return 1
    return 2  # NO_SAMPLES / budget_exceeded


if __name__ == "__main__":
    raise SystemExit(main())
