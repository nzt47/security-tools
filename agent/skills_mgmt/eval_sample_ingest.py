"""评估集扩充管道（任务1 Step 3）— 素材回流为评估样本

素材源（均可回流）:
    - reflection: 反思产物（TASK-02 schema: {type, task_id, input_hash, score,
      suggestions, created_at}）— 低分反思 = 失败任务候选
    - feedback:   feedback quality_cases / 负面反馈（content_summary/comment → 任务候选）
    - novelty:    TASK-06 NoveltyEvent（diff_summary/suggested_action → 任务候选）
    - manual:     人工标注（直接写正式样本，不走本管道）

流程（全部走 DRAFT → 审核 → 入评估集，人工把关）:
    1. extract_from_*: 按规则从素材提取候选样本（含类别推断、难度推断）
    2. run_ingest:     候选 → 脱敏（真实轨迹强制）→ 写入 data/evals/_pending/（DRAFT）
    3. review_pending / approve_draft: 复用 reviewer.SecurityScanner 安全扫描，
       通过后 approve 入对应类别 JSON（EvalSamplePool.save）

不变式（不易）:
    - 默认关闭（EVAL_SAMPLE_INGEST_ENABLED=false）→ run_ingest 零副作用
    - 显式开启后也只产生 DRAFT（_pending/），绝不直接写正式类别 JSON
    - 审核（安全扫描）通过前不得入库；DRAFT 态零副作用（测试证明）
    - 涉及真实交互轨迹（reflection/feedback/novelty）的素材入库前必须走脱敏管道
      （agent.security_utils.DataSanitizer；复用 memory/black_box 同源管道）

配置（优先级: 环境变量 > config.yaml > 硬编码默认值）:
    EVAL_SAMPLE_INGEST_ENABLED   / eval_samples.ingest_enabled      (默认 false)
    EVAL_SAMPLE_PENDING_DIR      / eval_samples.pending_dir         (默认 data/evals/_pending)
    EVAL_SAMPLES_DIR             / eval_samples.dir                 (默认 data/evals)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from .observability import logger

# ════════════════════════════════════════════════════════════
#  配置（env > config.yaml > 默认值）
# ════════════════════════════════════════════════════════════

_DEFAULT_SAMPLES_DIR = Path(__file__).parent.parent.parent / "data" / "evals"
_DEFAULT_PENDING_DIR = _DEFAULT_SAMPLES_DIR / "_pending"


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


def ingest_enabled() -> bool:
    """管道总开关（默认 false — 安全底线，零副作用）"""
    env = os.environ.get("EVAL_SAMPLE_INGEST_ENABLED")
    if env is not None and env.strip():
        return env.strip().lower() in ("true", "1", "yes")
    return str(_cfg_value("ingest_enabled", False)).strip().lower() in ("true", "1", "yes")


def pending_dir() -> Path:
    env = os.environ.get("EVAL_SAMPLE_PENDING_DIR")
    if env and env.strip():
        return Path(env)
    return Path(str(_cfg_value("pending_dir", str(_DEFAULT_PENDING_DIR))))


def samples_dir() -> Path:
    env = os.environ.get("EVAL_SAMPLES_DIR")
    if env and env.strip():
        return Path(env)
    return Path(str(_cfg_value("dir", str(_DEFAULT_SAMPLES_DIR))))


# ════════════════════════════════════════════════════════════
#  去重哈希（与 scripts/eval_samples_validate.py 同算法，契约一致）
# ════════════════════════════════════════════════════════════


def compute_input_hash(category: str, task: str, input_meta: Any) -> str:
    payload = json.dumps(
        {"category": category, "task": task, "input": input_meta or {}},
        sort_keys=True, ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


# ════════════════════════════════════════════════════════════
#  类别/难度推断（复用 enhanced_planner 复杂度语义）
# ════════════════════════════════════════════════════════════

_SEARCH_KEYWORDS = ("查询", "搜索", "检索", "查", "找", "search", "query", "天气", "新闻")
_CODE_KEYWORDS = ("实现", "函数", "代码", "算法", "排序", "去重", "回文", "斐波那契",
                  "编写", "code", "function", "程序")
_TOOL_KEYWORDS = ("计算", "工具", "时间戳", "文件工具", "翻译", "calculator", "tool")
_PLANNING_KEYWORDS = ("规划", "计划", "拆解", "安排", "步骤", "议程", "plan",
                      "planning", "list", "清单")
_COMPLEX_KEYWORDS = ("架构", "系统", "平台", "重构", "迁移", "分布式", "设计一个",
                     "三个月", "发布会", "减重", "方案")
_NORMAL_KEYWORDS = ("区别", "如何", "比较", "建议", "规划", "拆解", "清单")


def infer_category(text: str) -> str:
    """从素材文本推断目标类别（默认 chat — 开放域）"""
    low = text.lower()
    if any(k in low for k in _CODE_KEYWORDS):
        return "code"
    if any(k in low for k in _TOOL_KEYWORDS):
        return "tool"
    if any(k in low for k in _SEARCH_KEYWORDS):
        return "search"
    if any(k in low for k in _PLANNING_KEYWORDS):
        return "planning"
    return "chat"


def infer_difficulty(text: str) -> str:
    """从素材文本推断难度（TRIVIAL/SIMPLE/NORMAL/COMPLEX，复用 enhanced_planner 语义）"""
    low = text.lower()
    if any(k in low for k in _COMPLEX_KEYWORDS):
        return "COMPLEX"
    if any(k in low for k in _NORMAL_KEYWORDS) or len(text) > 40:
        return "NORMAL"
    if len(text) > 15:
        return "SIMPLE"
    return "TRIVIAL"


# ════════════════════════════════════════════════════════════
#  素材提取（候选样本生成）
# ════════════════════════════════════════════════════════════


@dataclass
class Candidate:
    """一条候选样本（素材 → DRAFT 的中间形态）"""
    task: str
    source: str                      # reflection/feedback/novelty/manual
    source_ref: str = ""             # 素材溯源（task_id/case_id/event_type）
    expected_output: Optional[Dict[str, Any]] = None
    input_meta: Dict[str, Any] = field(default_factory=dict)
    difficulty: str = "SIMPLE"
    category: str = "chat"
    note: str = ""

    def to_sample(self, category: Optional[str] = None,
                  difficulty: Optional[str] = None) -> Dict[str, Any]:
        cat = category or self.category
        diff = difficulty or self.difficulty
        meta = {
            "input": dict(self.input_meta),
            "difficulty": diff,
            "source": self.source,
            "source_ref": self.source_ref,
            "note": self.note,
        }
        meta["input_hash"] = compute_input_hash(cat, self.task, self.input_meta)
        return {
            "id": f"draft-{uuid.uuid4().hex[:8]}",
            "category": cat,
            "task": self.task,
            "expected_output": self.expected_output,
            "created_at": datetime.now().isoformat(timespec="seconds"),
            "metadata": meta,
        }


def extract_from_reflection(records: List[Dict[str, Any]],
                            *, min_score: float = 0.6) -> List[Candidate]:
    """反思产物 → 候选（低分反思 = 失败任务，作为行为样本回流）

    record schema（TASK-02 契约）: {task_id?, input_hash?, score, suggestions, task?}
    缺失 task 时尝试从 suggestions[0] 提取；无文本 → 跳过（不硬造）。
    """
    out: List[Candidate] = []
    for rec in records or []:
        if not isinstance(rec, dict):
            continue
        score = rec.get("score")
        if isinstance(score, (int, float)) and score >= min_score:
            continue  # 仅回流失败/待改进反思
        task = rec.get("task") or ""
        if not task and rec.get("suggestions"):
            task = str(rec["suggestions"][0])
        if not task or not str(task).strip():
            continue
        text = str(task)
        out.append(Candidate(
            task=text,
            source="reflection",
            source_ref=str(rec.get("task_id") or rec.get("input_hash") or "reflection"),
            input_meta={"input_text": text[:200]},
            difficulty=infer_difficulty(text),
            category=infer_category(text),
            note="反思产物回流（TASK-02）",
        ))
    return out


def extract_from_feedback(cases: List[Any]) -> List[Candidate]:
    """feedback quality_cases / 负面反馈 → 候选

    接受 QualityCase 对象（.content_summary/.title/.case_id/.tags）或 dict。
    内容为空 → 跳过；含敏感原文的字段在入库前统一走脱敏管道。
    """
    out: List[Candidate] = []
    for case in cases or []:
        if isinstance(case, dict):
            text = case.get("content_summary") or case.get("title") or case.get("comment") or ""
            ref = case.get("case_id") or case.get("feedback_id") or "feedback"
            tags = case.get("tags") or []
        else:
            text = getattr(case, "content_summary", "") or getattr(case, "title", "") or ""
            ref = getattr(case, "case_id", "") or getattr(case, "feedback_id", "") or "feedback"
            tags = list(getattr(case, "tags", []) or [])
        if not text or not str(text).strip():
            continue
        text = str(text)
        out.append(Candidate(
            task=text,
            source="feedback",
            source_ref=str(ref),
            input_meta={"input_text": text[:200]},
            difficulty=infer_difficulty(text),
            category=infer_category(text),
            note="feedback 案例回流",
        ))
    return out


def extract_from_novelty(events: List[Dict[str, Any]]) -> List[Candidate]:
    """TASK-06 NoveltyEvent → 候选

    event schema（novelty_hooks 契约）: {event_type, diff_summary, suggested_action,
    confidence, severity}。任务 = diff_summary（有动作建议时附建议）。
    """
    out: List[Candidate] = []
    for ev in events or []:
        if not isinstance(ev, dict):
            continue
        diff = ev.get("diff_summary") or ""
        action = ev.get("suggested_action") or ""
        if not diff or not str(diff).strip():
            continue
        text = str(diff)
        if action:
            text = f"{text}（建议动作：{action}）"
        out.append(Candidate(
            task=text,
            source="novelty",
            source_ref=str(ev.get("event_type") or "novelty"),
            input_meta={"event_type": str(ev.get("event_type") or ""),
                        "severity": str(ev.get("severity") or "")},
            difficulty=infer_difficulty(text),
            category=infer_category(text),
            note="NoveltyEvent 回流（TASK-06）",
        ))
    return out


# ════════════════════════════════════════════════════════════
#  脱敏管道（真实轨迹强制；复用 agent.security_utils.DataSanitizer）
# ════════════════════════════════════════════════════════════

_REAL_TRAJECTORY_SOURCES = ("reflection", "feedback", "novelty")


def sanitize_text(text: str, sanitizer: Any = None) -> str:
    """脱敏文本（邮箱/电话/密钥等敏感信息 → [REDACTED]）

    复用 memory/black_box.py 同源脱敏管道（agent.security_utils.DataSanitizer）；
    脱敏器不可用时降级为内置正则（不静默放行真实轨迹原文）。
    """
    if sanitizer is not None:
        try:
            return sanitizer.sanitize_string(text)
        except Exception as e:  # noqa: BLE001
            logger.warning("[EvalIngest] 脱敏器调用失败，降级内置正则: %s", e)
    return _fallback_sanitize(text)


# 内置降级脱敏（与 DataSanitizer 语义对齐的最小实现）
_FALLBACK_PATTERNS = (
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[EMAIL]"),
    (re.compile(r"(?:\+?86[- ]?)?1[3-9]\d{9}"), "[PHONE]"),
    (re.compile(r"(?i)(api[_-]?key|secret[_-]?key|password|passwd|token)\s*[:=]\s*\S+"),
     r"\1=[REDACTED]"),
)


def _fallback_sanitize(text: str) -> str:
    out = text or ""
    for pattern, repl in _FALLBACK_PATTERNS:
        out = pattern.sub(repl, out)
    return out


# ════════════════════════════════════════════════════════════
#  DRAFT 写入 / 审核 / 入库
# ════════════════════════════════════════════════════════════


def build_draft(candidate: Candidate, *, sanitizer: Any = None,
                force_sanitize: bool = True) -> Dict[str, Any]:
    """候选 → DRAFT 样本（含脱敏与 review 占位）

    force_sanitize=True 时对真实轨迹素材（reflection/feedback/novelty）强制脱敏。
    """
    sample = candidate.to_sample()
    if force_sanitize and candidate.source in _REAL_TRAJECTORY_SOURCES:
        sample["task"] = sanitize_text(sample["task"], sanitizer)
        sample["metadata"]["input"] = {
            k: sanitize_text(str(v), sanitizer) if isinstance(v, str) else v
            for k, v in sample["metadata"]["input"].items()
        }
        sample["metadata"]["sanitized"] = True
    sample["draft_status"] = "DRAFT"
    sample["review"] = {"status": "pending", "findings": []}
    return sample


def write_draft(sample: Dict[str, Any], *, target_dir: Optional[Path] = None) -> Path:
    """写入 DRAFT（data/evals/_pending/<category>/<id>.json）；返回路径

    零副作用不变式：本函数只写 _pending/，绝不触碰正式类别 JSON。
    """
    base = Path(target_dir) if target_dir else pending_dir()
    cat = str(sample.get("category") or "chat")
    sid = str(sample.get("id") or f"draft-{uuid.uuid4().hex[:8]}")
    path = base / cat / f"{sid}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(sample, f, ensure_ascii=False, indent=2)
    logger.info("[EvalIngest] DRAFT 已写入: %s", path)
    return path


def list_pending(*, base_dir: Optional[Path] = None) -> List[Path]:
    """列出 _pending/ 下全部 DRAFT 文件"""
    base = Path(base_dir) if base_dir else pending_dir()
    if not base.is_dir():
        return []
    return sorted(p for p in base.rglob("*.json"))


def _security_scan(task_text: str, scanner: Any = None) -> Tuple[float, List[Dict[str, Any]]]:
    """复用 reviewer.SecurityScanner 对样本文本做安全扫描

    构造最小 Skill（content=任务文本）走既有扫描链；返回 (评分, findings)。
    异常 → 视为不通过（评分 0），绝不静默放行。
    """
    if scanner is None:
        from .reviewer import SecurityScanner
        scanner = SecurityScanner(block_on_critical=False)
    try:
        from .models import ContentType, Skill
        skill = Skill(
            id="eval-sample-draft",
            name="eval-sample-draft",
            description="",
            content=task_text,
            content_type=ContentType.MARKDOWN,
        )
        score, findings = scanner.scan(skill)
        return score, [f.model_dump() for f in findings]
    except Exception as e:  # noqa: BLE001 扫描异常 → 不通过
        logger.warning("[EvalIngest] 安全扫描异常（按不通过处理）: %s", e)
        return 0.0, [{"severity": "error", "code": "SCAN_ERROR",
                      "message": f"安全扫描异常: {e}"}]


def review_pending(*, base_dir: Optional[Path] = None,
                   scanner: Any = None) -> List[Dict[str, Any]]:
    """审核全部 DRAFT：安全扫描并更新 review 字段（不改变入库状态）"""
    results: List[Dict[str, Any]] = []
    for path in list_pending(base_dir=base_dir):
        try:
            with open(path, "r", encoding="utf-8") as f:
                draft = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("[EvalIngest] DRAFT 读取失败 %s: %s", path, e)
            continue
        score, findings = _security_scan(str(draft.get("task", "")), scanner)
        critical = [f for f in findings if f.get("severity") == "critical"]
        draft["review"] = {
            "status": "rejected" if critical else "passed",
            "security_score": round(score, 2),
            "findings": findings,
            "reviewed_at": datetime.now().isoformat(timespec="seconds"),
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(draft, f, ensure_ascii=False, indent=2)
        results.append({"path": str(path), "review": draft["review"]})
    return results


def approve_draft(path: Path, *, samples_base: Optional[Path] = None,
                  scanner: Any = None) -> Optional[str]:
    """审核通过后入正式类别 JSON（EvalSamplePool.save）；返回 sample_id

    前置条件（不易）:
        - review.status == passed（复用 reviewer 安全扫描）；
        - 通过后追加到对应类别 JSON（与既有样本合并去重，按 id 后者覆盖）；
        - 入库成功删除 DRAFT 文件（幂等：文件不存在返回 None）。
    """
    path = Path(path)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            draft = json.load(f)
    except (OSError, json.JSONDecodeError) as e:
        logger.warning("[EvalIngest] DRAFT 读取失败 %s: %s", path, e)
        return None
    review = draft.get("review") or {}
    if review.get("status") != "passed":
        logger.warning("[EvalIngest] 审核未通过，拒绝入库: %s", path)
        return None
    category = str(draft.get("category") or "chat")
    sample_id = str(draft.get("id") or "")
    if not sample_id:
        logger.warning("[EvalIngest] DRAFT 缺少 id，拒绝入库: %s", path)
        return None

    base = Path(samples_base) if samples_base else samples_dir()
    pool = _make_pool(base)
    existing = pool.load_category(category, force=True)
    merged = dict.fromkeys([s.id for s in existing])
    merged[sample_id] = _sample_from_draft(draft)
    pool.save(category, list(merged.values()))
    try:
        path.unlink()
    except OSError as e:
        logger.warning("[EvalIngest] DRAFT 删除失败（已入库，幂等可忽略）: %s", e)
    logger.info("[EvalIngest] 样本已入库 category=%s id=%s", category, sample_id)
    return sample_id


def _make_pool(base: Path):
    from .evaluator import EvalSamplePool
    return EvalSamplePool(base_dir=str(base))


def _sample_from_draft(draft: Dict[str, Any]) -> Any:
    """DRAFT dict → EvalSample（去掉 draft_status/review 附加字段）"""
    from .evaluator import EvalSample
    return EvalSample(
        id=str(draft.get("id")),
        category=str(draft.get("category") or "chat"),
        task=str(draft.get("task", "")),
        expected_output=draft.get("expected_output"),
        created_at=str(draft.get("created_at", "")),
        metadata=dict(draft.get("metadata") or {}),
    )


def run_ingest(reflection: Optional[List[Dict[str, Any]]] = None,
               feedback: Optional[List[Any]] = None,
               novelty: Optional[List[Dict[str, Any]]] = None, *,
               enabled: Optional[bool] = None,
               sanitizer: Any = None,
               target_dir: Optional[Path] = None) -> Dict[str, Any]:
    """管道主入口：素材 → 候选 → DRAFT（默认关闭，零副作用）

    Args:
        reflection/feedback/novelty: 素材记录（None=不采集该源）
        enabled: 覆盖开关（None=读配置；默认 false → 返回 disabled 不写盘）
        sanitizer: 脱敏器（None=agent.security_utils.DataSanitizer）
        target_dir: DRAFT 写入目录（None=配置 _pending/）

    Returns:
        {"status": "disabled"|"ok", "candidates": n, "drafts": [路径...]}
    """
    if enabled is None:
        enabled = ingest_enabled()
    if not enabled:
        logger.info("[EvalIngest] 管道默认关闭（EVAL_SAMPLE_INGEST_ENABLED=false），零副作用")
        return {"status": "disabled", "candidates": 0, "drafts": []}

    candidates: List[Candidate] = []
    candidates.extend(extract_from_reflection(reflection or []))
    candidates.extend(extract_from_feedback(feedback or []))
    candidates.extend(extract_from_novelty(novelty or []))

    drafts: List[str] = []
    for cand in candidates:
        draft = build_draft(cand, sanitizer=sanitizer, force_sanitize=True)
        path = write_draft(draft, target_dir=target_dir)
        drafts.append(str(path))
    logger.info("[EvalIngest] 管道完成: candidates=%d drafts=%d", len(candidates), len(drafts))
    return {"status": "ok", "candidates": len(candidates), "drafts": drafts}


__all__ = [
    "Candidate", "compute_input_hash", "ingest_enabled", "pending_dir", "samples_dir",
    "infer_category", "infer_difficulty",
    "extract_from_reflection", "extract_from_feedback", "extract_from_novelty",
    "sanitize_text", "build_draft", "write_draft", "list_pending",
    "review_pending", "approve_draft", "run_ingest",
]
