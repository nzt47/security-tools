"""技能增强器 — 版本管理 / 参数优化 / 性能追踪 / 集成钩子

能力:
    1. 版本管理: bump_version (major/minor/patch)，自动保存旧版本快照
    2. 参数优化: 基于使用指标推荐参数调整 (简化版: 高失败率→重置默认)
    3. 性能追踪: record_execution 累积指标
    4. 集成钩子: register_integration_hook — 与云枢其他组件 (chat / memory / tool_router) 联动
"""

from __future__ import annotations
import hashlib
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .models import Skill, SkillVersion, SkillStatus, SkillMetrics
from .exceptions import (
    SkillNotFoundError,
    SkillValidationError,
    ErrorCode,
)
from .observability import logger, emit_metric, track_event, traced_action
from .store import SkillStore


# ──────────────────────────────────────────────
# 版本管理
# ──────────────────────────────────────────────

@dataclass
class VersionBump:
    """版本升级结果"""
    old_version: str
    new_version: str
    changelog: str


def _bump_semver(version: str, kind: str) -> str:
    """计算升级后的版本号"""
    major, minor, patch = (int(x) for x in version.split("-", 1)[0].split("."))
    if kind == "major":
        major += 1
        minor = 0
        patch = 0
    elif kind == "minor":
        minor += 1
        patch = 0
    elif kind == "patch":
        patch += 1
    else:
        raise SkillValidationError(f"非法版本升级类型: {kind} (major/minor/patch)")
    return f"{major}.{minor}.{patch}"


# ──────────────────────────────────────────────
# 集成钩子
# ──────────────────────────────────────────────

@dataclass
class IntegrationHook:
    """集成钩子 — 技能被触发时调用的回调"""
    name: str
    event: str  # on_enabled / on_disabled / on_executed / on_updated
    callback: Callable[[Skill], None]
    description: str = ""


# ──────────────────────────────────────────────
# 增强器
# ──────────────────────────────────────────────

