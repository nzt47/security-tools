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


def read_audit_log(limit: int = 100, skill_id: str = "",
                   offset: int = 0, since: str = "") -> list:
    """读取人工复核/强制发布审计记录（最新在前）。

    Args:
        limit: 每页最多条数。
        skill_id: 非空时仅返回该技能的记录（精确匹配）。
        offset: 跳过前 N 条匹配记录（分页）。
        since: 仅返回 ts >= since 的记录（ISO 字符串比较，如 "2026-09-03T00:00:00"）。

    Returns:
        list[dict] — {ts, event, skill_id, actor, reason}；文件缺失/损坏行跳过。
    """
    path = Path(_audit_file())
    if not path.exists():
        return []
    records = []
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError as e:
        logger.warning("[ReviewGate] 审计日志读取失败: %s", e)
        return []
    need = max(0, offset) + max(1, limit)
    # 从尾部回溯，收集 up to need 条有效记录（坏行/空行不占名额）
    for line in reversed(lines):
        line = (line or "").strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(rec, dict):
            continue
        rec_skill = str(rec.get("skill_id", ""))
        rec_ts = str(rec.get("ts", ""))
        if skill_id and rec_skill != skill_id:
            continue
        if since and rec_ts < since:
            continue
        records.append({
            "ts": rec_ts,
            "event": rec.get("event", ""),
            "skill_id": rec_skill,
            "actor": rec.get("actor", ""),
            "reason": rec.get("reason", ""),
        })
        if len(records) >= need:
            break
    return records[offset: offset + max(1, limit)]  # 已按文件倒序 = 最新在前


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
