"""成本系数校准脚本（TASK-S7-03）——路径 A 实跑 / 路径 B 降级重放（+ 导入 CSV）

实验设计与口径见 `docs/zh/成本系数校准方案.md`；核心算法在
`agent/observability/cost_calibration.py`（本脚本只做**取数 + 编排 + 出报告**，
不重复实现系数计算）。

用法::

    # 0) 凭证探测（**先跑这个**：决定走哪条路径；不花一分钱）
    python scripts/calibrate_cost_coefficients.py --probe-credentials

    # 1) 路径 A（有凭证）：在 L2 Core-50 上对多模型实跑
    python scripts/calibrate_cost_coefficients.py --path a \\
        --models gpt-4o-mini,gpt-3.5-turbo --max-cases 50 \\
        --max-cost-cents 200 --report 偏差分析报告.md --write

    # 2) 路径 B（无凭证，降级）：离线重放历史 cost 事件（+ 可选导入 CSV）
    python scripts/calibrate_cost_coefficients.py --path b \\
        --import-csv my_measurements.csv --report 偏差分析报告.md

    # 3) 自动选路（默认）：有凭证走 A，否则走 B（并在报告中如实声明）
    python scripts/calibrate_cost_coefficients.py --path auto

三条硬纪律（**代码层面保证，不靠自觉**）：

1. **不得编造数字**：所有样本要么来自事件流文件（带 `路径:行号`），要么来自导入 CSV
   （带 `文件:行号`）；无样本 → 报告写"暂无"，不给任何数字。
2. **样本 <20 只披露不结论**：由 `confidence_for()` 机械判定；不足者**不进**校准件。
3. **降级必须显式声明**：路径 B 的工件版本为 `measured.partial.v1` 且 `calibrated=False`，
   报告首段即写"**未完成完整实测校准，结论置信度受限**"。
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from agent.observability import cost_calibration as CC  # noqa: E402
from agent.observability import utc as U  # noqa: E402
from agent.observability.events import EV_COST, iter_events  # noqa: E402

#: 凭证探测的候选环境变量（**按优先级**；值为占位符的一律不作为凭证）
CREDENTIAL_ENVS: Tuple[str, ...] = (
    "DEEPSEEK_API_KEY", "LLM_API_KEY", "OPENAI_API_KEY", "ZHIPU_API_KEY",
    "GLM_API_KEY", "ANTHROPIC_API_KEY", "DASHSCOPE_API_KEY", "QWEN_API_KEY",
    "MOONSHOT_API_KEY", "SILICONFLOW_API_KEY",
)

#: 明显的占位符特征（**命中即判为无凭证**，避免"env 里有名字"被当成有凭证）
PLACEHOLDER_MARKERS: Tuple[str, ...] = (
    "sk-test", "your-api-key", "your_api_key", "changeme", "placeholder",
    "xxx", "<", ">", "dummy", "example",
)

#: 单次提示的 token 上限（**控预算**：输出越长越贵）
DEFAULT_MAX_TOKENS = 1024

#: 路径 A 写入的 `cost` 事件 `task_id` 前缀（回读时据此**排除历史噪声**）
CALIB_TASK_PREFIX = "calib:"


# ════════════════════════════════════════════════════════════
#  一、凭证探测（路径选择的依据）
# ════════════════════════════════════════════════════════════


def _read_env_file(path: str) -> Dict[str, str]:
    """极简 `.env` 解析（**只读关键项，不修改进程环境**）

    本项目把配置放在仓库根的 `.env`（未被任何模块自动加载），若只看 `os.environ`
    会得出"没有端点/没有密钥"的错误结论。故这里**只读取不注入**：
    解析出的值仅用于凭证状况披露，绝不 `os.environ[...] = ...`。
    """
    out: Dict[str, str] = {}
    if not path or not os.path.exists(path):
        return out
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                if key:
                    out[key] = value.strip().strip('"').strip("'")
    except OSError:
        return out
    return out


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _env_candidates() -> List[str]:
    """`.env` 候选路径（**顺序即优先级**）

    并行会话的隔离 worktree（`.worktrees/<id>/`）里 `.env` 通常是空的，
    真实凭证在主工作区；故把"主工作区根"一并作为候选，否则会得出
    "没有端点/没有密钥"的错误结论。`CP_ENV_FILE` 可显式指定。
    """
    candidates: List[str] = []
    explicit = str(os.getenv("CP_ENV_FILE") or "").strip()
    if explicit:
        candidates.append(explicit)
    candidates.append(os.path.join(_repo_root(), ".env"))
    # worktree 场景：`<repo>/.worktrees/<id>` → `<repo>`
    parent = os.path.dirname(_repo_root())
    if os.path.basename(parent) == ".worktrees":
        candidates.append(os.path.join(os.path.dirname(parent), ".env"))
    return candidates


def _env_lookup(name: str) -> Tuple[str, str]:
    """取值：进程环境 > `CP_ENV_FILE` > 本 worktree `.env` > 主工作区 `.env`

    返回 ``(值, 来源)``；**只读不注入**（绝不 `os.environ[...] = ...`）。
    """
    raw = str(os.environ.get(name) or "").strip()
    if raw:
        return raw, "os.environ"
    for candidate in _env_candidates():
        value = _read_env_file(candidate).get(name, "")
        if value:
            return value, candidate
    return "", ""


def find_credentials(env: Optional[Mapping[str, str]] = None) -> Dict[str, Dict[str, str]]:
    """扫描候选密钥，返回 ``{变量名: {value, verdict, reason, origin}}``

    ``verdict`` ∈ ``{"valid","placeholder","empty"}``：
    **占位符与空值都不算凭证**（判定只看值本身，不联网）。
    取值来源可以是进程环境，也可以是仓库根 `.env`（**只读**，见 `_env_lookup`）。
    """
    out: Dict[str, Dict[str, str]] = {}
    for name in CREDENTIAL_ENVS:
        if env is not None:
            raw, origin = str(env.get(name) or "").strip(), "env(mapping)"
        else:
            raw, origin = _env_lookup(name)
        if not raw:
            out[name] = {"value": "", "verdict": "empty", "reason": "未设置",
                         "origin": origin}
            continue
        lowered = raw.lower()
        hit = next((m for m in PLACEHOLDER_MARKERS if m in lowered), "")
        if hit:
            out[name] = {"value": _mask(raw), "verdict": "placeholder",
                         "reason": f"命中占位符特征 {hit!r}", "origin": origin}
        elif len(raw) < 16:
            out[name] = {"value": _mask(raw), "verdict": "placeholder",
                         "reason": f"长度 {len(raw)} < 16，不像真实密钥",
                         "origin": origin}
        else:
            out[name] = {"value": _mask(raw), "verdict": "valid",
                         "reason": "形态合法（未联网验证）", "origin": origin}
    return out


def _mask(raw: str) -> str:
    """脱敏显示（**绝不打印完整密钥**）"""
    if len(raw) <= 8:
        return "*" * len(raw)
    return f"{raw[:4]}…{raw[-4:]}(len={len(raw)})"


def probe_endpoint(base_url: str, api_key: str, timeout: float = 15.0
                   ) -> Dict[str, Any]:
    """对端点发一次最小请求（`GET /models`）判读凭证是否真的可用

    **不产生计费**（只列模型，不推理）。这是"有凭证"与"env 里有变量名"的区别所在。
    网络不可达 → ``reachable=False``（**不等于凭证无效**，如实区分）。
    """
    url = base_url.rstrip("/") + "/models"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {api_key}"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 固定 https 端点
            body = resp.read(400).decode("utf-8", "replace")
            return {"url": url, "reachable": True, "http_status": int(resp.status),
                    "usable": 200 <= int(resp.status) < 300, "body_head": body[:200]}
    except urllib.error.HTTPError as e:
        try:
            body = e.read(300).decode("utf-8", "replace")
        except Exception:  # noqa: BLE001 响应体读不出不影响结论
            body = ""
        return {"url": url, "reachable": True, "http_status": int(e.code),
                "usable": False, "body_head": body[:200],
                "reason": ("凭证被拒绝（401/403）" if e.code in (401, 403)
                           else f"HTTP {e.code}")}
    except Exception as e:  # noqa: BLE001 网络层失败 → 与"凭证无效"区分
        return {"url": url, "reachable": False, "http_status": None, "usable": False,
                "reason": f"{type(e).__name__}: {e}"}


def credential_status(models: Sequence[str]) -> Dict[str, Any]:
    """凭证总况（**决定路径 A/B 的唯一依据**）

    判定为"可用"需同时满足：形态合法 **且** 端点探测返回 2xx。
        * 只设置了 `LLM_API_KEY` 而端点不可知 → 无法探测 → 记为不可用（如实）。
        * 端点与密钥都支持从仓库根 `.env` **只读**取用（见 `_env_lookup`）。
    """
    scan = find_credentials()
    base_url, base_url_origin = _env_lookup("LLM_BASE_URL")
    providers: List[Dict[str, Any]] = []
    for name, item in scan.items():
        entry: Dict[str, Any] = {"env": name, **item, "probed": False}
        if item["verdict"] == "valid" and base_url:
            key, _ = _env_lookup(name)
            probe = probe_endpoint(base_url, key)
            entry.update({"probed": True, **probe})
        elif item["verdict"] == "valid":
            entry["reason"] += "；且无可探测端点（未设置 LLM_BASE_URL）"
        providers.append(entry)
    usable = [p for p in providers if p.get("usable")]
    return {
        "base_url": base_url,
        "base_url_origin": base_url_origin,
        "providers": providers,
        "usable_providers": [p["env"] for p in usable],
        "usable": bool(usable),
        "multi_model_usable": bool(usable) and len(models) >= 2,
        "models_requested": list(models),
        "note": ("可用性 = 形态合法 **且** 端点探测 2xx（探测不产生计费）；"
                 "占位符与空值一律不算凭证；`.env` 只读取不注入进程环境"),
    }


# ════════════════════════════════════════════════════════════
#  二、路径 B：离线重放事件流
# ════════════════════════════════════════════════════════════


def replay_event_rows(models: Sequence[str], *, events_dir: str = ""
                      ) -> Tuple[List[CC.SampleRow], Dict[str, Any]]:
    """**离线重放**历史 `cost` 事件 → 样本行（带 `路径:行号` 溯源）

    唯一数据源＝事件流（裁定 D）。这里**逐行**读取而非用聚合接口，是为了留下
    **行级溯源**：报告里的每个数字都能定位到具体文件的具体行。

    去重按 `event_id`（与 `events.iter_events` 同款保证：重放不重复计数）——
    先扫一遍收集候选行与 id，再按"首次出现"保留，**不做任何数值修补**。
    """
    directory = events_dir or str(os.getenv("CP_EVENTS_DIR") or "") or os.path.join(
        _repo_root(), "data", "events")
    wanted = set(models)
    files = sorted(_event_files(directory))
    candidates: List[Tuple[CC.SampleRow, str]] = []
    seen: set = set()
    duplicates = 0
    for path in files:
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as fh:
                for lineno, line in enumerate(fh, start=1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except (json.JSONDecodeError, ValueError):
                        continue
                    if not isinstance(data, dict) or str(data.get("type") or "") != EV_COST:
                        continue
                    payload = data.get("payload")
                    if not isinstance(payload, dict):
                        continue
                    model = str(payload.get("model") or "").strip()
                    if wanted and model not in wanted:
                        continue
                    event_id = str(data.get("event_id") or "")
                    if event_id and event_id in seen:
                        duplicates += 1
                        continue
                    if event_id:
                        seen.add(event_id)
                    candidates.append((CC.SampleRow(
                        model=model or "<empty>",
                        task_id=str(payload.get("task_id") or ""),
                        tokens_in=_as_int(payload.get("tokens_in")),
                        tokens_out=_as_int(payload.get("tokens_out")),
                        cost_raw_cents=_as_float(payload.get("cost_raw_cents")),
                        cost_normalized_cents=_as_float(
                            payload.get("cost_normalized_cents")),
                        retries=_as_int(payload.get("retries")),
                        error=str(payload.get("error") or ""),
                        cache_hit=bool(payload.get("cache_hit")),
                        ts=str(data.get("ts") or ""),
                        source_path=path, source_line=lineno), event_id))
        except OSError as e:
            return [row for row, _ in candidates], {
                "directory": directory, "files": files,
                "error": f"读取失败: {e}"}
    rows = [row for row, _ in candidates]
    meta = {
        "directory": directory,
        "files": [{"path": p, "bytes": os.path.getsize(p)} for p in files
                  if os.path.exists(p)],
        "dedupe": ("按 `event_id` 去重（与 `events.iter_events` 同款保证："
                   "重放不重复计数）"),
        "duplicate_rows_dropped": duplicates,
        "unique_event_ids": len(seen),
        "rows": len(rows),
        "models_without_samples": sorted(wanted - {r.model for r in rows}),
        "rows_without_task_id": sum(1 for r in rows if not r.task_id),
        "rows_with_error": sum(1 for r in rows if r.error),
    }
    return rows, meta


def _repo_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _event_files(directory: str) -> List[str]:
    from agent.observability.events import event_files
    return list(event_files(directory))


def _as_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _as_float(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


# ════════════════════════════════════════════════════════════
#  三、路径 B 补充：导入外部实测 CSV
# ════════════════════════════════════════════════════════════

#: CSV 必需列（缺任一列 → **整文件拒绝**，不做部分解析以免口径半截）
CSV_REQUIRED_COLUMNS: Tuple[str, ...] = ("model", "task_id", "tokens_in",
                                         "tokens_out", "cost_raw_cents")


def import_csv_rows(path: str, *, known_models: Sequence[str] = ()
                    ) -> Tuple[List[CC.SampleRow], Dict[str, Any]]:
    """导入人工实测 CSV（schema 见校准方案 §八）

    **行级纪律**：任何一行无法解析 → 跳过并计数（原因分布进报告）；
    `model` 既不在 `known_models` 也不在价格表 → **跳过并明确披露**（不静默丢弃）。
    缺必需列 → 抛 `CC.CalibrationError`（整文件拒绝）。
    """
    rows: List[CC.SampleRow] = []
    skipped: List[Dict[str, Any]] = []
    known = set(known_models) | set(U.model_costs().keys())
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        columns = tuple(reader.fieldnames or ())
        missing = [c for c in CSV_REQUIRED_COLUMNS if c not in columns]
        if missing:
            raise CC.CalibrationError(
                f"CSV 缺少必需列 {missing}（实际列：{list(columns)}）")
        for lineno, raw in enumerate(reader, start=2):  # 1 行为表头
            model = str(raw.get("model") or "").strip()
            if not model:
                skipped.append({"line": lineno, "reason": "model 为空"})
                continue
            if model not in known:
                skipped.append({"line": lineno, "reason": f"未知模型 {model!r}"
                                "（不在价格表也不在 --models）"})
                continue
            try:
                tokens_in = int(str(raw.get("tokens_in") or "0").strip() or 0)
                tokens_out = int(str(raw.get("tokens_out") or "0").strip() or 0)
                cost_raw = float(str(raw.get("cost_raw_cents") or "").strip())
            except (TypeError, ValueError) as e:
                skipped.append({"line": lineno, "reason": f"数值列非法: {e}"})
                continue
            rows.append(CC.SampleRow(
                model=model,
                task_id=str(raw.get("task_id") or "").strip(),
                tokens_in=tokens_in, tokens_out=tokens_out,
                cost_raw_cents=cost_raw,
                retries=_as_int(raw.get("retries")),
                error=str(raw.get("error") or "").strip(),
                cache_hit=_as_bool(raw.get("cache_hit")),
                ts=str(raw.get("ts") or "").strip(),
                source_path=path, source_line=lineno))
    by_model: Dict[str, int] = {}
    for row in rows:
        by_model[row.model] = by_model.get(row.model, 0) + 1
    meta = {
        "path": path,
        "columns": list(columns),
        "rows_total": len(rows) + len(skipped),
        "rows_used": len(rows),
        "rows_skipped": len(skipped),
        "skipped": skipped[:50],
        "by_model": dict(sorted(by_model.items())),
        "rows_without_ts": sum(1 for r in rows if not r.ts),
        "note": ("跳过行只计数不插值；**不补零、不臆造**（校准方案 §八）"),
    }
    return rows, meta


def _as_bool(value: Any) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on", "y", "t")


# ════════════════════════════════════════════════════════════
#  四、路径 A：在 L2 Core-50 上实跑
# ════════════════════════════════════════════════════════════


def build_model_solver(model: str, *, system_prompt: str = "",
                       max_tokens: int = DEFAULT_MAX_TOKENS,
                       record: bool = True, budget: Optional["Budget"] = None,
                       trace: Optional[List[Dict[str, Any]]] = None):
    """构造"被测模型解算器"（`Solver = Callable[[EvalCase], Mapping]`）

    每次调用：① 发一次真实请求；② 立即 `utc.record_cost()` 落 `cost` 事件
    （**唯一数据源**，裁定 D）；③ 记入 `trace`（供与 TraceStore 对账）。

    失败（网络/凭证/解析）→ 返回空映射（**如实标为未评测**，不伪造答案）。
    """
    from agent.model_router.adapters import OpenAIAdapter

    api_key, _ = _env_lookup("LLM_API_KEY")
    base_url, _ = _env_lookup("LLM_BASE_URL")
    adapter = OpenAIAdapter(model, api_key=api_key or None,
                            base_url=base_url or None)

    def solve(case: Any) -> Mapping[str, Any]:
        case_id = _case_id(case)
        prompt = _case_prompt(case, system_prompt=system_prompt)
        started = time.perf_counter()
        result = adapter.chat([{"role": "user", "content": prompt}],
                              max_tokens=max_tokens, temperature=0.0)
        duration_ms = (time.perf_counter() - started) * 1000.0
        if not result.get("success"):
            if trace is not None:
                trace.append({"case_id": case_id, "model": model,
                              "success": False, "error": str(result.get("error"))[:200],
                              "duration_ms": round(duration_ms, 3)})
            return {}
        usage = dict(result.get("usage") or {})
        tokens_in = _as_int(usage.get("prompt_tokens"))
        tokens_out = _as_int(usage.get("completion_tokens"))
        if record:
            U.record_cost(model=model, provider=str(result.get("provider") or ""),
                          source="calibration:l2_core50",
                          tokens_in=tokens_in, tokens_out=tokens_out,
                          task_id=f"{CALIB_TASK_PREFIX}{model}:{case_id}",
                          interaction_id=f"{CALIB_TASK_PREFIX}{model}:{case_id}",
                          duration_ms=duration_ms)
        if budget is not None:
            budget.charge(_as_float(U.normalize_cost(
                tokens_in=tokens_in, tokens_out=tokens_out, model=model
            )["cost_normalized_cents"]))
        if trace is not None:
            trace.append({"case_id": case_id, "model": model,
                          "success": True, "tokens_in": tokens_in,
                          "tokens_out": tokens_out,
                          "duration_ms": round(duration_ms, 3)})
        return _parse_answer(result.get("content") or "")

    return solve


def _case_id(case: Any) -> str:
    """用例标识（契约字段是 `id`；对 `case_id` 等别名保持宽容）"""
    for attr in ("id", "case_id"):
        value = getattr(case, attr, None)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _case_prompt(case: Any, *, system_prompt: str = "") -> str:
    """由评测用例渲染提示（**只用用例自带文本，不额外注入信息**）"""
    parts: List[str] = []
    if system_prompt:
        parts.append(system_prompt)
    title = str(getattr(case, "title", "") or "")
    scenario = str(getattr(case, "scenario", "") or "")
    parts.append(f"场景：{scenario}｜标题：{title}")
    payload = getattr(case, "input", None)
    if isinstance(payload, Mapping):
        parts.append(json.dumps(dict(payload), ensure_ascii=False)[:6000])
    elif isinstance(payload, str) and payload.strip():
        parts.append(payload.strip())
    for attr in ("prompt", "question", "instruction", "context"):
        value = getattr(case, attr, None)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    parts.append("只输出一个 JSON 对象作为答案，不要解释、不要 Markdown 代码块。")
    return "\n\n".join(parts)


def _parse_answer(content: str) -> Mapping[str, Any]:
    """从模型输出里取 JSON 答案（容忍 ```json 围栏；解析失败 → 空映射）"""
    text = str(content or "").strip()
    if not text:
        return {}
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            data = json.loads(text[start:end + 1])
        except (json.JSONDecodeError, ValueError):
            return {}
    return data if isinstance(data, dict) else {}


class Budget:
    """成本硬上限（**达到即停**：真实调用会花真钱）"""

    def __init__(self, limit_cents: Optional[float]) -> None:
        self.limit_cents = limit_cents
        self.spent_cents = 0.0
        self.exhausted = False
        self.charges = 0

    def charge(self, cents: float) -> None:
        self.spent_cents += max(0.0, float(cents or 0.0))
        self.charges += 1
        if self.limit_cents is not None and self.spent_cents >= self.limit_cents:
            self.exhausted = True

    def to_dict(self) -> Dict[str, Any]:
        return {"limit_cents": self.limit_cents,
                "spent_cents": round(self.spent_cents, 6),
                "charges": self.charges, "exhausted": self.exhausted}


def run_path_a(models: Sequence[str], *, max_cases: int, budget: Budget,
               caseset_path: str = "", trace: Optional[List[Dict[str, Any]]] = None
               ) -> Tuple[List[CC.SampleRow], Dict[str, Any]]:
    """路径 A：L2 Core-50 × 各参与模型实跑 → 样本行

    每条用例跑完后**从事件流回读**该模型的 `cost` 事件作为样本（**而不是直接用
    内存里的返回值**）——这样"报告里的数字"与"事件流里的记录"是同一份东西，
    可逐行溯源。
    """
    from agent.eval import cases as C
    from agent.eval import runner as R

    path = caseset_path or R.LAYER_CASESET_PATHS.get(C.LAYER_L2, "")
    case_set = C.load_case_set(path)
    cases = list(case_set.cases)
    if max_cases and max_cases > 0:
        cases = cases[:int(max_cases)]
    meta: Dict[str, Any] = {
        "caseset_path": path,
        "caseset_sha256": getattr(case_set, "caseset_sha256", ""),
        "cases_total": len(list(case_set.cases)),
        "cases_run": len(cases),
        "truncated_by_max_cases": len(cases) < len(list(case_set.cases)),
        "models": list(models),
        "per_model": [],
        "budget": None,
        "truncated_by_budget": False,
    }
    for model in models:
        if budget.exhausted:
            meta["truncated_by_budget"] = True
            meta["per_model"].append({"model": model, "skipped": "预算已达上限"})
            continue
        solver = build_model_solver(model, budget=budget, trace=trace)
        report = R.run_layer(C.LAYER_L2, case_set=C.EvalCaseSet(
            layer=C.LAYER_L2, cases=tuple(cases)), solver=solver,
            solver_name=f"model:{model}")
        meta["per_model"].append({
            "model": model,
            "assessed": report.assessed, "unassessed": report.unassessed,
            "passed": report.passed, "pass_rate": report.pass_rate,
            "p99_wall_ms": report.p99_wall_ms(),
            "by_scenario": report.by_scenario(),
            "verdict_counts": report.counts(),
        })
    # 从事件流回读（唯一数据源）
    rows: List[CC.SampleRow] = []
    for model in list(models) + [U.resolve_anchor_model()[0]]:
        model_rows, _ = replay_event_rows([model])
        rows.extend([r for r in model_rows if r.source_path and _is_calibration_row(r)])
    meta["budget"] = budget.to_dict()
    meta["event_rows_read_back"] = len(rows)
    return rows, meta


def _is_calibration_row(row: CC.SampleRow) -> bool:
    """只认本脚本写入的校准行（`task_id` 前缀 `calib:`）——**避免历史噪声混入**"""
    return str(row.task_id).startswith("calib:")


# ════════════════════════════════════════════════════════════
#  五、报告渲染
# ════════════════════════════════════════════════════════════


def render_report(*, path_used: str, calibration: Any, table: Mapping[str, Any],
                  credentials: Mapping[str, Any], provenance: Mapping[str, Any],
                  run_meta: Mapping[str, Any], generated_at: str) -> str:
    """逐数字可溯源的偏差分析报告（Markdown）"""
    anchor = table.get("anchor_model")
    rows = list(table.get("rows") or [])
    # 章节号按实际内容编排：路径 A 会有"三、执行元数据"一节，故结论顺延为「四」；
    # 路径 B 无该节 → 结论为「三」（不留空号，也不与上一节重号）。
    sec = {"conclusion": "四" if run_meta.get("path_a") else "三"}
    lines: List[str] = [
        "# 成本系数校准 · 偏差分析报告（TASK-S7-03）",
        "",
        f"> 生成时间：{generated_at}｜执行路径：**{path_used}**｜"
        f"方法 `{calibration.method}`｜工件版本 `{calibration.version}`",
        f"> 方案：[`docs/zh/成本系数校准方案.md`](docs/zh/成本系数校准方案.md)"
        "｜核心算法：`agent/observability/cost_calibration.py`",
        "",
    ]
    # ── 诚实声明（降级时必须首段出现）─────────────────────
    if calibration.version == CC.MEASURED_PARTIAL_VERSION:
        lines += [
            "## 〇、**必读：未完成完整实测校准，结论置信度受限**",
            "",
            f"本报告的数据来源为 **`{calibration.method}`**"
            "（离线重放历史 `cost` 事件，可选的导入 CSV），"
            "**不是在 L2 Core-50 上对多模型的同批实跑**。因此：",
            "",
            "1. 样本的**任务口径与实验设计不一致**（历史事件的 `task_id` 未必对应用例）；",
            "2. 样本量与 §二 设计门槛的差距见下表 `样本量` 列；",
            "3. 本报告**不作为系数替换依据**，工件版本为 `measured.partial.v1` 即为此意；",
            "4. **没有任何数字是估计或补全的**——每个数字都带 `路径:行号` 溯源。",
            "",
        ]
    if not credentials.get("usable"):
        reasons = "; ".join(
            f"{p['env']}={p['verdict']}（{p['reason']}；来源 {p.get('origin') or '—'}）"
            for p in credentials.get("providers", []) if p["verdict"] != "empty")
        lines += [
            "### 凭证状况（路径 A 未执行的原因）",
            "",
            f"- 端点 `{credentials.get('base_url') or '（无可探测端点）'}`"
            f"（来源 {credentials.get('base_url_origin') or '—'}）；"
            f"可用凭证：**{credentials.get('usable_providers') or '无'}**",
            f"- 探测结论：{reasons or '所有候选变量均为空'}",
            "- 端点探测为 `GET /models`（**不产生计费**）；"
            "占位符与空值一律判为**无凭证**，不以"
            "「env 里有这个变量名」充数。",
            "",
        ]
    # ── 偏差表 ────────────────────────────────────────────
    lines += [
        "## 一、偏差表（价格系数 vs 实测系数）",
        "",
        f"- 锚模型（分母）：**{anchor}**"
        f"（`anchor_source={U.resolve_anchor_model()[1]}`）"
        f"｜锚样本 token 构成 in={table.get('anchor_tokens', {}).get('in')} / "
        f"out={table.get('anchor_tokens', {}).get('out')}",
        f"- 最小样本量门槛：**{table.get('min_samples_per_model')}** 条/模型"
        "（低于此**只披露不结论**）",
        "",
        "| 模型 | 价格系数(有效标量) | 实测系数 | 偏差率 | 样本量 | 任务数 | 置信度 | 备注 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        measured = row.get("measured_coefficient")
        dev = row.get("deviation")
        lines.append(
            f"| `{row['model']}` "
            f"| {_fmt(row.get('price_coefficient_effective'))} "
            f"| {'—（样本不足）' if measured is None else _fmt(measured)} "
            f"| {'—' if dev is None else _fmt_pct(dev)} "
            f"| {row.get('samples')} | {row.get('tasks')} "
            f"| {row.get('confidence')} "
            f"| {row.get('note') or ('锚模型（自比）' if row.get('is_anchor') else '')} |")
    lines += ["", _anchors_row_explanation()]
    # ── 异常点名 ──────────────────────────────────────────
    flagged = [r for r in rows if r.get("deviation") is not None
               and abs(float(r["deviation"])) >= 0.30 and r.get("adequate")]
    lines += ["", "### 1.1 实测显著偏离标价的模型（点名）", ""]
    if flagged:
        for row in flagged:
            lines.append(
                f"- **`{row['model']}`**：实测 {_fmt(row.get('measured_coefficient'))} "
                f"vs 价格 {_fmt(row.get('price_coefficient_effective'))}，"
                f"偏差 **{_fmt_pct(row['deviation'])}**（样本 {row['samples']}；"
                f"成功 {_fmt(row.get('success_rate'))}、"
                f"每调用重试 {_fmt(row.get('retries_per_call'))}）")
    else:
        lines.append("- **无**：没有模型同时满足「样本达标」且「|偏差率| ≥ 30%」。"
                     "本项**不因样本不足而给出任何结论**。")
    # ── 指标明细（每个数字带来源）─────────────────────────
    lines += [
        "",
        "## 二、逐模型指标明细（来源可溯源）",
        "",
        "| 模型 | 调用数 | token(in/out) | 单位任务 token | 重试/调用 | 成功率 "
        "| 缓存命中率 | 单位任务成本(分) | 数据来源 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    by_model_stats = provenance.get("by_model", {})
    for row in rows:
        stats = by_model_stats.get(row["model"], {})
        lines.append(
            f"| `{row['model']}` | {row.get('samples')} "
            f"| {row.get('tokens_in')}/{row.get('tokens_out')} "
            f"| {_fmt(row.get('tokens_per_task'))} "
            f"| {_fmt(row.get('retries_per_call'))} "
            f"| {_fmt(row.get('success_rate'))} "
            f"| {_fmt(stats.get('cache_hit_rate'))} "
            f"| {_fmt(row.get('cents_per_task_raw'))} "
            f"| {_rel((stats.get('provenance') or ['—'])[0])} |")
    lines += [
        "",
        "### 2.1 溯源清单（每个数字的出处）",
        "",
        f"- 事件流目录：`{_rel(provenance.get('directory'))}`",
    ]
    for item in provenance.get("files", []):
        lines.append(f"  - `{_rel(item['path'])}`（{item['bytes']} bytes）")
    lines += [
        f"- 去重口径：{provenance.get('dedupe')}"
        f"｜唯一 `event_id` 数：{provenance.get('unique_event_ids')}",
        f"- 命中模型的 `cost` 事件行数：**{table.get('total_sample_rows')}**"
        f"（模型清单：{table.get('models_in_sample')}）",
        f"- 无样本的模型：{provenance.get('models_without_samples') or '无'}",
        f"- 缺 `task_id` 的行：{provenance.get('rows_without_task_id')}"
        "（每行各算一个独立任务，**这是任务口径的已知偏差**）",
        f"- 含 `error` 的行：{provenance.get('rows_with_error')}（计入成功率分母）",
    ]
    imported = run_meta.get("imported_csv") or {}
    if imported:
        lines += [
            "",
            "### 2.2 导入 CSV 的采用与跳过",
            "",
            f"- 文件：`{imported.get('path')}`｜列：{imported.get('columns')}",
            f"- 共 {imported.get('rows_total')} 行 → **采用 {imported.get('rows_used')}**，"
            f"跳过 {imported.get('rows_skipped')}（**不插值、不补零**）",
            f"- 逐模型行数：{imported.get('by_model')}",
            f"- 无时间戳的行：{imported.get('rows_without_ts')}",
        ]
        for item in imported.get("skipped", [])[:10]:
            lines.append(f"  - 跳过第 {item['line']} 行：{item['reason']}")
    # ── 路径 A 元数据 ────────────────────────────────────
    if run_meta.get("path_a"):
        pa = run_meta["path_a"]
        lines += [
            "",
            "## 三、路径 A 执行元数据",
            "",
            f"- 用例集：`{pa.get('caseset_path')}`"
            f"（sha256 `{str(pa.get('caseset_sha256') or '')[:16]}…`）",
            f"- 用例：{pa.get('cases_run')}/{pa.get('cases_total')}"
            f"（`--max-cases` 截断：{pa.get('truncated_by_max_cases')}）",
            f"- 预算：{pa.get('budget')}（**达到上限即停**："
            f"{pa.get('truncated_by_budget')}）",
            f"- 从事件流回读的校准样本行：{pa.get('event_rows_read_back')}",
            "",
            "| 模型 | 已评测 | 未评测 | 通过 | 通过率 | p99 墙钟(ms) |",
            "|---|---|---|---|---|---|",
        ]
        for item in pa.get("per_model", []):
            lines.append(
                f"| `{item.get('model')}` | {item.get('assessed')} "
                f"| {item.get('unassessed')} | {item.get('passed')} "
                f"| {item.get('pass_rate')} | {item.get('p99_wall_ms')} |")
        trace = run_meta.get("trace_reconciliation") or {}
        if trace:
            lines += [
                "",
                f"- TraceStore 对账：{trace.get('note')}"
                f"（台账 {trace.get('path')}）",
            ]
    # ── 结论 ──────────────────────────────────────────────
    lines += [
        "",
        f"## {sec['conclusion']}、结论与后续动作",
        "",
        f"- 可生效（实测）模型：**{table.get('measured_models') or '无'}**",
        f"- 样本不足未生效模型：**{table.get('insufficient_models') or '无'}**"
        "（**只披露不结论**）",
        "- 落地约定：未实测模型**逐模型回落**价格锚定系数"
        "（`coefficient_table()` 的 `coefficient_sources` 可逐项核对）；"
        "**历史成本口径不追溯**。",
        "- 复校周期：**季度**；提前触发条件见校准方案 §九"
        "（单价变更 / 锚模型变更 / 用例集哈希变更 / 滚动偏差 > 20%）。",
        "",
        f"### {sec['conclusion']}.1 本报告的自我边界",        "",
    ]
    lines += [f"- {item}" for item in calibration.disclosures]
    lines += [
        "- 全部数字均由 `agent/observability/cost_calibration.py` 从样本行导出，"
        "**无任何人工填写或估计值**；表内 `数据来源` 列给出首个溯源位置。",
        "",
    ]
    return "\n".join(lines)


def _anchors_row_explanation() -> str:
    return ("**有效标量价格系数**＝把 `{in,out}` 二元组压在"
            "**锚模型本批真实 token 构成**上的加权值"
            "（`(in×k_in + out×k_out)/(in+out)`）；"
            "不做这一压缩就相减，会把「输入输出构成差异」误记为「模型成本偏差」。")


def _fmt(value: Any, digits: int = 6) -> str:
    if value is None or value == "":
        return "—"
    try:
        return f"{float(value):.{digits}g}"
    except (TypeError, ValueError):
        return str(value)


def _fmt_pct(value: Any) -> str:
    try:
        return f"{float(value) * 100:+.2f}%"
    except (TypeError, ValueError):
        return "—"


def _rel(path: Any) -> str:
    """把绝对路径转成**仓库相对路径**（报告可读、可跨机器引用）

    优先相对**主工作区根**（并行会话的 worktree 场景下，事件流在主工作区里），
    否则相对本 worktree 根；两者都不可用时原样返回。
    """
    text = str(path or "")
    if not text or text == "—":
        return text or "—"
    for base in _env_base_roots():
        try:
            relative = os.path.relpath(text, base)
        except (ValueError, OSError):
            continue
        if not relative.startswith(".."):
            return relative.replace("\\", "/")
    return text


def _env_base_roots() -> List[str]:
    """仓库相对路径的基准根（顺序：本 worktree → 主工作区）"""
    roots = [_repo_root()]
    parent = os.path.dirname(_repo_root())
    if os.path.basename(parent) == ".worktrees":
        roots.append(os.path.dirname(parent))
    return roots


# ════════════════════════════════════════════════════════════
#  六、CLI
# ════════════════════════════════════════════════════════════


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="TASK-S7-03 成本系数校准（路径 A 实跑 / 路径 B 降级重放）",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--path", choices=("auto", "a", "b"), default="auto",
                        help="auto（默认）：有可用凭证走 A，否则走 B")
    parser.add_argument("--probe-credentials", action="store_true",
                        help="只探测凭证与端点，不跑任何用例（不产生计费）")
    parser.add_argument("--models", default="",
                        help="参与校准的模型（逗号分隔；缺省取 MODEL_COSTS 中"
                             "除锚以外的全部）")
    parser.add_argument("--anchor", default="",
                        help="锚模型（缺省用 utc.resolve_anchor_model()）")
    parser.add_argument("--events-dir", default="", help="事件流目录（缺省 data/events）")
    parser.add_argument("--import-csv", action="append", default=[],
                        help="导入外部实测 CSV（可重复）")
    parser.add_argument("--caseset", default="", help="用例集路径（缺省 L2 Core-50）")
    parser.add_argument("--max-cases", type=int, default=50,
                        help="路径 A 最多跑多少条用例（控预算；0=全部）")
    parser.add_argument("--max-cost-cents", type=float, default=None,
                        help="路径 A 成本硬上限（分）；达到即停")
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_MAX_TOKENS,
                        help="路径 A 单次输出 token 上限")
    parser.add_argument("--write", action="store_true",
                        help="写出校准件（**默认不写**：未达标时写件会造成口径污染）")
    parser.add_argument("--artifact", default="",
                        help="校准件路径（缺省 data/cost_coefficients.json）")
    parser.add_argument("--report", default="",
                        help="偏差分析报告输出路径（Markdown）")
    parser.add_argument("--json-out", default="", help="偏差表 JSON 输出路径")
    parser.add_argument("--print-md", action="store_true", help="把报告打到 stdout")
    return parser


def resolve_models(explicit: str, anchor: str) -> List[str]:
    """参与模型清单（**排除锚模型**，锚是分母不是变量）"""
    names = [x.strip() for x in str(explicit or "").split(",") if x.strip()]
    if not names:
        names = [n for n in sorted(U.model_costs().keys()) if n != anchor]
    return [n for n in names if n != anchor]


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    anchor, anchor_source = U.resolve_anchor_model()
    if args.anchor:
        os.environ[U.ENV_ANCHOR_MODEL] = args.anchor
        U.reset_config_cache()
        anchor, anchor_source = U.resolve_anchor_model()
    models = resolve_models(args.models, anchor)

    if args.probe_credentials:
        status = credential_status(models)
        print(json.dumps(status, ensure_ascii=False, indent=2))
        print(f"\n[结论] 可用凭证：{status['usable_providers'] or '无'}"
              f"｜路径 A 可执行：{status['multi_model_usable']}")
        # 探测本身**不代表成败**：无凭证是合法状态（走降级路径）
        return 0

    credentials = credential_status(models)
    path = args.path
    if path == "auto":
        path = "a" if credentials["multi_model_usable"] else "b"

    budget = Budget(args.max_cost_cents)
    trace: List[Dict[str, Any]] = []
    run_meta: Dict[str, Any] = {"requested_path": args.path, "used_path": path}
    rows: List[CC.SampleRow] = []
    replay_meta: Dict[str, Any] = {}

    if path == "a":
        if not credentials["multi_model_usable"]:
            print("[FAIL] 路径 A 需要可用凭证（≥2 个参与模型可在端点验证）；"
                  "请先跑 --probe-credentials，或改用 --path b", file=sys.stderr)
            return 1
        rows, run_meta["path_a"] = run_path_a(
            models, max_cases=args.max_cases, budget=budget,
            caseset_path=args.caseset, trace=trace)
        run_meta["path_a"]["max_tokens"] = args.max_tokens
        method = CC.METHOD_L2_RUN
        sample_scope = f"L2 Core-50 × {len(models)} 模型实跑"
        if budget.exhausted:
            run_meta["path_a"]["truncated_by_budget"] = True
    else:
        rows, replay_meta = replay_event_rows(models + [anchor],
                                              events_dir=args.events_dir)
        method = CC.METHOD_OFFLINE_REPLAY
        sample_scope = "历史 cost 事件离线重放"
        for csv_path in args.import_csv:
            extra, meta = import_csv_rows(csv_path, known_models=models + [anchor])
            rows.extend(extra)
            run_meta.setdefault("imported_csv_list", []).append(meta)
            method = (CC.METHOD_MIXED if method == CC.METHOD_OFFLINE_REPLAY
                      else CC.METHOD_IMPORTED_CSV)
            sample_scope = "历史 cost 事件离线重放 + 导入 CSV"

    if run_meta.get("imported_csv_list"):
        run_meta["imported_csv"] = run_meta["imported_csv_list"][0]

    calibration, table = CC.build_calibration(
        rows, anchor_model=anchor, method=method,
        caseset_sha256=str((run_meta.get("path_a") or {}).get("caseset_sha256") or ""),
        sample_scope=sample_scope)
    run_meta["replay"] = replay_meta
    run_meta["trace_reconciliation"] = _trace_reconciliation(trace)

    provenance = dict(replay_meta)
    provenance["by_model"] = _by_model_stats(rows)

    generated_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    report = render_report(path_used=path, calibration=calibration, table=table,
                           credentials=credentials, provenance=provenance,
                           run_meta=run_meta, generated_at=generated_at)

    if args.report:
        _write_text(args.report, report)
        print(f"[OK] 偏差分析报告已写出: {args.report}")
    if args.json_out:
        _write_text(args.json_out, json.dumps(
            {"generated_at": generated_at, "path": path,
             "credentials": credentials, "deviation_table": table,
             "calibration": calibration.to_dict(),
             "replay": replay_meta,
             "imported_csv": run_meta.get("imported_csv_list", []),
             "path_a": run_meta.get("path_a"),
             "trace_reconciliation": run_meta.get("trace_reconciliation")},
            ensure_ascii=False, indent=2, default=str) + "\n")
        print(f"[OK] 偏差表 JSON 已写出: {args.json_out}")
    if args.print_md or (not args.report and not args.json_out):
        print(report)

    if args.write:
        if not calibration.models:
            print("[WARN] 无达标模型（样本不足）→ **不写校准件**"
                  "（避免用不达标数据污染口径）", file=sys.stderr)
        else:
            target = CC.write_artifact(calibration, args.artifact or None)
            print(f"[OK] 校准件已写出: {target}"
                  f"（version={calibration.version}，"
                  f"生效模型={calibration.measured_models}）")

    degraded = calibration.version == CC.MEASURED_PARTIAL_VERSION
    print(f"\n[结论] 路径={path}｜方法={method}｜生效模型="
          f"{calibration.measured_models or '无'}｜样本不足="
          f"{calibration.insufficient_models or '无'}")
    if degraded:
        print("[诚实声明] **未完成完整实测校准，结论置信度受限**："
              "本次为降级路径（离线重放 / 导入 CSV），不作为系数替换依据。")
    # 退出码：有可用样本且无异常 → 0；样本全不足（无可结论）→ 3；执行失败 → 1
    return 0 if calibration.models else 3


def _by_model_stats(rows: Sequence[CC.SampleRow]) -> Dict[str, Dict[str, Any]]:
    grouped = CC.group_samples(rows)
    return {name: item.to_dict() for name, item in grouped.items()}


def _trace_reconciliation(trace: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """与 `UnifiedTraceStore` 对账（**只读统计，不写台账**）

    台账不存在 → 如实返回 `available=False`（**不创建运行时文件**）。
    """
    try:
        from agent.observability import trace_v2
    except Exception as e:  # noqa: BLE001 trace 不可用不影响校准
        return {"available": False, "reason": f"trace_v2 不可导入: {e}"}
    path = trace_v2._DEFAULT_DB_PATH
    if not os.path.exists(path):
        return {"available": False, "path": path,
                "calls_recorded": len(trace),
                "note": "统一轨迹库不存在（不创建运行时文件）：仅以事件流为数据源"}
    return {"available": True, "path": path, "calls_recorded": len(trace),
            "note": "台账存在；本次校准的样本仍**只取自事件流**（裁定 D 单一数据源）"}


def _write_text(path: str, text: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text if text.endswith("\n") else text + "\n")


if __name__ == "__main__":
    raise SystemExit(main())
