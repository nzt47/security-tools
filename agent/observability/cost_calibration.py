"""成本系数**实测校准件**（TASK-S7-03 · v7.2 §6.2 UTC 刹车 / §6.6 成本埋点 / §6.7 指标字典）

S5-03 把归一化口径固定在**价格锚定系数**（`utc.coefficient()` 的 `source="price_ratio"`，
版本 `price_anchor.v1`，`calibrated=False`）。本模块提供把口径升级为**实测校准**所需的一切
机制，且**不改变"未校准时"的既有行为**：

## 交付什么

1. **校准件（artifact）数据模型** —— `CostCalibration`：版本 / 方法 / 样本量 / 生效模型 /
   锚模型 / 用例集哈希 / 校准日期 / 逐模型置信度。带校验（非法即拒，绝不"容忍坏数据"）。
2. **落盘与加载** —— `write_artifact()` / `load_artifact()` / `artifact_path()`。
   加载**只读且不创建文件**；文件不存在 / 非法 / 过期 → 一律返回 `None` 并回落价格系数。
3. **来源与优先级** —— `resolve_coefficient(model)`：

   ```
   CP_UTC_COEFFICIENTS（override，人工兜底） > 实测件（measured） > 价格锚定（price_ratio 回落）
   ```

   优先级是**逐模型**的：某模型实测样本不足 → 该模型回落价格系数，其余模型照常生效。
4. **偏差表** —— `deviation_table()`：价格系数（**压在真实 in/out 构成上的有效标量**）vs
   实测系数 vs 偏差率 vs 样本量 vs 置信度。标量压缩的理由见 `effective_price_coefficient()`。
5. **稳健统计** —— `MeasuredSamples.from_rows()`：均值 + 中位数 + p95 + 成功率 + 重试率 +
   单位任务成本，全部由**原始行**算出，不引入任何外部数字。
6. **失效规则** —— 锚模型变更 / L2 用例集哈希变更 → 校准件过期（`stale_reason`），
   防止"分母换了还用旧系数"这类静默失真。

## 三条不可让步的纪律

* **不得编造数字**：本模块不产生任何"默认/示例"数值；所有样本来自调用方传入的行。
* **样本 <20 只披露不结论**：`MIN_COST_SAMPLES_PER_MODEL` 以下的模型**不进** `measured` 映射，
  只在 `deviation_table()` 的 `confidence="insufficient"` 行中出现（不给实测系数）。
* **历史口径不追溯**：校准只影响**此后新写入**的事件；旧 `cost` 事件自带写入时的
  `coefficient_*` / `coefficient_source` 字段，聚合侧（`utc.utc_daily/utc_window/…`）为
  "读字段求和"，**从不重算**——旧数字因此天然不变。本模块也不回写任何历史事件。
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

logger = logging.getLogger("agent.observability.cost_calibration")

# ════════════════════════════════════════════════════════════
#  版本与常量
# ════════════════════════════════════════════════════════════

#: 校准件 schema（结构变更必须升版）
CALIBRATION_SCHEMA = "cost_coefficients.v1"

#: **完整实测**校准件版本（路径 A：在 L2 Core-50 上对多模型同批实跑）
MEASURED_VERSION = "measured.v1"

#: **降级部分校准**工件版本（路径 B：离线重放历史事件 + 可选导入 CSV）
#: —— 版本号本身就写明"不完整"，避免与 `measured.v1` 混淆
MEASURED_PARTIAL_VERSION = "measured.partial.v1"

#: 回落口径版本（未实测时）
PRICE_ANCHOR_VERSION = "price_anchor.v1"

#: 系数来源枚举（顺序即优先级，从高到低）
COEFFICIENT_SOURCES: Tuple[str, ...] = ("override", "measured", "price_ratio")

#: 每模型最小成本样本量（**与 `agent/eval/baseline.MIN_COST_SAMPLES_PER_MODEL` 同值**：
#: 口径纪律"每能力 ≥20 条同类轨迹"，<20 只披露不结论）
MIN_COST_SAMPLES_PER_MODEL = 20

#: 计算方法的取值（进工件，供审计）
METHOD_L2_RUN = "l2_core50_run"          # 路径 A：L2 Core-50 实跑
METHOD_OFFLINE_REPLAY = "offline_replay"  # 路径 B：事件流离线重放
METHOD_IMPORTED_CSV = "imported_csv"      # 路径 B：人工导入外部实测 CSV
METHOD_MIXED = "replay_plus_csv"          # 路径 B：重放 + 导入合并

#: 工件默认落点（**运行期产物目录，不入库**；与 S5-02 的 `data/eval/*.json` 同性质）
DEFAULT_ARTIFACT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "cost_coefficients.json")

#: 显式指定校准件的环境变量（**缺省不设** → 走 `DEFAULT_ARTIFACT_PATH`；
#: 二者都不存在时纯价格锚定，与 S5-03 现状逐字一致）
ENV_ARTIFACT = "CP_UTC_CALIBRATION_FILE"

#: 本地模型口径标记（**不得与 API 计价混算**；见校准方案 §六）
COST_BASIS_API = "api_price"
COST_BASIS_LOCAL = "local_machine_time"


class CalibrationError(ValueError):
    """校准件/参数非法（**拒绝坏数据，不做静默容错**）"""


# ════════════════════════════════════════════════════════════
#  数据模型
# ════════════════════════════════════════════════════════════


@dataclass
class ModelCalibration:
    """单模型的实测结果（**每个字段都能溯源到样本行**）"""

    model: str
    samples: int = 0
    tasks: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    cost_raw_cents: float = 0.0
    cost_normalized_cents: float = 0.0
    retries: int = 0
    errors: int = 0
    cache_hits: int = 0
    #: 实测系数（单位任务正常化成本 ÷ 锚的同一量）；样本不足时为 `None`
    measured_coefficient: Optional[float] = None
    #: 价格锚定系数（in/out 二元组）
    price_coefficient: Optional[Dict[str, float]] = None
    #: 压在真实 token 构成上的有效标量价格系数
    price_coefficient_effective: Optional[float] = None
    #: (measured - price_effective) / price_effective
    deviation: Optional[float] = None
    confidence: str = "insufficient"
    cost_basis: str = COST_BASIS_API
    note: str = ""

    @property
    def adequate(self) -> bool:
        return self.confidence == "adequate"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model,
            "samples": int(self.samples),
            "tasks": int(self.tasks),
            "tokens_in": int(self.tokens_in),
            "tokens_out": int(self.tokens_out),
            "cost_raw_cents": round(float(self.cost_raw_cents), 6),
            "cost_normalized_cents": round(float(self.cost_normalized_cents), 6),
            "retries": int(self.retries),
            "errors": int(self.errors),
            "cache_hits": int(self.cache_hits),
            "success_rate": (round(1.0 - self.errors / self.samples, 6)
                             if self.samples else None),
            "retries_per_call": (round(self.retries / self.samples, 6)
                                 if self.samples else None),
            "tokens_per_task": (round((self.tokens_in + self.tokens_out) / self.tasks, 6)
                                if self.tasks else None),
            "cents_per_task_raw": (round(self.cost_raw_cents / self.tasks, 6)
                                   if self.tasks else None),
            "cents_per_task_normalized": (
                round(self.cost_normalized_cents / self.tasks, 6) if self.tasks else None),
            "measured_coefficient": (None if self.measured_coefficient is None
                                     else round(self.measured_coefficient, 9)),
            "price_coefficient": (dict(self.price_coefficient)
                                  if self.price_coefficient else None),
            "price_coefficient_effective": (
                None if self.price_coefficient_effective is None
                else round(self.price_coefficient_effective, 9)),
            "deviation": (None if self.deviation is None else round(self.deviation, 9)),
            "confidence": self.confidence,
            "adequate": self.adequate,
            "cost_basis": self.cost_basis,
            "note": self.note,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "ModelCalibration":
        model = str(data.get("model") or "").strip()
        if not model:
            raise CalibrationError("逐模型校准条目缺少 model")
        return cls(
            model=model,
            samples=int(data.get("samples") or 0),
            tasks=int(data.get("tasks") or 0),
            tokens_in=int(data.get("tokens_in") or 0),
            tokens_out=int(data.get("tokens_out") or 0),
            cost_raw_cents=float(data.get("cost_raw_cents") or 0.0),
            cost_normalized_cents=float(data.get("cost_normalized_cents") or 0.0),
            retries=int(data.get("retries") or 0),
            errors=int(data.get("errors") or 0),
            cache_hits=int(data.get("cache_hits") or 0),
            measured_coefficient=_opt_float(data.get("measured_coefficient")),
            price_coefficient=(dict(data["price_coefficient"])
                               if isinstance(data.get("price_coefficient"), Mapping)
                               else None),
            price_coefficient_effective=_opt_float(
                data.get("price_coefficient_effective")),
            deviation=_opt_float(data.get("deviation")),
            confidence=str(data.get("confidence") or "insufficient"),
            cost_basis=str(data.get("cost_basis") or COST_BASIS_API),
            note=str(data.get("note") or ""),
        )


@dataclass
class CostCalibration:
    """成本系数校准件（版本化 + 可追溯；**只含实测得到的数字**）"""

    version: str = MEASURED_VERSION
    method: str = METHOD_L2_RUN
    created_at: str = ""
    anchor_model: str = ""
    #: **仅**包含 `confidence="adequate"` 的模型 → 只有这些能真正生效
    models: Dict[str, ModelCalibration] = field(default_factory=dict)
    #: 样本不足 / 不可用而**未生效**的模型（披露用，不含系数）
    insufficient_models: List[str] = field(default_factory=list)
    caseset_sha256: str = ""
    sample_scope: str = ""
    path: str = ""
    disclosures: List[str] = field(default_factory=list)

    @property
    def measured_models(self) -> List[str]:
        """**参与实效的**实测模型清单（排序稳定；**不含锚模型**）

        锚模型的实测系数恒 `1.0`（它是分母），把它列进"已实测模型"只会让人
        误以为"锚也校准过了"。锚仍保留在 `models` 中（便于审计"分母样本量"），
        但对外清单里剔除。
        """
        return sorted(name for name in self.models if name != self.anchor_model)

    @property
    def calibrated(self) -> bool:
        """是否"完整实测"：路径 A + 至少一个达标**参与**模型 + 版本 `measured.v1`"""
        return bool(self.measured_models) and self.version == MEASURED_VERSION

    @property
    def partial(self) -> bool:
        """是否降级部分校准（**必须在报告中显式声明非完整实测**）"""
        return bool(self.measured_models) and self.version == MEASURED_PARTIAL_VERSION

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": CALIBRATION_SCHEMA,
            "version": self.version,
            "method": self.method,
            "created_at": self.created_at,
            "anchor_model": self.anchor_model,
            "calibrated": self.calibrated,
            "partial": self.partial,
            "measured_models": self.measured_models,
            "insufficient_models": sorted(self.insufficient_models),
            "caseset_sha256": self.caseset_sha256,
            "sample_scope": self.sample_scope,
            "min_samples_per_model": MIN_COST_SAMPLES_PER_MODEL,
            "models": {k: v.to_dict() for k, v in sorted(self.models.items())},
            "disclosures": list(self.disclosures),
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "CostCalibration":
        if not isinstance(data, Mapping):
            raise CalibrationError("校准件根必须是对象")
        schema = str(data.get("schema") or "")
        if schema and schema != CALIBRATION_SCHEMA:
            raise CalibrationError(
                f"未知校准件 schema: {schema!r}（期望 {CALIBRATION_SCHEMA}）")
        version = str(data.get("version") or "")
        if version and version not in (MEASURED_VERSION, MEASURED_PARTIAL_VERSION):
            raise CalibrationError(
                f"未知校准件版本: {version!r}"
                f"（期望 {MEASURED_VERSION} 或 {MEASURED_PARTIAL_VERSION}）")
        raw_models = data.get("models") or {}
        if not isinstance(raw_models, Mapping):
            raise CalibrationError("校准件 models 必须是对象")
        models: Dict[str, ModelCalibration] = {}
        for key, value in raw_models.items():
            if not isinstance(value, Mapping):
                raise CalibrationError(f"校准件模型条目非法: {key!r}")
            item = ModelCalibration.from_dict({**dict(value), "model": str(
                value.get("model") or key)})
            models[item.model] = item
        return cls(
            version=version or MEASURED_VERSION,
            method=str(data.get("method") or ""),
            created_at=str(data.get("created_at") or ""),
            anchor_model=str(data.get("anchor_model") or ""),
            models=models,
            insufficient_models=[str(x) for x in (data.get("insufficient_models") or [])],
            caseset_sha256=str(data.get("caseset_sha256") or ""),
            sample_scope=str(data.get("sample_scope") or ""),
            disclosures=[str(x) for x in (data.get("disclosures") or [])],
        )


def _opt_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as e:
        raise CalibrationError(f"期望数值，得到 {value!r}") from e


# ════════════════════════════════════════════════════════════
#  样本行 → 统计量（**唯一数字入口**）
# ════════════════════════════════════════════════════════════


@dataclass
class SampleRow:
    """一条可溯源的实测成本记录（事件流一行 或 导入 CSV 一行）"""

    model: str
    task_id: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_raw_cents: float = 0.0
    cost_normalized_cents: float = 0.0
    retries: int = 0
    error: str = ""
    cache_hit: bool = False
    ts: str = ""
    source_path: str = ""
    source_line: int = 0

    @property
    def provenance(self) -> str:
        """溯源串（报告里每个数字都带它）"""
        if self.source_path and self.source_line:
            return f"{self.source_path}:{self.source_line}"
        return self.source_path or "unknown"


@dataclass
class MeasuredSamples:
    """一个模型的样本聚合（含稳健统计；全部由样本行导出）"""

    model: str
    rows: List[SampleRow] = field(default_factory=list)

    # ── 聚合量 ──────────────────────────────────────────────

    @property
    def samples(self) -> int:
        return len(self.rows)

    @property
    def tasks(self) -> int:
        """任务数（`task_id` 去重；无 `task_id` 的行各算一个独立任务并披露）"""
        ids = {r.task_id for r in self.rows if r.task_id}
        anonymous = sum(1 for r in self.rows if not r.task_id)
        return len(ids) + anonymous

    @property
    def tokens_in(self) -> int:
        return sum(r.tokens_in for r in self.rows)

    @property
    def tokens_out(self) -> int:
        return sum(r.tokens_out for r in self.rows)

    @property
    def cost_raw_cents(self) -> float:
        return round(sum(r.cost_raw_cents for r in self.rows), 6)

    @property
    def cost_normalized_cents(self) -> float:
        return round(sum(r.cost_normalized_cents for r in self.rows), 6)

    @property
    def retries(self) -> int:
        return sum(r.retries for r in self.rows)

    @property
    def errors(self) -> int:
        return sum(1 for r in self.rows if r.error)

    @property
    def cache_hits(self) -> int:
        return sum(1 for r in self.rows if r.cache_hit)

    def cents_per_task_raw(self) -> Optional[float]:
        return (self.cost_raw_cents / self.tasks) if self.tasks else None

    def cents_per_task_normalized(self) -> Optional[float]:
        return (self.cost_normalized_cents / self.tasks) if self.tasks else None

    def _costs(self) -> List[float]:
        return sorted(r.cost_raw_cents for r in self.rows)

    def mean_cost_cents(self) -> Optional[float]:
        values = self._costs()
        return (sum(values) / len(values)) if values else None

    def median_cost_cents(self) -> Optional[float]:
        values = self._costs()
        if not values:
            return None
        mid = len(values) // 2
        if len(values) % 2:
            return values[mid]
        return (values[mid - 1] + values[mid]) / 2.0

    def p95_cost_cents(self) -> Optional[float]:
        """p95（样本 <100 取最大值，与 `EvalReport.p99_wall_ms` 同款保守口径）"""
        values = self._costs()
        if not values:
            return None
        if len(values) < 100:
            return values[-1]
        return values[min(len(values) - 1, int(0.95 * len(values)))]

    @classmethod
    def from_rows(cls, model: str, rows: Iterable[SampleRow]) -> "MeasuredSamples":
        return cls(model=model, rows=[r for r in rows if r.model == model])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model": self.model, "samples": self.samples, "tasks": self.tasks,
            "tokens_in": self.tokens_in, "tokens_out": self.tokens_out,
            "cost_raw_cents": self.cost_raw_cents,
            "cents_per_task_raw": _round_opt(self.cents_per_task_raw()),
            "mean_cost_cents": _round_opt(self.mean_cost_cents()),
            "median_cost_cents": _round_opt(self.median_cost_cents()),
            "p95_cost_cents": _round_opt(self.p95_cost_cents()),
            "success_rate": (round(1.0 - self.errors / self.samples, 6)
                             if self.samples else None),
            "retries_per_call": (round(self.retries / self.samples, 6)
                                 if self.samples else None),
            "cache_hit_rate": (round(self.cache_hits / self.samples, 6)
                               if self.samples else None),
            "provenance": sorted({r.provenance for r in self.rows})[:5],
        }


def _round_opt(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(float(value), 6)


def group_samples(rows: Iterable[SampleRow]) -> Dict[str, MeasuredSamples]:
    """按模型分组（**不做任何补零/插值**；无样本的模型不出现）"""
    grouped: Dict[str, List[SampleRow]] = {}
    for row in rows:
        grouped.setdefault(row.model, []).append(row)
    return {name: MeasuredSamples(model=name, rows=items)
            for name, items in sorted(grouped.items())}


# ════════════════════════════════════════════════════════════
#  系数计算
# ════════════════════════════════════════════════════════════


def measured_coefficient(samples: MeasuredSamples,
                         anchor: MeasuredSamples) -> Optional[float]:
    """`measured_coef(model) = 单位任务正常化成本(model) / 单位任务正常化成本(anchor)`

    **口径说明（为什么用 raw）**：归一字段是用**待校准的系数**算出来的，
    直接拿它反推系数会自洽但无信息（历史事件由旧系数写成）。故此处取
    `cost_raw_cents / 任务数`（真实金额）作为分子分母 —— 归一化的意义是
    "把不同单价压到同一尺度"，而校准要量的正是**这个尺度本身**。

    返回 ``None``（不猜）：任一模型无任务（分母为 0）或缺样本时不给数字。
    """
    num = samples.cents_per_task_raw()
    den = anchor.cents_per_task_raw()
    if num is None or den is None or den <= 0:
        return None
    return num / den


def effective_price_coefficient(price_coefficient: Mapping[str, Any],
                                tokens_in: int, tokens_out: int) -> Optional[float]:
    """把 `{in, out}` 二元组压成**本批样本真实构成**下的标量

    ``(in_tokens×k_in + out_tokens×k_out) / (in_tokens + out_tokens)``

    **为什么必须压缩**：`coefficient()` 给的是两维（输入/输出单价不同），
    实测给的是一维经验比。不做加权压缩就相减，会把"输入输出构成差异"
    误记为"模型成本偏差"。权重取**锚模型**的 token 构成（同尺度、可复现）。
    """
    total = int(tokens_in) + int(tokens_out)
    if total <= 0:
        return None
    try:
        k_in = float(price_coefficient.get("in", 1.0))
        k_out = float(price_coefficient.get("out", 1.0))
    except (TypeError, ValueError):
        return None
    return (int(tokens_in) * k_in + int(tokens_out) * k_out) / total


def deviation(measured: Optional[float], price_effective: Optional[float]) -> Optional[float]:
    """偏差率 `(measured - price) / price`；分母为 0/缺 → ``None``（不编造）"""
    if measured is None or price_effective is None or price_effective == 0:
        return None
    return (measured - price_effective) / price_effective


def confidence_for(samples: int,
                   *, method: str = METHOD_L2_RUN) -> str:
    """置信度分级（**机械判定，不设主观档**）

    * `adequate`：样本 ≥ `MIN_COST_SAMPLES_PER_MODEL` **且**来自路径 A 实跑；
    * `insufficient`：样本 < 阈值 → 只披露不结论（不给系数、不进工件）；
    * `provisional`：样本达标但**来源是降级路径**（离线重放/导入 CSV）→
      可披露偏差，**不作为系数替换依据**。
    """
    if int(samples) < MIN_COST_SAMPLES_PER_MODEL:
        return "insufficient"
    if method in (METHOD_L2_RUN, ""):
        return "adequate"
    return "provisional"


def build_calibration(rows: Sequence[SampleRow], *, anchor_model: str,
                      method: str, caseset_sha256: str = "",
                      sample_scope: str = "",
                      price_coefficient_lookup: Any = None,
                      created_at: str = "",
                      cost_basis: str = COST_BASIS_API) -> Tuple[CostCalibration, Dict[str, Any]]:
    """由样本行构建校准件 + 偏差表

    Args:
        rows: 全部样本行（**调用方负责来源可信**；本函数不联网、不读盘）。
        anchor_model: 锚模型名（分母）。
        method: 见 `METHOD_*`（决定版本与置信度）。
        price_coefficient_lookup: `model -> {"in","out","source"}`；
            缺省用 `utc.coefficient()`（惰性导入，避免循环依赖）。
        cost_basis: `api_price` / `local_machine_time`（本地模型必须传后者）。

    Returns:
        ``(校准件, 偏差表)``。校准件的 `models` **只含达标模型**；
        样本不足者在 `insufficient_models` 中披露。
    """
    lookup = price_coefficient_lookup or _default_price_coefficient
    grouped = group_samples(rows)
    anchor_samples = grouped.get(anchor_model)
    version = (MEASURED_VERSION if method == METHOD_L2_RUN
               else MEASURED_PARTIAL_VERSION)

    models: Dict[str, ModelCalibration] = {}
    insufficient: List[str] = []
    deviations: List[Dict[str, Any]] = []

    anchor_tokens = ((anchor_samples.tokens_in, anchor_samples.tokens_out)
                     if anchor_samples is not None else (0, 0))
    if anchor_samples is None:
        # 锚模型**一行样本都没有**：分母不存在 → 所有实测系数都算不出来。
        # 如实登记一行（samples=0、无系数），而不是让锚"消失"在表里。
        anchor_price = _safe_price_lookup(lookup, anchor_model)
        missing_anchor = _entry(
            anchor_model, MeasuredSamples(model=anchor_model), anchor_price,
            None, None, None, "insufficient", cost_basis,
            note=("锚模型在本批样本中**无任何记录** → 分母不存在，"
                  "全部实测系数不可计算（只披露不结论）"))
        deviations.append(_deviation_row(missing_anchor, is_anchor=True))
    for name, samples in grouped.items():
        price = _safe_price_lookup(lookup, name)
        p_in, p_out = anchor_tokens
        price_eff = effective_price_coefficient(price, p_in, p_out) if price else None
        conf = confidence_for(samples.samples, method=method)

        if name == anchor_model:
            # 锚模型实测系数恒 1.0（自比）：**不参与偏差表**，否则等于自己跟自己比。
            # 但锚自身样本不足时它同样"不达标"——**不放进 models**（否则会给出一张
            # 与实际不符的"生效模型清单"）。
            entry = _entry(name, samples, price, 1.0, 1.0, 0.0, conf, cost_basis,
                           note="锚模型：实测系数恒 1.0（自比），偏差恒 0，"
                                "不参与点名；**锚自身系数恒为 1.0，"
                                "不需要（也不应）替换**")
            deviations.append(_deviation_row(entry, is_anchor=True))
        else:
            measured = (measured_coefficient(samples, anchor_samples)
                        if anchor_samples is not None else None)
            anchor_ok = (anchor_samples is not None
                         and anchor_samples.samples >= MIN_COST_SAMPLES_PER_MODEL)
            if conf == "insufficient" or measured is None or not anchor_ok:
                # 只披露不结论：**不给实测系数**（置 None 而非照给）。
                # 判据有三条，任一不满足都不得生效：
                #   ① 该模型样本 < 阈值；② 分母（锚）样本 < 阈值；③ 算不出比值。
                why = []
                if conf == "insufficient":
                    why.append(f"样本 {samples.samples} < "
                               f"{MIN_COST_SAMPLES_PER_MODEL}")
                if not anchor_ok:
                    anchor_n = (anchor_samples.samples
                                if anchor_samples is not None else 0)
                    why.append(f"锚模型样本 {anchor_n} < "
                               f"{MIN_COST_SAMPLES_PER_MODEL}（分母不达标）")
                if measured is None and anchor_ok and conf != "insufficient":
                    why.append("单位任务成本比不可计算（分母为 0）")
                entry = _entry(name, samples, price, None, price_eff, None,
                               "insufficient", cost_basis,
                               note="；".join(why) + "：**只披露不结论**（不给实测系数）")
            else:
                entry = _entry(name, samples, price, measured, price_eff,
                               deviation(measured, price_eff), conf, cost_basis)
            deviations.append(_deviation_row(entry))

        if entry.confidence == "insufficient":
            insufficient.append(name)
        else:
            models[name] = entry

    if anchor_samples is None and anchor_model not in insufficient:
        # 锚无样本 → 同时进入"未生效"清单（**不放进 models**，否则会自称已生效）
        insufficient.append(anchor_model)

    calibration = CostCalibration(
        version=version, method=method,
        created_at=created_at or time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        anchor_model=anchor_model, models=models,
        insufficient_models=insufficient, caseset_sha256=caseset_sha256,
        sample_scope=sample_scope,
        disclosures=_disclosures(method=method, anchor=anchor_model,
                                 inadequate=insufficient,
                                 anchor_samples=(anchor_samples.samples
                                                 if anchor_samples is not None else 0)),
    )
    table = {
        "schema": "cost_coefficient_deviation.v1",
        "anchor_model": anchor_model,
        "method": method,
        "version": version,
        "min_samples_per_model": MIN_COST_SAMPLES_PER_MODEL,
        "anchor_tokens": {"in": anchor_tokens[0], "out": anchor_tokens[1]},
        "total_sample_rows": len(rows),
        "models_in_sample": sorted(grouped),
        "rows": deviations,
        "measured_models": calibration.measured_models,
        "insufficient_models": sorted(insufficient),
        "provenance": sorted({r.provenance for r in rows})[:20],
    }
    return calibration, table


def _disclosures(*, method: str, anchor: str, inadequate: Sequence[str],
                 anchor_samples: int) -> List[str]:
    out = [
        "校准件只含 `confidence` 达标的模型；样本不足者只披露不结论（不给系数）",
        "历史口径不追溯：旧 `cost` 事件保留写入时的系数与来源字段，聚合永不重算",
        f"锚模型 `{anchor}` 的实测系数恒 1.0（自比），不参与偏差表",
    ]
    if anchor_samples < MIN_COST_SAMPLES_PER_MODEL:
        out.append(f"**锚模型自身样本 {anchor_samples} < "
                   f"{MIN_COST_SAMPLES_PER_MODEL}**：分母不达标 → 全部实测系数不可用")
    if method != METHOD_L2_RUN:
        out.append("**未完成完整实测校准，结论置信度受限**：本件来源为"
                   f"`{method}`（离线重放 / 导入 CSV），**不是在 L2 Core-50 上"
                   "对多模型的同批实跑**；版本号 `measured.partial.v1` 即为此意，"
                   "**不作为系数替换依据**")
    if inadequate:
        out.append(f"样本不足未生效的模型（{len(inadequate)} 个）：{sorted(inadequate)}")
    return out


def _entry(name: str, samples: MeasuredSamples, price: Optional[Dict[str, Any]],
           measured: Optional[float], price_eff: Optional[float],
           dev: Optional[float], conf: str, cost_basis: str,
           note: str = "") -> ModelCalibration:
    return ModelCalibration(
        model=name, samples=samples.samples, tasks=samples.tasks,
        tokens_in=samples.tokens_in, tokens_out=samples.tokens_out,
        cost_raw_cents=samples.cost_raw_cents,
        cost_normalized_cents=samples.cost_normalized_cents,
        retries=samples.retries, errors=samples.errors,
        cache_hits=samples.cache_hits,
        measured_coefficient=measured,
        price_coefficient=({"in": float(price.get("in", 1.0)),
                            "out": float(price.get("out", 1.0))} if price else None),
        price_coefficient_effective=price_eff, deviation=dev,
        confidence=conf, cost_basis=cost_basis, note=note)


def _deviation_row(entry: ModelCalibration, *, is_anchor: bool = False) -> Dict[str, Any]:
    row = entry.to_dict()
    row["is_anchor"] = bool(is_anchor)
    row["provenance"] = []
    return row


def _safe_price_lookup(lookup: Any, model: str) -> Optional[Dict[str, Any]]:
    try:
        price = lookup(model)
    except Exception as e:  # noqa: BLE001 价格表不可用 → 该模型无价格对照（如实置 None）
        logger.debug("价格系数查询失败 model=%s: %s", model, e)
        return None
    return dict(price) if isinstance(price, Mapping) else None


def _default_price_coefficient(model: str) -> Dict[str, Any]:
    from agent.observability import utc as U
    return U.price_ratio_coefficient(model)


# ════════════════════════════════════════════════════════════
#  落盘 / 加载
# ════════════════════════════════════════════════════════════


def artifact_path(path: Optional[str] = None) -> str:
    """校准件路径（显式参数 > `CP_UTC_CALIBRATION_FILE` > 默认落点）"""
    if path:
        return str(path)
    env = str(os.getenv(ENV_ARTIFACT) or "").strip()
    return env or DEFAULT_ARTIFACT_PATH


def write_artifact(calibration: CostCalibration, path: Optional[str] = None) -> str:
    """写出校准件（**调用方显式指定路径时行为可预期；不改历史事件**）"""
    target = artifact_path(path)
    parent = os.path.dirname(os.path.abspath(target))
    if parent:
        os.makedirs(parent, exist_ok=True)
    payload = calibration.to_dict()
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n")
    return target


def load_artifact(path: Optional[str] = None, *, required: bool = False
                  ) -> Optional[CostCalibration]:
    """加载校准件

    * 文件不存在 → ``None``（**不创建**、不报错：未校准是合法状态）；
    * 内容非法 → ``None`` + 告警（**拒绝坏数据**，不静默回落成半截系数）；
    * `required=True` 时文件缺失/非法抛 `CalibrationError`（脚本要"必须读到"时用）。
    """
    target = artifact_path(path)
    if not os.path.exists(target):
        if required:
            raise CalibrationError(f"校准件不存在: {target}")
        return None
    try:
        with open(target, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError) as e:
        logger.warning("校准件读取/解析失败（回落价格锚定）: %s: %s", target, e)
        if required:
            raise CalibrationError(f"校准件非法: {target}: {e}") from e
        return None
    try:
        calibration = CostCalibration.from_dict(data)
    except CalibrationError as e:
        logger.warning("校准件非法（回落价格锚定）: %s: %s", target, e)
        if required:
            raise
        return None
    calibration.path = target
    return calibration


def stale_reason(calibration: Optional[CostCalibration], *, anchor_model: str,
                 caseset_sha256: str = "") -> str:
    """校准件是否过期（**返回原因串，空串 = 有效**）

    过期情形（任一命中即过期，防止静默失真）：

    1. 锚模型与当前解析值不一致 → 分母换了，历史系数作废；
    2. 校准件带用例集哈希且与当前哈希不一致 → 量尺换了；
    3. 校准件记录为空（无生效模型）。
    """
    if calibration is None:
        return "no_artifact"
    if not calibration.models:
        return "empty_artifact"
    if calibration.anchor_model and anchor_model and \
            calibration.anchor_model != anchor_model:
        return (f"anchor_changed:{calibration.anchor_model}->{anchor_model}")
    if calibration.caseset_sha256 and caseset_sha256 and \
            calibration.caseset_sha256 != caseset_sha256:
        return "caseset_changed"
    return ""


def measured_coefficients(calibration: Optional[CostCalibration], *,
                          anchor_model: str, caseset_sha256: str = ""
                          ) -> Dict[str, Dict[str, float]]:
    """从校准件抽出**可生效**的 `model -> {in, out}` 映射

    实测系数是一维标量（单位任务成本比），而 `coefficient()` 需要 `{in, out}`。
    落地规则（**单一系数、双维同值**）：把实测标量**同时**写入 `in`/`out`
    —— 因为"单位任务成本比"本身就是对全部 token 的加权结果，拆分到两维
    需要额外的 in/out 分列样本，而当前口径只保证总量可信。

    校准件过期 → 返回空映射（**逐模型回落价格系数**）。
    """
    if calibration is None or stale_reason(calibration, anchor_model=anchor_model,
                                           caseset_sha256=caseset_sha256):
        return {}
    out: Dict[str, Dict[str, float]] = {}
    for name, item in calibration.models.items():
        if name == anchor_model or item.measured_coefficient is None:
            # 锚模型不参与 `coefficient()` 替换（它恒为 1.0，且是分母）
            continue
        out[name] = {"in": float(item.measured_coefficient),
                     "out": float(item.measured_coefficient)}
    return out


__all__ = [
    "CALIBRATION_SCHEMA", "MEASURED_VERSION", "MEASURED_PARTIAL_VERSION",
    "PRICE_ANCHOR_VERSION", "COEFFICIENT_SOURCES", "MIN_COST_SAMPLES_PER_MODEL",
    "METHOD_L2_RUN", "METHOD_OFFLINE_REPLAY", "METHOD_IMPORTED_CSV", "METHOD_MIXED",
    "DEFAULT_ARTIFACT_PATH", "ENV_ARTIFACT", "COST_BASIS_API", "COST_BASIS_LOCAL",
    "CalibrationError", "ModelCalibration", "CostCalibration", "SampleRow",
    "MeasuredSamples", "group_samples", "measured_coefficient",
    "effective_price_coefficient", "deviation", "confidence_for",
    "build_calibration", "artifact_path", "write_artifact", "load_artifact",
    "stale_reason", "measured_coefficients",
]
