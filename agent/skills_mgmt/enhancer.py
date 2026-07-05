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

    # Item 4 自动参数迭代阈值
    MIN_PARAM_SAMPLE = 5              # 参数组合最少样本数
    PARAM_WIN_MARGIN = 0.10           # 比当前 default 高 10%+ 才采纳
    PARAM_AVOID_FAILURE_RATE = 0.50   # 失败率 ≥ 50% → 加入黑名单
    PARAM_AVOID_MIN_SAMPLE = 5        # 黑名单触发的最小样本数

    def optimize_params(self, skill_id: str,
                        feedback_summary: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """基于使用指标 + 用户反馈推荐参数调整 + 自动持久化最佳参数

        策略:
            - 失败率 > 30% → 重置到默认参数（自动持久化）
            - 平均延迟 > 5s → 标记需要优化 (返回建议)
            - 成功率 ≥ 99% & 使用 ≥ 10 & 状态 APPROVED → 自动晋升 PUBLISHED
            - 反馈维度（若传入 feedback_summary）:
                * 平均评分 < 3.0 → 标记需改进参数
                * 满意度 ≥ 90% & 总反馈 ≥ 5 → 加分项，建议晋升
                * 满意度 < 50% & 总反馈 ≥ 5 → 建议降级或合并
            - Item 4 自动参数迭代:
                * 从 param_stats 中找出成功率最高、样本≥5 的参数组合
                * 若比当前 default_params 成功率高 ≥ 10% → 持久化为新 default (patch 版本)
                * 失败率 ≥ 50% 的参数组合 → 加入 avoid_params 黑名单
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

            # Item 4: 自动参数迭代 — 找出最佳参数组合
            auto_result = self._auto_persist_best_params(skill)
            if auto_result:
                actions.update(auto_result["actions"])
                recommendations.extend(auto_result["recommendations"])

            # Item 4: 黑名单参数组合
            avoid_added = self._scan_avoid_params(skill)
            if avoid_added:
                actions["avoid_params_added"] = avoid_added
                recommendations.append(
                    f"识别 {len(avoid_added)} 个失败率过高的参数组合，"
                    "已加入黑名单"
                )

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

    def _auto_persist_best_params(self, skill: Skill) -> Dict[str, Any]:
        """从 param_stats 找出最优参数组合并持久化为 default_params

        判定条件（同时满足）:
            - 样本数 ≥ MIN_PARAM_SAMPLE
            - 成功率比当前 default_params 的成功率高 ≥ PARAM_WIN_MARGIN
            - 平均延迟不劣于当前 default_params 的 1.5 倍
            - 与当前 default_params 哈希不同（避免空操作）
            - 不在 avoid_params 黑名单内

        持久化方式:
            - 调用 bump_version("patch") 升级版本
            - 写入新 default_params + 旧版本快照
        """
        m = skill.metrics
        if not m.param_stats:
            logger.debug(
                "[ParamIter] skill=%s 跳过: param_stats 为空", skill.id,
            )
            return {"actions": {}, "recommendations": []}

        # 计算当前 default_params 的成功率基线
        current_hash = self._hash_params(skill.default_params)
        current_stat = m.param_stats.get(current_hash)
        if current_stat:
            cur_total = current_stat.get("success", 0) + current_stat.get("failure", 0)
            cur_success_rate = (
                current_stat.get("success", 0) / cur_total if cur_total > 0 else 0.0
            )
            cur_avg_latency = (
                current_stat.get("total_latency_ms", 0.0) / cur_total
                if cur_total > 0 else 0.0
            )
        else:
            cur_success_rate = m.success_rate
            cur_avg_latency = m.avg_latency_ms

        logger.info(
            "[ParamIter] skill=%s 开始扫描 | param_stats=%d 组 | "
            "current_default_hash=%s | baseline_success_rate=%.4f "
            "(样本 %d) | baseline_avg_latency=%.1fms",
            skill.id, len(m.param_stats), current_hash,
            cur_success_rate,
            (current_stat.get("success", 0) + current_stat.get("failure", 0))
            if current_stat else 0,
            cur_avg_latency,
        )

        best_key = None
        best_stat = None
        best_rate = -1.0
        avoid_keys = {
            self._hash_params(entry.get("params", {}))
            for entry in m.avoid_params
            if isinstance(entry, dict)
        }
        scanned = 0
        skipped_blacklist = 0
        skipped_low_sample = 0
        skipped_no_margin = 0
        skipped_high_latency = 0
        for key, stat in m.param_stats.items():
            if key == current_hash:
                continue
            if key in avoid_keys:
                skipped_blacklist += 1
                logger.debug(
                    "[ParamIter] skill=%s 候选 %s 跳过: 在 avoid_params 黑名单",
                    skill.id, key,
                )
                continue
            scanned += 1
            total = stat.get("success", 0) + stat.get("failure", 0)
            if total < self.MIN_PARAM_SAMPLE:
                skipped_low_sample += 1
                logger.debug(
                    "[ParamIter] skill=%s 候选 %s 跳过: 样本 %d < %d",
                    skill.id, key, total, self.MIN_PARAM_SAMPLE,
                )
                continue
            rate = stat.get("success", 0) / total
            avg_lat = stat.get("total_latency_ms", 0.0) / total
            margin = rate - cur_success_rate
            logger.info(
                "[ParamIter] skill=%s 候选 %s 命中: 样本=%d success=%d "
                "failure=%d | success_rate=%.4f (Δ%+.4f) | avg_latency=%.1fms",
                skill.id, key, total,
                stat.get("success", 0), stat.get("failure", 0),
                rate, margin, avg_lat,
            )
            # 成功率优势 + 延迟可接受
            latency_cap = max(cur_avg_latency * 1.5, 1000)
            if rate < cur_success_rate + self.PARAM_WIN_MARGIN:
                skipped_no_margin += 1
                logger.debug(
                    "[ParamIter] skill=%s 候选 %s 跳过: 优势 %+.4f < 阈值 %.2f",
                    skill.id, key, margin, self.PARAM_WIN_MARGIN,
                )
                continue
            if avg_lat > latency_cap:
                skipped_high_latency += 1
                logger.debug(
                    "[ParamIter] skill=%s 候选 %s 跳过: 延迟 %.1fms > 上限 %.1fms",
                    skill.id, key, avg_lat, latency_cap,
                )
                continue
            if rate > best_rate:
                best_rate = rate
                best_key = key
                best_stat = stat

        logger.info(
            "[ParamIter] skill=%s 扫描完成 | 扫描=%d 黑名单跳过=%d "
            "样本不足=%d 优势不足=%d 延迟过大=%d | best=%s",
            skill.id, scanned, skipped_blacklist, skipped_low_sample,
            skipped_no_margin, skipped_high_latency,
            best_key or "(无)",
        )

        if not best_key or not best_stat:
            logger.info(
                "[ParamIter] skill=%s 无候选满足持久化条件，保持当前 default",
                skill.id,
            )
            return {"actions": {}, "recommendations": []}

        # 找到候选 — 持久化
        new_params = dict(best_stat.get("params", {}))
        old_params = dict(skill.default_params)
        total = best_stat.get("success", 0) + best_stat.get("failure", 0)
        new_rate = best_stat.get("success", 0) / total
        new_avg_lat = best_stat.get("total_latency_ms", 0.0) / total
        changelog = (
            f"自动参数迭代: success_rate {cur_success_rate:.2f}→{new_rate:.2f} "
            f"(样本 {total})"
        )
        logger.info(
            "[ParamIter] skill=%s 准备持久化 best=%s | "
            "success_rate: %.4f → %.4f (Δ%+.4f) | "
            "avg_latency: %.1fms → %.1fms | 样本=%d",
            skill.id, best_key,
            cur_success_rate, new_rate, new_rate - cur_success_rate,
            cur_avg_latency, new_avg_lat, total,
        )
        try:
            old_version = skill.version
            self.bump_version(
                skill.id, "patch",
                changelog=changelog,
                content=skill.content,
            )
            # bump_version 会重新拉取一次技能，需要重新设置 default_params
            fresh = self._store.get(skill.id)
            if fresh is None:
                logger.warning(
                    "[ParamIter] skill=%s bump_version 后技能丢失", skill.id,
                )
                return {"actions": {}, "recommendations": []}
            fresh.default_params = new_params
            fresh.touch()
            self._store.upsert(fresh)
            # 让上层调用者看到最新值
            skill.default_params = new_params
            skill.version = fresh.version
            skill.versions = fresh.versions

            logger.info(
                "[ParamIter] skill=%s ✓ 已持久化 | version %s → %s | "
                "default_params: %s → %s",
                skill.id, old_version, fresh.version,
                old_params, new_params,
            )
            track_event("skill_params_auto_persisted", {
                "skill_id": skill.id,
                "old_success_rate": cur_success_rate,
                "new_success_rate": new_rate,
                "sample_size": total,
            })
            return {
                "actions": {
                    "param_auto_persisted": True,
                    "new_default_params": new_params,
                    "old_default_params": old_params,
                    "new_version": fresh.version,
                    "sample_size": total,
                    "improvement": new_rate - cur_success_rate,
                },
                "recommendations": [
                    f"参数组合样本 {total} 成功率 {new_rate:.2f}，"
                    f"较当前 default 高 {new_rate - cur_success_rate:.2f}，"
                    f"已自动持久化为新 default_params（版本 → {fresh.version}）"
                ],
            }
        except Exception as e:  # noqa: BLE001
            logger.warning(
                "[ParamIter] skill=%s 自动参数持久化失败: %s", skill.id, e
            )
            return {"actions": {}, "recommendations": []}

    def _scan_avoid_params(self, skill: Skill) -> List[Dict[str, Any]]:
        """扫描 param_stats，把失败率过高的参数组合加入黑名单"""
        m = skill.metrics
        if not m.param_stats:
            return []
        added: List[Dict[str, Any]] = []
        existing_keys = {
            self._hash_params(entry.get("params", {}))
            for entry in m.avoid_params
            if isinstance(entry, dict)
        }
        logger.info(
            "[ParamIter] skill=%s 扫描黑名单 | param_stats=%d 组 | "
            "现有黑名单=%d 条",
            skill.id, len(m.param_stats), len(existing_keys),
        )
        for key, stat in m.param_stats.items():
            total = stat.get("success", 0) + stat.get("failure", 0)
            if total < self.PARAM_AVOID_MIN_SAMPLE:
                continue
            failure_rate = stat.get("failure", 0) / total
            if failure_rate >= self.PARAM_AVOID_FAILURE_RATE and \
                    key not in existing_keys:
                added.append({
                    "params_hash": key,
                    "params": stat.get("params", {}),
                    "failure_rate": failure_rate,
                    "sample_size": total,
                    "added_at": __import__("datetime").datetime.now().isoformat(),
                })
                existing_keys.add(key)
                logger.info(
                    "[ParamIter] skill=%s ⚠ 加入黑名单 %s | "
                    "failure_rate=%.4f (≥%.2f) | 样本=%d | params=%s",
                    skill.id, key, failure_rate,
                    self.PARAM_AVOID_FAILURE_RATE, total,
                    stat.get("params", {}),
                )
            else:
                logger.debug(
                    "[ParamIter] skill=%s 候选 %s 未触发黑名单 "
                    "(failure_rate=%.4f, 样本=%d)",
                    skill.id, key, failure_rate, total,
                )
        if added:
            m.avoid_params.extend(added)
            skill.touch()
            self._store.upsert(skill)
            logger.info(
                "[ParamIter] skill=%s 黑名单扫描完成 | 新增 %d 条 | "
                "总黑名单=%d 条",
                skill.id, len(added), len(m.avoid_params),
            )
        else:
            logger.info(
                "[ParamIter] skill=%s 黑名单扫描完成 | 无新增", skill.id,
            )
        return added

    @staticmethod
    def _hash_params(params: Dict[str, Any]) -> str:
        """计算参数组合的 8 位哈希（与 SkillMetrics._record_param_stats 一致）"""
        import hashlib
        import json as _json
        try:
            key_str = _json.dumps(params, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            key_str = str(sorted(params.items()))
        return hashlib.md5(key_str.encode("utf-8")).hexdigest()[:8]

    # ─── 性能追踪 ───

    def record_execution(self, skill_id: str, *,
                         success: bool, latency_ms: float,
                         feedback_rating: int = 0,
                         feedback_id: str = "",
                         trace_id: str = "",
                         params_used: Optional[Dict[str, Any]] = None) -> None:
        """记录一次技能执行

        Args:
            skill_id: 技能ID
            success: 是否成功
            latency_ms: 延迟毫秒
            feedback_rating: 用户评分 1-5（0 表示未采集）
            feedback_id: 关联的 FeedbackRecord ID
            trace_id: 追踪ID（用于可观测性关联）
            params_used: 本次使用的参数组合（用于 Item 4 参数级追踪）
        """
        skill = self._require(skill_id)
        # 若未传 params_used，默认追踪当前 default_params
        effective_params = params_used if params_used is not None else (
            dict(skill.default_params) if skill.default_params else None
        )
        # 记录执行前的指标快照（用于日志对比）
        before_usage = skill.metrics.usage_count
        before_success_rate = skill.metrics.success_rate
        param_hash = self._hash_params(effective_params) if effective_params else None
        before_param_total = 0
        before_param_success = 0
        if param_hash and param_hash in skill.metrics.param_stats:
            s = skill.metrics.param_stats[param_hash]
            before_param_total = s.get("success", 0) + s.get("failure", 0)
            before_param_success = s.get("success", 0)

        skill.metrics.record(
            success=success, latency_ms=latency_ms,
            params_used=effective_params,
        )
        skill.touch()
        self._store.upsert(skill)
        self._fire_hooks("on_executed", skill)

        # 详细日志：每次执行命中参数组合 + 累计成功率变化
        after_param_total = before_param_total + 1
        after_param_success = before_param_success + (1 if success else 0)
        after_param_rate = (
            after_param_success / after_param_total
            if after_param_total > 0 else 0.0
        )
        logger.info(
            "[ParamIter] skill=%s 执行记录 | param_hash=%s | success=%s | "
            "latency=%.1fms | param 命中: %d → %d (success_rate %.4f → %.4f) | "
            "全局 success_rate: %.4f → %.4f (usage %d → %d)",
            skill_id, param_hash or "(none)", success, latency_ms,
            before_param_total, after_param_total,
            (before_param_success / before_param_total
             if before_param_total > 0 else 0.0),
            after_param_rate,
            before_success_rate, skill.metrics.success_rate,
            before_usage, skill.metrics.usage_count,
        )
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
