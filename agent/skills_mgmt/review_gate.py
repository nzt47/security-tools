"""技能发布强制审核链（TASK-04 Step 3）

背景（Why）:
    既有审核流程（reviewer.SkillReviewer）审核通过后只把技能置为 APPROVED，
    没有"发布"动作与强制门槛 —— DRAFT/APPROVED 技能可被任意路径晋升 PUBLISHED。
    本模块为 publish 提供强制审核闸门：无 PASSED ReviewResult 禁止发布。

配置（优先级: 环境变量 > config.yaml > 默认值）:
    skills_mgmt.review.enforce_before_publish（.env: SKILLS_REVIEW_ENFORCE_PUBLISH）
        true（默认）: publish 前必须存在 PASSED 的 ReviewResult，否则拒绝
        false（显式豁免）: 允许跳过，但必须写审计日志（audit_file）
    skills_mgmt.review.audit_file（.env: SKILLS_REVIEW_AUDIT_FILE，
        默认 ./data/skills_mgmt_review_audit.jsonl）: 豁免发布审计日志

【不易】约束（禁止触碰）:
    - 不改 reviewer / review 流程：PASSED 判据 = ReviewResult.status == PASSED
      （reviewer 置 PASSED 时已保证三维分达 ReviewThresholds，无需二次计算）
    - 豁免（配置关闭 / force=True）必须写审计日志，留痕可追溯
    - 不触碰 workflow_learning 自动升格链路（自身已带质量门控，裁决 R3 见变更说明）
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from .observability import logger

DEFAULT_AUDIT_FILE = "./data/skills_mgmt_review_audit.jsonl"


def enforce_review(skill, *, force: bool = False, actor: str = "reviewer",
                   reason: str = "") -> None:
    """发布前强制审核校验；未通过时抛 SkillReviewError。

    Args:
        skill: 待发布技能（SkillsMgmtService 已持有）
        force: True 时显式豁免强制审核（必须写审计日志）
        actor: 操作者（审计留痕）
        reason: 豁免原因（审计留痕，空时记 "explicit_waiver"）
    """
    from .exceptions import SkillReviewError
    from .models import ReviewStatus

    if not force and _enforce_before_publish():
        review = skill.review
        if review is None or review.status != ReviewStatus.PASSED:
            raise SkillReviewError(
                f"未通过审核，禁止发布（请先 review 且三维评分达标）: {skill.id}")
    else:
        # 豁免路径（配置关闭或显式 force）：必须写审计日志
        audit_exemption(skill.id, actor=actor,
                        reason=reason or "explicit_waiver")

    # 任务1：发布前置回归门禁查询（只读，不拦截）
    # 【接线】TASK-04 发布审核链前置回归门禁查询：仅读取基线状态并记录日志，
    # 不评估、不写盘、不阻断发布（零行为变化；无基线/查询失败静默跳过）。
    regression_note = _query_regression_note(skill.id)
    if regression_note:
        logger.info("[ReviewGate] 回归门禁查询 skill=%s: %s", skill.id, regression_note)


def _query_regression_note(skill_id: str) -> str:
    """只读查询技能回归基线状态（任务1 接线）；无数据/异常返回空串"""
    try:
        from .eval_regression import query_regression_status
        status = query_regression_status(skill_id)
        if status is None:
            return ""
        return (f"样本集 {status.get('sampleset_version')} "
                f"基线分={status.get('baseline_score')} "
                f"(samples={status.get('sample_count')})")
    except Exception as e:  # noqa: BLE001 只读查询失败不阻断发布
        logger.debug("[ReviewGate] 回归门禁查询失败 skill=%s: %s", skill_id, e)
        return ""


def audit_exemption(skill_id: str, *, actor: str, reason: str) -> None:
    """豁免发布审计日志（JSONL 追加；写盘失败仅告警，不阻断发布）。"""
    rec = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "event": "review_waiver_publish",
        "skill_id": skill_id,
        "actor": actor,
        "reason": reason,
    }
    try:
        path = Path(_audit_file())
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as e:
        logger.warning("[ReviewGate] 审计日志写入失败 skill=%s: %s", skill_id, e)


def _enforce_before_publish() -> bool:
    """优先级: 环境变量 SKILLS_REVIEW_ENFORCE_PUBLISH > config.yaml > 默认 true。"""
    env = os.environ.get("SKILLS_REVIEW_ENFORCE_PUBLISH")
    if env is not None and env.strip():
        return env.strip().lower() in ("true", "1", "yes")
    try:
        cfg = _config_yaml()
        if cfg is not None:
            val = ((cfg.get("skills_mgmt", {}) or {}).get("review", {})
                   or {}).get("enforce_before_publish")
            if val is not None:
                return str(val).strip().lower() in ("true", "1", "yes")
    except Exception as e:  # noqa: BLE001 配置解析失败回退默认 true（保守）
        logger.debug("[ReviewGate] config.yaml 读取失败: %s", e)
    return True


def _audit_file() -> str:
    """优先级: 环境变量 SKILLS_REVIEW_AUDIT_FILE > config.yaml > 默认。"""
    env = os.environ.get("SKILLS_REVIEW_AUDIT_FILE")
    if env is not None and env.strip():
        return env.strip()
    try:
        cfg = _config_yaml()
        if cfg is not None:
            val = ((cfg.get("skills_mgmt", {}) or {}).get("review", {})
                   or {}).get("audit_file")
            if val:
                return str(val)
    except Exception as e:  # noqa: BLE001
        logger.debug("[ReviewGate] config.yaml 读取失败: %s", e)
    return DEFAULT_AUDIT_FILE


def _config_yaml() -> Optional[Dict[str, Any]]:
    """读取仓库根 config.yaml（失败返回 None，不抛异常）。"""
    cfg_path = Path(__file__).resolve().parent.parent.parent / "config.yaml"
    if not cfg_path.exists():
        return None
    import yaml as _yaml

    with open(cfg_path, "r", encoding="utf-8") as f:
        return _yaml.safe_load(f) or {}
