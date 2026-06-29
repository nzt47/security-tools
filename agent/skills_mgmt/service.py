"""技能管理总服务 — 组合所有子服务为一个易用门面

提供:
    - SkillsMgmtService.create_via_ai / create_manual / install
    - SkillsMgmtService.review / search / get / list_all / delete
    - SkillsMgmtService.bump_version / list_versions / rollback_version
    - SkillsMgmtService.optimize_params / record_execution / set_enabled
    - SkillsMgmtService.health
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional

from .models import (
    Skill,
    SkillSearchParams,
    SkillSearchResult,
    SkillStatus,
    ReviewResult,
    SkillVersion,
)
from .exceptions import (
    SkillNotFoundError,
    SkillMgmtError,
)
from .observability import logger, traced_action
from .store import SkillStore
from .creator import SkillCreator
from .reviewer import SkillReviewer, ReviewThresholds
from .searcher import SkillSearcher
from .enhancer import SkillEnhancer, VersionBump, IntegrationHook


class SkillsMgmtService:
    """技能管理总服务 (单例建议)"""

    def __init__(self, *, store_path: Optional[str] = None,
                 llm_client: Optional[Any] = None,
                 http_timeout: int = 15,
                 review_thresholds: Optional[ReviewThresholds] = None):
        self.store = SkillStore(path=store_path)
        self.creator = SkillCreator(self.store, llm_client=llm_client,
                                    http_timeout=http_timeout)
        self.reviewer = SkillReviewer(thresholds=review_thresholds)
        self.searcher = SkillSearcher()
        self.enhancer = SkillEnhancer(self.store)

    # ─── 创建 ───

    def create_via_ai(self, *, name: str, intent: str,
                      category: str = "custom",
                      tags: Optional[list] = None) -> Skill:
        return self.creator.create_via_ai(
            name=name, intent=intent, category=category, tags=tags)

    def create_manual(self, data: Dict[str, Any]) -> Skill:
        return self.creator.create_manual(data)

    def install(self, source: str, *, force: bool = False) -> Skill:
        return self.creator.install(source, force=force)

    # ─── 审核 ───

    def review(self, skill_id: str) -> ReviewResult:
        """审核指定技能 (与所有其他技能做重复检测)"""
        with traced_action("svc_review", skill_id=skill_id):
            skill = self._require(skill_id)
            others = [s for s in self.store.list_all() if s.id != skill_id]
            result = self.reviewer.review(skill, others=others)
            self.store.upsert(skill)  # 持久化审核结果
            return result

    def review_all_pending(self) -> List[Dict[str, Any]]:
        """批量审核所有 pending_review 状态的技能"""
        results = []
        for s in self.store.list_all():
            if s.status == SkillStatus.PENDING_REVIEW.value:
                try:
                    r = self.review(s.id)
                    results.append({
                        "skill_id": s.id, "status": r.status, "score": r.score,
                    })
                except SkillMgmtError as e:
                    results.append({"skill_id": s.id, "error": e.message})
        return results

    # ─── 搜索 ───

    def search(self, params: SkillSearchParams) -> SkillSearchResult:
        return self.searcher.search(self.store.list_all(), params)

    def list_all(self) -> List[Skill]:
        return self.store.list_all()

    def get(self, skill_id: str) -> Skill:
        return self._require(skill_id)

    # ─── 增删改 ───

    def update(self, skill_id: str, patch: Dict[str, Any]) -> Skill:
        """部分更新技能字段"""
        skill = self._require(skill_id)
        data = skill.model_dump()
        # 白名单字段
        allowed = {"name", "description", "tags", "content", "content_type",
                   "config_schema", "default_params", "dependencies",
                   "author", "enabled"}
        for k, v in patch.items():
            if k in allowed:
                data[k] = v
        updated = Skill.from_storage_dict(data)
        updated.touch()
        self.store.upsert(updated)
        return updated

    def delete(self, skill_id: str) -> bool:
        ok = self.store.remove(skill_id)
        if not ok:
            raise SkillNotFoundError(skill_id)
        logger.info("[Service] 技能已删除: %s", skill_id)
        return True

    # ─── 增强器代理 ───

    def bump_version(self, skill_id: str, kind: str, *,
                     changelog: str = "", content: Optional[str] = None) -> VersionBump:
        return self.enhancer.bump_version(
            skill_id, kind, changelog=changelog, content=content)

    def list_versions(self, skill_id: str) -> List[SkillVersion]:
        return self.enhancer.list_versions(skill_id)

    def rollback_version(self, skill_id: str, target_version: str) -> Skill:
        return self.enhancer.rollback_version(skill_id, target_version)

    def optimize_params(self, skill_id: str) -> Dict[str, Any]:
        return self.enhancer.optimize_params(skill_id)

    def record_execution(self, skill_id: str, *,
                         success: bool, latency_ms: float) -> None:
        self.enhancer.record_execution(
            skill_id, success=success, latency_ms=latency_ms)

    def set_enabled(self, skill_id: str, enabled: bool) -> Skill:
        return self.enhancer.set_enabled(skill_id, enabled)

    def register_hook(self, hook: IntegrationHook) -> None:
        self.enhancer.register_hook(hook)

    # ─── 健康检查 ───

    def health(self) -> Dict[str, Any]:
        """健康检查 (供 /api/skills-mgmt/health 调用)"""
        store_health = self.store.health()
        all_skills = self.store.list_all()
        return {
            "ok": store_health.get("ok", False),
            "module": "skills_mgmt",
            "version": "1.0.0",
            "store": store_health,
            "stats": {
                "total": len(all_skills),
                "enabled": sum(1 for s in all_skills if s.enabled),
                "approved": sum(
                    1 for s in all_skills
                    if s.status == SkillStatus.APPROVED.value),
                "pending_review": sum(
                    1 for s in all_skills
                    if s.status == SkillStatus.PENDING_REVIEW.value),
                "rejected": sum(
                    1 for s in all_skills
                    if s.status == SkillStatus.REJECTED.value),
            },
        }

    # ─── 内部 ───

    def _require(self, skill_id: str) -> Skill:
        skill = self.store.get(skill_id)
        if not skill:
            raise SkillNotFoundError(skill_id)
        return skill