class SkillEnhancer:
    """技能增强器"""

    def __init__(self, store: SkillStore):
        self._store = store
        self._hooks: Dict[str, List[IntegrationHook]] = {}

    # ─── 版本管理 ───

    def bump_version(self, skill_id: str, kind: str, *,
                     changelog: str = "",
                     content: Optional[str] = None) -> VersionBump:
        """升级技能版本

        Args:
            skill_id: 技能ID
            kind: major / minor / patch
            changelog: 变更说明
            content: 新内容 (None 表示保持不变)
        """
        with traced_action("skill_bump_version", skill_id=skill_id, kind=kind) as ctx:
            skill = self._require(skill_id)
            old_ver = skill.version
            new_ver = _bump_semver(old_ver, kind)
            # 保存旧版本快照
            snapshot = SkillVersion(
                version=old_ver,
                content=skill.content,
                changelog=changelog or f"升级到 {new_ver} 前的快照",
                created_by="system",
                hash=hashlib.sha256(
                    skill.content.encode("utf-8")).hexdigest()[:16],
            )
            skill.versions.append(snapshot)
            skill.version = new_ver
            if content is not None:
                skill.content = content
            skill.touch()
            self._store.upsert(skill)
            self._fire_hooks("on_updated", skill)
            ctx["old_version"] = old_ver
            ctx["new_version"] = new_ver
            emit_metric("yunshu_skill_version_bump_total",
                        labels={"success": "true", "kind": kind},
                        kind="counter")
            track_event("skill_version_bumped", {
                "skill_id": skill_id, "old": old_ver, "new": new_ver,
            })
            return VersionBump(old_version=old_ver, new_version=new_ver,
                               changelog=changelog)

    def list_versions(self, skill_id: str) -> List[SkillVersion]:
        """列出技能的所有历史版本 (按时间倒序)"""
        skill = self._require(skill_id)
        # 当前版本也加入列表头部
        current = SkillVersion(
            version=skill.version,
            content=skill.content,
            changelog="当前版本",
            created_at=skill.updated_at,
            created_by=skill.author,
            hash=hashlib.sha256(
                skill.content.encode("utf-8")).hexdigest()[:16],
        )
        return [current] + list(reversed(skill.versions))

    def rollback_version(self, skill_id: str, target_version: str) -> Skill:
        """回滚到指定历史版本"""
        with traced_action("skill_rollback", skill_id=skill_id,
                           target=target_version):
            skill = self._require(skill_id)
            target = next(
                (v for v in skill.versions if v.version == target_version),
                None,
            )
            if not target:
                raise SkillValidationError(
                    f"未找到版本 {target_version}",
                    code=ErrorCode.NOT_FOUND,
                )
            # 当前版本先存档
            current = SkillVersion(
                version=skill.version,
                content=skill.content,
                changelog=f"回滚到 {target_version} 前的快照",
                hash=hashlib.sha256(
                    skill.content.encode("utf-8")).hexdigest()[:16],
            )
            skill.versions.append(current)
            # 应用目标版本
            skill.version = target.version
            skill.content = target.content
            skill.touch()
            self._store.upsert(skill)
            self._fire_hooks("on_updated", skill)
            return skill

    # ─── 参数优化 ───

    def optimize_params(self, skill_id: str,
                        feedback_summary: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """基于使用指标 + 用户反馈推荐参数调整

        策略:
            - 失败率 > 30% → 重置到默认参数
            - 平均延迟 > 5s → 标记需要优化 (返回建议)
            - 成功率 ≥ 99% & 使用 ≥ 10 & 状态 APPROVED → 自动晋升 PUBLISHED
            - 反馈维度（若传入 feedback_summary）:
                * 平均评分 < 3.0 → 标记需改进参数
                * 满意度 ≥ 90% & 总反馈 ≥ 5 → 加分项，建议晋升
                * 满意度 < 50% & 总反馈 ≥ 5 → 建议降级或合并
        """
        with traced_action("skill_optimize_params", skill_id=skill_id):
            skill = self._require(skill_id)
            m = skill.metrics
            recommendations: List[str] = []
            actions: Dict[str, Any] = {}

            if m.usage_count == 0:
                recommendations.append("暂无使用数据，保持当前参数")
            else:
                if m.success_rate < 0.7:
                    recommendations.append(
                        f"失败率 {(1 - m.success_rate) * 100:.1f}% 过高，"
                        "建议重置到默认参数"
                    )
                    actions["reset_to_defaults"] = True
                    skill.default_params = dict(skill.default_params)
                if m.avg_latency_ms > 5000:
                    recommendations.append(
                        f"平均延迟 {m.avg_latency_ms:.0f}ms 过高，"
                        "建议优化内容或拆分子任务"
                    )
                    actions["high_latency"] = True
                if (m.success_rate >= 0.99 and m.usage_count >= 10
                        and skill.status == SkillStatus.APPROVED.value):
                    recommendations.append("表现稳定，建议升级状态为 PUBLISHED")
                    actions["promote_to_published"] = True

            # 反馈维度驱动
            if feedback_summary:
                total_fb = feedback_summary.get("total_feedback", 0)
                sat = feedback_summary.get("satisfaction_rate_percent", 0.0)
                avg_rating = feedback_summary.get("avg_rating", 0.0)
                if total_fb > 0:
                    if avg_rating < 3.0:
                        recommendations.append(
                            f"用户平均评分 {avg_rating:.2f} 偏低，"
                            "建议优化技能参数或内容"
                        )
                        actions["low_rating"] = True
                    if sat >= 90 and total_fb >= 5:
                        recommendations.append("用户反馈满意度高，建议晋升 PUBLISHED")
                        actions["promote_to_published"] = True
                    if sat < 50 and total_fb >= 5:
                        recommendations.append(
                            f"用户满意度仅 {sat:.1f}%，建议降级或与重复技能合并"
                        )
                        actions["consider_deprecate"] = True

            if actions.get("promote_to_published") and \
                    skill.status == SkillStatus.APPROVED.value:
                skill.status = SkillStatus.PUBLISHED
                skill.touch()
                self._store.upsert(skill)

            return {
                "skill_id": skill_id,
                "recommendations": recommendations,
                "actions_taken": actions,
                "metrics_snapshot": m.model_dump(),
                "feedback_summary": feedback_summary or {},
            }

    # ─── 性能追踪 ───

    def record_execution(self, skill_id: str, *,
                         success: bool, latency_ms: float,
                         feedback_rating: int = 0,
                         feedback_id: str = "",
                         trace_id: str = "") -> None:
        """记录一次技能执行

        Args:
            skill_id: 技能ID
            success: 是否成功
            latency_ms: 延迟毫秒
            feedback_rating: 用户评分 1-5（0 表示未采集）
            feedback_id: 关联的 FeedbackRecord ID
            trace_id: 追踪ID（用于可观测性关联）
        """
        skill = self._require(skill_id)
        skill.metrics.record(success=success, latency_ms=latency_ms)
        skill.touch()
        self._store.upsert(skill)
        self._fire_hooks("on_executed", skill)
        emit_metric(
            "yunshu_skill_execution_latency_ms",
            value=latency_ms,
            labels={"success": "true" if success else "failure",
                    "skill_id": skill_id},
            kind="histogram",
        )
        if feedback_rating > 0:
            emit_metric(
                "yunshu_skill_feedback_rating",
                value=feedback_rating,
                labels={"skill_id": skill_id,
                        "success": "true" if success else "failure"},
                kind="histogram",
            )
            track_event("skill_feedback_received", {
                "skill_id": skill_id,
                "rating": feedback_rating,
                "feedback_id": feedback_id,
                "trace_id": trace_id,
            })

    # ─── 反馈驱动 ───

    def get_skill_feedback_summary(self, skill_id: str,
                                   days: int = 30) -> Dict[str, Any]:
        """获取技能的用户反馈聚合统计

        代理调用 FeedbackManager.get_skill_feedback_summary，
        失败时返回空统计而不阻塞主流程。
        """
        with traced_action("skill_get_feedback_summary",
                           skill_id=skill_id, days=days):
            try:
                from agent.feedback import get_feedback_manager
                mgr = get_feedback_manager()
                return mgr.get_skill_feedback_summary(skill_id, days=days)
            except Exception as e:
                logger.warning(
                    "[Enhancer] 获取反馈统计失败 skill=%s: %s",
                    skill_id, e)
                return {
                    "skill_id": skill_id,
                    "total_feedback": 0,
                    "satisfaction_rate_percent": 0.0,
                    "avg_rating": 0.0,
                    "error": str(e),
                }

    def optimize_with_feedback(self, skill_id: str,
                               days: int = 30) -> Dict[str, Any]:
        """一键式：拉取反馈 + 触发优化

        Returns:
            {
                skill_id, recommendations, actions_taken,
                metrics_snapshot, feedback_summary
            }
        """
        fb_summary = self.get_skill_feedback_summary(skill_id, days=days)
        return self.optimize_params(skill_id, feedback_summary=fb_summary)

    # ─── 启用/禁用 ───

    def set_enabled(self, skill_id: str, enabled: bool) -> Skill:
        """启用/禁用技能"""
        skill = self._require(skill_id)
        skill.enabled = enabled
        skill.touch()
        self._store.upsert(skill)
        self._fire_hooks("on_enabled" if enabled else "on_disabled", skill)
        emit_metric(
            "yunshu_skill_toggle_total",
            labels={"success": "true",
                    "enabled": "true" if enabled else "false"},
            kind="counter",
        )
        return skill

    # ─── 集成钩子 ───

    def register_hook(self, hook: IntegrationHook) -> None:
        """注册集成钩子"""
        self._hooks.setdefault(hook.event, []).append(hook)
        logger.info("[Enhancer] 已注册钩子: %s @ %s", hook.name, hook.event)

    def _fire_hooks(self, event: str, skill: Skill) -> None:
        """触发钩子 (失败不阻塞主流程)"""
        for hook in self._hooks.get(event, []):
            try:
                hook.callback(skill)
            except Exception as e:  # noqa: BLE001
                logger.warning(
                    "[Enhancer] 钩子 %s 执行失败: %s", hook.name, e)

    # ─── 内部 ───

    def _require(self, skill_id: str) -> Skill:
        skill = self._store.get(skill_id)
        if not skill:
            raise SkillNotFoundError(skill_id)
        return skill
